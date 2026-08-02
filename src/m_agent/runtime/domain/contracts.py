"""Engine-neutral data contracts for the product runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from m_agent.layers.perception.contracts import Stimulus, StimulusKind

if TYPE_CHECKING:
    from m_agent.layers.thinking.contracts import TaskState


def _new_task_state() -> "TaskState":
    from m_agent.layers.thinking.contracts import TaskState

    return TaskState()


class TransactionState(str, Enum):
    """Authoritative four-state transaction domain model."""

    CONTINUE = "continue"
    PAUSE = "pause"
    COMPLETE = "complete"
    ARCHIVE = "archive"


class PauseReason(str, Enum):
    """Why a live transaction is intentionally not advancing."""

    AWAITING_USER = "awaiting_user"
    SCHEDULED_WAIT = "scheduled_wait"
    MANUAL_HOLD = "manual_hold"
    RUNTIME_ERROR = "runtime_error"


class TransactionLifecycle(str, Enum):
    """Lifecycle is orthogonal to the four-state task model."""

    ACTIVE = "active"
    DELETED = "deleted"


class ActivationStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    INVALIDATED = "invalidated"


class DelegateStatus(str, Enum):
    PENDING = "pending"
    CONSUMED = "consumed"
    INVALIDATED = "invalidated"


class TransactionKind(str, Enum):
    USER_TASK = "user_task"
    SCHEDULE = "schedule"
    CONTINUATION = "continuation"
    SYSTEM = "system"


class SceneEntryType(str, Enum):
    UTTERANCE = "utterance"
    THOUGHT = "thought"
    ACTION = "action"
    OUTCOME = "outcome"
    REPLY = "reply"


class SceneActor(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    THINK = "think"
    WORK = "work"


@dataclass
class TransactionCorrelation:
    schedule_id: Optional[str] = None
    schedule_owner_id: Optional[str] = None
    schedule_run_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.schedule_id:
            out["schedule_id"] = self.schedule_id
        if self.schedule_owner_id:
            out["schedule_owner_id"] = self.schedule_owner_id
        if self.schedule_run_id:
            out["schedule_run_id"] = self.schedule_run_id
        return out


@dataclass
class ActivationRecord:
    activation_id: str
    transaction_id: str
    status: ActivationStatus = ActivationStatus.ACTIVE
    source: str = "runtime"
    source_stimulus_id: Optional[str] = None
    created_at: str = ""
    ended_at: Optional[str] = None
    invalidated_reason: Optional[str] = None
    from_revision: int = 0
    to_revision: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "activation_id": self.activation_id,
            "transaction_id": self.transaction_id,
            "status": self.status.value,
            "source": self.source,
            "source_stimulus_id": self.source_stimulus_id,
            "created_at": self.created_at,
            "ended_at": self.ended_at,
            "invalidated_reason": self.invalidated_reason,
            "from_revision": int(self.from_revision),
            "to_revision": self.to_revision,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActivationRecord":
        return cls(
            activation_id=str(data.get("activation_id", "") or ""),
            transaction_id=str(data.get("transaction_id", "") or ""),
            status=ActivationStatus(
                str(data.get("status", ActivationStatus.ACTIVE.value))
            ),
            source=str(data.get("source", "runtime") or "runtime"),
            source_stimulus_id=data.get("source_stimulus_id"),
            created_at=str(data.get("created_at", "") or ""),
            ended_at=data.get("ended_at"),
            invalidated_reason=data.get("invalidated_reason"),
            from_revision=int(data.get("from_revision", 0) or 0),
            to_revision=(
                int(data["to_revision"])
                if data.get("to_revision") is not None
                else None
            ),
        )


@dataclass
class DelegateRecord:
    delegate_id: str
    transaction_id: str
    activation_id: str
    status: DelegateStatus = DelegateStatus.PENDING
    created_at: str = ""
    consumed_at: Optional[str] = None
    invalidated_at: Optional[str] = None
    invalidated_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delegate_id": self.delegate_id,
            "transaction_id": self.transaction_id,
            "activation_id": self.activation_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "consumed_at": self.consumed_at,
            "invalidated_at": self.invalidated_at,
            "invalidated_reason": self.invalidated_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DelegateRecord":
        return cls(
            delegate_id=str(data.get("delegate_id", "") or ""),
            transaction_id=str(data.get("transaction_id", "") or ""),
            activation_id=str(data.get("activation_id", "") or ""),
            status=DelegateStatus(
                str(data.get("status", DelegateStatus.PENDING.value))
            ),
            created_at=str(data.get("created_at", "") or ""),
            consumed_at=data.get("consumed_at"),
            invalidated_at=data.get("invalidated_at"),
            invalidated_reason=data.get("invalidated_reason"),
        )


@dataclass
class TransactionRecord:
    transaction_id: str
    thread_id: str
    conversation_id: str = ""
    state: TransactionState = TransactionState.CONTINUE
    pause_reason: Optional[PauseReason] = None
    lifecycle_status: TransactionLifecycle = TransactionLifecycle.ACTIVE
    revision: int = 0
    current_activation_id: Optional[str] = None
    priority: int = 50
    kind: TransactionKind = TransactionKind.USER_TASK
    correlation: TransactionCorrelation = field(default_factory=TransactionCorrelation)
    # Schedules created by this activation are registered atomically when the
    # user-visible turn settles. Keeping them on the transaction bridges the
    # tool side effect and the later scheduled_wait transition across crashes.
    pending_schedule_intents: List[Dict[str, str]] = field(default_factory=list)
    wm_entries: List[Dict[str, Any]] = field(default_factory=list)
    task_state: "TaskState" = field(default_factory=_new_task_state)
    active_delegate_id: Optional[str] = None
    think_rounds: int = 0
    delegate_count: int = 0
    # True after reply_to_user(finalize=true) in the current activation.
    reply_finalized_in_activation: bool = False
    created_at: str = ""
    updated_at: str = ""
    terminal_at: Optional[str] = None
    archived_at: Optional[str] = None
    deleted_at: Optional[str] = None
    last_error: Optional[str] = None
    runtime_engine: str = "langgraph_v1"
    schema_version: int = 4

    def __post_init__(self) -> None:
        if not str(self.conversation_id or "").strip():
            self.conversation_id = f"{str(self.thread_id or '').strip()}::0"

    def can_accept_wm_write(self) -> bool:
        return (
            self.lifecycle_status == TransactionLifecycle.ACTIVE
            and self.state == TransactionState.CONTINUE
            and self.deleted_at is None
        )

    @property
    def deleted(self) -> bool:
        return self.lifecycle_status == TransactionLifecycle.DELETED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "thread_id": self.thread_id,
            "conversation_id": self.conversation_id,
            "state": self.state.value,
            "pause_reason": (
                self.pause_reason.value if self.pause_reason is not None else None
            ),
            "lifecycle_status": self.lifecycle_status.value,
            "revision": int(self.revision),
            "current_activation_id": self.current_activation_id,
            "priority": int(self.priority),
            "kind": self.kind.value,
            "correlation": self.correlation.to_dict(),
            "pending_schedule_intents": [
                dict(item) for item in self.pending_schedule_intents
            ],
            "wm_entries": list(self.wm_entries),
            "task_state": self.task_state.to_dict(),
            "active_delegate_id": self.active_delegate_id,
            "think_rounds": int(self.think_rounds),
            "delegate_count": int(self.delegate_count),
            "reply_finalized_in_activation": bool(
                self.reply_finalized_in_activation
            ),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "terminal_at": self.terminal_at,
            "archived_at": self.archived_at,
            "deleted_at": self.deleted_at,
            "last_error": self.last_error,
            "runtime_engine": self.runtime_engine,
            "schema_version": int(self.schema_version),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TransactionRecord":
        from m_agent.layers.thinking.contracts import (
            TaskState,
            normalize_task_completion_status,
        )

        correlation_data = data.get("correlation")
        correlation = (
            dict(correlation_data)
            if isinstance(correlation_data, dict)
            else {}
        )
        task_data = data.get("task_state")
        task = dict(task_data) if isinstance(task_data, dict) else {}
        task_state = TaskState(
            goal=str(task.get("goal", "") or ""),
            completion_status=normalize_task_completion_status(
                task.get("completion_status")
            ),
            completed=[
                str(item) for item in task.get("completed", [])
            ],
            remaining=[
                str(item) for item in task.get("remaining", [])
            ],
        )
        return cls(
            transaction_id=str(data.get("transaction_id", "") or ""),
            thread_id=str(data.get("thread_id", "") or ""),
            conversation_id=str(data.get("conversation_id", "") or ""),
            state=TransactionState(
                str(data.get("state", TransactionState.CONTINUE.value))
            ),
            pause_reason=(
                PauseReason(str(data["pause_reason"]))
                if data.get("pause_reason") is not None
                else None
            ),
            lifecycle_status=TransactionLifecycle(
                str(
                    data.get(
                        "lifecycle_status",
                        TransactionLifecycle.ACTIVE.value,
                    )
                )
            ),
            revision=int(data.get("revision", 0) or 0),
            current_activation_id=data.get("current_activation_id"),
            priority=int(data.get("priority", 50) or 50),
            kind=TransactionKind(
                str(data.get("kind", TransactionKind.USER_TASK.value))
            ),
            correlation=TransactionCorrelation(
                schedule_id=correlation.get("schedule_id"),
                schedule_owner_id=correlation.get("schedule_owner_id"),
                schedule_run_id=correlation.get("schedule_run_id"),
            ),
            pending_schedule_intents=[
                {
                    str(key): str(value or "").strip()
                    for key, value in item.items()
                    if str(key).strip() and str(value or "").strip()
                }
                for item in list(
                    data.get("pending_schedule_intents", []) or []
                )
                if isinstance(item, dict)
            ],
            wm_entries=list(data.get("wm_entries", [])),
            task_state=task_state,
            active_delegate_id=data.get("active_delegate_id"),
            think_rounds=int(data.get("think_rounds", 0) or 0),
            delegate_count=int(data.get("delegate_count", 0) or 0),
            reply_finalized_in_activation=bool(
                data.get("reply_finalized_in_activation", False)
            ),
            created_at=str(data.get("created_at", "") or ""),
            updated_at=str(data.get("updated_at", "") or ""),
            terminal_at=data.get("terminal_at"),
            archived_at=data.get("archived_at"),
            deleted_at=data.get("deleted_at"),
            last_error=data.get("last_error"),
            runtime_engine=str(
                data.get("runtime_engine", "langgraph_v1")
                or "langgraph_v1"
            ),
            schema_version=int(data.get("schema_version", 2) or 2),
        )


@dataclass(frozen=True)
class StimulusEnvelope:
    """Queue/runtime metadata wrapped around readable stimulus content."""

    stimulus_id: str
    thread_id: str
    conversation_id: str
    stimulus: Stimulus
    occurred_at: str
    transaction_id: Optional[str] = None
    activation_id: Optional[str] = None
    delegate_id: Optional[str] = None
    schedule_id: Optional[str] = None
    schedule_run_id: Optional[str] = None
    schedule_delivery_id: Optional[str] = None
    ingress_key: Optional[str] = None
    priority_override: Optional[int] = None
    accepted_seq: Optional[int] = None
    accepted_at: Optional[str] = None
    effective_priority: Optional[int] = None
    disposition: str = "new"
    disposition_stage: Optional[str] = None
    disposition_reason: Optional[str] = None
    claimed_by: Optional[str] = None
    consumer_epoch: Optional[int] = None
    claim_epoch: Optional[int] = None
    claimed_at: Optional[str] = None
    finalized_at: Optional[str] = None
    worker_latch: Optional[str] = None

    @property
    def kind(self) -> StimulusKind:
        return self.stimulus.kind

    @property
    def text(self) -> str:
        return self.stimulus.text

    @property
    def payload(self) -> Dict[str, Any]:
        return self.stimulus.payload


@dataclass
class SceneEntry:
    seq: int
    occurred_at: str
    entry_type: SceneEntryType
    actor: SceneActor
    text: str
    append_id: Optional[str] = None
    transaction_id: Optional[str] = None
    delegate_id: Optional[str] = None
    tool_name: Optional[str] = None
    payload_ref: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "occurred_at": self.occurred_at,
            "entry_type": self.entry_type.value,
            "actor": self.actor.value,
            "text": self.text,
            "append_id": self.append_id,
            "transaction_id": self.transaction_id,
            "delegate_id": self.delegate_id,
            "tool_name": self.tool_name,
            "payload_ref": self.payload_ref,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneEntry":
        return cls(
            seq=int(data.get("seq", 0)),
            occurred_at=str(data.get("occurred_at", "") or ""),
            entry_type=SceneEntryType(str(data.get("entry_type", SceneEntryType.OUTCOME.value))),
            actor=SceneActor(str(data.get("actor", SceneActor.WORK.value))),
            text=str(data.get("text", "") or ""),
            append_id=data.get("append_id"),
            transaction_id=data.get("transaction_id"),
            delegate_id=data.get("delegate_id"),
            tool_name=data.get("tool_name"),
            payload_ref=data.get("payload_ref"),
        )
