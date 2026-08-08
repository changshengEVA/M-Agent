"""Canonical Observation → durable pool admission (Ingress Freeze).

Product runtimes and acceptance harnesses share this path. Typed Gateway
helpers and harness submit APIs must compile to Observation first.
"""

from __future__ import annotations

from typing import Any, Optional

from m_agent.layers.perception.contracts import StimulusKind
from m_agent.runtime.domain.contracts import StimulusEnvelope
from m_agent.runtime.perception.feedback_adapter import render_feedback_stimulus_view
from m_agent.runtime.perception.observation import (
    CHAT_STIMULUS_VIEW,
    observation_to_envelope,
)
from m_agent.runtime.perception.schedule_adapter import render_schedule_stimulus_view
from m_agent.sdk.stimulus.contracts import (
    Disposition,
    IngestResult,
    Observation,
    ObservationValidationError,
    PoolState,
    validate_observation,
)

_KIND_TO_TYPE = {
    StimulusKind.USER_MESSAGE: "user_message",
    StimulusKind.EXECUTION_FEEDBACK: "execution_feedback",
    StimulusKind.SCHEDULED_PLAN: "scheduled_plan",
    StimulusKind.OBSERVATION_TRIGGER: "observation_trigger",
}


def admit_observation(
    gateway: Any,
    observation: Observation,
    *,
    schedule_drainer: bool = True,
) -> IngestResult:
    """Validate Observation and admit it through ``gateway.submit``."""

    try:
        observation = validate_observation(observation)
    except ObservationValidationError as exc:
        raise ValueError(str(exc)) from exc

    store = getattr(getattr(gateway, "inbox", None), "store", None)
    expires_at = str(observation.expires_at or "").strip()
    if expires_at and expires_at < observation.observed_at:
        envelope = observation_to_envelope(observation)
        if store is None:
            raise RuntimeError("stimulus store unavailable for expired admit")
        stored = store.admit_stimulus(
            envelope,
            effective_priority=0,
            pool_state=PoolState.TERMINATED.value,
            disposition=Disposition.DISCARDED.value,
            disposition_stage="admission",
            disposition_reason="expired",
            reason_code="expired",
        )
        return IngestResult(
            stimulus_id=stored.stimulus_id,
            pool_state=stored.pool_state,
            created=True,
            merged=False,
            disposition=stored.disposition,
            reason_code=stored.reason_code,
            reason=stored.disposition_reason,
        )

    if str(observation.type or "").strip().lower() == "irrelevant":
        envelope = observation_to_envelope(observation)
        if store is None:
            raise RuntimeError("stimulus store unavailable for irrelevant admit")
        stored = store.admit_stimulus(
            envelope,
            effective_priority=0,
            pool_state=PoolState.TERMINATED.value,
            disposition=Disposition.DISCARDED.value,
            disposition_stage="admission",
            disposition_reason="irrelevant",
            reason_code="irrelevant",
        )
        return IngestResult(
            stimulus_id=stored.stimulus_id,
            pool_state=stored.pool_state,
            created=True,
            merged=False,
            disposition=stored.disposition,
            reason_code=stored.reason_code,
            reason=stored.disposition_reason,
        )

    envelope = observation_to_envelope(observation)
    prior = None
    if store is not None and envelope.ingress_key:
        prior = store.load_stimulus_by_ingress_key(envelope.ingress_key)
    stimulus_id = gateway.submit(envelope, schedule_drainer=schedule_drainer)
    if store is None:
        return IngestResult(
            stimulus_id=stimulus_id,
            pool_state=PoolState.READY.value,
            created=True,
            merged=False,
        )
    stored = store.load_stimulus(stimulus_id)
    if stored is None:
        raise RuntimeError(f"stimulus missing after ingest: {stimulus_id}")
    merged = prior is not None
    return IngestResult(
        stimulus_id=stored.stimulus_id,
        pool_state=stored.pool_state,
        created=not merged,
        merged=merged,
        disposition=(
            Disposition.MERGED.value if merged else stored.disposition
        ),
        reason_code=stored.reason_code
        or ("merged_existing" if merged else None),
        reason=stored.disposition_reason,
    )


def envelope_to_observation(envelope: StimulusEnvelope) -> Observation:
    """Compile an internal envelope back into a public Observation for ingest."""

    payload = dict(envelope.payload or {})
    kind = envelope.kind
    obs_type = _KIND_TO_TYPE.get(kind, "observation_trigger")
    if envelope.activation_id:
        payload.setdefault("activation_id", envelope.activation_id)
    if envelope.delegate_id:
        payload.setdefault("delegate_id", envelope.delegate_id)
    if envelope.schedule_id:
        payload.setdefault("schedule_id", envelope.schedule_id)
    if envelope.schedule_run_id:
        payload.setdefault("schedule_run_id", envelope.schedule_run_id)
        payload.setdefault("run_id", envelope.schedule_run_id)
    if envelope.schedule_delivery_id:
        payload.setdefault(
            "schedule_delivery_id",
            envelope.schedule_delivery_id,
        )
    if envelope.stimulus_id:
        payload.setdefault("stimulus_id", envelope.stimulus_id)
    if envelope.priority_override is not None:
        payload["priority_override"] = int(envelope.priority_override)

    view = str(payload.get("stimulus_view", "") or "").strip()
    if not view:
        view = _default_stimulus_view(kind, envelope, payload)
        if view:
            payload["stimulus_view"] = view

    source = str(payload.get("source", "") or "").strip() or {
        StimulusKind.USER_MESSAGE: "chat",
        StimulusKind.EXECUTION_FEEDBACK: "execution",
        StimulusKind.SCHEDULED_PLAN: "heartbeat",
        StimulusKind.OBSERVATION_TRIGGER: "runtime",
    }.get(kind, "runtime")

    occurred = str(envelope.occurred_at or "").strip() or "1970-01-01T00:00:00Z"
    return Observation(
        source=source,
        type=obs_type,
        thread_id=envelope.thread_id,
        conversation_id=envelope.conversation_id,
        occurred_at=occurred,
        observed_at=occurred,
        subject=str(payload.get("subject", "") or "").strip(),
        text=str(envelope.text or "").strip(),
        idempotency_key=envelope.ingress_key,
        transaction_id=envelope.transaction_id,
        payload=payload,
        stimulus_view=view,
    )


def _default_stimulus_view(
    kind: StimulusKind,
    envelope: StimulusEnvelope,
    payload: dict,
) -> str:
    if kind == StimulusKind.USER_MESSAGE:
        return CHAT_STIMULUS_VIEW
    if kind == StimulusKind.EXECUTION_FEEDBACK:
        return render_feedback_stimulus_view(
            tool_history=payload.get("tool_history"),
            summary=str(payload.get("summary", "") or "").strip(),
        )
    if kind == StimulusKind.SCHEDULED_PLAN:
        return render_schedule_stimulus_view(
            schedule_id=str(
                envelope.schedule_id or payload.get("schedule_id", "") or ""
            ),
            deferred_objective=str(
                envelope.text
                or payload.get("deferred_objective", "")
                or payload.get("prompt", "")
                or ""
            ).strip(),
            due_at_utc=str(payload.get("due_at_utc", "") or "").strip(),
            timezone_name=str(payload.get("timezone_name", "") or "").strip(),
            run_id=str(
                envelope.schedule_run_id
                or payload.get("run_id", "")
                or ""
            ).strip(),
        )
    return (
        f"kind: {kind.value}\n"
        f"semantic_role: runtime_event\n"
        f"text:\n{str(envelope.text or '').strip() or '(empty)'}"
    )


class GatewayIngestHost:
    """Minimal runtime host so Source Adapters can call ``ingest`` on a Gateway."""

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    def ingest(
        self,
        observation: Observation,
        *,
        schedule_drainer: bool = True,
    ) -> IngestResult:
        return admit_observation(
            self.gateway,
            observation,
            schedule_drainer=schedule_drainer,
        )
