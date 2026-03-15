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
from typing import Any, Dict, List, Optional

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
        if depth > 6:
            return "<max_depth>"
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
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

    def _record_tool_call(self, tool_name: str, params: Dict[str, Any]) -> None:
        self._current_tool_calls.append(
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "tool_name": str(tool_name),
                "params": self._safe_trace_value(params),
            }
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
        self._current_tool_calls: List[Dict[str, Any]] = []
        self._last_tool_calls: List[Dict[str, Any]] = []
        self._last_question_plan: Optional[Dict[str, Any]] = None

        self.macro_search_defaults: Dict[str, Any] = {
            "use_threshold": True,
            "threshold": 0.7,
            "topk": 5,
        }
        macro_cfg = self.config.get("macro_search_defaults", {})
        if isinstance(macro_cfg, dict):
            self.macro_search_defaults.update(macro_cfg)

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

        self.memory_sys = self._init_memory_sys(self.config)
        if bool(self.config.get("auto_bootstrap_kg_data", True)):
            self._ensure_kg_data_initialized(self.memory_sys)

        self.thread_id = str(self.config.get("thread_id", "memory-agent-1"))
        self.recursion_limit = int(self.config.get("recursion_limit", 60))
        self.retry_recursion_limit = int(
            self.config.get("retry_recursion_limit", max(self.recursion_limit, 120))
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
                    "search_events_by_time_range, search_details, search_content.\n"
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
            timeout=None,
            max_retries=2,
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
        return config

    @staticmethod
    def _init_memory_sys(config: Dict[str, Any]) -> MemoryCore:
        workflow_id = str(config.get("workflow_id", "testrt"))
        llm_temperature = float(config.get("memory_llm_temperature", 0.0))
        similarity_threshold = float(config.get("memory_similarity_threshold", 0.88))
        top_k = int(config.get("memory_top_k", 3))
        use_threshold = bool(config.get("memory_use_threshold", True))

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
        )

    @staticmethod
    def _ensure_kg_data_initialized(memory_core: MemoryCore) -> None:
        kg_data_path = memory_core.kg_data_path
        kg_candidates_path = memory_core.memory_root / "kg_candidates"
        kg_files = [p for p in kg_data_path.rglob("*") if p.is_file()]
        if kg_files:
            logger.info("kg_data already has %d file(s), skip bootstrap import.", len(kg_files))
            return

        logger.info("kg_data is empty, bootstrap import from: %s", kg_candidates_path)
        load_result = memory_core.load_from_dialogue_path(kg_candidates_path)
        if not load_result.get("success", False):
            raise RuntimeError(f"Failed to initialize kg_data from kg_candidates: {load_result}")
        logger.info(
            "Bootstrap import completed: processed=%s, failed=%s",
            load_result.get("files_processed", 0),
            load_result.get("files_failed", 0),
        )

    def _build_tools(self):
        @tool
        def resolve_entity(name: str) -> Dict[str, Any]:
            """Resolve a person/entity name to canonical entity UID in memory_sys."""

            self._record_tool_call("resolve_entity", {"name": name})
            logger.info("API call: resolve_entity(name=%s)", name)
            result = self.memory_sys.resolve_entity(name=name)
            logger.info(
                "API response: resolve_entity(success=%s, entity_uid=%s)",
                result.get("success") if isinstance(result, dict) else None,
                result.get("entity_uid") if isinstance(result, dict) else None,
            )
            return result

        @tool
        def query_entity_property(entity_uid: str, query_text: str) -> Dict[str, Any]:
            """Query structured attributes/features for an entity by UID and query text."""

            self._record_tool_call(
                "query_entity_property",
                {"entity_uid": entity_uid, "query_text": query_text},
            )
            logger.info(
                "API call: query_entity_property(entity_uid=%s, query_text_len=%d)",
                entity_uid,
                len(query_text or ""),
            )
            result = self.memory_sys.query_entity_property(
                entity_uid=entity_uid,
                query_text=query_text,
            )
            logger.info(
                "API response: query_entity_property(success=%s)",
                result.get("success") if isinstance(result, dict) else None,
            )
            return result

        @tool
        def search_macro_events(
            theme: str,
            use_threshold: Optional[bool] = None,
            threshold: Optional[float] = None,
            topk: Optional[int] = None,
        ) -> Dict[str, Any]:
            """Search relevant scenes by semantic theme. Returns scene IDs and episode references."""

            cfg_use_threshold = (
                self.macro_search_defaults["use_threshold"]
                if use_threshold is None
                else bool(use_threshold)
            )
            cfg_threshold = (
                self.macro_search_defaults["threshold"] if threshold is None else float(threshold)
            )
            cfg_topk = self.macro_search_defaults["topk"] if topk is None else int(topk)
            self._record_tool_call(
                "search_macro_events",
                {
                    "theme": theme,
                    "use_threshold": cfg_use_threshold,
                    "threshold": cfg_threshold,
                    "topk": cfg_topk,
                },
            )
            logger.info(
                "API call: search_macro_events(theme=%s, use_threshold=%s, threshold=%s, topk=%s)",
                theme,
                cfg_use_threshold,
                cfg_threshold,
                cfg_topk,
            )
            result = self.memory_sys.search_macro_events(
                query={"theme": theme},
                use_threshold=cfg_use_threshold,
                threshold=cfg_threshold,
                topk=cfg_topk,
            )
            logger.info(
                "API response: search_macro_events(success=%s, result_count=%s)",
                result.get("success") if isinstance(result, dict) else None,
                len(result.get("results", []))
                if isinstance(result, dict) and isinstance(result.get("results"), list)
                else None,
            )
            return result

        @tool
        def search_content(dialogue_id: str, episode_id: str) -> Dict[str, Any]:
            """Fetch dialogue original text details and event/time info by dialogue_id + episode_id."""

            self._record_tool_call(
                "search_content",
                {"dialogue_id": dialogue_id, "episode_id": episode_id},
            )
            logger.info(
                "API call: search_content(dialogue_id=%s, episode_id=%s)",
                dialogue_id,
                episode_id,
            )
            result = self.memory_sys.search_content(
                dialogue_id=dialogue_id,
                episode_id=episode_id,
            )
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

            self._record_tool_call(
                "search_events_by_time_range",
                {"start_time": start_time, "end_time": end_time},
            )
            logger.info(
                "API call: search_events_by_time_range(start_time=%s, end_time=%s)",
                start_time,
                end_time,
            )
            result = self.memory_sys.search_events_by_time_range(
                start_time=start_time,
                end_time=end_time,
            )
            logger.info(
                "API response: search_events_by_time_range(result_count=%s)",
                len(result) if isinstance(result, list) else None,
            )
            return result

        @tool
        def search_details(detail: str, topk: Optional[int] = None) -> Dict[str, Any]:
            """
            Search concrete behavior/action details from scene memories by semantic similarity.
            Returns top-K matched details with atomic-fact/evidence.
            """

            cfg_topk = self.detail_search_defaults["topk"] if topk is None else int(topk)
            self._record_tool_call(
                "search_details",
                {"detail": detail, "topk": cfg_topk},
            )
            logger.info(
                "API call: search_details(detail=%s, topk=%s)",
                detail,
                cfg_topk,
            )
            result = self.memory_sys.search_details(
                detail_query=detail,
                topk=cfg_topk,
            )
            logger.info(
                "API response: search_details(success=%s, result_count=%s)",
                result.get("hit") if isinstance(result, dict) else None,
                len(result.get("results", []))
                if isinstance(result, dict) and isinstance(result.get("results"), list)
                else None,
            )
            return result

        return [search_content, search_events_by_time_range, search_details]

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
            sub_questions = [
                f"What concrete evidence directly answers: {normalized}",
                f"What final answer is supported by that evidence for: {normalized}",
            ]

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

    def _decompose_question(self, question_text: str) -> Dict[str, Any]:
        logger.info(
            "API call: decompose_question(question_len=%d)",
            len(question_text or ""),
        )
        try:
            response = self.model.invoke(
                f"{self.planner_prompt}\n\n[User Question]\n{question_text}"
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

    def _invoke_tool_agent(self, prompt_text: str, thread_id: str) -> Dict[str, Any]:
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
        logger.info(
            "API call: synthesize_final_answer(sub_question_count=%d)",
            len(sub_question_results),
        )
        response = self.model.invoke(synthesis_prompt)
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

    def ask(self, question: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """Run one QA round: input question -> output structured answer dict."""
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")

        question_text = question.strip()
        active_thread_id = thread_id or self.thread_id
        self._current_tool_calls = []
        self._last_tool_calls = []
        self._last_question_plan = None
        question_plan = self._decompose_question(question_text)
        self._last_question_plan = question_plan
        try:
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
