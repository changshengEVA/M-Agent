"""Product Feedback Source Adapter.

Adapter-private FeedbackSignal stays here. Runtime only sees Observation via ingest.
Renders provable tool results into ``stimulus_view`` — never deferred todos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional

from m_agent.api.chat_api_shared import _now_iso
from m_agent.runtime.turn_support.execution_feedback import (
    feedback_summary_from_tool_history,
)
from m_agent.sdk.stimulus.contracts import IngestResult, Observation


def render_feedback_stimulus_view(
    *,
    tool_history: Any,
    summary: str = "",
) -> str:
    """Render adapter-owned Current Stimulus body for execution feedback."""

    lines = [
        "kind: execution_feedback",
        "semantic_role: provable_tool_results",
    ]
    structured = feedback_summary_from_tool_history(tool_history)
    readable = str(structured or summary or "").strip()
    if readable:
        lines.append(f"summary: {readable}")
    history = tool_history if isinstance(tool_history, list) else []
    if not history:
        lines.append("evidence: (none)")
        return "\n".join(lines)
    lines.append("evidence:")
    for index, item in enumerate(history, start=1):
        if not isinstance(item, Mapping):
            continue
        tool_name = str(item.get("tool_name", "") or "").strip() or "tool"
        result = item.get("result")
        result_dict = dict(result) if isinstance(result, Mapping) else {}
        item_summary = str(
            result_dict.get(
                "summary",
                result_dict.get("message", result_dict.get("answer", "")),
            )
            or ""
        ).strip()
        if not item_summary and result is not None:
            item_summary = str(result).strip()
        lines.append(f"- evidence_{index}:")
        lines.append(f"  tool_name: {tool_name}")
        if "success" in result_dict:
            lines.append(f"  success: {result_dict.get('success')}")
        if "count" in result_dict:
            lines.append(f"  count: {result_dict.get('count')}")
        if "partial" in result_dict:
            lines.append(f"  partial: {result_dict.get('partial')}")
        if "stage" in result_dict:
            lines.append(f"  stage: {result_dict.get('stage')}")
        if "tool_invoked" in result_dict:
            lines.append(f"  tool_invoked: {result_dict.get('tool_invoked')}")
        missing = result_dict.get("missing_fields")
        if isinstance(missing, list) and missing:
            lines.append(
                "  missing_fields: "
                + ", ".join(str(field) for field in missing if str(field).strip())
            )
        lines.append(f"  summary: {item_summary or '(empty)'}")
    return "\n".join(lines)


@dataclass(frozen=True)
class FeedbackSignal:
    """Adapter-private execution-feedback event. Not part of the public SDK."""

    tool_history: List[Any] = field(default_factory=list)
    summary: str = ""
    delegate_id: str = ""
    activation_id: str = ""
    effect_id: str = ""
    effect_status: str = ""
    occurred_at: str = ""
    ingress_key: str = ""
    stimulus_id: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


class FeedbackSourceAdapter:
    """Normalize private FeedbackSignals and ingest Observations."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def render_view(self, signal: FeedbackSignal) -> str:
        return render_feedback_stimulus_view(
            tool_history=list(signal.tool_history or []),
            summary=signal.summary,
        )

    def to_observation(
        self,
        signal: FeedbackSignal,
        *,
        thread_id: str,
        conversation_id: str,
        transaction_id: str,
        observed_at: Optional[str] = None,
    ) -> Observation:
        occurred_at = str(signal.occurred_at or "").strip() or _now_iso()
        observed = str(observed_at or occurred_at).strip() or occurred_at
        summary = str(signal.summary or "").strip()
        readable = summary or "Execution finished and returned tool evidence."
        payload: dict[str, Any] = dict(signal.payload or {})
        payload["activation_id"] = str(signal.activation_id or "").strip()
        payload["delegate_id"] = str(signal.delegate_id or "").strip()
        payload["tool_history"] = list(signal.tool_history or [])
        payload["summary"] = summary
        if signal.effect_id:
            payload["effect_id"] = str(signal.effect_id).strip()
            payload.setdefault("stimulus_id", f"stim_{signal.effect_id}")
        if signal.stimulus_id:
            payload["stimulus_id"] = str(signal.stimulus_id).strip()
        if signal.effect_status:
            payload["effect_status"] = str(signal.effect_status).strip()
        return Observation(
            source="execution",
            type="execution_feedback",
            thread_id=thread_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            observed_at=observed,
            subject="execution",
            text=readable,
            idempotency_key=str(signal.ingress_key or "").strip() or None,
            transaction_id=str(transaction_id or "").strip() or None,
            payload=payload,
            stimulus_view=self.render_view(signal),
        )

    def handle_feedback(
        self,
        signal: FeedbackSignal,
        *,
        thread_id: str,
        conversation_id: str,
        transaction_id: str,
        observed_at: Optional[str] = None,
        schedule_drainer: bool = False,
    ) -> IngestResult:
        observation = self.to_observation(
            signal,
            thread_id=thread_id,
            conversation_id=conversation_id,
            transaction_id=transaction_id,
            observed_at=observed_at,
        )
        return self.runtime.ingest(
            observation,
            schedule_drainer=schedule_drainer,
        )
