from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langgraph.errors import GraphRecursionError

from m_agent.agents.memory_agent import MemoryAgent
from m_agent.chat.capabilities import (
    ControllerCapabilityContext,
    build_controller_tools,
    resolve_enabled_controller_capability_names,
)
from m_agent.config_paths import (
    CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH,
    DEFAULT_CHAT_AGENT_CONFIG_PATH,
    resolve_config_path,
    resolve_related_config_path,
)
from m_agent.prompt_utils import (
    load_resolved_prompt_config,
    normalize_prompt_language,
    render_prompt_template,
    resolve_prompt_value,
)
from m_agent.utils.api_error_utils import is_network_api_error


logger = logging.getLogger(__name__)

DEFAULT_CHAT_CONFIG_PATH = DEFAULT_CHAT_AGENT_CONFIG_PATH


@dataclass
class ChatAgentResponse:
    answer: str


class ChatControllerAgent:
    """Top-level chat controller with persona, using tools via a capability registry."""

    def __init__(self, config_path: str | Path = DEFAULT_CHAT_CONFIG_PATH) -> None:
        self.config_path = resolve_config_path(config_path)
        self.config = self._load_config(self.config_path)
        self.prompt_language = normalize_prompt_language(self.config.get("prompt_language", "zh"))
        self.runtime_prompt_config_path = self._resolve_runtime_prompt_config_path(
            self.config.get("runtime_prompt_config_path")
        )
        self.runtime_prompts = self._load_runtime_prompts(self.runtime_prompt_config_path)
        self.memory_agent_config_path = self._resolve_memory_agent_config_path(
            self.config.get("memory_agent_config_path")
        )
        self.memory_agent = MemoryAgent(config_path=self.memory_agent_config_path)
        self.default_thread_id = str(self.config.get("thread_id", "test-agent-1")).strip() or "test-agent-1"
        self.enabled_tool_names = self._load_enabled_tool_names()
        self.tool_defaults = self._load_tool_defaults()
        self.chat_persona_prompt = self._load_chat_persona_prompt()
        self.chat_system_prompt = self._build_chat_system_prompt()

    @staticmethod
    def _load_config(path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Chat controller config not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        if not isinstance(config, dict):
            raise ValueError(f"Chat controller config must be a dict: {path}")

        if not isinstance(config.get("memory_agent_config_path"), str) or not str(
            config.get("memory_agent_config_path")
        ).strip():
            raise ValueError("`memory_agent_config_path` is required in chat controller config")

        return config

    def _resolve_memory_agent_config_path(self, raw_path: Any) -> Path:
        return resolve_related_config_path(self.config_path, raw_path)

    def _resolve_runtime_prompt_config_path(self, raw_path: Any) -> Path:
        return resolve_related_config_path(
            self.config_path,
            raw_path,
            default_path=CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH,
        )

    def _load_runtime_prompts(self, path: Path) -> Dict[str, Any]:
        config = load_resolved_prompt_config(path, language=self.prompt_language)
        prompts = config.get("chat_controller")
        if not isinstance(prompts, dict):
            raise ValueError(f"`chat_controller` prompt namespace is required in runtime prompt config: {path}")
        return prompts

    def _load_enabled_tool_names(self) -> List[str]:
        return resolve_enabled_controller_capability_names(self.config.get("enabled_tools"))

    def _load_tool_defaults(self) -> Dict[str, Dict[str, Any]]:
        raw_defaults = self.config.get("tool_defaults")
        normalized: Dict[str, Dict[str, Any]] = {}
        if isinstance(raw_defaults, dict):
            for tool_name, value in raw_defaults.items():
                if not isinstance(value, dict):
                    continue
                normalized[str(tool_name)] = dict(value)

        legacy_timezone_name = str(self.config.get("current_time_tool_timezone", "") or "").strip()
        if legacy_timezone_name:
            normalized.setdefault("get_current_time", {})
            normalized["get_current_time"].setdefault("timezone_name", legacy_timezone_name)
        return normalized

    def _load_chat_persona_prompt(self) -> str:
        return self._resolve_prompt_from_config_or_runtime(
            config_key="chat_persona_prompt",
            runtime_key="persona_prompt",
            allow_empty=True,
        )

    def _resolve_prompt_from_config_or_runtime(
        self,
        *,
        config_key: str,
        runtime_key: str,
        allow_empty: bool = False,
    ) -> str:
        config_value = self.config.get(config_key)
        if config_value is not None:
            return self._resolve_prompt_node(
                config_value,
                path_desc=f"{self.config_path}.{config_key}",
                allow_empty=allow_empty,
            )
        return self._get_runtime_prompt_text(runtime_key, allow_empty=allow_empty)

    def _resolve_prompt_node(self, node: Any, *, path_desc: str, allow_empty: bool = False) -> str:
        if isinstance(node, str):
            text = node.strip()
        else:
            text = resolve_prompt_value(
                node,
                language=self.prompt_language,
                path_desc=path_desc,
            )
        if not text and not allow_empty:
            raise ValueError(f"Prompt text is empty: {path_desc}")
        return text

    def _get_runtime_prompt_text(self, key: str, *, allow_empty: bool = False) -> str:
        text = str(self.runtime_prompts.get(key, "") or "").strip()
        if not text and not allow_empty:
            raise ValueError(
                f"`chat_controller.{key}` is required in runtime prompt config: "
                f"{self.runtime_prompt_config_path}"
            )
        return text

    def _build_chat_system_prompt(self) -> str:
        base_prompt = self._resolve_prompt_from_config_or_runtime(
            config_key="chat_system_prompt",
            runtime_key="system_prompt",
            allow_empty=False,
        )
        merged_prompt = self._merge_chat_system_prompt(
            base_prompt,
            self.chat_persona_prompt,
            prompt_language=self.prompt_language,
            runtime_prompt_config_path=self.runtime_prompt_config_path,
        )

        sections = [merged_prompt]
        global_tool_policy = self._get_runtime_prompt_text("global_tool_policy", allow_empty=True)
        if global_tool_policy:
            sections.append(global_tool_policy)

        tool_block = self._build_enabled_tool_description_block()
        if tool_block:
            sections.append(tool_block)

        return "\n\n".join(section for section in sections if section).strip()

    def _build_enabled_tool_description_block(self) -> str:
        if not self.enabled_tool_names:
            return ""

        header = "[可用顶层工具]" if self.prompt_language == "zh" else "[Available Top-Level Tools]"
        lines = [header]
        for tool_name in self.enabled_tool_names:
            description = self._get_controller_tool_description(tool_name)
            lines.append(f"- `{tool_name}`: {description}")
        return "\n".join(lines)

    def _get_controller_tool_description(self, tool_name: str) -> str:
        tools_config = self.runtime_prompts.get("tools")
        if isinstance(tools_config, dict):
            tool_config = tools_config.get(tool_name)
            if isinstance(tool_config, dict):
                description = str(tool_config.get("description", "") or "").strip()
                if description:
                    return description
        return f"Top-level tool: {tool_name}"

    @staticmethod
    def _merge_chat_system_prompt(
        base_prompt: str,
        persona_prompt: str,
        *,
        prompt_language: str = "en",
        runtime_prompt_config_path: str | Path = CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH,
    ) -> str:
        normalized_base_prompt = str(base_prompt or "").strip()
        normalized_persona_prompt = str(persona_prompt or "").strip()
        if not normalized_persona_prompt:
            return normalized_base_prompt

        resolved_path = Path(runtime_prompt_config_path).resolve()
        runtime_prompts = load_resolved_prompt_config(
            resolved_path,
            language=normalize_prompt_language(prompt_language),
        )
        controller_prompts = runtime_prompts.get("chat_controller")
        if not isinstance(controller_prompts, dict):
            raise ValueError(f"`chat_controller` prompt namespace is required in runtime prompt config: {resolved_path}")

        template = str(controller_prompts.get("merge_system_with_persona", "") or "").strip()
        if not template:
            raise ValueError(
                f"`chat_controller.merge_system_with_persona` is required in runtime prompt config: "
                f"{resolved_path}"
            )

        return render_prompt_template(
            template,
            {
                "<base_prompt>": normalized_base_prompt,
                "<persona_prompt>": normalized_persona_prompt,
            },
        ).strip()

    def _build_no_recall_result(
        self,
        question: str,
        answer_text: str,
        *,
        controller_tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        controller_tool_history = list(controller_tools or [])
        controller_tool_names = self._collect_controller_tool_names(controller_tool_history)
        tool_call_count = len(controller_tool_history)
        plan_summary = self._build_no_tool_plan_summary(controller_tool_names)
        return {
            "answer": answer_text,
            "gold_answer": None,
            "evidence": None,
            "sub_questions": [],
            "plan_summary": plan_summary,
            "tool_call_count": tool_call_count,
            "controller_tool_count": tool_call_count,
            "controller_tool_names": controller_tool_names,
            "controller_tool_history": controller_tool_history,
            "question_plan": {
                "goal": "",
                "question_type": "",
                "decomposition_reason": "",
                "sub_questions": [],
                "suggested_tool_order": [],
                "completion_criteria": "",
            },
            "sub_question_results": [],
        }

    def _build_no_tool_plan_summary(self, controller_tool_names: List[str]) -> str:
        if controller_tool_names:
            if controller_tool_names == ["get_current_time"]:
                if self.prompt_language == "zh":
                    return "本轮对话使用了当前时间工具。"
                return "This chat turn used the current-time tool."
            names_text = ", ".join(controller_tool_names)
            if self.prompt_language == "zh":
                return f"本轮对话使用了顶层工具：{names_text}。"
            return f"This chat turn used top-level controller tools: {names_text}."

        plan_summary = str(self.runtime_prompts.get("no_tool_plan_summary", "") or "").strip()
        if not plan_summary:
            plan_summary = str(self.runtime_prompts.get("no_recall_plan_summary", "") or "").strip()
        if not plan_summary:
            raise ValueError(
                "`chat_controller.no_tool_plan_summary` is required in runtime prompt config: "
                f"{self.runtime_prompt_config_path}"
            )
        return plan_summary

    @staticmethod
    def _normalize_chat_response(response: Dict[str, Any]) -> Dict[str, Any]:
        structured = response.get("structured_response") if isinstance(response, dict) else None
        if is_dataclass(structured):
            payload = asdict(structured)
        elif isinstance(structured, dict):
            payload = structured
        else:
            payload = {"answer": str(structured) if structured is not None else str(response)}
        return {
            "answer": str(payload.get("answer", "") or "").strip(),
        }

    @staticmethod
    def _normalize_history_messages(history_messages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
        normalized: List[Dict[str, str]] = []
        if not isinstance(history_messages, list):
            return normalized

        for item in history_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip().lower()
            content = str(item.get("content", "") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            normalized.append({"role": role, "content": content})
        return normalized

    @staticmethod
    def _collect_controller_tool_names(controller_tools: List[Dict[str, Any]]) -> List[str]:
        tool_names: List[str] = []
        for item in controller_tools:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name", "") or "").strip()
            if tool_name and tool_name not in tool_names:
                tool_names.append(tool_name)
        return tool_names

    def _build_chat_controller(
        self,
        *,
        active_thread_id: str,
        recall_state: Dict[str, Any],
        controller_state: Dict[str, Any],
    ):
        capability_context = ControllerCapabilityContext(
            active_thread_id=active_thread_id,
            memory_agent=self.memory_agent,
            recall_state=recall_state,
            controller_state=controller_state,
            tool_defaults=self.tool_defaults,
            logger=logger,
        )
        tool_descriptions = {
            tool_name: self._get_controller_tool_description(tool_name)
            for tool_name in self.enabled_tool_names
        }
        tools = build_controller_tools(
            context=capability_context,
            enabled_tool_names=self.enabled_tool_names,
            tool_descriptions=tool_descriptions,
        )
        return create_agent(
            model=self.memory_agent.model,
            system_prompt=self.chat_system_prompt,
            tools=tools,
            response_format=ToolStrategy(ChatAgentResponse),
        )

    def _invoke_chat_controller_once(
        self,
        controller: Any,
        *,
        message: str,
        active_thread_id: str,
        history_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        prompt_messages = self._normalize_history_messages(history_messages)
        prompt_messages.append({"role": "user", "content": message})
        invoke_config = {
            "configurable": {"thread_id": f"{active_thread_id}:chat"},
            "recursion_limit": self.memory_agent.recursion_limit,
        }
        try:
            return controller.invoke(
                {"messages": prompt_messages},
                config=invoke_config,
            )
        except GraphRecursionError:
            retry_config = {
                "configurable": {"thread_id": f"{active_thread_id}:chat:retry"},
                "recursion_limit": self.memory_agent.retry_recursion_limit,
            }
            return controller.invoke(
                {"messages": prompt_messages},
                config=retry_config,
            )

    def _invoke_chat_controller(
        self,
        controller: Any,
        *,
        message: str,
        active_thread_id: str,
        history_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        total_attempts = max(self.memory_agent.network_retry_attempts, 1)
        for attempt in range(1, total_attempts + 1):
            try:
                return self._invoke_chat_controller_once(
                    controller,
                    message=message,
                    active_thread_id=(
                        active_thread_id if attempt == 1 else f"{active_thread_id}:netretry:{attempt}"
                    ),
                    history_messages=history_messages,
                )
            except Exception as exc:
                if not is_network_api_error(exc) or attempt >= total_attempts:
                    raise
                delay = self.memory_agent._compute_network_retry_delay(attempt)
                logger.warning(
                    "chat controller invoke hit network/API error on attempt %d/%d: %s; retrying in %.2fs",
                    attempt,
                    total_attempts,
                    exc,
                    delay,
                )
                if delay > 0:
                    threading.Event().wait(delay)
        raise RuntimeError("chat controller invoke exhausted retry attempts unexpectedly")

    def chat(
        self,
        message: str,
        thread_id: Optional[str] = None,
        history_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")

        safe_message = message.strip()
        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        normalized_history = self._normalize_history_messages(history_messages)
        recall_state: Dict[str, Any] = {"mode": None, "result": None, "history": []}
        controller_state: Dict[str, Any] = {"history": [], "call_seq": 0}
        controller = self._build_chat_controller(
            active_thread_id=active_thread_id,
            recall_state=recall_state,
            controller_state=controller_state,
        )
        controller_response = self._invoke_chat_controller(
            controller,
            message=safe_message,
            active_thread_id=active_thread_id,
            history_messages=normalized_history,
        )
        answer_text = self._normalize_chat_response(controller_response)["answer"]

        agent_result = recall_state["result"]
        if not answer_text and isinstance(agent_result, dict):
            answer_text = str(agent_result.get("answer", "") or "").strip()
        controller_tool_history = list(controller_state.get("history", []) or [])
        if isinstance(agent_result, dict):
            agent_result = dict(agent_result)
            if recall_state["history"]:
                agent_result["recall_history"] = list(recall_state["history"])
                agent_result["recall_mode"] = recall_state["mode"]
            if controller_tool_history:
                agent_result["controller_tool_count"] = len(controller_tool_history)
                agent_result["controller_tool_names"] = self._collect_controller_tool_names(controller_tool_history)
                agent_result["controller_tool_history"] = controller_tool_history
        else:
            agent_result = self._build_no_recall_result(
                safe_message,
                answer_text,
                controller_tools=controller_tool_history,
            )

        return {
            "success": True,
            "thread_id": active_thread_id,
            "question": safe_message,
            "answer": answer_text,
            "history_messages": normalized_history,
            "agent_result": agent_result,
        }


def create_chat_controller_agent(
    config_path: str | Path = DEFAULT_CHAT_CONFIG_PATH,
) -> ChatControllerAgent:
    return ChatControllerAgent(config_path=config_path)
