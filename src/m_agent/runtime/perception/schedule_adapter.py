"""Product Schedule Source Adapter.

Adapter-private ScheduleSignal stays here. Runtime only sees Observation via ingest.
Renders wake-up + deferred todo into ``stimulus_view`` — never completion proof.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from m_agent.api.chat_api_shared import _now_iso
from m_agent.sdk.stimulus.contracts import IngestResult, Observation


def render_schedule_stimulus_view(
    *,
    schedule_id: str,
    deferred_objective: str = "",
    due_at_utc: str = "",
    timezone_name: str = "",
    run_id: str = "",
) -> str:
    """Render adapter-owned Current Stimulus body for a due schedule."""

    lines = [
        "kind: scheduled_plan",
        "semantic_role: schedule_due_todo",
        f"schedule_id: {str(schedule_id or '').strip() or '(unknown)'}",
    ]
    if run_id:
        lines.append(f"run_id: {run_id}")
    if due_at_utc:
        lines.append(f"due_at_utc: {due_at_utc}")
    if timezone_name:
        lines.append(f"timezone_name: {timezone_name}")
    objective = str(deferred_objective or "").strip()
    lines.append("deferred_objective:")
    lines.append(objective or "(none)")
    lines.append(
        "note: this is a wake-up with a previously planned todo; "
        "it is not proof that the work was completed"
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class ScheduleSignal:
    """Adapter-private schedule-due event. Not part of the public SDK."""

    schedule_id: str
    text: str = ""
    occurred_at: str = ""
    run_id: str = ""
    delivery_id: str = ""
    owner_id: str = ""
    transaction_id: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


class ScheduleSourceAdapter:
    """Normalize private ScheduleSignals and ingest Observations."""

    def __init__(self, runtime: Any, *, agent_name: str = "Agent") -> None:
        self.runtime = runtime
        self.agent_name = str(agent_name or "Agent").strip() or "Agent"

    @staticmethod
    def idempotency_key(*, delivery_id: str = "", run_id: str = "") -> Optional[str]:
        identity = str(delivery_id or "").strip() or str(run_id or "").strip()
        if not identity:
            return None
        return f"schedule_delivery:{identity}"

    def render_view(self, signal: ScheduleSignal) -> str:
        body = dict(signal.payload or {})
        objective = str(
            signal.text
            or body.get("deferred_objective", "")
            or body.get("prompt", "")
            or ""
        ).strip()
        return render_schedule_stimulus_view(
            schedule_id=signal.schedule_id,
            deferred_objective=objective,
            due_at_utc=str(body.get("due_at_utc", "") or "").strip(),
            timezone_name=str(body.get("timezone_name", "") or "").strip(),
            run_id=str(signal.run_id or body.get("run_id", "") or "").strip(),
        )

    def to_observation(
        self,
        signal: ScheduleSignal,
        *,
        thread_id: str,
        conversation_id: str,
        observed_at: Optional[str] = None,
    ) -> Observation:
        occurred_at = str(signal.occurred_at or "").strip() or _now_iso()
        observed = str(observed_at or occurred_at).strip() or occurred_at
        body = dict(signal.payload or {})
        body.setdefault("schedule_id", signal.schedule_id)
        body.setdefault("agent_name", self.agent_name)
        run_id = str(signal.run_id or body.get("run_id", "") or "").strip()
        delivery_id = str(
            signal.delivery_id or body.get("schedule_delivery_id", "") or ""
        ).strip()
        if run_id:
            body["run_id"] = run_id
            body["schedule_run_id"] = run_id
        if delivery_id:
            body["schedule_delivery_id"] = delivery_id
        if signal.owner_id:
            body["owner_id"] = signal.owner_id
        source_transaction_id = str(
            signal.transaction_id or body.get("transaction_id", "") or ""
        ).strip()
        if source_transaction_id:
            body["transaction_id"] = source_transaction_id
        text = str(signal.text or "").strip() or "schedule_due"
        return Observation(
            source="heartbeat",
            type="scheduled_plan",
            thread_id=thread_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            observed_at=observed,
            subject="schedule",
            text=text,
            idempotency_key=self.idempotency_key(
                delivery_id=delivery_id,
                run_id=run_id,
            ),
            transaction_id=source_transaction_id or None,
            payload=body,
            stimulus_view=self.render_view(signal),
        )

    def handle_due(
        self,
        signal: ScheduleSignal,
        *,
        thread_id: str,
        conversation_id: str,
        observed_at: Optional[str] = None,
        schedule_drainer: bool = True,
    ) -> IngestResult:
        observation = self.to_observation(
            signal,
            thread_id=thread_id,
            conversation_id=conversation_id,
            observed_at=observed_at,
        )
        return self.runtime.ingest(
            observation,
            schedule_drainer=schedule_drainer,
        )
