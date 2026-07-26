"""Structured contracts exposed by the thinking layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, cast


SILENT_MODES = frozenset({"silent", "wait", "defer"})


def normalize_thinking_mode(mode: Any) -> str:
    """Return a canonical thinking mode."""
    value = str(mode or "").strip().lower()
    if value in SILENT_MODES:
        return "silent"
    if value == "execute":
        return "execute"
    if value in {"answer_directly", "reply"}:
        return "answer_directly"
    return value or "answer_directly"


def is_silent_mode(mode: Any) -> bool:
    return normalize_thinking_mode(mode) == "silent"


def is_execute_mode(mode: Any) -> bool:
    return normalize_thinking_mode(mode) == "execute"


def is_reply_mode(mode: Any) -> bool:
    return normalize_thinking_mode(mode) == "answer_directly"


TaskCompletionStatus = Literal["processing", "awaiting_user", "completed"]
TASK_COMPLETION_PROCESSING: TaskCompletionStatus = "processing"
TASK_COMPLETION_AWAITING_USER: TaskCompletionStatus = "awaiting_user"
TASK_COMPLETION_COMPLETED: TaskCompletionStatus = "completed"
_TASK_COMPLETION_STATUSES = frozenset(
    {
        TASK_COMPLETION_PROCESSING,
        TASK_COMPLETION_AWAITING_USER,
        TASK_COMPLETION_COMPLETED,
    }
)


def normalize_task_completion_status(
    value: Any,
    *,
    default: TaskCompletionStatus = TASK_COMPLETION_PROCESSING,
) -> TaskCompletionStatus:
    normalized = str(value or "").strip().lower()
    if normalized in _TASK_COMPLETION_STATUSES:
        return cast(TaskCompletionStatus, normalized)
    return default


@dataclass
class TaskState:
    """Authoritative readable task state owned by one transaction."""

    goal: str = ""
    completion_status: TaskCompletionStatus = TASK_COMPLETION_PROCESSING
    completed: List[str] = field(default_factory=list)
    remaining: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            str(self.goal or "").strip()
            or normalize_task_completion_status(self.completion_status)
            != TASK_COMPLETION_PROCESSING
            or self.completed
            or self.remaining
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": str(self.goal or "").strip(),
            "completion_status": normalize_task_completion_status(
                self.completion_status
            ),
            "completed": list(self.completed),
            "remaining": list(self.remaining),
        }


@dataclass
class TaskStateUpdate:
    """Partial task-state update; ``None`` leaves a field unchanged."""

    goal: Optional[str] = None
    completion_status: Optional[TaskCompletionStatus] = None
    completed: Optional[List[str]] = None
    remaining: Optional[List[str]] = None

    def is_empty(self) -> bool:
        return (
            self.goal is None
            and self.completion_status is None
            and self.completed is None
            and self.remaining is None
        )

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.goal is not None:
            data["goal"] = str(self.goal or "").strip()
        if self.completion_status is not None:
            data["completion_status"] = normalize_task_completion_status(
                self.completion_status
            )
        if self.completed is not None:
            data["completed"] = list(self.completed)
        if self.remaining is not None:
            data["remaining"] = list(self.remaining)
        return data


# Compatibility names retained for callers that adopted the first schema.
TaskProgress = TaskState
TaskProgressUpdate = TaskStateUpdate


@dataclass
class ThinkingDecision:
    """Structured output of the decision pass."""

    mode: str = "answer_directly"
    tool_name: Optional[str] = None
    instruction: Optional[str] = None
    answer: Optional[str] = None
    episode_note: Optional[str] = None
    capability_hint: Optional[List[str]] = None
    request_complete: Optional[bool] = None
    reasoning: Optional[str] = None


def request_is_complete(decision: ThinkingDecision) -> bool:
    return bool(getattr(decision, "request_complete", None))


@dataclass
class TransactionResolution:
    """Structured choice made before transaction-specific reasoning."""

    action: str = "create"
    transaction_id: Optional[str] = None
    reasoning: Optional[str] = None
