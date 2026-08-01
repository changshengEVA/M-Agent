"""Background thread drainer for the Think-life perception inbox."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS

logger = logging.getLogger(__name__)

ThinkingEventEmitter = Callable[[str, Dict[str, Any]], None]


class ThreadDrainerService:
    """Run at most one inbox-draining worker for each thread ID."""

    def __init__(
        self,
        *,
        drain_fn: Callable[..., Dict[str, Any]],
        get_pending: Callable[[str], int],
        build_emitter: Callable[[str], Optional[ThinkingEventEmitter]],
        get_history: Callable[[str], Optional[List[Dict[str, Any]]]],
        on_runtime_updated: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._drain_fn = drain_fn
        self._get_pending = get_pending
        self._build_emitter = build_emitter
        self._get_history = get_history
        self._on_runtime_updated = on_runtime_updated
        self._lock = threading.RLock()
        self._active: Dict[str, threading.Thread] = {}

    def ensure_running(self, thread_id: str) -> bool:
        tid = str(thread_id or "").strip()
        if not tid:
            return False
        with self._lock:
            worker = self._active.get(tid)
            if worker is not None and worker.is_alive():
                return False
            THREAD_RUNTIME_STATUS.set_drainer_active(tid, True)
            worker = threading.Thread(
                target=self._run_loop,
                args=(tid,),
                name=f"think-life-drainer-{tid}",
                daemon=True,
            )
            self._active[tid] = worker
            worker.start()
            return True

    def active_drainer_count(self) -> int:
        with self._lock:
            return sum(1 for worker in self._active.values() if worker.is_alive())

    def _run_loop(self, thread_id: str) -> None:
        exited_cleanly = False
        try:
            while True:
                if self._get_pending(thread_id) <= 0:
                    # Close the lost-wakeup window: recheck pending under the
                    # same lock that gates ensure_running before unregistering.
                    with self._lock:
                        if self._get_pending(thread_id) <= 0:
                            self._active.pop(thread_id, None)
                            exited_cleanly = True
                            break
                    continue
                emitter = self._build_emitter(thread_id)
                history = self._get_history(thread_id)
                self._drain_fn(
                    thread_id,
                    history_messages=history,
                    event_emitter=emitter,
                )
                THREAD_RUNTIME_STATUS.set_pending_stimuli(
                    thread_id,
                    self._get_pending(thread_id),
                )
                self._emit_runtime(thread_id)
        except Exception:
            logger.exception("Think-life drainer failed thread_id=%s", thread_id)
        finally:
            THREAD_RUNTIME_STATUS.set_drainer_active(thread_id, False)
            THREAD_RUNTIME_STATUS.set_pending_stimuli(
                thread_id,
                self._get_pending(thread_id),
            )
            if not exited_cleanly:
                with self._lock:
                    self._active.pop(thread_id, None)
            self._emit_runtime(thread_id)

    def _emit_runtime(self, thread_id: str) -> None:
        if self._on_runtime_updated is not None:
            try:
                self._on_runtime_updated(thread_id)
            except Exception:
                logger.exception("drainer runtime_updated failed thread_id=%s", thread_id)

        emitter = self._build_emitter(thread_id)
        if emitter is not None:
            emitter(
                "thread_runtime_updated",
                {"thread_runtime": THREAD_RUNTIME_STATUS.snapshot(thread_id).to_dict()},
            )
