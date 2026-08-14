"""Structured contracts exposed by the thinking layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


SILENT_MODES = frozenset({"silent", "wait", "defer"})


def normalize_thinking_mode(mode: Any) -> str:
    """Return a canonical action-decision mode (not transaction lifecycle)."""
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
    """Normalize macro task-progress status.

    ``awaiting_user`` is the macro decision that the task line needs user
    collaboration. Runtime maps it to ``Transaction.state=pause``; action
    planning uses ``mode`` separately (and is forced silent once paused).
    """
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


class TaskStateOutput(BaseModel):
    """Complete task-state snapshot returned by one thinking turn."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(max_length=1000)
    completion_status: TaskCompletionStatus
    completed: List[str] = Field(max_length=32)
    remaining: List[str] = Field(max_length=32)


class DecisionOutput(BaseModel):
    """Strict action portion of a joint thinking-turn response."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["execute", "answer_directly", "silent"]
    # In Pydantic v2, ``Optional[str]`` without a default is still a required
    # key. Irrelevant action fields are genuinely optional so providers may
    # omit them instead of having to emit explicit nulls on every turn.
    tool_name: Optional[str] = None
    instruction: Optional[str] = None
    answer: Optional[str] = None
    episode_note: Optional[str] = None

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "DecisionOutput":
        tool_name = str(self.tool_name or "").strip()
        instruction = str(self.instruction or "").strip()
        answer = str(self.answer or "").strip()

        if self.mode == "execute":
            if not tool_name or not instruction:
                raise PydanticCustomError(
                    "decision_execute_fields_missing",
                    "execute requires non-empty tool_name and instruction",
                )
            if answer:
                raise PydanticCustomError(
                    "decision_execute_answer_present",
                    "execute requires answer to be empty",
                )
        elif self.mode == "answer_directly":
            if not answer:
                raise PydanticCustomError(
                    "decision_answer_missing",
                    "answer_directly requires a non-empty answer",
                )
            if tool_name or instruction:
                raise PydanticCustomError(
                    "decision_answer_fields_present",
                    "answer_directly requires tool_name and instruction to be empty",
                )
        elif tool_name or instruction or answer:
            raise PydanticCustomError(
                "decision_silent_fields_present",
                "silent requires tool_name, instruction, and answer to be empty",
            )
        return self


class ThinkingTurnOutput(BaseModel):
    """Joint task-state and action output produced by one LLM invocation."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)
    task_state: TaskStateOutput
    decision: DecisionOutput

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        reason = str(value or "").strip()
        if not reason:
            raise ValueError("reason must not be blank")
        return reason


@dataclass
class ThinkingDecision:
    """Structured output of the decision pass."""

    mode: str = "answer_directly"
    tool_name: Optional[str] = None
    instruction: Optional[str] = None
    answer: Optional[str] = None
    episode_note: Optional[str] = None
    request_complete: Optional[bool] = None
    reasoning: Optional[str] = None
    planning_reason: Optional[str] = None


def request_is_complete(decision: ThinkingDecision) -> bool:
    return bool(getattr(decision, "request_complete", None))


@dataclass
class TransactionResolution:
    """Structured choice made before transaction-specific reasoning."""

    action: str = "create"
    transaction_id: Optional[str] = None
    reasoning: Optional[str] = None
