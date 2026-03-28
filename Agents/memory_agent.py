#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from calendar import monthrange
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langgraph.errors import GraphRecursionError

from load_model.AlibabaEmbeddingCall import get_embed_model as get_alibaba_embed_model
from load_model.BGEcall import get_embed_model as get_local_embed_model
from load_model.OpenAIcall import get_llm
from memory.memory_core.memory_system import MemoryCore
from utils.api_error_utils import is_network_api_error

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/prompt/agent_sys.yaml")
DEFAULT_MEMORY_CORE_CONFIG_PATH = Path("config/memory_core_config/agent_sys_memory.yaml")


@dataclass
class AgentResponse:
    """Structured output schema of memory QA agent."""

    answer: str
    gold_answer: Optional[str] = None
    evidence: Optional[str] = None
    sub_questions: Optional[List[str]] = None
    plan_summary: Optional[str] = None


class MemoryAgent:
    """LangChain agent wrapper for MemoryCore-based QA."""

    _RELATIVE_MONTH_FROM_PATTERN = re.compile(
        r"\b(?P<direction>next|last)\s+month\s+from\s+(?P<anchor>[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{1,2}-\d{1,2})\b",
        flags=re.IGNORECASE,
    )
    _JSON_BLOCK_PATTERN = re.compile(r"\{[\s\S]*\}")
    _TRACE_PREFIX_QUESTION_STRATEGY = "QUESTION STRATEGY: "
    _TRACE_PREFIX_DIRECT_ANSWER = "DIRECT ANSWER PAYLOAD: "
    _TRACE_PREFIX_DIRECT_FALLBACK = "DIRECT ANSWER FALLBACK: "
    _TRACE_PREFIX_TOOL_CALL = "TOOL CALL DETAIL: "
    _TRACE_PREFIX_TOOL_RESULT = "TOOL RESULT DETAIL: "
    _TRACE_PREFIX_FINAL_PAYLOAD = "FINAL ANSWER PAYLOAD: "
    _MULTI_HOP_MARKERS = ("respectively", "both", "either", "together", "combined", "in addition")
    _TEMPORAL_CHAIN_MARKERS = ("before", "after", "during", "between", "while")

    @staticmethod
    def _is_unanswerable_text(text: Any) -> bool:
        if not isinstance(text, str):
            return False
        normalized = text.strip().lower()
        if not normalized:
            return True
        markers = (
            "cannot determine",
            "can't determine",
            "cannot answer",
            "can't answer",
            "insufficient evidence",
            "not enough information",
            "no information",
            "no relevant information",
            "not mentioned",
            "unknown",
            "无法确定",
            "无法回答",
            "信息不足",
            "没有足够信息",
            "未提及",
        )
        return any(marker in normalized for marker in markers)

    @classmethod
    def _normalize_output(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        answer_text = payload.get("answer")
        gold_answer = payload.get("gold_answer")

        if isinstance(answer_text, str):
            answer_text = cls._absolutize_relative_time(answer_text).strip()
            payload["answer"] = answer_text

        if isinstance(gold_answer, str):
            gold_answer = cls._absolutize_relative_time(gold_answer).strip() or None
        payload["gold_answer"] = gold_answer

        if cls._is_unanswerable_text(answer_text):
            payload["gold_answer"] = None

        return payload

    @classmethod
    def _absolutize_relative_time(cls, text: str) -> str:
        if not isinstance(text, str) or not text.strip():
            return text

        def _replace(match: re.Match[str]) -> str:
            direction = (match.group("direction") or "").strip().lower()
            anchor_text = (match.group("anchor") or "").strip()
            anchor_dt = cls._parse_anchor_date(anchor_text)
            if anchor_dt is None:
                return match.group(0)

            delta = 1 if direction == "next" else -1
            shifted = cls._shift_month(anchor_dt, delta)
            return shifted.strftime("%B %Y")

        return cls._RELATIVE_MONTH_FROM_PATTERN.sub(_replace, text)

    @staticmethod
    def _parse_anchor_date(text: str) -> Optional[datetime]:
        cleaned = (text or "").strip()
        if not cleaned:
            return None

        formats = (
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%B %d, %Y",
            "%B %d %Y",
            "%b %d, %Y",
            "%b %d %Y",
        )
        for fmt in formats:
            try:
                return datetime.strptime(cleaned, fmt)
            except Exception:
                continue
        return None

    @staticmethod
    def _shift_month(dt: datetime, delta: int) -> datetime:
        month_index = dt.month - 1 + delta
        year = dt.year + month_index // 12
        month = month_index % 12 + 1
        day = min(dt.day, monthrange(year, month)[1])
        return dt.replace(year=year, month=month, day=day)

    @staticmethod
    def _safe_trace_value(value: Any, depth: int = 0) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if depth > 6:
            return "<max_depth>"
        if is_dataclass(value):
            return MemoryAgent._safe_trace_value(asdict(value), depth=depth + 1)
        if isinstance(value, dict):
            return {
                str(k): MemoryAgent._safe_trace_value(v, depth=depth + 1)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [MemoryAgent._safe_trace_value(v, depth=depth + 1) for v in value]
        return str(value)

    @classmethod
    def _compact_trace_value(cls, value: Any, depth: int = 0) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            compact = re.sub(r"\s+", " ", value).strip()
            if len(compact) > 400:
                return compact[:397] + "..."
            return compact
        if depth > 4:
            return "<max_depth>"
        if is_dataclass(value):
            return cls._compact_trace_value(asdict(value), depth=depth + 1)
        if isinstance(value, dict):
            items = list(value.items())
            compact_dict: Dict[str, Any] = {}
            for idx, (k, v) in enumerate(items):
                if idx >= 16:
                    compact_dict["<truncated>"] = f"{len(items) - 16} more fields"
                    break
                compact_dict[str(k)] = cls._compact_trace_value(v, depth=depth + 1)
            return compact_dict
        if isinstance(value, (list, tuple, set)):
            seq = list(value)
            compact_list = [
                cls._compact_trace_value(item, depth=depth + 1) for item in seq[:8]
            ]
            if len(seq) > 8:
                compact_list.append(f"<truncated {len(seq) - 8} more items>")
            return compact_list
        return cls._compact_trace_value(str(value), depth=depth + 1)

    def _log_structured_trace(self, prefix: str, payload: Dict[str, Any]) -> None:
        logger.info(
            "%s%s",
            prefix,
            json.dumps(self._compact_trace_value(payload), ensure_ascii=False),
        )

    def _record_tool_call(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._tool_call_seq += 1
        entry = {
            "call_id": self._tool_call_seq,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": str(tool_name),
            "params": self._safe_trace_value(params),
            "status": "started",
        }
        self._current_tool_calls.append(entry)
        self._log_structured_trace(self._TRACE_PREFIX_TOOL_CALL, entry)
        return entry

    def _finalize_tool_call(
        self,
        entry: Dict[str, Any],
        *,
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        if error is None:
            entry["status"] = "completed"
            entry["result"] = self._compact_trace_value(result)
        else:
            entry["status"] = "failed"
            entry["error"] = str(error)
        self._log_structured_trace(
            self._TRACE_PREFIX_TOOL_RESULT,
            entry,
        )

    def _consume_current_tool_calls(self) -> List[Dict[str, Any]]:
        calls = self._current_tool_calls
        self._current_tool_calls = []
        return calls

    def get_last_tool_calls(self) -> List[Dict[str, Any]]:
        return [dict(call) for call in self._last_tool_calls]

    def get_last_question_plan(self) -> Optional[Dict[str, Any]]:
        if not isinstance(self._last_question_plan, dict):
            return None
        return {
            str(k): self._safe_trace_value(v)
            for k, v in self._last_question_plan.items()
        }

    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH):
        self.config_path = Path(config_path)
        self.config = self._load_config(self.config_path)
        self.memory_core_config_path = self._resolve_related_path(
            self.config_path,
            self.config.get("memory_core_config_path"),
        )
        self.memory_core_config = self._load_memory_core_config(self.memory_core_config_path)
        self._current_tool_calls: List[Dict[str, Any]] = []
        self._last_tool_calls: List[Dict[str, Any]] = []
        self._last_question_plan: Optional[Dict[str, Any]] = None
        self._tool_call_seq = 0

        self.detail_search_defaults: Dict[str, Any] = {
            "topk": 5,
        }
        detail_cfg = self.config.get("detail_search_defaults", {})
        if isinstance(detail_cfg, dict):
            self.detail_search_defaults.update(detail_cfg)
        # backward compatibility
        action_cfg = self.config.get("action_search_defaults", {})
        if isinstance(action_cfg, dict):
            self.detail_search_defaults.update(action_cfg)

        self.memory_sys = self._init_memory_sys(self.memory_core_config)
        if bool(self.config.get("auto_bootstrap_kg_data", True)):
            self._ensure_kg_data_initialized(self.memory_sys)

        self.thread_id = str(self.config.get("thread_id", "memory-agent-1"))
        self.recursion_limit = int(self.config.get("recursion_limit", 60))
        self.retry_recursion_limit = int(
            self.config.get("retry_recursion_limit", max(self.recursion_limit, 120))
        )
        self.model_timeout = self.config.get("model_timeout_seconds")
        if self.model_timeout is not None:
            self.model_timeout = float(self.model_timeout)
            if self.model_timeout <= 0:
                self.model_timeout = None
        self.model_max_retries = max(0, int(self.config.get("model_max_retries", 2)))
        self.network_retry_attempts = max(1, int(self.config.get("network_retry_attempts", 4)))
        self.network_retry_backoff_seconds = max(
            0.0,
            float(self.config.get("network_retry_backoff_seconds", 2.0)),
        )
        self.network_retry_backoff_multiplier = max(
            1.0,
            float(self.config.get("network_retry_backoff_multiplier", 2.0)),
        )
        self.network_retry_max_backoff_seconds = max(
            self.network_retry_backoff_seconds,
            float(self.config.get("network_retry_max_backoff_seconds", 20.0)),
        )
        self.system_prompt = str(self.config["system_prompt"])
        self.planner_prompt = str(
            self.config.get(
                "planner_prompt",
                (
                    "You are a question decomposition planner for a memory QA agent.\n"
                    "Break the question into a small number of evidence-seeking sub-questions.\n"
                    "Return JSON only with keys: goal, question_type, decomposition_reason, "
                    "sub_questions, suggested_tool_order, completion_criteria.\n"
                    "question_type must be one of: direct_lookup, comparison, summary, counting, "
                    "temporal, causal, multi_hop.\n"
                    "sub_questions must be an ordered list of concrete, answerable questions.\n"
                    "suggested_tool_order must be an ordered list chosen from: "
                    "resolve_entity_id, get_entity_profile, search_entity_feature, "
                    "search_entity_event, search_entity_events_by_time, "
                    "search_events_by_time_range, search_details, search_content.\n"
                    "The entity-profile tools are macro/coarse retrieval tools, not the default for ordinary detail lookup.\n"
                    "Use resolve_entity_id early only when you truly plan to use an entity-profile tool.\n"
                    "If the question exposes a concrete semantic anchor besides the entity, prefer search_details.\n"
                    "For overview/profile-summary questions, prefer resolve_entity_id -> get_entity_profile.\n"
                    "For macro entity comparison, use resolve_entity_id plus macro entity tools as needed.\n"
                    "For entity-centered time-bounded macro questions, prefer resolve_entity_id -> search_entity_events_by_time.\n"
                    "Keep the plan minimal and goal-directed."
                ),
            )
        )
        self.model_name = str(self.config.get("model_name", "deepseek-chat"))
        self.agent_temperature = float(self.config.get("agent_temperature", 0.0))

        self.model = init_chat_model(
            self.model_name,
            temperature=self.agent_temperature,
            max_tokens=None,
            timeout=self.model_timeout,
            max_retries=self.model_max_retries,
        )
        self.tools = self._build_tools()
        self.agent = create_agent(
            model=self.model,
            system_prompt=self.system_prompt,
            tools=self.tools,
            response_format=ToolStrategy(AgentResponse),
        )

    @staticmethod
    def _load_config(path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Agent config not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        if not isinstance(config, dict):
            raise ValueError(f"Agent config must be a dict: {path}")

        if not isinstance(config.get("system_prompt"), str) or not config["system_prompt"].strip():
            raise ValueError("`system_prompt` is required in config/prompt/agent_sys.yaml")
        if not isinstance(config.get("memory_core_config_path"), str) or not str(
            config.get("memory_core_config_path")
        ).strip():
            raise ValueError("`memory_core_config_path` is required in agent config")
        return config

    @staticmethod
    def _resolve_related_path(base_config_path: Path, raw_path: Any) -> Path:
        if raw_path is None or not str(raw_path).strip():
            return DEFAULT_MEMORY_CORE_CONFIG_PATH.resolve()
        path = Path(str(raw_path).strip())
        if path.is_absolute():
            return path
        return (base_config_path.parent / path).resolve()

    @staticmethod
    def _load_memory_core_config(path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"MemoryCore config not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        if not isinstance(config, dict):
            raise ValueError(f"MemoryCore config must be a dict: {path}")

        return config

    @staticmethod
    def _init_memory_sys(memory_core_config: Dict[str, Any]) -> MemoryCore:
        config = memory_core_config
        workflow_id = str(config.get("workflow_id", "testrt"))
        llm_temperature = float(config.get("memory_llm_temperature", 0.0))
        similarity_threshold = float(config.get("memory_similarity_threshold", 0.88))
        top_k = int(config.get("memory_top_k", 3))
        use_threshold = bool(config.get("memory_use_threshold", True))
        scene_prompt_version = str(config.get("scene_prompt_version", "v2"))
        fact_prompt_version = str(config.get("fact_prompt_version", "v2"))
        memory_owner_name = str(config.get("memory_owner_name", "changshengEVA"))

        embed_provider = str(
            config.get("embed_provider", os.getenv("EMBED_PROVIDER", "local"))
        ).strip().lower()
        if embed_provider in {"alibaba", "aliyun", "dashscope"}:
            logger.info("Embedding provider: %s (Alibaba API)", embed_provider)
            embed_func = get_alibaba_embed_model()
        elif embed_provider in {"local", "bge"}:
            logger.info("Embedding provider: %s (local BGE)", embed_provider)
            embed_func = get_local_embed_model()
        else:
            raise ValueError(
                f"Unsupported embed_provider: {embed_provider}. "
                "Use one of: local, bge, alibaba, aliyun, dashscope."
            )

        return MemoryCore(
            workflow_id=workflow_id,
            llm_func=get_llm(llm_temperature),
            embed_func=embed_func,
            llm_temperature=llm_temperature,
            similarity_threshold=similarity_threshold,
            top_k=top_k,
            use_threshold=use_threshold,
            scene_prompt_version=scene_prompt_version,
            fact_prompt_version=fact_prompt_version,
            memory_owner_name=memory_owner_name,
        )

    @staticmethod
    def _load_facts_situation(memory_core: MemoryCore) -> Dict[str, Any]:
        facts_situation_path = getattr(memory_core, "facts_situation_file", None)
        if not facts_situation_path:
            return {}
        facts_situation_file = Path(facts_situation_path)
        if not facts_situation_file.exists():
            return {}
        try:
            with open(facts_situation_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:
            logger.warning("Failed to load facts_situation for bootstrap repair (%s): %s", facts_situation_file, exc)
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _should_repair_fact_entity_import(memory_core: MemoryCore) -> Tuple[bool, str]:
        kg_base = getattr(memory_core, "kg_base", None)
        kg_store = getattr(kg_base, "store", None)
        if kg_base is None or not bool(getattr(kg_store, "available", False)):
            return False, "kg unavailable"

        facts_root_path = getattr(memory_core, "facts_dir", None)
        if not facts_root_path:
            return False, "facts dir unavailable"
        facts_root = Path(facts_root_path)
        fact_files = [p for p in facts_root.glob("*.json") if p.is_file()] if facts_root.exists() else []
        if not fact_files:
            return False, "no fact files"

        facts_situation = MemoryAgent._load_facts_situation(memory_core)
        expected_uids = set()
        fact_nodes = facts_situation.get("facts", {}) if isinstance(facts_situation.get("facts"), dict) else {}
        if isinstance(fact_nodes, dict):
            pending_facts = 0
            for node in fact_nodes.values():
                if not isinstance(node, dict):
                    continue
                entity_uid = str(node.get("entity_UID", "") or "").strip()
                if not entity_uid:
                    continue
                expected_uids.add(entity_uid)
                if not bool(node.get("kg_imported", False)):
                    pending_facts += 1
            if pending_facts > 0:
                return True, f"{pending_facts} fact(s) still marked kg_imported=false"

        entities = facts_situation.get("entities", []) if isinstance(facts_situation.get("entities"), list) else []
        for item in entities:
            if not isinstance(item, dict):
                continue
            entity_uid = str(item.get("UID", "") or "").strip()
            if entity_uid:
                expected_uids.add(entity_uid)

        actual_uids = set()
        try:
            actual_uids = set(str(x or "").strip() for x in kg_base.list_entity_ids())
            actual_uids.discard("")
        except Exception as exc:
            logger.warning("Failed to inspect KG entity ids for bootstrap repair: %s", exc)

        missing_uids = expected_uids - actual_uids
        if missing_uids:
            return True, f"{len(missing_uids)} expected fact entity uid(s) missing in KG"

        if not expected_uids and not actual_uids and fact_files:
            return True, "KG is empty while fact files already exist"

        return False, "KG fact entities already aligned"

    @staticmethod
    def _ensure_kg_data_initialized(memory_core: MemoryCore) -> None:
        scene_files = [p for p in memory_core.scene_dir.glob("*.json") if p.is_file()]
        if scene_files:
            should_repair, reason = MemoryAgent._should_repair_fact_entity_import(memory_core)
            if not should_repair:
                logger.info("scene already has %d file(s), skip bootstrap import.", len(scene_files))
                return

            logger.info(
                "scene already has %d file(s); run fact-entity import repair because %s.",
                len(scene_files),
                reason,
            )
            import_result = memory_core.import_fact_entities(force_update=False, use_tqdm=True)
            if not import_result.get("success", False):
                raise RuntimeError(f"Failed to repair fact entity import: {import_result}")
            logger.info(
                "Fact entity import repair completed: scanned=%s created=%s updated=%s failed=%s",
                import_result.get("facts_scanned", 0),
                import_result.get("kg_entities_created", 0),
                import_result.get("kg_entities_updated", 0),
                import_result.get("kg_entities_failed", 0),
            )
            return

        episodes_path = memory_core.episodes_dir
        logger.info("scene is empty, bootstrap import from episodes: %s", episodes_path)
        load_result = memory_core.load_from_episode_path(episodes_path)
        if not load_result.get("success", False):
            error_text = str(load_result.get("error", ""))
            if "no episode json files found" in error_text or "path not found" in error_text:
                logger.warning(
                    "No episode data found for bootstrap, continue with empty memory state (0 entities/0 relations)."
                )
                return
            raise RuntimeError(f"Failed to initialize from episodes: {load_result}")
        logger.info(
            "Bootstrap import completed: processed=%s, failed=%s",
            load_result.get("files_processed", 0),
            load_result.get("files_failed", 0),
        )

    def _build_tools(self):
        @tool
        def resolve_entity_id(entity_name_or_id: str) -> Dict[str, Any]:
            """
            Resolve a mentioned entity name/alias/id into a canonical entity_id.
            Use this before any entity-specific retrieval tool.
            """

            call_entry = self._record_tool_call(
                "resolve_entity_id",
                {"entity_name_or_id": entity_name_or_id},
            )
            logger.info(
                "API call: resolve_entity_id(entity_name_or_id=%s)",
                entity_name_or_id,
            )
            try:
                result = self.memory_sys.resolve_entity_id(
                    entity_name_or_id=entity_name_or_id,
                )
            except Exception as exc:
                self._finalize_tool_call(call_entry, error=exc)
                raise
            self._finalize_tool_call(call_entry, result=result)
            logger.info(
                "API response: resolve_entity_id(hit=%s, entity_id=%s, match_type=%s)",
                result.get("hit") if isinstance(result, dict) else None,
                result.get("entity_id") if isinstance(result, dict) else None,
                result.get("match_type") if isinstance(result, dict) else None,
            )
            return result

        @tool
        def get_entity_profile(entity_id: str) -> Dict[str, Any]:
            """
            Get a short profile summary for one resolved entity_id.
            Use for macro overview, not as the default path for ordinary detail lookup.
            """

            call_entry = self._record_tool_call(
                "get_entity_profile",
                {"entity_id": entity_id},
            )
            logger.info(
                "API call: get_entity_profile(entity_id=%s)",
                entity_id,
            )
            try:
                result = self.memory_sys.get_entity_profile(entity_id=entity_id)
            except Exception as exc:
                self._finalize_tool_call(call_entry, error=exc)
                raise
            self._finalize_tool_call(call_entry, result=result)
            logger.info(
                "API response: get_entity_profile(hit=%s, summary_len=%s)",
                result.get("hit") if isinstance(result, dict) else None,
                len(str(result.get("summary", "") or ""))
                if isinstance(result, dict)
                else None,
            )
            return result

        @tool
        def search_entity_feature(
            entity_id: str,
            feature_query: str,
            topk: Optional[int] = None,
        ) -> Dict[str, Any]:
            """
            Coarse semantic search over one resolved entity's profile features.
            Use for macro profile retrieval, not routine detail lookup.
            """

            cfg_topk = self.detail_search_defaults["topk"] if topk is None else int(topk)
            call_entry = self._record_tool_call(
                "search_entity_feature",
                {"entity_id": entity_id, "feature_query": feature_query, "topk": cfg_topk},
            )
            logger.info(
                "API call: search_entity_feature(entity_id=%s, feature_query=%s, topk=%s)",
                entity_id,
                feature_query,
                cfg_topk,
            )
            try:
                result = self.memory_sys.search_entity_feature(
                    entity_id=entity_id,
                    feature_query=feature_query,
                    topk=cfg_topk,
                )
            except Exception as exc:
                self._finalize_tool_call(call_entry, error=exc)
                raise
            self._finalize_tool_call(call_entry, result=result)
            logger.info(
                "API response: search_entity_feature(hit=%s, matched_count=%s)",
                result.get("hit") if isinstance(result, dict) else None,
                result.get("matched_count") if isinstance(result, dict) else None,
            )
            return result

        @tool
        def search_entity_event(
            entity_id: str,
            event_query: str,
            topk: Optional[int] = None,
        ) -> Dict[str, Any]:
            """
            Coarse event recall for one resolved entity_id.
            Use for macro entity-centered event retrieval.
            """

            cfg_topk = self.detail_search_defaults["topk"] if topk is None else int(topk)
            call_entry = self._record_tool_call(
                "search_entity_event",
                {"entity_id": entity_id, "event_query": event_query, "topk": cfg_topk},
            )
            logger.info(
                "API call: search_entity_event(entity_id=%s, event_query=%s, topk=%s)",
                entity_id,
                event_query,
                cfg_topk,
            )
            try:
                result = self.memory_sys.search_entity_event(
                    entity_id=entity_id,
                    event_query=event_query,
                    topk=cfg_topk,
                )
            except Exception as exc:
                self._finalize_tool_call(call_entry, error=exc)
                raise
            self._finalize_tool_call(call_entry, result=result)
            logger.info(
                "API response: search_entity_event(hit=%s, matched_count=%s)",
                result.get("hit") if isinstance(result, dict) else None,
                result.get("matched_count") if isinstance(result, dict) else None,
            )
            return result

        @tool
        def search_entity_events_by_time(
            entity_id: str,
            start_time: str,
            end_time: Optional[str] = None,
        ) -> Dict[str, Any]:
            """
            Search one resolved entity_id's events within a time window.
            Use when the question is entity-centered, time-bounded, and macro in nature.
            """

            call_entry = self._record_tool_call(
                "search_entity_events_by_time",
                {"entity_id": entity_id, "start_time": start_time, "end_time": end_time},
            )
            logger.info(
                "API call: search_entity_events_by_time(entity_id=%s, start_time=%s, end_time=%s)",
                entity_id,
                start_time,
                end_time,
            )
            try:
                result = self.memory_sys.search_entity_events_by_time(
                    entity_id=entity_id,
                    start_time=start_time,
                    end_time=end_time,
                )
            except Exception as exc:
                self._finalize_tool_call(call_entry, error=exc)
                raise
            self._finalize_tool_call(call_entry, result=result)
            logger.info(
                "API response: search_entity_events_by_time(hit=%s, matched_count=%s)",
                result.get("hit") if isinstance(result, dict) else None,
                result.get("matched_count") if isinstance(result, dict) else None,
            )
            return result

        @tool
        def search_content(dialogue_id: str, episode_id: str) -> Dict[str, Any]:
            """Fetch dialogue original text details and event/time info by dialogue_id + episode_id."""

            call_entry = self._record_tool_call(
                "search_content",
                {"dialogue_id": dialogue_id, "episode_id": episode_id},
            )
            logger.info(
                "API call: search_content(dialogue_id=%s, episode_id=%s)",
                dialogue_id,
                episode_id,
            )
            try:
                result = self.memory_sys.search_content(
                    dialogue_id=dialogue_id,
                    episode_id=episode_id,
                )
            except Exception as exc:
                self._finalize_tool_call(call_entry, error=exc)
                raise
            self._finalize_tool_call(call_entry, result=result)
            logger.info(
                "API response: search_content(success=%s)",
                result.get("success") if isinstance(result, dict) else None,
            )
            return result

        @tool
        def search_events_by_time_range(start_time: str, end_time: str) -> list[Dict[str, Any]]:
            """
            Search scenes by time range.
            Returns a list of items with scene_id, theme, starttime, endtime.
            """

            call_entry = self._record_tool_call(
                "search_events_by_time_range",
                {"start_time": start_time, "end_time": end_time},
            )
            logger.info(
                "API call: search_events_by_time_range(start_time=%s, end_time=%s)",
                start_time,
                end_time,
            )
            try:
                result = self.memory_sys.search_events_by_time_range(
                    start_time=start_time,
                    end_time=end_time,
                )
            except Exception as exc:
                self._finalize_tool_call(call_entry, error=exc)
                raise
            self._finalize_tool_call(call_entry, result=result)
            logger.info(
                "API response: search_events_by_time_range(result_count=%s)",
                len(result) if isinstance(result, list) else None,
            )
            return result

        @tool
        def search_details(detail: str, topk: Optional[int] = None) -> Dict[str, Any]:
            """
            Search concrete behavior/action details from scene memories by semantic similarity.
            This is the default retrieval tool for most detail questions.
            """

            cfg_topk = self.detail_search_defaults["topk"] if topk is None else int(topk)
            call_entry = self._record_tool_call(
                "search_details",
                {"detail": detail, "topk": cfg_topk},
            )
            logger.info(
                "API call: search_details(detail=%s, topk=%s)",
                detail,
                cfg_topk,
            )
            try:
                result = self.memory_sys.search_details(
                    detail_query=detail,
                    topk=cfg_topk,
                )
            except Exception as exc:
                self._finalize_tool_call(call_entry, error=exc)
                raise
            self._finalize_tool_call(call_entry, result=result)
            logger.info(
                "API response: search_details(success=%s, result_count=%s)",
                result.get("hit") if isinstance(result, dict) else None,
                len(result.get("results", []))
                if isinstance(result, dict) and isinstance(result.get("results"), list)
                else None,
            )
            return result

        return [
            resolve_entity_id,
            get_entity_profile,
            search_entity_feature,
            search_entity_event,
            search_entity_events_by_time,
            search_content,
            search_events_by_time_range,
            search_details,
        ]

    @staticmethod
    def _extract_message_text(message: Any) -> str:
        content = getattr(message, "content", message)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: List[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
            return "\n".join(chunk for chunk in chunks if chunk)
        return str(content or "")

    @classmethod
    def _parse_json_block(cls, text: str) -> Optional[Dict[str, Any]]:
        if not isinstance(text, str) or not text.strip():
            return None
        stripped = text.strip()
        candidates = [stripped]
        matched = cls._JSON_BLOCK_PATTERN.search(stripped)
        if matched:
            candidates.append(matched.group(0))

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _normalize_question_plan(question_text: str, plan: Dict[str, Any]) -> Dict[str, Any]:
        normalized_sub_questions = plan.get("sub_questions", [])
        if not isinstance(normalized_sub_questions, list):
            normalized_sub_questions = []
        normalized_sub_questions = [
            str(item).strip() for item in normalized_sub_questions if str(item).strip()
        ]

        tool_order = plan.get("suggested_tool_order", [])
        if not isinstance(tool_order, list):
            tool_order = []
        tool_order = [str(item).strip() for item in tool_order if str(item).strip()]

        question_type = str(plan.get("question_type", "") or "").strip().lower()
        if not question_type:
            question_type = "direct_lookup"

        goal = str(plan.get("goal", "") or "").strip() or question_text
        decomposition_reason = str(plan.get("decomposition_reason", "") or "").strip()
        completion_criteria = str(plan.get("completion_criteria", "") or "").strip()

        if not normalized_sub_questions:
            normalized_sub_questions = [question_text]

        return {
            "goal": goal,
            "question_type": question_type,
            "decomposition_reason": decomposition_reason,
            "sub_questions": normalized_sub_questions,
            "suggested_tool_order": tool_order,
            "completion_criteria": completion_criteria,
        }

    @staticmethod
    def _fallback_question_plan(question_text: str) -> Dict[str, Any]:
        normalized = question_text.strip()
        lowered = normalized.lower()

        if any(token in lowered for token in ("compare", "difference", "different", "similar")) or any(
            token in normalized for token in ("对比", "比较", "区别", "不同", "相同")
        ):
            question_type = "comparison"
            sub_questions = [
                f"What is the evidence for the first side of: {normalized}",
                f"What is the evidence for the second side of: {normalized}",
                f"Based on both sides, what is the supported comparison result for: {normalized}",
            ]
        elif any(token in lowered for token in ("how many", "count", "number of")) or any(
            token in normalized for token in ("多少", "几次", "几个人", "数量", "总共")
        ):
            question_type = "counting"
            sub_questions = [
                f"What candidate evidence items are relevant to: {normalized}",
                f"After deduplication, what is the correct count for: {normalized}",
            ]
        elif any(token in lowered for token in ("summary", "summarize")) or any(
            token in normalized for token in ("总结", "概括", "概述")
        ):
            question_type = "summary"
            sub_questions = [
                f"What are the key evidence points needed to summarize: {normalized}",
                f"What concise summary is supported by those evidence points for: {normalized}",
            ]
        elif any(token in lowered for token in ("when", "before", "after", "during", "date", "time")) or any(
            token in normalized for token in ("什么时候", "之前", "之后", "期间", "日期", "时间")
        ):
            question_type = "temporal"
            sub_questions = [
                f"What events or details establish the time anchors for: {normalized}",
                f"What exact time answer is supported for: {normalized}",
            ]
        elif any(token in lowered for token in ("why", "reason", "because", "cause")) or any(
            token in normalized for token in ("为什么", "原因", "因为", "导致")
        ):
            question_type = "causal"
            sub_questions = [
                f"What happened before or around the target event in: {normalized}",
                f"What evidence-supported cause or reason answers: {normalized}",
            ]
        else:
            question_type = "direct_lookup"
            sub_questions = [normalized]

        return {
            "goal": normalized,
            "question_type": question_type,
            "decomposition_reason": "Fallback heuristic decomposition.",
            "sub_questions": sub_questions,
            "suggested_tool_order": [
                "search_events_by_time_range" if question_type == "temporal" else "search_details",
                "search_content",
            ],
            "completion_criteria": "Each core sub-question is answered with tool-grounded evidence.",
        }

    @classmethod
    def _detect_direct_answer_strategy(cls, question_text: str) -> Tuple[bool, str]:
        normalized = str(question_text or "").strip()
        lowered = normalized.lower()
        heuristic_plan = cls._fallback_question_plan(normalized)
        question_type = str(heuristic_plan.get("question_type", "direct_lookup") or "direct_lookup")

        if question_type in {"comparison", "counting", "summary", "causal"}:
            return True, f"Obvious {question_type} question; decompose first."

        wh_hits = 0
        for pattern in (r"\bwho\b", r"\bwhat\b", r"\bwhen\b", r"\bwhere\b", r"\bwhich\b", r"\bhow\b"):
            if re.search(pattern, lowered):
                wh_hits += 1

        marker_hits = 0
        for token in cls._MULTI_HOP_MARKERS:
            if token in lowered:
                marker_hits += 1

        temporal_chain_hits = 0
        for token in cls._TEMPORAL_CHAIN_MARKERS:
            if token in lowered:
                temporal_chain_hits += 1

        if wh_hits >= 2 and (" and " in lowered or marker_hits > 0):
            return True, "Question contains multiple explicit query targets; decompose first."

        if temporal_chain_hits >= 2 and (" and " in lowered or " then " in lowered):
            return True, "Question chains multiple temporal constraints; decompose first."

        if marker_hits >= 2 and len(normalized) >= 80:
            return True, "Question has clear multi-hop coordination markers; decompose first."

        return False, "Question appears to be a single-goal lookup; answer directly first."

    @staticmethod
    def _build_direct_question_plan(question_text: str, reason: str) -> Dict[str, Any]:
        normalized = str(question_text or "").strip()
        return {
            "goal": normalized,
            "question_type": "direct_lookup",
            "decomposition_reason": reason,
            "sub_questions": [],
            "suggested_tool_order": ["search_details", "search_content"],
            "completion_criteria": "Answer the original question directly with tool-grounded evidence.",
        }

    def _decompose_question(self, question_text: str) -> Dict[str, Any]:
        try:
            response = self._invoke_model_with_network_retry(
                prompt_text=f"{self.planner_prompt}\n\n[User Question]\n{question_text}",
                call_name="decompose_question",
            )
            plan_text = self._extract_message_text(response)
            parsed = self._parse_json_block(plan_text)
            if isinstance(parsed, dict):
                normalized = self._normalize_question_plan(question_text, parsed)
                logger.info(
                    "PLAN UPDATE: %s",
                    json.dumps(self._safe_trace_value(normalized), ensure_ascii=False),
                )
                logger.info(
                    "API response: decompose_question(question_type=%s, sub_question_count=%d)",
                    normalized.get("question_type"),
                    len(normalized.get("sub_questions", [])),
                )
                return normalized
        except Exception as exc:
            if is_network_api_error(exc):
                logger.exception("decompose_question hit network/API error; aborting current run")
                raise
            logger.warning("decompose_question failed, fallback to heuristic plan: %s", exc)

        fallback = self._fallback_question_plan(question_text)
        logger.info(
            "PLAN UPDATE: %s",
            json.dumps(self._safe_trace_value(fallback), ensure_ascii=False),
        )
        logger.info(
            "API response: decompose_question(question_type=%s, sub_question_count=%d, fallback=true)",
            fallback.get("question_type"),
            len(fallback.get("sub_questions", [])),
        )
        return fallback

    @staticmethod
    def _build_sub_question_prompt(
        question_text: str,
        question_plan: Dict[str, Any],
        sub_question: str,
        sub_index: int,
        total_sub_questions: int,
    ) -> str:
        return (
            "[Original Question]\n"
            f"{question_text}\n\n"
            "[Question Plan]\n"
            f"{json.dumps(question_plan, ensure_ascii=False, indent=2)}\n\n"
            "[Current Sub-question]\n"
            f"#{sub_index}/{total_sub_questions}: {sub_question}\n\n"
            "[Task]\n"
            "- Answer only the current sub-question.\n"
            "- Use tools if needed.\n"
            "- search_details is the default tool for ordinary detail questions.\n"
            "- Use the entity-profile tools only for macro/coarse retrieval questions.\n"
            "- If the sub-question is a macro entity question, resolve_entity_id before using entity-specific tools.\n"
            "- Never pass a raw name directly into get_entity_profile, search_entity_feature, "
            "search_entity_event, or search_entity_events_by_time.\n"
            "- If the question includes a concrete semantic anchor besides the entity, prefer search_details.\n"
            "- Use get_entity_profile for overview, entity_feature for macro profile recall, entity_event for coarse actions, "
            "and entity_events_by_time for entity + time-window macro questions.\n"
            "- Use search_content when you need raw dialogue details, exact timing, or final verification.\n"
            "- Keep the answer concrete and grounded in evidence.\n"
            "- gold_answer should be the concise value for this sub-question when possible."
        )

    @staticmethod
    def _build_final_synthesis_prompt(
        question_text: str,
        question_plan: Dict[str, Any],
        sub_question_results: List[Dict[str, Any]],
    ) -> str:
        return (
            "You are the final synthesis stage of a memory QA pipeline.\n"
            "Use only the provided solved sub-questions and their evidence.\n"
            "Return JSON only with keys: answer, gold_answer, evidence.\n"
            "gold_answer must be concise. If the final answer is unknown, set gold_answer to null.\n\n"
            "[Original Question]\n"
            f"{question_text}\n\n"
            "[Question Plan]\n"
            f"{json.dumps(question_plan, ensure_ascii=False, indent=2)}\n\n"
            "[Solved Sub-questions]\n"
            f"{json.dumps(sub_question_results, ensure_ascii=False, indent=2)}"
        )

    def _compute_network_retry_delay(self, attempt: int) -> float:
        exponent = max(attempt - 1, 0)
        delay = self.network_retry_backoff_seconds * (
            self.network_retry_backoff_multiplier ** exponent
        )
        return min(delay, self.network_retry_max_backoff_seconds)

    def _invoke_model_with_network_retry(self, prompt_text: str, call_name: str) -> Any:
        total_attempts = max(self.network_retry_attempts, 1)
        for attempt in range(1, total_attempts + 1):
            invoke_start = time.perf_counter()
            try:
                logger.info(
                    "API call: %s(attempt=%d/%d, prompt_len=%d)",
                    call_name,
                    attempt,
                    total_attempts,
                    len(prompt_text or ""),
                )
                response = self.model.invoke(prompt_text)
                logger.info(
                    "API response: %s(attempt=%d/%d, elapsed_ms=%.2f)",
                    call_name,
                    attempt,
                    total_attempts,
                    (time.perf_counter() - invoke_start) * 1000.0,
                )
                return response
            except Exception as exc:
                if not is_network_api_error(exc) or attempt >= total_attempts:
                    raise
                delay = self._compute_network_retry_delay(attempt)
                logger.warning(
                    "%s hit network/API error on attempt %d/%d: %s; retrying in %.2fs",
                    call_name,
                    attempt,
                    total_attempts,
                    exc,
                    delay,
                )
                if delay > 0:
                    time.sleep(delay)
        raise RuntimeError(f"{call_name} exhausted retry attempts unexpectedly")

    def _invoke_tool_agent_once(self, prompt_text: str, thread_id: str) -> Dict[str, Any]:
        invoke_config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self.recursion_limit,
        }
        try:
            invoke_start = time.perf_counter()
            logger.info(
                "API call: agent.invoke(thread_id=%s, recursion_limit=%s, prompt_len=%d)",
                thread_id,
                self.recursion_limit,
                len(prompt_text or ""),
            )
            response = self.agent.invoke(
                {"messages": [{"role": "user", "content": prompt_text}]},
                config=invoke_config,
            )
            logger.info(
                "API response: agent.invoke(thread_id=%s, elapsed_ms=%.2f)",
                thread_id,
                (time.perf_counter() - invoke_start) * 1000.0,
            )
            return response
        except GraphRecursionError:
            logger.warning(
                "GraphRecursionError on thread_id=%s with recursion_limit=%s; retrying with recursion_limit=%s",
                thread_id,
                self.recursion_limit,
                self.retry_recursion_limit,
            )
            retry_config = {
                "configurable": {"thread_id": thread_id},
                "recursion_limit": self.retry_recursion_limit,
            }
            retry_start = time.perf_counter()
            logger.info(
                "API call: agent.invoke.retry(thread_id=%s, recursion_limit=%s, prompt_len=%d)",
                thread_id,
                self.retry_recursion_limit,
                len(prompt_text or ""),
            )
            response = self.agent.invoke(
                {"messages": [{"role": "user", "content": prompt_text}]},
                config=retry_config,
            )
            logger.info(
                "API response: agent.invoke.retry(thread_id=%s, elapsed_ms=%.2f)",
                thread_id,
                (time.perf_counter() - retry_start) * 1000.0,
            )
            return response

    def _invoke_tool_agent(self, prompt_text: str, thread_id: str) -> Dict[str, Any]:
        total_attempts = max(self.network_retry_attempts, 1)
        for attempt in range(1, total_attempts + 1):
            attempt_thread_id = thread_id if attempt == 1 else f"{thread_id}:netretry:{attempt}"
            try:
                return self._invoke_tool_agent_once(
                    prompt_text=prompt_text,
                    thread_id=attempt_thread_id,
                )
            except Exception as exc:
                if not is_network_api_error(exc) or attempt >= total_attempts:
                    raise
                delay = self._compute_network_retry_delay(attempt)
                next_thread_id = f"{thread_id}:netretry:{attempt + 1}"
                logger.warning(
                    "agent.invoke(thread_id=%s) hit network/API error on attempt %d/%d: %s; retrying with fresh thread_id=%s in %.2fs",
                    attempt_thread_id,
                    attempt,
                    total_attempts,
                    exc,
                    next_thread_id,
                    delay,
                )
                if delay > 0:
                    time.sleep(delay)
        raise RuntimeError("agent.invoke exhausted retry attempts unexpectedly")

    def _normalize_agent_structured_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        structured = response.get("structured_response")
        if is_dataclass(structured):
            return self._normalize_output(asdict(structured))
        if isinstance(structured, dict):
            return self._normalize_output(structured)
        return self._normalize_output(
            {
                "answer": str(structured) if structured is not None else str(response),
                "gold_answer": None,
                "evidence": None,
            }
        )

    def _solve_sub_questions(
        self,
        question_text: str,
        question_plan: Dict[str, Any],
        active_thread_id: str,
    ) -> List[Dict[str, Any]]:
        sub_questions = question_plan.get("sub_questions", [])
        if not isinstance(sub_questions, list):
            sub_questions = []

        results: List[Dict[str, Any]] = []
        total = max(len(sub_questions), 1)

        for idx, item in enumerate(sub_questions, start=1):
            sub_question = str(item).strip()
            if not sub_question:
                continue

            logger.info(
                "SUBQ START: %s",
                json.dumps(
                    {
                        "index": idx,
                        "question": sub_question,
                        "status": "in_progress",
                    },
                    ensure_ascii=False,
                ),
            )

            try:
                prompt_text = self._build_sub_question_prompt(
                    question_text=question_text,
                    question_plan=question_plan,
                    sub_question=sub_question,
                    sub_index=idx,
                    total_sub_questions=total,
                )
                response = self._invoke_tool_agent(
                    prompt_text=prompt_text,
                    thread_id=f"{active_thread_id}:subq:{idx}",
                )
                payload = self._normalize_agent_structured_response(response)
                result_item = {
                    "index": idx,
                    "question": sub_question,
                    "status": "completed",
                    "answer": str(payload.get("answer", "") or "").strip(),
                    "gold_answer": payload.get("gold_answer"),
                    "evidence": payload.get("evidence"),
                }
            except Exception as exc:
                if is_network_api_error(exc):
                    logger.exception(
                        "Sub-question %s hit network/API error; aborting current run",
                        idx,
                    )
                    raise
                result_item = {
                    "index": idx,
                    "question": sub_question,
                    "status": "failed",
                    "answer": "",
                    "gold_answer": None,
                    "evidence": None,
                    "error": str(exc),
                }

            logger.info(
                "SUBQ DONE: %s",
                json.dumps(self._safe_trace_value(result_item), ensure_ascii=False),
            )
            results.append(result_item)

        return results

    def _synthesize_final_answer(
        self,
        question_text: str,
        question_plan: Dict[str, Any],
        sub_question_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        synthesis_prompt = self._build_final_synthesis_prompt(
            question_text=question_text,
            question_plan=question_plan,
            sub_question_results=sub_question_results,
        )
        response = self._invoke_model_with_network_retry(
            prompt_text=synthesis_prompt,
            call_name="synthesize_final_answer",
        )
        response_text = self._extract_message_text(response)
        parsed = self._parse_json_block(response_text)
        if isinstance(parsed, dict):
            payload = self._normalize_output(parsed)
        else:
            payload = self._normalize_output(
                {
                    "answer": response_text.strip(),
                    "gold_answer": None,
                    "evidence": None,
                }
            )
        logger.info(
            "API response: synthesize_final_answer(answer_len=%d)",
            len(str(payload.get("answer", "") or "")),
        )
        return payload

    @staticmethod
    def _build_execution_prompt(question_text: str, question_plan: Dict[str, Any]) -> str:
        return (
            "[Original Question]\n"
            f"{question_text}\n\n"
            "[Question Plan]\n"
            f"{json.dumps(question_plan, ensure_ascii=False, indent=2)}\n\n"
            "[Execution Rules]\n"
            "- Answer the original question, not the plan itself.\n"
            "- Use the plan as the default execution order.\n"
            "- If tool evidence invalidates the plan, refine it mentally and continue.\n"
            "- Do not skip the sub-questions for comparison, summary, counting, or multi-hop questions."
        )

    @staticmethod
    def _build_direct_execution_prompt(question_text: str) -> str:
        return (
            "[Original Question]\n"
            f"{question_text}\n\n"
            "[Execution Mode]\n"
            "Answer directly first.\n\n"
            "[Task]\n"
            "- Answer the original question directly with the minimum necessary tool calls.\n"
            "- Do not explicitly decompose into sub-questions unless the question clearly requires comparison, counting, summary, causal explanation, or another obvious multi-hop structure.\n"
            "- search_details is the default tool for ordinary detail questions.\n"
            "- Use the entity-profile tools only for macro/coarse retrieval questions.\n"
            "- If the question is an ordinary single-goal lookup, keep sub_questions as an empty list.\n"
            "- If direct retrieval is insufficient, state what remains unknown instead of inventing facts.\n"
            "- gold_answer must be the concise final value only.\n"
            "- evidence should briefly cite the supporting tool results."
        )

    @classmethod
    def _should_retry_with_decomposition(cls, payload: Dict[str, Any]) -> Tuple[bool, str]:
        answer_text = str(payload.get("answer", "") or "").strip()
        gold_answer = payload.get("gold_answer")
        evidence_text = str(payload.get("evidence", "") or "").strip()

        if not answer_text:
            return True, "Direct answer was empty."

        if cls._is_unanswerable_text(answer_text):
            return True, "Direct answer reported insufficient information."

        if gold_answer is None:
            return True, "Direct answer lacked a concise gold_answer."

        if not evidence_text:
            return True, "Direct answer lacked supporting evidence."

        return False, "Direct answer is sufficient."

    def _answer_directly(self, question_text: str, active_thread_id: str) -> Dict[str, Any]:
        prompt_text = self._build_direct_execution_prompt(question_text)
        response = self._invoke_tool_agent(
            prompt_text=prompt_text,
            thread_id=f"{active_thread_id}:direct",
        )
        payload = self._normalize_agent_structured_response(response)
        answer_text = str(payload.get("answer", "") or "").strip()
        if (
            payload.get("gold_answer") is None
            and answer_text
            and not self._is_unanswerable_text(answer_text)
            and len(answer_text) <= 120
            and "\n" not in answer_text
        ):
            payload["gold_answer"] = answer_text
        if not isinstance(payload.get("sub_questions"), list):
            payload["sub_questions"] = []
        if not payload.get("plan_summary"):
            payload["plan_summary"] = "Answered directly without explicit decomposition."
        self._log_structured_trace(
            self._TRACE_PREFIX_DIRECT_ANSWER,
            payload,
        )
        logger.info(
            "API response: direct_answer(answer_len=%d, gold_answer_present=%s, evidence_present=%s)",
            len(str(payload.get("answer", "") or "")),
            payload.get("gold_answer") is not None,
            bool(str(payload.get("evidence", "") or "").strip()),
        )
        return payload

    def ask(self, question: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """Run one QA round: input question -> output structured answer dict."""
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")

        question_text = question.strip()
        active_thread_id = thread_id or self.thread_id
        self._current_tool_calls = []
        self._last_tool_calls = []
        self._last_question_plan = None
        try:
            decompose_first, strategy_reason = self._detect_direct_answer_strategy(question_text)
            logger.info(
                "QUESTION STRATEGY: %s",
                json.dumps(
                    {
                        "question": question_text,
                        "decompose_first": decompose_first,
                        "reason": strategy_reason,
                    },
                    ensure_ascii=False,
                ),
            )

            if not decompose_first:
                direct_plan = self._build_direct_question_plan(question_text, strategy_reason)
                self._last_question_plan = direct_plan
                try:
                    payload = self._answer_directly(
                        question_text=question_text,
                        active_thread_id=active_thread_id,
                    )
                    retry_with_decomposition, retry_reason = self._should_retry_with_decomposition(payload)
                    if not retry_with_decomposition:
                        tool_calls = self._consume_current_tool_calls()
                        payload["tool_calls"] = tool_calls
                        payload["question_plan"] = direct_plan
                        payload["sub_question_results"] = []
                        if not payload.get("sub_questions"):
                            payload["sub_questions"] = []
                        if not payload.get("plan_summary"):
                            payload["plan_summary"] = direct_plan.get("decomposition_reason")
                        self._log_structured_trace(
                            self._TRACE_PREFIX_FINAL_PAYLOAD,
                            payload,
                        )
                        self._last_tool_calls = tool_calls
                        return payload
                    self._log_structured_trace(
                        self._TRACE_PREFIX_DIRECT_FALLBACK,
                        {
                            "reason": retry_reason,
                            "question": question_text,
                        },
                    )
                except Exception as exc:
                    if is_network_api_error(exc):
                        raise
                    logger.warning(
                        "Direct answer path failed; fallback to decomposition: %s",
                        exc,
                    )

            question_plan = self._decompose_question(question_text)
            self._last_question_plan = question_plan
            sub_question_results = self._solve_sub_questions(
                question_text=question_text,
                question_plan=question_plan,
                active_thread_id=active_thread_id,
            )
            payload = self._synthesize_final_answer(
                question_text=question_text,
                question_plan=question_plan,
                sub_question_results=sub_question_results,
            )
            tool_calls = self._consume_current_tool_calls()
            payload["tool_calls"] = tool_calls
            payload["question_plan"] = question_plan
            payload["sub_question_results"] = sub_question_results
            if not payload.get("sub_questions"):
                payload["sub_questions"] = question_plan.get("sub_questions", [])
            if not payload.get("plan_summary"):
                payload["plan_summary"] = question_plan.get("decomposition_reason")
            self._log_structured_trace(
                self._TRACE_PREFIX_FINAL_PAYLOAD,
                payload,
            )
            self._last_tool_calls = tool_calls
            return payload
        except Exception:
            self._last_tool_calls = self._consume_current_tool_calls()
            raise


def create_memory_agent(config_path: str | Path = DEFAULT_CONFIG_PATH) -> MemoryAgent:
    """Convenience function for tests and scripts."""
    return MemoryAgent(config_path=config_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    config_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 and sys.argv[1].endswith(".yaml") else DEFAULT_CONFIG_PATH
    question_offset = 2 if len(sys.argv) > 1 and sys.argv[1].endswith(".yaml") else 1
    question = " ".join(sys.argv[question_offset:]).strip()

    agent = MemoryAgent(config_path=config_path)
    if not question:
        question = str(agent.config.get("default_question", "What are Emi's hobbies?"))

    result = agent.ask(question)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
