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


@dataclass
class ScheduleItem:
    schedule_id: str
    owner_id: str
    thread_id: str
    title: str
    status: str
    due_at_utc: str
    timezone_name: str
    original_time_text: str
    action_type: str
    action_payload: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    source_text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "owner_id": self.owner_id,
            "thread_id": self.thread_id,
            "title": self.title,
            "status": self.status,
            "due_at_utc": self.due_at_utc,
            "timezone_name": self.timezone_name,
            "original_time_text": self.original_time_text,
            "action_type": self.action_type,
            "action_payload": dict(self.action_payload or {}),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_text": self.source_text,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduleItem":
        if not isinstance(data, dict):
            raise ValueError("ScheduleItem payload must be a dict")
        return cls(
            schedule_id=str(data.get("schedule_id", "") or "").strip(),
            owner_id=str(data.get("owner_id", "") or "").strip(),
            thread_id=str(data.get("thread_id", "") or "").strip(),
            title=str(data.get("title", "") or "").strip(),
            status=str(data.get("status", "") or "").strip() or SCHEDULE_STATUS_PENDING,
            due_at_utc=str(data.get("due_at_utc", "") or "").strip(),
            timezone_name=str(data.get("timezone_name", "") or "").strip() or "UTC",
            original_time_text=str(data.get("original_time_text", "") or "").strip(),
            action_type=str(data.get("action_type", "") or "").strip() or "chat_prompt",
            action_payload=dict(data.get("action_payload") or {}),
            created_at=str(data.get("created_at", "") or "").strip(),
            updated_at=str(data.get("updated_at", "") or "").strip(),
            source_text=str(data.get("source_text", "") or "").strip(),
            metadata=dict(data.get("metadata") or {}),
        )
