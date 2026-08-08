"""Compile public Observations into internal StimulusEnvelope records."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.runtime.domain.contracts import StimulusEnvelope
from m_agent.sdk.stimulus.contracts import (
    Observation,
    ObservationValidationError,
    validate_observation,
)

_TYPE_TO_KIND = {
    "user_message": StimulusKind.USER_MESSAGE,
    "execution_feedback": StimulusKind.EXECUTION_FEEDBACK,
    "scheduled_plan": StimulusKind.SCHEDULED_PLAN,
    "heartbeat": StimulusKind.SCHEDULED_PLAN,
    "observation": StimulusKind.OBSERVATION_TRIGGER,
    "observation_trigger": StimulusKind.OBSERVATION_TRIGGER,
    "webhook": StimulusKind.OBSERVATION_TRIGGER,
    "external_event": StimulusKind.OBSERVATION_TRIGGER,
}

CHAT_STIMULUS_VIEW = (
    "kind: user_message\n"
    "semantic_role: user_utterance\n"
    "content_source: current_user_message"
)


def observation_to_envelope(
    observation: Observation,
    *,
    stimulus_id: Optional[str] = None,
) -> StimulusEnvelope:
    """Validate and compile an Observation into a StimulusEnvelope."""

    try:
        observation = validate_observation(observation)
    except ObservationValidationError:
        raise
    kind = _TYPE_TO_KIND.get(
        str(observation.type or "").strip().lower(),
        StimulusKind.OBSERVATION_TRIGGER,
    )
    payload: Dict[str, Any] = dict(observation.payload or {})
    payload.setdefault("observation", observation.to_dict())
    payload.setdefault("source", observation.source)
    payload.setdefault("type", observation.type)
    view = str(observation.stimulus_view or "").strip()
    if view:
        payload["stimulus_view"] = view
    if observation.subject:
        payload.setdefault("subject", observation.subject)
    if observation.causation_id:
        payload.setdefault("causation_id", observation.causation_id)
    if observation.confidence is not None:
        payload.setdefault("confidence", observation.confidence)
    if observation.privacy_class:
        payload.setdefault("privacy_class", observation.privacy_class)
    if observation.payload_ref:
        payload.setdefault("payload_ref", observation.payload_ref)
    if observation.expires_at:
        payload.setdefault("expires_at", observation.expires_at)
    text = str(observation.text or observation.subject or "").strip()
    sid = (
        str(stimulus_id or "").strip()
        or str(payload.get("stimulus_id", "") or "").strip()
        or f"stim_{uuid.uuid4().hex}"
    )
    activation_id = str(payload.get("activation_id", "") or "").strip() or None
    delegate_id = str(payload.get("delegate_id", "") or "").strip() or None
    schedule_id = str(payload.get("schedule_id", "") or "").strip() or None
    schedule_run_id = str(
        payload.get("schedule_run_id", payload.get("run_id", "")) or ""
    ).strip() or None
    schedule_delivery_id = str(
        payload.get("schedule_delivery_id", "") or ""
    ).strip() or None
    priority_override = None
    if payload.get("priority_override") is not None:
        try:
            priority_override = int(payload.get("priority_override"))
        except (TypeError, ValueError):
            priority_override = None
    return StimulusEnvelope(
        stimulus_id=sid,
        thread_id=observation.thread_id,
        conversation_id=observation.conversation_id,
        stimulus=Stimulus(kind=kind, text=text, payload=payload),
        occurred_at=observation.occurred_at,
        transaction_id=observation.transaction_id,
        activation_id=activation_id,
        delegate_id=delegate_id,
        schedule_id=schedule_id,
        schedule_run_id=schedule_run_id,
        schedule_delivery_id=schedule_delivery_id,
        ingress_key=observation.idempotency_key,
        priority_override=priority_override,
        pool_state="new",
    )


def user_message_observation(
    *,
    thread_id: str,
    conversation_id: str,
    text: str,
    occurred_at: str,
    observed_at: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    subject: str = "user",
    stimulus_view: str = CHAT_STIMULUS_VIEW,
) -> Observation:
    return Observation(
        source="chat",
        type="user_message",
        thread_id=thread_id,
        conversation_id=conversation_id,
        occurred_at=occurred_at,
        observed_at=observed_at or occurred_at,
        subject=str(subject or "").strip() or "user",
        text=str(text or "").strip(),
        idempotency_key=idempotency_key,
        payload=dict(payload or {}),
        stimulus_view=str(stimulus_view or "").strip() or CHAT_STIMULUS_VIEW,
    )
