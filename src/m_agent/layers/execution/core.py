"""ExecutionAgent: the controller-style worker of the three-layer architecture.

The execution layer:

* Owns the LangChain agent that runs the LLM-with-tools loop.
* Holds the capability registry, but is persona-less; instructions are
  natural-language messages from the thinking layer above.
* Exposes :py:meth:`describe_capabilities` so the thinking layer can build a
  capability boundary into its own system prompt.
* Accepts per-invocation WM entries via :class:`~m_agent.systems.wm.WMDisplay`
  for its own system prompt (recent tail, same policy as the thinking layer).
* Returns a structured :class:`~m_agent.layers.execution.contracts.ExecutionResult`
  (the final NL summary + the raw tool call history) instead of the
  controller-level ``ChatAgentResponse``.

This module deliberately keeps the same LangChain agent factory and retry
policy as the legacy :mod:`m_agent.agents.chat_controller_agent` so that the
behavioral parity tests in Phase 5 can compare like-for-like.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence

import yaml
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from m_agent.systems.tools import (
    ControllerCapabilityContext,
    ControllerCapabilityRegistry,
    build_controller_tools,
    get_default_capability_registry,
    resolve_enabled_controller_capability_names,
)
from m_agent.systems.episodic import EpisodeQueryModule
from m_agent.layers.execution.contracts import (
    CapabilityDescriptor,
    ExecutionRequest,
    ExecutionResult,
    ParamFillResult,
)
from m_agent.layers.execution.errors import ExecutionCancelledError
from m_agent.layers.execution.model_provider import ModelProvider
from m_agent.systems.wm import WMDisplay
from m_agent.utils.api_error_utils import is_network_api_error


logger = logging.getLogger(__name__)


_CONTROLLER_LIMIT_KEY = "__controller__"


@dataclass
class _ExecutionAnswer:
    """Internal LangChain structured-output schema.

    The execution layer still uses LangChain's ``ToolStrategy`` to force the
    LLM to emit a final summary string; this dataclass mirrors the legacy
    ``ChatAgentResponse`` but is private to the execution layer because the
    thinking layer never sees it.
    """

    answer: str


class _ParamFillOutcome(BaseModel):
    """Structured output schema for Think-life param fill."""

    outcome: Literal["invoke", "clarify"]
    args: Optional[Dict[str, Any]] = None
    missing_fields: List[str] = Field(default_factory=list)
    reason: str = ""


class ExecutionAgent:
    """Persona-less controller that turns NL instructions into tool calls + a summary."""

    def __init__(
        self,
        *,
        model_provider: ModelProvider,
        enabled_capability_names: List[str],
        capability_descriptions: Dict[str, str],
        tool_defaults: Dict[str, Dict[str, Any]],
        email_agent_provider: Optional[Callable[[], Any]] = None,
        schedule_agent_provider: Optional[Callable[[], Any]] = None,
        registry: Optional[ControllerCapabilityRegistry] = None,
        episode_query_module: Optional[EpisodeQueryModule] = None,
        episodic_backend: Any = None,
        system_prompt_base: str = "",
        tool_policy_prompt: str = "",
        prompt_language: str = "zh",
        capability_block_header: str = "",
        fallback_system_prompt: str = "",
        wm_display: Optional[WMDisplay] = None,
    ) -> None:
        self.model_provider = model_provider
        self.tool_defaults = dict(tool_defaults or {})
        self.email_agent_provider = email_agent_provider
        self.schedule_agent_provider = schedule_agent_provider
        self.registry = registry or get_default_capability_registry()
        self.episode_query_module = episode_query_module or EpisodeQueryModule(enabled=True)
        self.episodic_backend = episodic_backend
        self.system_prompt_base = str(system_prompt_base or "").strip()
        self.tool_policy_prompt = str(tool_policy_prompt or "").strip()
        self.prompt_language = str(prompt_language or "zh").strip().lower() or "zh"
        self._capability_block_header_override = str(capability_block_header or "").strip()
        self._fallback_system_prompt_override = str(fallback_system_prompt or "").strip()
        self.wm_display = wm_display

        # Apply the episode-query switch BEFORE storing the active capability set.
        all_enabled = list(enabled_capability_names or [])
        self.enabled_capability_names: List[str] = self.episode_query_module.filter_capability_names(
            all_enabled
        )
        self.capability_descriptions: Dict[str, str] = {
            name: str(capability_descriptions.get(name, "") or "").strip()
            for name in self.enabled_capability_names
        }
        # Pre-compute the capability boundary block for the thinking layer.
        self._capability_block_cache = self._build_capability_block(tool_names=None)

    # ------------------------------------------------------------------
    # Public API exposed to the thinking layer
    # ------------------------------------------------------------------

    def describe_capabilities(self) -> List[CapabilityDescriptor]:
        """Return the list of capability descriptors visible upward.

        Capabilities disabled via :class:`EpisodeQueryModule` are not present.
        """
        descriptors: List[CapabilityDescriptor] = []
        for name in self.enabled_capability_names:
            descriptors.append(
                CapabilityDescriptor(
                    name=name,
                    category=self._capability_category(name),
                    short_description=self.capability_descriptions.get(name, "") or f"Top-level tool: {name}",
                )
            )
        return descriptors

    def describe_capabilities_block(self, names: Optional[Sequence[str]] = None) -> str:
        """Return the human-readable capability list for prepending to thinking-layer prompts."""
        if not names:
            return self._capability_block_cache
        filtered = [
            n
            for n in names
            if str(n or "").strip() and str(n).strip() in self.enabled_capability_names
        ]
        if not filtered:
            return ""
        return self._build_capability_block(tool_names=filtered)

    def execute(
        self,
        request: ExecutionRequest,
        *,
        wm_entries: Optional[List[Dict[str, Any]]] = None,
        wm_writer_callback: Optional[Callable[[ExecutionResult], None]] = None,
        think_life_hooks: Optional[Dict[str, Any]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> ExecutionResult:
        """Run one LLM-with-tools loop over ``request.instruction`` and return a structured result."""

        instruction = str(request.instruction or "").strip()
        if not instruction:
            raise ValueError("ExecutionRequest.instruction must be a non-empty string")
        active_thread_id = str(request.thread_id or "").strip()
        if not active_thread_id:
            raise ValueError("ExecutionRequest.thread_id must be a non-empty string")

        # Per-execution mutable state (closed-over by capability tools)
        recall_state: Dict[str, Any] = {"mode": None, "result": None, "history": []}
        controller_state: Dict[str, Any] = {"history": [], "call_seq": 0}
        if think_life_hooks:
            controller_state["think_life"] = dict(think_life_hooks)

        enabled_for_invoke = self._enabled_names_for_request(request)
        tool_defaults = self._tool_defaults_for_request(request, enabled_for_invoke)

        capability_context = ControllerCapabilityContext(
            active_thread_id=active_thread_id,
            recall_state=recall_state,
            controller_state=controller_state,
            tool_defaults=tool_defaults,
            logger=logger,
            email_agent_provider=self.email_agent_provider,
            schedule_agent_provider=self.schedule_agent_provider,
            episodic_backend=self.episodic_backend,
        )
        tools = build_controller_tools(
            context=capability_context,
            enabled_tool_names=enabled_for_invoke,
            tool_descriptions=self.capability_descriptions,
            registry=self.registry,
        )
        if not tools:
            raise ValueError(
                f"No tools available for execution request (allowed={request.allowed_tool_names!r})"
            )
        system_prompt = self._build_execution_system_prompt(wm_entries=wm_entries)
        agent = create_agent(
            model=self.model_provider.model,
            system_prompt=system_prompt,
            tools=tools,
            response_format=ToolStrategy(_ExecutionAnswer),
        )

        response = self._invoke_with_retries(
            agent,
            instruction=instruction,
            active_thread_id=active_thread_id,
            correlation_id=request.correlation_id or "",
            cancel_event=cancel_event,
        )

        summary = self._extract_summary(response)
        tool_history = list(controller_state.get("history", []) or [])

        # Surface explicit failure modes for the thinking-layer summarize pass.
        insufficient = False
        limit_reached = False
        for item in tool_history:
            if not isinstance(item, dict):
                continue
            result = item.get("result")
            if isinstance(result, dict):
                if bool(result.get("limit_reached")):
                    limit_reached = True
                if bool(result.get("insufficient")):
                    insufficient = True
        recall_result = recall_state.get("result") if isinstance(recall_state, dict) else None
        if not summary and isinstance(recall_result, dict):
            summary = str(recall_result.get("answer", "") or "").strip()

        execution_result = ExecutionResult(
            summary=summary,
            tool_history=tool_history,
            success=True,
            insufficient=insufficient,
            limit_reached=limit_reached,
            raw={
                "recall_state": recall_state,
                "instruction": instruction,
                "correlation_id": request.correlation_id or "",
            },
        )

        if wm_writer_callback is not None:
            try:
                wm_writer_callback(execution_result)
            except Exception:
                logger.exception("ExecutionAgent wm_writer_callback failed")

        return execution_result

    def fill_tool_args(
        self,
        *,
        tool_name: str,
        instruction: str,
        thread_id: str,
        pending_user_request: str = "",
        correlation_id: str = "",
    ) -> ParamFillResult:
        """Think-life param pass: structured LLM call to fill args or report gaps."""
        name = str(tool_name or "").strip()
        if not name:
            raise ValueError("tool_name must be a non-empty string")
        active_thread_id = str(thread_id or "").strip()
        if not active_thread_id:
            raise ValueError("thread_id must be a non-empty string")
        if name not in self.enabled_capability_names:
            supported = ", ".join(self.enabled_capability_names)
            raise ValueError(f"Unknown or disabled tool: {name}. Enabled: {supported}")

        tool_obj = self._build_single_tool_object(tool_name=name, thread_id=active_thread_id)
        schema_text = self._tool_args_schema_text(tool_obj)
        system_prompt = self._build_param_fill_system_prompt(tool_name=name, tool_schema=schema_text)
        user_content = self._build_param_fill_user_message(
            tool_name=name,
            instruction=str(instruction or "").strip(),
            pending_user_request=str(pending_user_request or "").strip(),
        )
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            try:
                structured_model = self.model_provider.model.with_structured_output(
                    _ParamFillOutcome,
                    include_raw=False,
                )
            except Exception:
                structured_model = self.model_provider.model.with_structured_output(_ParamFillOutcome)

            def _attempt(_: int) -> _ParamFillOutcome:
                return structured_model.invoke(messages)

            outcome = self.model_provider.invoke_with_network_retry(
                _attempt,
                call_name=f"execution.fill_tool_args.{name}",
            )
        except Exception:
            logger.exception(
                "fill_tool_args failed tool=%s correlation_id=%s",
                name,
                correlation_id,
            )
            return ParamFillResult(
                tool_name=name,
                status="needs_clarification",
                reason="param_llm_failed",
            )

        if not isinstance(outcome, _ParamFillOutcome):
            outcome = _ParamFillOutcome.model_validate(outcome)

        if outcome.outcome == "clarify":
            return ParamFillResult(
                tool_name=name,
                status="needs_clarification",
                missing_fields=list(outcome.missing_fields or []),
                reason=str(outcome.reason or "").strip() or "missing_required_args",
            )

        args = dict(outcome.args or {})
        if not args:
            return ParamFillResult(
                tool_name=name,
                status="needs_clarification",
                missing_fields=list(outcome.missing_fields or []),
                reason=str(outcome.reason or "").strip() or "invoke_missing_args",
            )

        logger.debug(
            "fill_tool_args tool=%s correlation_id=%s args=%s",
            name,
            correlation_id,
            args,
        )
        return ParamFillResult(tool_name=name, status="ready", args=args)

    def _build_single_tool_bundle(
        self,
        *,
        tool_name: str,
        thread_id: str,
        think_life_hooks: Optional[Dict[str, Any]] = None,
    ) -> tuple[Any, Dict[str, Any], Dict[str, Any]]:
        name = str(tool_name or "").strip()
        recall_state: Dict[str, Any] = {"mode": None, "result": None, "history": []}
        controller_state: Dict[str, Any] = {"history": [], "call_seq": 0}
        if think_life_hooks:
            controller_state["think_life"] = dict(think_life_hooks)
        tool_defaults = self._tool_defaults_for_request(
            ExecutionRequest(
                instruction="",
                thread_id=thread_id,
                allowed_tool_names=[name],
            ),
            [name],
        )
        capability_context = ControllerCapabilityContext(
            active_thread_id=thread_id,
            recall_state=recall_state,
            controller_state=controller_state,
            tool_defaults=tool_defaults,
            logger=logger,
            email_agent_provider=self.email_agent_provider,
            schedule_agent_provider=self.schedule_agent_provider,
            episodic_backend=self.episodic_backend,
        )
        tools = build_controller_tools(
            context=capability_context,
            enabled_tool_names=[name],
            tool_descriptions=self.capability_descriptions,
            registry=self.registry,
        )
        if not tools:
            raise ValueError(f"Failed to build tool: {name}")
        return tools[0], controller_state, recall_state

    def _build_single_tool_object(
        self,
        *,
        tool_name: str,
        thread_id: str,
        think_life_hooks: Optional[Dict[str, Any]] = None,
    ) -> Any:
        tool_obj, _, _ = self._build_single_tool_bundle(
            tool_name=tool_name,
            thread_id=thread_id,
            think_life_hooks=think_life_hooks,
        )
        return tool_obj

    def _build_param_fill_system_prompt(self, *, tool_name: str, tool_schema: str = "") -> str:
        schema_block = ""
        safe_schema = str(tool_schema or "").strip()
        if safe_schema:
            schema_block = f"\n目标工具参数 schema（JSON）：\n{safe_schema}\n"
        if self.prompt_language == "zh":
            return (
                f"你是 Think-life 工具参数助手。思考层已选定唯一工具 `{tool_name}`。\n"
                "请输出结构化结果：\n"
                "- outcome=invoke：args 必须符合工具 schema，且不得猜测关键字段（时间、收件人等）。\n"
                "- outcome=clarify：信息不足时列出 missing_fields 与 reason，禁止勉强填参。\n"
                f"{schema_block}"
            )
        return (
            f"You are the Think-life tool-argument assistant. The thinking layer chose `{tool_name}` only.\n"
            "Return structured output:\n"
            "- outcome=invoke: args must match the tool schema; do not guess critical fields.\n"
            "- outcome=clarify: list missing_fields and reason when required args are unavailable.\n"
            f"{schema_block}"
        )

    @staticmethod
    def _tool_args_schema_text(tool_obj: Any) -> str:
        schema = getattr(tool_obj, "args_schema", None)
        if schema is None:
            return ""
        try:
            if hasattr(schema, "model_json_schema"):
                return json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
            if hasattr(schema, "schema"):
                return json.dumps(schema.schema(), ensure_ascii=False, indent=2)
        except Exception:
            logger.debug("Failed to serialize tool args schema", exc_info=True)
        return str(schema)

    @staticmethod
    def _build_param_fill_user_message(
        *,
        tool_name: str,
        instruction: str,
        pending_user_request: str,
    ) -> str:
        parts: List[str] = [f"Fill arguments for `{tool_name}` or report missing fields (outcome=clarify)."]
        if instruction:
            parts.append(f"Upper-layer intent: {instruction}")
        if pending_user_request:
            parts.append(f"Original user request: {pending_user_request}")
        return "\n".join(parts)

    @staticmethod
    def _extract_tool_call_args(message: Any, *, expected_name: str) -> Dict[str, Any]:
        expected = str(expected_name or "").strip()
        tool_calls = getattr(message, "tool_calls", None)
        if isinstance(tool_calls, list):
            for item in tool_calls:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "") or "").strip()
                if name != expected:
                    continue
                args = item.get("args")
                if isinstance(args, dict):
                    return dict(args)
                if isinstance(args, str) and args.strip():
                    try:
                        parsed = json.loads(args)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        continue
        additional = getattr(message, "additional_kwargs", None)
        if isinstance(additional, dict):
            raw_calls = additional.get("tool_calls")
            if isinstance(raw_calls, list):
                for item in raw_calls:
                    if not isinstance(item, dict):
                        continue
                    fn = item.get("function")
                    if not isinstance(fn, dict):
                        continue
                    name = str(fn.get("name", "") or "").strip()
                    if name != expected:
                        continue
                    arguments = fn.get("arguments")
                    if isinstance(arguments, dict):
                        return dict(arguments)
                    if isinstance(arguments, str) and arguments.strip():
                        try:
                            parsed = json.loads(arguments)
                            if isinstance(parsed, dict):
                                return parsed
                        except json.JSONDecodeError:
                            continue
        return {}

    def invoke_tool_direct(
        self,
        *,
        tool_name: str,
        tool_input: Dict[str, Any],
        thread_id: str,
        correlation_id: str = "",
        think_life_hooks: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """Think-life: invoke one capability without an execution-layer LLM."""
        name = str(tool_name or "").strip()
        if not name:
            raise ValueError("tool_name must be a non-empty string")
        active_thread_id = str(thread_id or "").strip()
        if not active_thread_id:
            raise ValueError("thread_id must be a non-empty string")
        if name not in self.enabled_capability_names:
            supported = ", ".join(self.enabled_capability_names)
            raise ValueError(f"Unknown or disabled tool: {name}. Enabled: {supported}")

        recall_state: Dict[str, Any] = {"mode": None, "result": None, "history": []}
        controller_state: Dict[str, Any] = {"history": [], "call_seq": 0}

        tool_obj, controller_state, recall_state = self._build_single_tool_bundle(
            tool_name=name,
            thread_id=active_thread_id,
            think_life_hooks=think_life_hooks,
        )
        invoke_fn = getattr(tool_obj, "invoke", None) or getattr(tool_obj, "run", None)
        if invoke_fn is None:
            raise RuntimeError(f"Tool {name} has no invoke/run method")

        payload = dict(tool_input or {})
        try:
            raw_result = invoke_fn(payload)
        except Exception as exc:
            logger.exception("invoke_tool_direct failed tool=%s", name)
            controller_state.setdefault("history", []).append(
                {
                    "tool_name": name,
                    "params": payload,
                    "result": {"success": False, "error": str(exc)},
                }
            )
            tool_history = list(controller_state.get("history", []) or [])
            return ExecutionResult(
                summary=f"tool={name}; success=false; error={exc}",
                tool_history=tool_history,
                success=False,
                raw={"tool_name": name, "correlation_id": correlation_id},
            )

        tool_history = list(controller_state.get("history", []) or [])
        summary = self._summary_from_tool_history(tool_history) or str(raw_result or "")

        insufficient = False
        limit_reached = False
        for item in tool_history:
            if not isinstance(item, dict):
                continue
            result = item.get("result")
            if isinstance(result, dict):
                if bool(result.get("limit_reached")):
                    limit_reached = True
                if bool(result.get("insufficient")):
                    insufficient = True

        return ExecutionResult(
            summary=summary,
            tool_history=tool_history,
            success=True,
            insufficient=insufficient,
            limit_reached=limit_reached,
            raw={
                "recall_state": recall_state,
                "tool_name": name,
                "tool_input": payload,
                "correlation_id": correlation_id,
                "direct_invoke": True,
            },
        )

    @staticmethod
    def _summary_from_tool_history(tool_history: List[Dict[str, Any]]) -> str:
        from m_agent.runtime.think_life.scheduler.execution_feedback import (
            feedback_summary_from_tool_history,
        )

        return feedback_summary_from_tool_history(tool_history)

    def _enabled_names_for_request(self, request: ExecutionRequest) -> List[str]:
        allowed = request.allowed_tool_names
        if not allowed:
            return list(self.enabled_capability_names)
        names: List[str] = []
        for item in allowed:
            name = str(item or "").strip()
            if not name or name not in self.enabled_capability_names:
                continue
            if name not in names:
                names.append(name)
        if not names:
            supported = ", ".join(self.enabled_capability_names)
            raise ValueError(
                f"allowed_tool_names {allowed!r} not in enabled capabilities: {supported}"
            )
        return names

    def _tool_defaults_for_request(
        self,
        request: ExecutionRequest,
        enabled_for_invoke: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        if not request.allowed_tool_names:
            return self.tool_defaults
        merged = copy.deepcopy(self.tool_defaults)
        controller = dict(merged.get(_CONTROLLER_LIMIT_KEY) or {})
        controller["max_calls_per_turn"] = 1
        merged[_CONTROLLER_LIMIT_KEY] = controller
        if len(enabled_for_invoke) == 1:
            only = enabled_for_invoke[0]
            per_tool = dict(merged.get(only) or {})
            per_tool["max_calls_per_turn"] = 1
            merged[only] = per_tool
        return merged

    # ------------------------------------------------------------------
    # System-prompt assembly (no persona)
    # ------------------------------------------------------------------

    def _build_execution_system_prompt(
        self,
        *,
        wm_entries: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        sections: List[str] = []
        if self.system_prompt_base:
            sections.append(self.system_prompt_base)
        if self.tool_policy_prompt:
            sections.append(self.tool_policy_prompt)
        if self.wm_display is not None and wm_entries:
            wm_block = self.wm_display.render(list(wm_entries), language=self.prompt_language)
            if wm_block:
                sections.append(wm_block)
        if self._capability_block_cache:
            sections.append(self._capability_block_cache)
        if not sections:
            # Fall back to a minimal task-executor framing so the model is never
            # left with an empty system prompt.
            if self._fallback_system_prompt_override:
                sections.append(self._fallback_system_prompt_override)
            else:
                sections.append(
                    "你是一个工具执行助手，请严格按照工具说明完成上层提出的指令。"
                    if self.prompt_language == "zh"
                    else "You are a tool-executing assistant. Follow the instruction precisely using the tools below."
                )
        return "\n\n".join(section for section in sections if section).strip()

    def _build_capability_block(self, tool_names: Optional[Sequence[str]] = None) -> str:
        names = list(tool_names) if tool_names is not None else list(self.enabled_capability_names)
        if not names:
            return ""
        if self._capability_block_header_override:
            header = self._capability_block_header_override
        else:
            header = "[可用顶层工具]" if self.prompt_language == "zh" else "[Available Top-Level Tools]"
        lines = [header]
        for name in names:
            description = self.capability_descriptions.get(name, "") or f"Top-level tool: {name}"
            lines.append(f"- `{name}`: {description}")
        return "\n".join(lines)

    @staticmethod
    def _capability_category(name: str) -> str:
        if name in {"shallow_recall", "deep_recall"}:
            return "episode_query"
        if name in {"email_ask", "email_read", "email_send"}:
            return "email"
        if name in {"schedule_create", "schedule_query", "schedule_delete"}:
            return "schedule"
        if name == "get_current_time":
            return "time"
        return "general"

    # ------------------------------------------------------------------
    # Invocation with recursion + network retries
    # ------------------------------------------------------------------

    def _invoke_with_retries(
        self,
        agent: Any,
        *,
        instruction: str,
        active_thread_id: str,
        correlation_id: str,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        total_attempts = max(int(self.model_provider.network_retry_attempts), 1)
        last_exc: Optional[BaseException] = None
        for attempt in range(1, total_attempts + 1):
            try:
                tid_attempt = (
                    active_thread_id if attempt == 1 else f"{active_thread_id}:netretry:{attempt}"
                )
                return self._invoke_once(
                    agent,
                    instruction=instruction,
                    active_thread_id=tid_attempt,
                    correlation_id=correlation_id,
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                last_exc = exc
                if not is_network_api_error(exc) or attempt >= total_attempts:
                    raise
                delay = self.model_provider.compute_network_retry_delay(attempt)
                logger.warning(
                    "ExecutionAgent invoke hit network/API error on attempt %d/%d: %s; retrying in %.2fs",
                    attempt,
                    total_attempts,
                    exc,
                    delay,
                )
                if delay > 0:
                    threading.Event().wait(delay)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("ExecutionAgent invoke exhausted retry attempts unexpectedly")

    def _invoke_once(
        self,
        agent: Any,
        *,
        instruction: str,
        active_thread_id: str,
        correlation_id: str,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        invoke_config = {
            "configurable": {"thread_id": f"{active_thread_id}:exec"},
            "recursion_limit": self.model_provider.recursion_limit,
        }
        payload = {"messages": [{"role": "user", "content": instruction}]}

        def _check_cancel() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise ExecutionCancelledError("execution preempted")

        try:
            return self._invoke_cooperative(
                agent,
                payload=payload,
                config=invoke_config,
                check_cancel=_check_cancel,
            )
        except GraphRecursionError:
            retry_config = {
                "configurable": {"thread_id": f"{active_thread_id}:exec:retry"},
                "recursion_limit": self.model_provider.retry_recursion_limit,
            }
            return self._invoke_cooperative(
                agent,
                payload=payload,
                config=retry_config,
                check_cancel=_check_cancel,
            )

    def _invoke_cooperative(
        self,
        agent: Any,
        *,
        payload: Dict[str, Any],
        config: Dict[str, Any],
        check_cancel: Callable[[], None],
    ) -> Dict[str, Any]:
        check_cancel()
        stream_fn = getattr(agent, "stream", None)
        if stream_fn is None:
            check_cancel()
            return agent.invoke(payload, config=config)

        last_chunk: Any = None
        for chunk in stream_fn(payload, config=config):
            check_cancel()
            if isinstance(chunk, dict):
                last_chunk = chunk
            elif last_chunk is None:
                last_chunk = chunk
        check_cancel()
        if last_chunk is None:
            return agent.invoke(payload, config=config)
        if isinstance(last_chunk, dict):
            return last_chunk
        return {"messages": [], "structured_response": last_chunk}

    @staticmethod
    def _extract_summary(response: Any) -> str:
        if not isinstance(response, dict):
            return str(response or "").strip()
        structured = response.get("structured_response")
        if structured is None:
            messages = response.get("messages")
            if isinstance(messages, list) and messages:
                last = messages[-1]
                text = getattr(last, "content", None)
                if text is None and isinstance(last, dict):
                    text = last.get("content")
                if isinstance(text, str):
                    return text.strip()
            return ""
        if hasattr(structured, "answer"):
            return str(getattr(structured, "answer", "") or "").strip()
        if isinstance(structured, dict):
            return str(structured.get("answer", "") or "").strip()
        return str(structured or "").strip()
