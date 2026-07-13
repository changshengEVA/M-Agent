"""Priority stimulus inbox (per-thread buckets)."""
from __future__ import annotations

import heapq
import itertools
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from m_agent.runtime.think_life.contracts import StimulusEnvelope


@dataclass(order=True)
class _QueuedItem:
    priority: int
    occurred_at: str
    counter: int
    stimulus: StimulusEnvelope = field(compare=False)


class StimulusInbox:
    """Thread-safe priority queue of stimuli, bucketed by thread_id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: Dict[str, List[_QueuedItem]] = {}
        self._counter = itertools.count()

    def push(self, stimulus: StimulusEnvelope, *, priority: int) -> None:
        tid = str(stimulus.thread_id or "").strip()
        if not tid:
            raise ValueError("stimulus.thread_id is required")
        item = _QueuedItem(
            priority=int(priority),
            occurred_at=str(stimulus.occurred_at or ""),
            counter=next(self._counter),
            stimulus=stimulus,
        )
        with self._lock:
            heapq.heappush(self._queues.setdefault(tid, []), item)

    def pop_next(self, thread_id: str) -> Optional[StimulusEnvelope]:
        tid = str(thread_id or "").strip()
        with self._lock:
            queue = self._queues.get(tid)
            if not queue:
                return None
            item = heapq.heappop(queue)
            if not queue:
                self._queues.pop(tid, None)
            return item.stimulus

    def peek_next_priority(self, thread_id: str) -> Optional[int]:
        tid = str(thread_id or "").strip()
        with self._lock:
            queue = self._queues.get(tid)
            if not queue:
                return None
            return int(queue[0].priority)

    def has_pending(self, thread_id: str) -> bool:
        tid = str(thread_id or "").strip()
        with self._lock:
            return bool(self._queues.get(tid))

    def pending_count(self, thread_id: Optional[str] = None) -> int:
        with self._lock:
            if thread_id is not None:
                return len(self._queues.get(str(thread_id).strip(), []))
            return sum(len(q) for q in self._queues.values())

    def clear_thread(self, thread_id: str) -> int:
        """Drop all queued stimuli for one thread and return the removed count."""
        tid = str(thread_id or "").strip()
        with self._lock:
            queue = self._queues.pop(tid, None)
            return len(queue or [])
