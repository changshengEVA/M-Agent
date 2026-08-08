"""Observation / Stimulus public contracts for runtime.ingest()."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional


class PoolState(str, Enum):
    """Process-like stimulus pool state used for scheduling and recovery."""

    NEW = "new"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    TERMINATED = "terminated"


class Disposition(str, Enum):
    """Terminal classification for audit; not a second scheduling state machine."""

    REJECTED = "rejected"
    MERGED = "merged"
    DISCARDED = "discarded"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class ReasonCode(str, Enum):
    """Stable reason codes attached to dispositions and Trace events."""

    INVALID_OBSERVATION = "invalid_observation"
    INVALID_FEEDBACK_SOURCE = "invalid_feedback_source"
    INVALID_SCHEDULE_SOURCE = "invalid_schedule_source"
    DUPLICATE_INGRESS = "duplicate_ingress"
    EXPIRED = "expired"
    IRRELEVANT = "irrelevant"
    PRECONSUME = "preconsume"
    TRANSACTION_DELETED = "transaction_deleted"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"
    CONTROL_CLEAR = "control_clear"
    MERGED_EXISTING = "merged_existing"


CLAIMABLE_POOL_STATES = frozenset({PoolState.READY.value})
ACTIVE_POOL_STATES = frozenset(
    {
        PoolState.NEW.value,
        PoolState.READY.value,
        PoolState.RUNNING.value,
        PoolState.WAITING.value,
    }
)
TERMINAL_DISPOSITIONS = frozenset(item.value for item in Disposition)


@dataclass(frozen=True)
class Observation:
    """Normalized external observation accepted by ``runtime.ingest()``.

    Adapters may keep a private Signal for replay; Runtime only consumes
    Observation. Do not encode model inferences as facts at the ingress edge.
    """

    source: str
    type: str
    thread_id: str
    conversation_id: str
    occurred_at: str
    observed_at: str
    subject: str = ""
    text: str = ""
    idempotency_key: Optional[str] = None
    causation_id: Optional[str] = None
    confidence: Optional[float] = None
    privacy_class: Optional[str] = None
    payload_ref: Optional[str] = None
    transaction_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    expires_at: Optional[str] = None
    stimulus_view: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Observation":
        raw = dict(data or {})
        payload = raw.get("payload")
        return cls(
            source=str(raw.get("source", "") or "").strip(),
            type=str(raw.get("type", "") or "").strip(),
            thread_id=str(raw.get("thread_id", "") or "").strip(),
            conversation_id=str(raw.get("conversation_id", "") or "").strip(),
            occurred_at=str(raw.get("occurred_at", "") or "").strip(),
            observed_at=str(raw.get("observed_at", "") or "").strip(),
            subject=str(raw.get("subject", "") or "").strip(),
            text=str(raw.get("text", "") or "").strip(),
            idempotency_key=_optional_text(raw.get("idempotency_key")),
            causation_id=_optional_text(raw.get("causation_id")),
            confidence=_optional_float(raw.get("confidence")),
            privacy_class=_optional_text(raw.get("privacy_class")),
            payload_ref=_optional_text(raw.get("payload_ref")),
            transaction_id=_optional_text(raw.get("transaction_id")),
            payload=dict(payload) if isinstance(payload, Mapping) else {},
            expires_at=_optional_text(raw.get("expires_at")),
            stimulus_view=str(raw.get("stimulus_view", "") or "").strip(),
        )


@dataclass(frozen=True)
class PublicStimulus:
    """Public view of an admitted stimulus (pool state + optional disposition)."""

    stimulus_id: str
    thread_id: str
    conversation_id: str
    pool_state: str
    source: str = ""
    type: str = ""
    subject: str = ""
    text: str = ""
    occurred_at: str = ""
    observed_at: str = ""
    idempotency_key: Optional[str] = None
    causation_id: Optional[str] = None
    confidence: Optional[float] = None
    privacy_class: Optional[str] = None
    payload_ref: Optional[str] = None
    transaction_id: Optional[str] = None
    disposition: Optional[str] = None
    reason_code: Optional[str] = None
    reason: Optional[str] = None
    terminal: bool = False
    retryable: bool = False
    reenterable: bool = False
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IngestResult:
    """Result of ``runtime.ingest(observation)``."""

    stimulus_id: str
    pool_state: str
    created: bool
    merged: bool = False
    disposition: Optional[str] = None
    reason_code: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ObservationValidationError(ValueError):
    """Raised when an Observation fails public contract validation."""


def validate_observation(observation: Observation) -> Observation:
    """Validate required Observation fields; return the same instance if ok."""

    if not isinstance(observation, Observation):
        raise ObservationValidationError("observation must be an Observation")
    missing = [
        name
        for name, value in (
            ("source", observation.source),
            ("type", observation.type),
            ("thread_id", observation.thread_id),
            ("conversation_id", observation.conversation_id),
            ("occurred_at", observation.occurred_at),
            ("observed_at", observation.observed_at),
        )
        if not str(value or "").strip()
    ]
    if missing:
        raise ObservationValidationError(
            "observation missing required fields: " + ", ".join(missing)
        )
    if observation.confidence is not None and not (
        0.0 <= float(observation.confidence) <= 1.0
    ):
        raise ObservationValidationError(
            "observation.confidence must be between 0.0 and 1.0"
        )
    return observation


def _optional_text(value: object) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _optional_float(value: object) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)
