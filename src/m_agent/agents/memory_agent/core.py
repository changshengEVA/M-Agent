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

import yaml

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model

from m_agent.config_paths import (
    DEFAULT_MEMORY_AGENT_CONFIG_PATH,
    MEMORY_AGENT_TOOL_DESCRIPTIONS_PATH,
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
    _TRACE_PREFIX_TOOL_CALL = "TOOL CALL DETAIL: "
    _TRACE_PREFIX_TOOL_RESULT = "TOOL RESULT DETAIL: "
    _TRACE_PREFIX_WORKSPACE_STATE = "WORKSPACE STATE: "
    _TRACE_PREFIX_FINAL_PAYLOAD = "FINAL ANSWER PAYLOAD: "
    _CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")

    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH, memory_workflow_id: Optional[str] = None):
        """初始化 MemoryAgent：装配配置、模型、工具与运行参数。

        memory_workflow_id:
            When set, overrides ``workflow_id`` in the MemoryCore YAML so one agent config
            can resolve ``data/memory/<workflow_id>/`` to a dataset-specific subtree (e.g. ``locomo/conv-30``).
        """
        self.config_path = resolve_config_path(config_path)
        self.config = self._load_config(self.config_path)
        self.memory_core_config_path = self._resolve_related_path(self.config.get("memory_core_config_path"))
        self.memory_core_config = self._load_memory_core_config(self.memory_core_config_path)
        if memory_workflow_id is not None and str(memory_workflow_id).strip():
            self.memory_core_config = dict(self.memory_core_config)
            self.memory_core_config["workflow_id"] = str(memory_workflow_id).strip()
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
        self._active_search_scope = "global"
        self._tool_call_seq = 0

        self.detail_search_defaults: Dict[str, Any] = {
            "topk": 5,
        }
        detail_cfg = self.config.get("detail_search_defaults", {})
        if isinstance(detail_cfg, dict):
            self.detail_search_defaults.update(detail_cfg)
        workspace_cfg = self.config.get("workspace", {})
        if not isinstance(workspace_cfg, dict):
            workspace_cfg = {}
        self.enable_state_machine = bool(workspace_cfg.get("enable_state_machine", True))
        self.workspace_max_rounds = max(1, int(workspace_cfg.get("max_rounds", 2)))
        self.workspace_max_actions_per_round = max(1, int(workspace_cfg.get("max_actions_per_round", 4)))
        self.workspace_max_episode_candidates = max(1, int(workspace_cfg.get("max_episode_candidates", 12)))
        self.workspace_max_keep = max(1, int(workspace_cfg.get("max_keep", 6)))
        self.workspace_min_evidence_to_answer = max(1, int(workspace_cfg.get("min_evidence_to_answer", 1)))
        self.workspace_remedy_recall_max_times = max(
            0,
            int(workspace_cfg.get("remedy_recall_max_times", 1)),
        )
        self.action_planner_mode = str(workspace_cfg.get("action_planner", "rule")).strip().lower()
        # When action_planner is ``llm``, MemoryAgentExecutionMixin retries the LLM planner up to
        # this many attempts (including the first), then raises instead of falling back to rules.
        self.workspace_action_planner_max_attempts = max(
            1,
            int(workspace_cfg.get("action_planner_max_attempts", 3)),
        )
        enabled_tools_cfg = workspace_cfg.get("enabled_tools")
        if isinstance(enabled_tools_cfg, list):
            self._enabled_tools = [str(t).strip() for t in enabled_tools_cfg if str(t).strip()]
        else:
            self._enabled_tools = None
        rerank_cfg = workspace_cfg.get("rerank", {})
        if not isinstance(rerank_cfg, dict):
            rerank_cfg = {}
        self.rerank_enabled = bool(rerank_cfg.get("enable", False))
        self.rerank_score_threshold = float(rerank_cfg.get("score_threshold", 0.2))
        self.rerank_max_documents = max(1, int(rerank_cfg.get("max_documents", 16)))
        self.rerank_chunk_chars = max(32, int(rerank_cfg.get("chunk_chars", 800)))
        self.rerank_chunk_batch_size = max(4, int(rerank_cfg.get("chunk_batch_size", 32)))
        self.rerank_func = None
        if self.rerank_enabled:
            rerank_provider = str(rerank_cfg.get("provider", "aliyun")).strip().lower()
            rerank_model = str(rerank_cfg.get("model_name", "")).strip()
            if rerank_provider in {"alibaba", "aliyun", "dashscope"}:
                from m_agent.load_model.AlibabaRerankCall import get_rerank_func
                self.rerank_func = get_rerank_func(model_name=rerank_model)
                logger.info("Workspace rerank enabled: provider=%s, model=%s", rerank_provider, rerank_model or "(default)")
            else:
                logger.warning("Unsupported rerank provider: %s, rerank disabled", rerank_provider)
                self.rerank_enabled = False

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
        tool_descriptions = self._load_tool_descriptions()
        if tool_descriptions and self._enabled_tools:
            from .action_planner import build_tool_registry_from_config
            self.tool_registry = build_tool_registry_from_config(
                tool_descriptions,
                self._enabled_tools,
                language=self.prompt_language,
            )
        else:
            self.tool_registry = None

        if self.action_planner_mode == "llm" and not self.tool_registry:
            raise ValueError(
                "workspace.action_planner is 'llm' but tool_registry could not be built. "
                "Set workspace.enabled_tools (non-empty list) in the agent YAML and ensure "
                "tool_descriptions.yaml loads with matching tool keys."
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
    def _load_tool_descriptions() -> Dict[str, Any]:
        """Load the canonical tool_descriptions.yaml (fixed path, not configurable)."""
        path = MEMORY_AGENT_TOOL_DESCRIPTIONS_PATH
        if not path.exists():
            logger.warning("tool_descriptions.yaml not found at %s, using built-in defaults", path)
            return {}
        with open(path, "r", encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}
        if not isinstance(payload, dict):
            logger.warning("tool_descriptions.yaml must be a dict, got %s", type(payload).__name__)
            return {}
        return payload


def create_memory_agent(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    memory_workflow_id: Optional[str] = None,
) -> MemoryAgent:
    """按配置路径创建 MemoryAgent 实例。"""
    return MemoryAgent(config_path=config_path, memory_workflow_id=memory_workflow_id)


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
