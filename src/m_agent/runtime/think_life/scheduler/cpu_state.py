"""Per-thread CPU in-flight state for queue-derived runtime projection."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, Optional

from m_agent.api.chat_api_shared import _now_iso


@dataclass
class InFlightRecord:
    stimulus_id: str
    transaction_id: str
    started_at: str
    cancel_event: threading.Event
    priority: int = 50


class ThreadCpuStateRegistry:
    """Process-wide in-flight stimulus tracking (Think-life CPU slot)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._in_flight: Dict[str, InFlightRecord] = {}

    def set_in_flight(
        self,
        thread_id: str,
        *,
        stimulus_id: str,
        transaction_id: str,
        priority: int = 50,
    ) -> threading.Event:
        tid = str(thread_id or "").strip()
        cancel_event = threading.Event()
        record = InFlightRecord(
            stimulus_id=str(stimulus_id or "").strip(),
            transaction_id=str(transaction_id or "").strip(),
            started_at=_now_iso(),
            cancel_event=cancel_event,
            priority=int(priority),
        )
        with self._lock:
            self._in_flight[tid] = record
        return cancel_event

    def clear_in_flight(self, thread_id: str, *, stimulus_id: Optional[str] = None) -> None:
        tid = str(thread_id or "").strip()
        with self._lock:
            current = self._in_flight.get(tid)
            if current is None:
                return
            if stimulus_id is not None and current.stimulus_id != str(stimulus_id).strip():
                return
            self._in_flight.pop(tid, None)

    def get_in_flight(self, thread_id: str) -> Optional[InFlightRecord]:
        tid = str(thread_id or "").strip()
        with self._lock:
            return self._in_flight.get(tid)

    def cancel_in_flight(self, thread_id: str, *, force: bool = False) -> bool:
        record = self.get_in_flight(thread_id)
        if record is None:
            return False
        if force:
            setattr(record.cancel_event, "force_stop", True)
        record.cancel_event.set()
        return True

    def cancel_if_matches(
        self,
        thread_id: str,
        transaction_id: str,
        *,
        reason: str = "transaction_cancelled",
    ) -> bool:
        """Cancel only when the CPU slot still belongs to ``transaction_id``.

        Comparison and signalling happen under the same lock so a completed
        transaction cannot be replaced by another one between lookup and
        cancellation.  The reason is attached to the event for cooperative
        runtime fences; callers must not treat this as a hard thread stop.
        """

        tid = str(thread_id or "").strip()
        tx_id = str(transaction_id or "").strip()
        if not tid or not tx_id:
            return False
        with self._lock:
            record = self._in_flight.get(tid)
            if record is None or record.transaction_id != tx_id:
                return False
            setattr(
                record.cancel_event,
                "cancel_reason",
                str(reason or "transaction_cancelled").strip()
                or "transaction_cancelled",
            )
            record.cancel_event.set()
            return True


THREAD_CPU_STATE = ThreadCpuStateRegistry()


def compute_runtime_phase(*, inbox_pending: int, in_flight: bool) -> tuple[int, str]:
    """Return (effective_depth, runtime_phase) for ready|processing|busy."""
    depth = max(0, int(inbox_pending)) + (1 if in_flight else 0)
    if depth <= 0:
        return 0, "ready"
    if depth == 1:
        return 1, "processing"
    return depth, "busy"


__all__ = [
    "THREAD_CPU_STATE",
    "InFlightRecord",
    "ThreadCpuStateRegistry",
    "compute_runtime_phase",
]
