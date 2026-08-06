"""Priority stimulus inbox (per-thread buckets)."""
from __future__ import annotations

import heapq
import itertools
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from m_agent.runtime.domain.contracts import StimulusEnvelope


@dataclass(order=True)
class _QueuedItem:
    priority: int
    counter: int
    stimulus: StimulusEnvelope = field(compare=False)


_RUNTIME_FIELDS = (
    "stimulus_id",
    "accepted_seq",
    "accepted_at",
    "effective_priority",
    "pool_state",
    "disposition",
    "disposition_stage",
    "disposition_reason",
    "reason_code",
    "terminal",
    "retryable",
    "reenterable",
    "claimed_by",
    "consumer_epoch",
    "claim_epoch",
    "claimed_at",
    "finalized_at",
    "worker_latch",
)


def _copy_runtime_fields(
    target: StimulusEnvelope,
    source: StimulusEnvelope,
) -> None:
    for field_name in _RUNTIME_FIELDS:
        object.__setattr__(
            target,
            field_name,
            getattr(source, field_name),
        )


class StimulusInbox:
    """Thread-safe priority queue of stimuli, bucketed by thread_id."""

    def __init__(self, *, store: Optional[Any] = None) -> None:
        self._lock = threading.Lock()
        self._queues: Dict[str, List[_QueuedItem]] = {}
        self._counter = itertools.count()
        self.store = store

    def push(
        self,
        stimulus: StimulusEnvelope,
        *,
        priority: int,
    ) -> StimulusEnvelope:
        tid = str(stimulus.thread_id or "").strip()
        if not tid:
            raise ValueError("stimulus.thread_id is required")
        if self.store is not None:
            stored = self.store.admit_stimulus(
                stimulus,
                effective_priority=int(priority),
            )
            _copy_runtime_fields(stimulus, stored)
            return stored
        counter = next(self._counter)
        object.__setattr__(stimulus, "effective_priority", int(priority))
        object.__setattr__(stimulus, "accepted_seq", counter + 1)
        object.__setattr__(stimulus, "pool_state", "ready")
        object.__setattr__(stimulus, "disposition", None)
        object.__setattr__(stimulus, "terminal", False)
        item = _QueuedItem(
            priority=int(priority),
            counter=counter,
            stimulus=stimulus,
        )
        with self._lock:
            heapq.heappush(self._queues.setdefault(tid, []), item)
        return stimulus

    def pop_next(self, thread_id: str) -> Optional[StimulusEnvelope]:
        tid = str(thread_id or "").strip()
        if self.store is not None:
            return self.store.pop_next_stimulus(tid)
        with self._lock:
            queue = self._queues.get(tid)
            if not queue:
                return None
            item = heapq.heappop(queue)
            if not queue:
                self._queues.pop(tid, None)
            return item.stimulus

    def requeue_claimed(
        self,
        stimulus: StimulusEnvelope,
        *,
        priority: int,
    ) -> StimulusEnvelope:
        """Return the same claimed stimulus to ready without re-admission."""

        if self.store is not None:
            return self.store.requeue_claimed_stimulus(
                stimulus,
                effective_priority=int(priority),
            )
        return self.push(stimulus, priority=int(priority))

    def peek_next_priority(self, thread_id: str) -> Optional[int]:
        tid = str(thread_id or "").strip()
        if self.store is not None:
            return self.store.peek_next_stimulus_priority(tid)
        with self._lock:
            queue = self._queues.get(tid)
            if not queue:
                return None
            return int(queue[0].priority)

    def has_pending(self, thread_id: str) -> bool:
        tid = str(thread_id or "").strip()
        if self.store is not None:
            return self.pending_count(tid) > 0
        with self._lock:
            return bool(self._queues.get(tid))

    def pending_count(self, thread_id: Optional[str] = None) -> int:
        if self.store is not None:
            if thread_id is not None:
                return self.store.count_ready_stimuli(
                    thread_id=str(thread_id).strip()
                )
            return len(self.store.list_ready_stimuli())
        with self._lock:
            if thread_id is not None:
                return len(self._queues.get(str(thread_id).strip(), []))
            return sum(len(q) for q in self._queues.values())

    def clear_thread(self, thread_id: str) -> int:
        """Drop all queued stimuli for one thread and return the removed count."""
        tid = str(thread_id or "").strip()
        if self.store is not None:
            return self.store.clear_thread_stimuli(
                tid,
                reason="clear_thread",
            )
        with self._lock:
            queue = self._queues.pop(tid, None)
            return len(queue or [])

    def mark_disposition(
        self,
        stimulus_id: str,
        *,
        disposition: str,
        stage: str,
        reason: str = "",
    ) -> Optional[StimulusEnvelope]:
        if self.store is None:
            return None
        return self.store.set_stimulus_disposition(
            stimulus_id,
            disposition=disposition,
            stage=stage,
            reason=reason,
        )
