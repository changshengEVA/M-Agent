"""Mutable state containers used by the thinking layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, List, Optional

from .contracts import TaskState


@dataclass
class ConversationState:
    """Conversation-level compatibility state for direct thinking calls.

    Transaction task state and working memory do not live here. Buffered
    episode notes are cleared by :meth:`reset`; long-term memory belongs to
    the episodic backend.
    """

    conversation_id: str
    thread_id: str
    episode_buffer: List[Dict[str, Any]] = field(default_factory=list)
    turn_count: int = 0

    def reset(self) -> None:
        """Clear conversation-level buffered state."""
        self.episode_buffer.clear()
        self.turn_count = 0


@dataclass
class StandaloneTransactionState(ConversationState):
    """Compatibility transaction state for direct, non-runtime agent calls."""

    transaction_id: str = ""
    wm_entries: List[Dict[str, Any]] = field(default_factory=list)
    task_state: TaskState = field(default_factory=TaskState)

    @property
    def task_progress(self) -> TaskState:
        return self.task_state

    @task_progress.setter
    def task_progress(self, value: TaskState) -> None:
        self.task_state = value

    def reset(self) -> None:
        super().reset()
        self.wm_entries.clear()
        self.task_state = TaskState()


class ConversationStateRegistry:
    """Registry for compatibility state used outside the Think-life runtime.

    Think-life passes its authoritative ``TransactionRecord`` directly to the
    thinking layer and does not use this registry for transaction working
    memory or task state.
    """

    def __init__(self) -> None:
        self._states: Dict[str, StandaloneTransactionState] = {}
        self._lock = RLock()

    def get(self, conversation_id: str) -> Optional[StandaloneTransactionState]:
        with self._lock:
            return self._states.get(conversation_id)

    def get_or_create(
        self, conversation_id: str, *, thread_id: str
    ) -> StandaloneTransactionState:
        with self._lock:
            state = self._states.get(conversation_id)
            if state is None:
                state = StandaloneTransactionState(
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                    transaction_id=f"standalone::{conversation_id}",
                )
                self._states[conversation_id] = state
            return state

    def drop(self, conversation_id: str) -> Optional[StandaloneTransactionState]:
        with self._lock:
            return self._states.pop(conversation_id, None)

    def snapshot(self, conversation_id: str) -> Optional[StandaloneTransactionState]:
        """Return the live state; callers must treat it as read-only."""
        return self.get(conversation_id)
