"""Single-capability execution for the Runtime scheduler.

The execution layer exposes capability metadata, fills one selected tool's
arguments, and invokes that tool directly. It is persona-less and never runs
an autonomous multi-tool loop.
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field

from m_agent.systems.tools import (
    ControllerCapabilityContext,
    ControllerCapabilityRegistry,
    build_controller_tools,
    get_default_capability_registry,
)
from m_agent.systems.episodic import EpisodeQueryModule
from m_agent.layers.execution.contracts import (
    CapabilityDescriptor,
    ExecutionResult,
    ParamFillResult,
)
from m_agent.layers.execution.model_provider import ModelProvider


logger = logging.getLogger(__name__)


_CONTROLLER_LIMIT_KEY = "__controller__"

class _ParamFillOutcome(BaseModel):
    """Structured output schema for Runtime param fill."""

    outcome: Literal["invoke", "clarify"]
    args: Optional[Dict[str, Any]] = None
    missing_fields: List[str] = Field(default_factory=list)
    reason: str = ""


class ExecutionAgent:
    """Fill arguments for and directly invoke one selected capability."""

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
        prompt_language: str = "zh",
        capability_block_header: str = "",
    ) -> None:
        self.model_provider = model_provider
        self.tool_defaults = dict(tool_defaults or {})
        self.email_agent_provider = email_agent_provider
        self.schedule_agent_provider = schedule_agent_provider
        self.registry = registry or get_default_capability_registry()
        self.episode_query_module = episode_query_module or EpisodeQueryModule(enabled=True)
        self.episodic_backend = episodic_backend
        self.prompt_language = str(prompt_language or "zh").strip().lower() or "zh"
        self._capability_block_header_override = str(capability_block_header or "").strip()

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

    def fill_tool_args(
        self,
        *,
        tool_name: str,
        instruction: str,
        thread_id: str,
        pending_user_request: str = "",
        correlation_id: str = "",
    ) -> ParamFillResult:
        """Runtime param pass: structured LLM call to fill args or report gaps."""
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
        runtime_hooks: Optional[Dict[str, Any]] = None,
    ) -> tuple[Any, Dict[str, Any], Dict[str, Any]]:
        name = str(tool_name or "").strip()
        recall_state: Dict[str, Any] = {"mode": None, "result": None, "history": []}
        controller_state: Dict[str, Any] = {"history": [], "call_seq": 0}
        if runtime_hooks:
            controller_state["runtime"] = dict(runtime_hooks)
        tool_defaults = copy.deepcopy(self.tool_defaults)
        controller_defaults = dict(tool_defaults.get(_CONTROLLER_LIMIT_KEY) or {})
        controller_defaults["max_calls_per_turn"] = 1
        tool_defaults[_CONTROLLER_LIMIT_KEY] = controller_defaults
        per_tool_defaults = dict(tool_defaults.get(name) or {})
        per_tool_defaults["max_calls_per_turn"] = 1
        tool_defaults[name] = per_tool_defaults
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
        runtime_hooks: Optional[Dict[str, Any]] = None,
    ) -> Any:
        tool_obj, _, _ = self._build_single_tool_bundle(
            tool_name=tool_name,
            thread_id=thread_id,
            runtime_hooks=runtime_hooks,
        )
        return tool_obj

    def _build_param_fill_system_prompt(self, *, tool_name: str, tool_schema: str = "") -> str:
        schema_block = ""
        safe_schema = str(tool_schema or "").strip()
        if safe_schema:
            schema_block = f"\n目标工具参数 schema（JSON）：\n{safe_schema}\n"
        if self.prompt_language == "zh":
            return (
                f"你是 Runtime 工具参数助手。思考层已选定唯一工具 `{tool_name}`。\n"
                "请输出结构化结果：\n"
                "- outcome=invoke：args 必须符合工具 schema，且不得猜测关键字段（时间、收件人等）。\n"
                "- outcome=clarify：信息不足时列出 missing_fields 与 reason，禁止勉强填参。\n"
                f"{schema_block}"
            )
        return (
            f"You are the Runtime tool-argument assistant. The thinking layer chose `{tool_name}` only.\n"
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

    def invoke_tool_direct(
        self,
        *,
        tool_name: str,
        tool_input: Dict[str, Any],
        thread_id: str,
        correlation_id: str = "",
        runtime_hooks: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """Runtime: invoke one capability without an execution-layer LLM."""
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
            runtime_hooks=runtime_hooks,
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
        from m_agent.runtime.turn_support.execution_feedback import (
            feedback_summary_from_tool_history,
        )

        return feedback_summary_from_tool_history(tool_history)

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

    def _capability_category(self, name: str) -> str:
        spec = self.registry.get(name)
        if spec is not None:
            category = str(getattr(spec, "category", "") or "").strip()
            if category and category != "general":
                return category
        if name in {"shallow_recall", "deep_recall"}:
            return "episode_query"
        if name in {"email_ask", "email_read", "email_send"}:
            return "email"
        if name in {"schedule_create", "schedule_query", "schedule_delete"}:
            return "schedule"
        if name == "get_current_time":
            return "time"
        return "general"
