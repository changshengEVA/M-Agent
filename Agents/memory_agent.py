#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional

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

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/prompt/agent_sys.yaml")


@dataclass
class AgentResponse:
    """Structured output schema of memory QA agent."""

    answer: str
    gold_answer: Optional[str] = None
    evidence: Optional[str] = None


class MemoryAgent:
    """LangChain agent wrapper for MemoryCore-based QA."""

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

        if isinstance(gold_answer, str):
            gold_answer = gold_answer.strip() or None
        payload["gold_answer"] = gold_answer

        if cls._is_unanswerable_text(answer_text):
            payload["gold_answer"] = None

        return payload

    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH):
        self.config_path = Path(config_path)
        self.config = self._load_config(self.config_path)

        self.macro_search_defaults: Dict[str, Any] = {
            "use_threshold": True,
            "threshold": 0.7,
            "topk": 5,
        }
        macro_cfg = self.config.get("macro_search_defaults", {})
        if isinstance(macro_cfg, dict):
            self.macro_search_defaults.update(macro_cfg)

        self.memory_sys = self._init_memory_sys(self.config)
        if bool(self.config.get("auto_bootstrap_kg_data", True)):
            self._ensure_kg_data_initialized(self.memory_sys)

        self.thread_id = str(self.config.get("thread_id", "memory-agent-1"))
        self.recursion_limit = int(self.config.get("recursion_limit", 60))
        self.retry_recursion_limit = int(
            self.config.get("retry_recursion_limit", max(self.recursion_limit, 120))
        )
        self.system_prompt = str(self.config["system_prompt"])
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
            """Fetch original dialogue turns by dialogue_id + episode_id."""

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

        return [
            resolve_entity,
            query_entity_property,
            search_macro_events,
            search_content,
            search_events_by_time_range,
        ]

    def ask(self, question: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """Run one QA round: input question -> output structured answer dict."""
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")

        question_text = question.strip()
        active_thread_id = thread_id or self.thread_id

        invoke_config = {
            "configurable": {"thread_id": active_thread_id},
            "recursion_limit": self.recursion_limit,
        }
        try:
            invoke_start = time.perf_counter()
            logger.info(
                "API call: agent.invoke(thread_id=%s, recursion_limit=%s, question_len=%d)",
                active_thread_id,
                self.recursion_limit,
                len(question_text),
            )
            response = self.agent.invoke(
                {"messages": [{"role": "user", "content": question_text}]},
                config=invoke_config,
            )
            logger.info(
                "API response: agent.invoke(thread_id=%s, elapsed_ms=%.2f)",
                active_thread_id,
                (time.perf_counter() - invoke_start) * 1000.0,
            )
        except GraphRecursionError:
            logger.warning(
                "GraphRecursionError on thread_id=%s with recursion_limit=%s; retrying with recursion_limit=%s",
                active_thread_id,
                self.recursion_limit,
                self.retry_recursion_limit,
            )
            retry_config = {
                "configurable": {"thread_id": active_thread_id},
                "recursion_limit": self.retry_recursion_limit,
            }
            retry_start = time.perf_counter()
            logger.info(
                "API call: agent.invoke.retry(thread_id=%s, recursion_limit=%s, question_len=%d)",
                active_thread_id,
                self.retry_recursion_limit,
                len(question_text),
            )
            response = self.agent.invoke(
                {"messages": [{"role": "user", "content": question_text}]},
                config=retry_config,
            )
            logger.info(
                "API response: agent.invoke.retry(thread_id=%s, elapsed_ms=%.2f)",
                active_thread_id,
                (time.perf_counter() - retry_start) * 1000.0,
            )

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
