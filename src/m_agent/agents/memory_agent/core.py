#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model

from m_agent.config_paths import (
    DEFAULT_MEMORY_AGENT_CONFIG_PATH,
    resolve_config_path,
)
from m_agent.paths import ENV_PATH
from m_agent.prompt_utils import (
    normalize_prompt_language,
    resolve_prompt_value,
)

from .mixins import (
    MemoryAgentConfigMixin,
    MemoryAgentExecutionMixin,
    MemoryAgentPlanningMixin,
    MemoryAgentStateMixin,
    MemoryAgentToolingMixin,
)


def _load_env_file(path: Path) -> None:
    """从 .env 文件加载环境变量，不覆盖已存在的变量。"""
    if not path.exists():
        return

    try:
        from dotenv import load_dotenv  # type: ignore
    except ModuleNotFoundError:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    else:
        load_dotenv(dotenv_path=path)


_load_env_file(ENV_PATH)

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = DEFAULT_MEMORY_AGENT_CONFIG_PATH


@dataclass
class AgentResponse:
    """Structured output schema of memory QA agent."""

    answer: str
    gold_answer: Optional[str] = None
    evidence: Optional[str] = None
    sub_questions: Optional[List[str]] = None
    plan_summary: Optional[str] = None


class MemoryAgent(
    MemoryAgentStateMixin,
    MemoryAgentConfigMixin,
    MemoryAgentToolingMixin,
    MemoryAgentPlanningMixin,
    MemoryAgentExecutionMixin,
):
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
    _CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")

    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH):
        """初始化 MemoryAgent：装配配置、模型、工具与运行参数。"""
        self.config_path = resolve_config_path(config_path)
        self.config = self._load_config(self.config_path)
        self.memory_core_config_path = self._resolve_related_path(self.config.get("memory_core_config_path"))
        self.memory_core_config = self._load_memory_core_config(self.memory_core_config_path)
        self.prompt_language = normalize_prompt_language(
            self.config.get("prompt_language", self.memory_core_config.get("prompt_language", "zh"))
        )
        self.runtime_prompt_config_path = self._resolve_runtime_prompt_config_path(
            self.config.get("runtime_prompt_config_path")
        )
        self.runtime_prompts = self._load_runtime_prompts(self.runtime_prompt_config_path)
        self._current_tool_calls: List[Dict[str, Any]] = []
        self._last_tool_calls: List[Dict[str, Any]] = []
        self._last_question_plan: Optional[Dict[str, Any]] = None
        self._search_details_scope_counts: Dict[str, int] = {}
        self._search_details_round_count = 0
        self._active_search_scope = "global"
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
        self.max_consecutive_search_details_calls = max(
            1,
            int(self.config.get("max_consecutive_search_details_calls", 3)),
        )
        self.max_search_details_calls_per_scope = max(
            1,
            int(self.config.get("max_search_details_calls_per_scope", 3)),
        )
        self.max_search_details_calls_per_round = max(
            self.max_search_details_calls_per_scope,
            int(self.config.get("max_search_details_calls_per_round", 20)),
        )
        self.attach_episode_refs_to_answer = bool(
            self.config.get("attach_episode_refs_to_answer", True)
        )
        self.evidence_episode_ref_max_in_text = max(
            1,
            int(self.config.get("evidence_episode_ref_max_in_text", 8)),
        )

        self.memory_sys = self._init_memory_sys(self.memory_core_config, self.memory_core_config_path)
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
        self.system_prompt = resolve_prompt_value(
            self.runtime_prompts.get("system_prompt"),
            language=self.prompt_language,
            path_desc=f"{self.runtime_prompt_config_path}.memory_agent.system_prompt",
        )
        self.planner_prompt = resolve_prompt_value(
            self.runtime_prompts.get("planner_prompt"),
            language=self.prompt_language,
            path_desc=f"{self.runtime_prompt_config_path}.memory_agent.planner_prompt",
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



def create_memory_agent(config_path: str | Path = DEFAULT_CONFIG_PATH) -> MemoryAgent:
    """按配置路径创建 MemoryAgent 实例。"""
    return MemoryAgent(config_path=config_path)


def main() -> None:
    """命令行入口：解析参数、提问并输出 JSON 结果。"""
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
