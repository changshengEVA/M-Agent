from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import tool
from langgraph.errors import GraphRecursionError

from m_agent.config_paths import (
    AGENT_RUNTIME_PROMPT_CONFIG_PATH,
    DEFAULT_CHAT_AGENT_CONFIG_PATH,
    resolve_config_path,
    resolve_related_config_path,
)
from m_agent.agents.memory_agent import MemoryAgent
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
    """Top-level chat controller with persona, using neutral recall tools underneath."""

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
            default_path=AGENT_RUNTIME_PROMPT_CONFIG_PATH,
        )

    def _load_runtime_prompts(self, path: Path) -> Dict[str, Any]:
        config = load_resolved_prompt_config(path, language=self.prompt_language)
        prompts = config.get("chat_controller")
        if not isinstance(prompts, dict):
            raise ValueError(f"`chat_controller` prompt namespace is required in runtime prompt config: {path}")
        return prompts

    def _load_chat_persona_prompt(self) -> str:
        prompt = self.config.get("chat_persona_prompt")
        if prompt is None:
            return ""
        if isinstance(prompt, str) and not prompt.strip():
            return ""
        return resolve_prompt_value(
            prompt,
            language=self.prompt_language,
            path_desc=f"{self.config_path}.chat_persona_prompt",
        )

    def _build_chat_system_prompt(self) -> str:
        base_prompt = resolve_prompt_value(
            self.config.get("chat_system_prompt"),
            language=self.prompt_language,
            path_desc=f"{self.config_path}.chat_system_prompt",
        )
        return self._merge_chat_system_prompt(
            base_prompt,
            self.chat_persona_prompt,
            prompt_language=self.prompt_language,
            runtime_prompt_config_path=self.runtime_prompt_config_path,
        )

    @staticmethod
    def _merge_chat_system_prompt(
        base_prompt: str,
        persona_prompt: str,
        *,
        prompt_language: str = "en",
        runtime_prompt_config_path: str | Path = AGENT_RUNTIME_PROMPT_CONFIG_PATH,
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

    def _build_no_recall_result(self, question: str, answer_text: str) -> Dict[str, Any]:
        plan_summary = str(self.runtime_prompts.get("no_recall_plan_summary", "") or "").strip()
        if not plan_summary:
            raise ValueError(
                f"`chat_controller.no_recall_plan_summary` is required in runtime prompt config: "
                f"{self.runtime_prompt_config_path}"
            )
        return {
            "answer": answer_text,
            "gold_answer": None,
            "evidence": None,
            "sub_questions": [],
            "plan_summary": plan_summary,
            "tool_call_count": 0,
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

    def _build_chat_controller(self, *, active_thread_id: str, recall_state: Dict[str, Any]):
        @tool
        def shallow_recall(question: str) -> Dict[str, Any]:
            """Use for ordinary single-goal memory lookup from stored chat details."""

            logger.info(
                "RECALL START: %s",
                json.dumps(
                    {
                        "mode": "shallow_recall",
                        "question": str(question or "").strip(),
                    },
                    ensure_ascii=False,
                ),
            )
            result = self.memory_agent.shallow_recall(
                question=question,
                thread_id=f"{active_thread_id}:shallow",
            )
            logger.info(
                "RECALL DONE: %s",
                json.dumps(
                    {
                        "mode": "shallow_recall",
                        "question": str(question or "").strip(),
                        "answer": str(result.get("answer", "") or "").strip(),
                    },
                    ensure_ascii=False,
                ),
            )
            recall_state["mode"] = "shallow_recall"
            recall_state["result"] = result
            recall_state["history"].append({"mode": "shallow_recall", "result": result})
            return result

        @tool
        def deep_recall(question: str) -> Dict[str, Any]:
            """Use for complex memory questions such as comparison, counting, multi-hop, or time chaining."""

            logger.info(
                "RECALL START: %s",
                json.dumps(
                    {
                        "mode": "deep_recall",
                        "question": str(question or "").strip(),
                    },
                    ensure_ascii=False,
                ),
            )
            result = self.memory_agent.deep_recall(
                question=question,
                thread_id=f"{active_thread_id}:deep",
            )
            logger.info(
                "RECALL DONE: %s",
                json.dumps(
                    {
                        "mode": "deep_recall",
                        "question": str(question or "").strip(),
                        "answer": str(result.get("answer", "") or "").strip(),
                    },
                    ensure_ascii=False,
                ),
            )
            recall_state["mode"] = "deep_recall"
            recall_state["result"] = result
            recall_state["history"].append({"mode": "deep_recall", "result": result})
            return result

        return create_agent(
            model=self.memory_agent.model,
            system_prompt=self.chat_system_prompt,
            tools=[shallow_recall, deep_recall],
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
        controller = self._build_chat_controller(
            active_thread_id=active_thread_id,
            recall_state=recall_state,
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
        if isinstance(agent_result, dict):
            agent_result = dict(agent_result)
            if recall_state["history"]:
                agent_result["recall_history"] = list(recall_state["history"])
                agent_result["recall_mode"] = recall_state["mode"]
        else:
            agent_result = self._build_no_recall_result(safe_message, answer_text)

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
