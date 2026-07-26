from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class ScheduleItem:
    """The minimal durable representation of a future agent stimulus."""

    schedule_id: str
    thread_id: str
    due_at_utc: str
    timezone_name: str
    text: str
    status: str = SCHEDULE_STATUS_PENDING
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "thread_id": self.thread_id,
            "due_at_utc": self.due_at_utc,
            "timezone_name": self.timezone_name,
            "text": self.text,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduleItem":
        """Load the current schema and migrate legacy schedule records in memory."""
        if not isinstance(data, dict):
            raise ValueError("ScheduleItem payload must be a dict")

        action_payload = data.get("action_payload")
        legacy_prompt = action_payload.get("prompt") if isinstance(action_payload, dict) else ""
        text = (
            str(data.get("text", "") or "").strip()
            or str(legacy_prompt or "").strip()
            or str(data.get("title", "") or "").strip()
            or str(data.get("source_text", "") or "").strip()
        )
        created_at = str(data.get("created_at", "") or "").strip()
        if not created_at:
            created_at = str(data.get("updated_at", "") or "").strip()

        return cls(
            schedule_id=str(data.get("schedule_id", "") or "").strip(),
            thread_id=str(data.get("thread_id", "") or "").strip(),
            due_at_utc=str(data.get("due_at_utc", "") or "").strip(),
            timezone_name=str(data.get("timezone_name", "") or "").strip() or "UTC",
            text=text,
            status=str(data.get("status", "") or "").strip() or SCHEDULE_STATUS_PENDING,
            created_at=created_at,
        )
