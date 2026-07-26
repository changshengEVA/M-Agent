"""Resolve Think-life delegate targets (one tool per delegate)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

from m_agent.layers.execution.contracts import ParamFillResult
from m_agent.layers.thinking.contracts import ThinkingDecision, is_execute_mode
from m_agent.layers.execution.core import ExecutionAgent
from m_agent.runtime.think_life.scheduler.tool_runner import (
    REPLY_TOOL_NAME,
    SKIP_PARAM_LLM_TOOLS,
    build_tool_input,
)


@dataclass(frozen=True)
class DelegateTarget:
    """One delegate step: tool name plus intent text (param LLM fills structured args)."""

    tool_name: str
    instruction: str = ""
    for_user_reply: bool = False
    user_reply_text: Optional[str] = None


def resolve_tool_name(
    decision: ThinkingDecision,
    *,
    enabled_tools: Sequence[str],
    for_user_reply: bool = False,
) -> Optional[str]:
    """Pick exactly one capability name for the next delegate, or None if invalid."""
    enabled = {str(n or "").strip() for n in enabled_tools if str(n or "").strip()}
    if not enabled:
        return None

    if for_user_reply:
        return REPLY_TOOL_NAME if REPLY_TOOL_NAME in enabled else None

    if not is_execute_mode(decision.mode):
        return None

    name = str(decision.tool_name or "").strip()
    if name and name in enabled:
        return name

    hints = decision.capability_hint or []
    if isinstance(hints, list):
        for item in hints:
            hint = str(item or "").strip()
            if hint in enabled:
                return hint

    return None


def plan_delegate_target(
    decision: ThinkingDecision,
    *,
    enabled_tools: Sequence[str],
    for_user_reply: bool = False,
    user_reply_text: Optional[str] = None,
) -> Optional[DelegateTarget]:
    """Return the next delegate target (tool + intent), or None."""
    tool_name = resolve_tool_name(
        decision,
        enabled_tools=enabled_tools,
        for_user_reply=for_user_reply,
    )
    if not tool_name:
        return None
    if for_user_reply:
        return DelegateTarget(
            tool_name=tool_name,
            instruction=str(decision.instruction or "").strip(),
            for_user_reply=True,
            user_reply_text=user_reply_text,
        )
    return DelegateTarget(
        tool_name=tool_name,
        instruction=str(decision.instruction or "").strip(),
    )


def plan_delegate(
    decision: ThinkingDecision,
    *,
    enabled_tools: Sequence[str],
    for_user_reply: bool = False,
    user_reply_text: Optional[str] = None,
    registry=None,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return one tool payload using the deterministic skip-param mapping."""
    target = plan_delegate_target(
        decision,
        enabled_tools=enabled_tools,
        for_user_reply=for_user_reply,
        user_reply_text=user_reply_text,
    )
    if not target:
        return None
    tool_input = build_tool_input(
        target.tool_name,
        instruction=target.instruction,
        user_reply_text=target.user_reply_text,
        registry=registry,
    )
    return target.tool_name, tool_input


def uses_param_llm(
    tool_name: str,
    *,
    for_user_reply: bool = False,
    registry=None,
) -> bool:
    if for_user_reply:
        return False
    name = str(tool_name or "").strip()
    spec = registry.get(name) if registry is not None and name else None
    input_mode = str(getattr(spec, "input_mode", "") or "").strip()
    if input_mode in {"param_llm", "instruction_arg", "no_args", "reply"}:
        return input_mode == "param_llm"
    return bool(name) and name not in SKIP_PARAM_LLM_TOOLS


def resolve_delegate_tool_input(
    execution_agent: ExecutionAgent,
    target: DelegateTarget,
    *,
    thread_id: str,
    correlation_id: str = "",
    pending_user_request: str = "",
) -> ParamFillResult:
    """Fill tool arguments with the param LLM or deterministic skip-param mapping."""
    tool_name = str(target.tool_name or "").strip()
    if not uses_param_llm(
        target.tool_name,
        for_user_reply=target.for_user_reply,
        registry=execution_agent.registry,
    ):
        args = build_tool_input(
            target.tool_name,
            instruction=target.instruction,
            user_reply_text=target.user_reply_text,
            registry=execution_agent.registry,
        )
        return ParamFillResult(tool_name=tool_name, status="ready", args=args)

    return execution_agent.fill_tool_args(
        tool_name=target.tool_name,
        instruction=target.instruction,
        thread_id=thread_id,
        pending_user_request=pending_user_request,
        correlation_id=correlation_id,
    )
