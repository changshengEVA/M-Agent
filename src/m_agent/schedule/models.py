from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


SCHEDULE_STATUS_PENDING = "pending"
SCHEDULE_STATUS_LEASED = "leased"
SCHEDULE_STATUS_RUNNING = "running"
SCHEDULE_STATUS_DONE = "done"
SCHEDULE_STATUS_FAILED = "failed"
SCHEDULE_STATUS_CANCELED = "canceled"

ACTIVE_SCHEDULE_STATUSES = (
    SCHEDULE_STATUS_PENDING,
    SCHEDULE_STATUS_LEASED,
    SCHEDULE_STATUS_RUNNING,
)

TERMINAL_SCHEDULE_STATUSES = (
    SCHEDULE_STATUS_DONE,
    SCHEDULE_STATUS_FAILED,
    SCHEDULE_STATUS_CANCELED,
)

SCHEDULE_SCHEMA_VERSION = 2
OBJECTIVE_ENCODING_NATIVE = "native"
OBJECTIVE_ENCODING_LEGACY_TEXT = "legacy_text"


@dataclass(frozen=True)
class DeferredObjective:
    """Work that remains to be interpreted and carried out after a trigger.

    ``description`` is deliberately not an event report or execution result.
    ``encoding`` records whether the objective was produced under the typed
    contract or recovered from an older untyped text field.
    """

    description: str
    encoding: str = OBJECTIVE_ENCODING_NATIVE

    def to_dict(self) -> Dict[str, str]:
        return {
            "description": str(self.description or "").strip(),
            "encoding": str(self.encoding or OBJECTIVE_ENCODING_NATIVE).strip()
            or OBJECTIVE_ENCODING_NATIVE,
        }

    @classmethod
    def from_dict(
        cls,
        data: Any,
        *,
        default_encoding: str = OBJECTIVE_ENCODING_NATIVE,
    ) -> "DeferredObjective":
        if isinstance(data, dict):
            description = str(data.get("description", "") or "").strip()
            encoding = str(data.get("encoding", "") or "").strip()
        else:
            description = str(data or "").strip()
            encoding = ""
        return cls(
            description=description,
            encoding=encoding or default_encoding,
        )


@dataclass
class ScheduleItem:
    """Durable trigger plus the objective that becomes active when it fires."""

    schedule_id: str
    thread_id: str
    due_at_utc: str
    timezone_name: str
    deferred_objective: DeferredObjective
    status: str = SCHEDULE_STATUS_PENDING
    created_at: str = ""
    origin: Dict[str, str] = field(default_factory=dict)
    schema_version: int = SCHEDULE_SCHEMA_VERSION

    @property
    def text(self) -> str:
        """Deprecated API alias; never use this value as an event or evidence."""

        return str(self.deferred_objective.description or "").strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEDULE_SCHEMA_VERSION,
            "schedule_id": self.schedule_id,
            "thread_id": self.thread_id,
            "due_at_utc": self.due_at_utc,
            "timezone_name": self.timezone_name,
            "deferred_objective": self.deferred_objective.to_dict(),
            "status": self.status,
            "created_at": self.created_at,
            "origin": {
                str(key): str(value or "").strip()
                for key, value in dict(self.origin or {}).items()
                if str(key).strip() and str(value or "").strip()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduleItem":
        """Load the current schema and migrate legacy schedule records in memory."""
        if not isinstance(data, dict):
            raise ValueError("ScheduleItem payload must be a dict")

        objective_data = data.get("deferred_objective")
        objective = DeferredObjective.from_dict(objective_data)
        if not objective.description:
            action_payload = data.get("action_payload")
            legacy_prompt = (
                action_payload.get("prompt")
                if isinstance(action_payload, dict)
                else ""
            )
            legacy_text = (
                str(data.get("text", "") or "").strip()
                or str(legacy_prompt or "").strip()
                or str(data.get("title", "") or "").strip()
                or str(data.get("source_text", "") or "").strip()
            )
            objective = DeferredObjective(
                description=legacy_text,
                encoding=OBJECTIVE_ENCODING_LEGACY_TEXT,
            )
        created_at = str(data.get("created_at", "") or "").strip()
        if not created_at:
            created_at = str(data.get("updated_at", "") or "").strip()

        raw_origin = data.get("origin")
        origin = {
            str(key): str(value or "").strip()
            for key, value in (
                raw_origin.items() if isinstance(raw_origin, dict) else []
            )
            if str(key).strip() and str(value or "").strip()
        }

        return cls(
            schedule_id=str(data.get("schedule_id", "") or "").strip(),
            thread_id=str(data.get("thread_id", "") or "").strip(),
            due_at_utc=str(data.get("due_at_utc", "") or "").strip(),
            timezone_name=str(data.get("timezone_name", "") or "").strip() or "UTC",
            deferred_objective=objective,
            status=str(data.get("status", "") or "").strip() or SCHEDULE_STATUS_PENDING,
            created_at=created_at,
            origin=origin,
            schema_version=SCHEDULE_SCHEMA_VERSION,
        )
