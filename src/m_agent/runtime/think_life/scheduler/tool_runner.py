"""Direct single-tool invocation for Think-life (param LLM + direct invoke)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from m_agent.layers.execution.contracts import ExecutionResult
from m_agent.layers.execution.core import ExecutionAgent
from m_agent.runtime.think_life.scheduler.execution_feedback import feedback_summary_from_tool_history
from m_agent.systems.tools.registry import ControllerCapabilityRegistry

logger = logging.getLogger(__name__)

REPLY_TOOL_NAME = "reply_to_user"

# Think-life: tools that skip the param LLM; think-layer ``instruction`` maps to invoke kwargs.
# Value = target kwarg name, or ``None`` when the tool takes no instruction-derived args.
# Compatibility fallback for programmatic/legacy registries. File-backed suites
# declare this behavior through each manifest's input.mode and instruction_arg.
THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG: Dict[str, Optional[str]] = {
    "get_current_time": None,
    "shallow_recall": "question",
    "deep_recall": "question",
}

# Tools whose args are fixed or trivial — skip the param LLM.
SKIP_PARAM_LLM_TOOLS = frozenset({REPLY_TOOL_NAME, *THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG.keys()})

# Param-LLM tools: primary string arg filled from think-layer instruction text.
_TOOL_PRIMARY_ARG: Dict[str, str] = {
    "schedule_create": "due_at",
    "schedule_query": "keyword",
    "schedule_delete": "schedule_id",
    "shallow_recall": "question",
    "deep_recall": "question",
    "email_ask": "keywords",
}


def build_tool_input(
    tool_name: str,
    *,
    instruction: str = "",
    user_reply_text: Optional[str] = None,
    registry: Optional[ControllerCapabilityRegistry] = None,
) -> Dict[str, Any]:
    """Map skip-param instructions deterministically to one tool payload."""
    name = str(tool_name or "").strip()
    text = str(instruction or "").strip()

    if name == REPLY_TOOL_NAME:
        message = str(user_reply_text or instruction or "").strip()
        if not message:
            message = "Acknowledge the user briefly."
        return {"message": message, "finalize": True}

    spec = registry.get(name) if registry is not None else None
    input_mode = str(getattr(spec, "input_mode", "") or "").strip()
    if input_mode == "no_args":
        return {}
    if input_mode == "instruction_arg":
        instruction_arg = str(getattr(spec, "instruction_arg", "") or "").strip()
        if not instruction_arg:
            raise ValueError(f"Tool {name} uses instruction_arg mode without an argument name")
        return {instruction_arg: text}
    if input_mode == "reply":
        message = str(user_reply_text or instruction or "").strip() or "Acknowledge the user briefly."
        return {"message": message, "finalize": True}

    skip_arg = THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG.get(name)
    if skip_arg is not None or name in THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG:
        if skip_arg is None:
            return {}
        return {skip_arg: text}

    primary = _TOOL_PRIMARY_ARG.get(name)
    if primary:
        return {primary: text}

    if text:
        return {"instruction": text}
    return {}


def invoke_single_tool(
    execution_agent: ExecutionAgent,
    *,
    tool_name: str,
    tool_input: Dict[str, Any],
    thread_id: str,
    correlation_id: str = "",
    think_life_hooks: Optional[Dict[str, Any]] = None,
) -> ExecutionResult:
    """Invoke exactly one registry tool; summary comes from tool output only."""
    return execution_agent.invoke_tool_direct(
        tool_name=tool_name,
        tool_input=dict(tool_input or {}),
        thread_id=thread_id,
        correlation_id=correlation_id,
        think_life_hooks=think_life_hooks,
    )


def summarize_tool_history(tool_history: List[Dict[str, Any]]) -> str:
    return feedback_summary_from_tool_history(tool_history)
