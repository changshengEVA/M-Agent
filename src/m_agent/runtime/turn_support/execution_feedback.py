"""Structured execution feedback + premature-reply gating for Runtime."""
from __future__ import annotations

import copy
import re
from dataclasses import replace
from typing import Any, Dict, List, Optional

from m_agent.layers.execution.contracts import ParamFillResult
from m_agent.layers.perception.contracts import PerceptionInput
from m_agent.runtime.domain.contracts import StimulusEnvelope, StimulusKind

_MULTI_STEP_MARKERS = (
    "一周",
    "七天",
    "7天",
    "每天",
    "每日",
    "多个",
    "分别",
    "批量",
    "全部",
    "逐个",
    "each day",
    "every day",
    "daily",
    "for a week",
    "for the week",
    "whole week",
    "multiple",
)


def looks_like_multi_step_request(text: str) -> bool:
    """Heuristic: user text likely needs more than one tool delegate."""
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    for marker in _MULTI_STEP_MARKERS:
        if marker.lower() in lowered:
            return True
    if re.search(r"\b\d+\s*(个|条|封|次|天|items?|times?)\b", lowered):
        return True
    return False


def extract_last_tool_step(tool_history: Any) -> Dict[str, Any]:
    """Summarize the most recent tool call from controller history."""
    if not isinstance(tool_history, list):
        return {}
    for item in reversed(tool_history):
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool_name", "") or "").strip()
        if not name:
            continue
        result = item.get("result")
        facts: Dict[str, Any] = {"tool_name": name}
        if isinstance(result, dict):
            facts["success"] = bool(result.get("success", True))
            mode = str(result.get("mode", "") or "").strip()
            if mode:
                facts["mode"] = mode
            query = str(result.get("query", "") or "").strip()
            if query:
                facts["query"] = query
            url = str(result.get("url", "") or "").strip()
            if url:
                facts["url"] = url
            facts["action"] = str(result.get("action", "") or "").strip() or None
            if result.get("count") is not None:
                facts["count"] = int(result.get("count") or 0)
            if result.get("result_count") is not None:
                facts["result_count"] = int(result.get("result_count") or 0)
            if result.get("content_chars") is not None:
                facts["content_chars"] = int(result.get("content_chars") or 0)
            facts["answer"] = str(
                result.get("answer", result.get("message", "")) or ""
            ).strip() or None
            if "finalize" in result:
                facts["finalize"] = bool(result.get("finalize"))
            facts["needs_clarification"] = bool(result.get("needs_clarification"))
            facts["partial"] = bool(result.get("partial"))
            stage = str(result.get("stage", "") or "").strip()
            if stage:
                facts["stage"] = stage
            if "tool_invoked" in result:
                facts["tool_invoked"] = bool(result.get("tool_invoked"))
            missing_fields = result.get("missing_fields")
            if isinstance(missing_fields, list) and missing_fields:
                facts["missing_fields"] = [str(item or "").strip() for item in missing_fields if str(item or "").strip()]
        elif result is not None:
            facts["answer"] = str(result).strip()
        return facts
    return {}


def feedback_summary_from_tool_history(tool_history: Any) -> str:
    """Prefer structured tool output over execution-layer LLM summary."""
    step = extract_last_tool_step(tool_history)
    if not step:
        return ""
    parts: List[str] = []
    name = step.get("tool_name")
    if name:
        parts.append(f"tool={name}")
    action = step.get("action")
    if action:
        parts.append(f"action={action}")
    mode = step.get("mode")
    if mode:
        parts.append(f"mode={mode}")
    query = step.get("query")
    if query:
        parts.append(f"query={query}")
    url = step.get("url")
    if url:
        parts.append(f"url={url}")
    if step.get("count") is not None:
        parts.append(f"count={step['count']}")
    if step.get("result_count") is not None:
        parts.append(f"result_count={step['result_count']}")
    if step.get("content_chars") is not None:
        parts.append(f"content_chars={step['content_chars']}")
    if step.get("success") is False:
        parts.append("success=false")
    if step.get("finalize") is not None:
        parts.append(f"finalize={str(bool(step['finalize'])).lower()}")
    if step.get("needs_clarification") is True:
        parts.append("needs_clarification=true")
    if step.get("partial") is True:
        parts.append("partial=true")
    stage = step.get("stage")
    if stage:
        parts.append(f"stage={stage}")
    if step.get("tool_invoked") is False:
        parts.append("tool_invoked=false")
    missing_fields = step.get("missing_fields")
    if isinstance(missing_fields, list) and missing_fields:
        parts.append(f"missing_fields={','.join(missing_fields)}")
    answer = step.get("answer")
    if answer:
        parts.append(f"result={answer}")
    return "; ".join(parts)


def build_param_gap_tool_history(
    fill: ParamFillResult,
    *,
    instruction: str = "",
) -> List[Dict[str, Any]]:
    """Synthetic controller history when param fill blocks tool invoke."""
    reason = str(fill.reason or "").strip() or "missing_required_args"
    missing = [str(item or "").strip() for item in (fill.missing_fields or []) if str(item or "").strip()]
    answer = reason
    if missing:
        answer = f"{reason}; missing: {', '.join(missing)}"
    params: Dict[str, Any] = {}
    safe_instruction = str(instruction or "").strip()
    if safe_instruction:
        params["instruction"] = safe_instruction
    return [
        {
            "tool_name": str(fill.tool_name or "").strip(),
            "params": params,
            "result": {
                "success": False,
                "action": "param_clarify",
                "needs_clarification": True,
                "stage": "param_fill",
                "tool_invoked": False,
                "missing_fields": missing,
                "answer": answer,
                "message": answer,
            },
        }
    ]


def param_gap_summary(fill: ParamFillResult) -> str:
    return feedback_summary_from_tool_history(build_param_gap_tool_history(fill))


def premature_reply_block_reason(
    *,
    pending_user_request: str,
    stimulus: StimulusEnvelope,
) -> Optional[str]:
    """Return a block reason when answer_directly is likely too early."""
    if stimulus.kind != StimulusKind.EXECUTION_FEEDBACK:
        return None
    pending = str(pending_user_request or "").strip()
    if not looks_like_multi_step_request(pending):
        return None

    tool_history = stimulus.payload.get("tool_history")
    step = extract_last_tool_step(tool_history)
    if not step:
        return "multi_step_no_tool_evidence"

    tool_name = str(step.get("tool_name", "") or "").strip()
    count = step.get("count")
    if step.get("partial"):
        return "schedule_create_partial_step"
    if tool_name == "schedule_create" and count == 1 and looks_like_multi_step_request(pending):
        return "schedule_create_created_only_one"

    if tool_name == "schedule_query":
        # Query with zero items on multi-step create request — still in progress
        if count == 0 and any(token in pending for token in ("安排", "创建", "设", "schedule", "create")):
            return "schedule_query_empty_while_creating"

    return None


def build_feedback_user_message(
    *,
    pending_user_request: str,
    tool_history: Any,
    llm_summary: str = "",
) -> str:
    """User-visible plan input after one delegate step."""
    structured = feedback_summary_from_tool_history(tool_history)
    pending = str(pending_user_request or "").strip()
    multi = looks_like_multi_step_request(pending)
    step = extract_last_tool_step(tool_history)
    is_param_gap = step.get("stage") == "param_fill" and step.get("tool_invoked") is False

    if is_param_gap:
        parts = [
            "[Param fill] Required arguments were missing; the tool was NOT invoked.",
        ]
        if pending:
            parts.append(f"Original user request: {pending}")
        if structured:
            parts.append(f"Structured tool result: {structured}")
        missing_fields = step.get("missing_fields")
        if isinstance(missing_fields, list) and missing_fields:
            parts.append(f"Missing fields: {', '.join(missing_fields)}")
        parts.append(
            "Try other enabled tools (recall, get_current_time, schedule_query) to obtain "
            "missing info from context; if still unavailable, answer_directly to ask the user. "
            "Do not repeat execute on the same tool_name with the same missing fields."
        )
        return " ".join(parts)

    parts = [
        "[Execution feedback] One delegate step finished.",
        "The user's full request may still be incomplete — do not assume all work is done.",
    ]
    if pending:
        parts.append(f"Original user request: {pending}")
    if structured:
        parts.append(f"Structured tool result: {structured}")
    elif llm_summary:
        parts.append(f"Execution note (secondary): {llm_summary}")

    if multi:
        parts.append(
            "This request likely needs multiple tool steps (one tool_name per plan round). "
            "Continue with mode=execute and the next single tool_name unless you verified completion "
            "(e.g. schedule_query). Set request_complete=true only when truly finished."
        )
    else:
        parts.append(
            "Plan next: mode=execute with one tool_name for another step, "
            "mode=silent when no reply or tool step is needed yet, "
            "or answer_directly when the user request is satisfied."
        )
    return " ".join(parts)


def build_completion_nudge_message(block_reason: str) -> str:
    templates = {
        "schedule_create_partial_step": (
            "[System gate] schedule_create returned partial=true (only one item created for a bulk-style request). "
            "Do NOT answer_directly. Plan mode=execute with tool_name=schedule_create for the next single item."
        ),
        "schedule_create_created_only_one": (
            "[System gate] schedule_create only created count=1, but the user asked for a multi-day "
            "or repeating schedule. Do NOT answer_directly. Plan mode=execute with tool_name=schedule_create "
            "to create the next single item (due_at + deferred_objective per call), or schedule_query to verify."
        ),
        "schedule_query_empty_while_creating": (
            "[System gate] Schedules are not created yet for this multi-step request. "
            "Plan mode=execute with tool_name=schedule_create for the next item."
        ),
        "multi_step_no_tool_evidence": (
            "[System gate] Multi-step user request but no tool evidence yet. "
            "Plan mode=execute with one tool_name; do not answer_directly."
        ),
    }
    return templates.get(
        block_reason,
        "[System gate] User request likely incomplete. Continue with mode=execute (one tool_name).",
    )


def augment_perception_with_nudge(perception: PerceptionInput, nudge: str) -> PerceptionInput:
    """Copy perception with an appended gate nudge in readable stimulus text."""
    base = str(perception.stimulus.text or "").strip()
    merged = f"{base}\n\n{nudge}".strip()
    payload = copy.deepcopy(dict(perception.stimulus.payload or {}))
    payload["completion_gate_nudge"] = str(nudge or "").strip()
    return replace(
        perception,
        stimulus=replace(perception.stimulus, text=merged, payload=payload),
    )
