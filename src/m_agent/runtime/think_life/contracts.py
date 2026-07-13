"""Data contracts for the Think-life runtime."""
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


class TransactionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_EXECUTION = "waiting_execution"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def is_terminal(self) -> bool:
        return self in {
            TransactionStatus.COMPLETED,
            TransactionStatus.FAILED,
            TransactionStatus.CANCELLED,
        }


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
    delegate_id: Optional[str] = None
    parent_transaction_id: Optional[str] = None
    supersedes: Optional[str] = None
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
        if self.delegate_id:
            out["delegate_id"] = self.delegate_id
        if self.parent_transaction_id:
            out["parent_transaction_id"] = self.parent_transaction_id
        if self.supersedes:
            out["supersedes"] = self.supersedes
        return out


@dataclass
class TransactionRecord:
    transaction_id: str
    thread_id: str
    conversation_id: str = ""
    status: TransactionStatus = TransactionStatus.PENDING
    priority: int = 50
    kind: TransactionKind = TransactionKind.USER_TASK
    correlation: TransactionCorrelation = field(default_factory=TransactionCorrelation)
    wm_entries: List[Dict[str, Any]] = field(default_factory=list)
    task_state: "TaskState" = field(default_factory=_new_task_state)
    episode_buffer: List[Dict[str, Any]] = field(default_factory=list)
    turn_count: int = 0
    active_delegate_id: Optional[str] = None
    linked_transaction_ids: List[str] = field(default_factory=list)
    think_rounds: int = 0
    delegate_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    terminal_at: Optional[str] = None
    last_error: Optional[str] = None

    def __post_init__(self) -> None:
        if not str(self.conversation_id or "").strip():
            self.conversation_id = f"{str(self.thread_id or '').strip()}::0"

    def can_accept_wm_write(self) -> bool:
        return self.status in {
            TransactionStatus.RUNNING,
            TransactionStatus.WAITING_EXECUTION,
        }

    @property
    def task_progress(self) -> "TaskState":
        """Compatibility alias while prompt code migrates to ``task_state``."""
        return self.task_state

    @task_progress.setter
    def task_progress(self, value: "TaskState") -> None:
        self.task_state = value


@dataclass(frozen=True)
class StimulusEnvelope:
    """Queue/runtime metadata wrapped around readable stimulus content."""

    stimulus_id: str
    thread_id: str
    conversation_id: str
    stimulus: Stimulus
    occurred_at: str
    transaction_id: Optional[str] = None
    delegate_id: Optional[str] = None
    schedule_id: Optional[str] = None
    priority_override: Optional[int] = None

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
            transaction_id=data.get("transaction_id"),
            delegate_id=data.get("delegate_id"),
            tool_name=data.get("tool_name"),
            payload_ref=data.get("payload_ref"),
        )
