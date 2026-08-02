"""Perception-layer data contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class StimulusKind(str, Enum):
    USER_MESSAGE = "user_message"
    EXECUTION_FEEDBACK = "execution_feedback"
    SCHEDULED_PLAN = "scheduled_plan"
    OBSERVATION_TRIGGER = "observation_trigger"


@dataclass(frozen=True)
class EventFrame:
    """Typed fact explaining why the current cognitive turn exists."""

    event_type: str
    source: str
    subject_ref: Optional[str] = None
    occurred_at: str = ""
    facts: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": "activation_event",
            "type": str(self.event_type or "").strip(),
            "source": str(self.source or "").strip(),
            "subject_ref": str(self.subject_ref or "").strip() or None,
            "occurred_at": str(self.occurred_at or "").strip(),
            "facts": dict(self.facts or {}),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "EventFrame":
        raw = dict(data) if isinstance(data, dict) else {}
        return cls(
            event_type=str(raw.get("type", raw.get("event_type", "")) or "").strip(),
            source=str(raw.get("source", "runtime") or "runtime").strip(),
            subject_ref=str(raw.get("subject_ref", "") or "").strip() or None,
            occurred_at=str(raw.get("occurred_at", "") or "").strip(),
            facts=dict(raw.get("facts") or {}) if isinstance(raw.get("facts"), dict) else {},
        )


@dataclass(frozen=True)
class ObjectiveFrame:
    """Typed work target activated by an event; it is not an outcome."""

    description: str
    encoding: str = "native"
    objective_id: Optional[str] = None
    origin: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": "deferred_objective",
            "description": str(self.description or "").strip(),
            "encoding": str(self.encoding or "native").strip() or "native",
            "objective_id": str(self.objective_id or "").strip() or None,
            "origin": dict(self.origin or {}),
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["ObjectiveFrame"]:
        raw = dict(data) if isinstance(data, dict) else {}
        description = str(raw.get("description", "") or "").strip()
        if not description:
            return None
        return cls(
            description=description,
            encoding=str(raw.get("encoding", "native") or "native").strip()
            or "native",
            objective_id=str(raw.get("objective_id", "") or "").strip() or None,
            origin=dict(raw.get("origin") or {}) if isinstance(raw.get("origin"), dict) else {},
        )


@dataclass(frozen=True)
class EvidenceFrame:
    """Observed result that may support task-state interpretation."""

    evidence_type: str
    source: str
    evidence_id: Optional[str] = None
    summary: str = ""
    occurred_at: str = ""
    facts: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": "observed_evidence",
            "type": str(self.evidence_type or "").strip(),
            "source": str(self.source or "").strip(),
            "evidence_id": str(self.evidence_id or "").strip() or None,
            "summary": str(self.summary or "").strip(),
            "occurred_at": str(self.occurred_at or "").strip(),
            "facts": dict(self.facts or {}),
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["EvidenceFrame"]:
        raw = dict(data) if isinstance(data, dict) else {}
        evidence_type = str(raw.get("type", raw.get("evidence_type", "")) or "").strip()
        if not evidence_type:
            return None
        return cls(
            evidence_type=evidence_type,
            source=str(raw.get("source", "runtime") or "runtime").strip(),
            evidence_id=str(raw.get("evidence_id", "") or "").strip() or None,
            summary=str(raw.get("summary", "") or "").strip(),
            occurred_at=str(raw.get("occurred_at", "") or "").strip(),
            facts=dict(raw.get("facts") or {}) if isinstance(raw.get("facts"), dict) else {},
        )


@dataclass(frozen=True)
class ActivationFrame:
    """Causal semantic frame consumed by task-state and decision passes."""

    event: EventFrame
    objective: Optional[ObjectiveFrame] = None
    evidence: List[EvidenceFrame] = field(default_factory=list)
    activation_id: Optional[str] = None
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": int(self.schema_version or 1),
            "activation_id": str(self.activation_id or "").strip() or None,
            "event": self.event.to_dict(),
            "objective": self.objective.to_dict() if self.objective is not None else None,
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["ActivationFrame"]:
        raw = dict(data) if isinstance(data, dict) else {}
        raw_event = raw.get("event")
        if not isinstance(raw_event, dict):
            return None
        event = EventFrame.from_dict(raw_event)
        if not event.event_type:
            return None
        evidence: List[EvidenceFrame] = []
        for item in raw.get("evidence", []):
            parsed = EvidenceFrame.from_dict(item)
            if parsed is not None:
                evidence.append(parsed)
        objective = ObjectiveFrame.from_dict(raw.get("objective"))
        raw_origin = raw.get("origin")
        if (
            objective is not None
            and not objective.origin
            and isinstance(raw_origin, dict)
        ):
            objective = ObjectiveFrame(
                description=objective.description,
                encoding=objective.encoding,
                objective_id=objective.objective_id,
                origin=dict(raw_origin),
            )
        return cls(
            event=event,
            objective=objective,
            evidence=evidence,
            activation_id=str(raw.get("activation_id", "") or "").strip() or None,
            schema_version=int(raw.get("schema_version", 1) or 1),
        )


@dataclass(frozen=True)
class Stimulus:
    """Readable event content presented to the thinking layer."""

    kind: StimulusKind
    text: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PerceptionInput:
    """Structured input handed to the thinking layer by the perception layer.

    Built from the raw HTTP request (or heartbeat-triggered schedule item) so
    the thinking layer never has to parse the original message format.
    """

    thread_id: str
    conversation_id: str
    transaction_id: Optional[str]
    stimulus: Stimulus
    dialogue_history: List[Dict[str, str]] = field(default_factory=list)
    scene_context: str = ""
    activation: Optional[ActivationFrame] = None
