from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import threading
import uuid
from typing import Any, Dict, Iterable, Optional, Tuple

from m_agent.api.user_access import UserAccessService
from m_agent.schedule.store import ANONYMOUS_OWNER_ID

from .chat_api_runtime import ChatServiceRuntime, ThreadEventSink
from .chat_api_shared import _get_thread_lock, _now_iso

logger = logging.getLogger(__name__)


class ScheduleHeartbeatCoordinator:
    """Background scheduler that leases due schedules and routes them back into chat runtimes."""

    def __init__(
        self,
        *,
        service_runtime: ChatServiceRuntime,
        user_access: Optional[UserAccessService] = None,
        beat_interval_seconds: int = 10,
        batch_limit: int = 20,
        busy_retry_seconds: int = 5,
        thread_event_sink: ThreadEventSink = None,
        autostart: bool = True,
    ) -> None:
        self.service_runtime = service_runtime
        self.user_access = user_access
        self.beat_interval_seconds = max(1, int(beat_interval_seconds or 10))
        self.batch_limit = max(1, min(200, int(batch_limit or 20)))
        self.busy_retry_seconds = max(1, int(busy_retry_seconds or 5))
        self.thread_event_sink = thread_event_sink
        self.created_at = _now_iso()
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._stats_lock = threading.Lock()
        self._beats_total = 0
        self._items_leased = 0
        self._items_started = 0
        self._items_completed = 0
        self._items_failed = 0
        self._items_busy_retried = 0
        self._last_beat_started_at: Optional[str] = None
        self._last_beat_finished_at: Optional[str] = None
        self._last_error: Optional[str] = None
        if autostart:
            self.start()

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._run_loop,
            name="schedule-heartbeat",
            daemon=True,
        )
        self._worker.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=2.0)

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self.beat_interval_seconds):
            try:
                self.beat_once()
            except Exception as exc:
                logger.exception("Schedule heartbeat beat failed")
                with self._stats_lock:
                    self._last_error = str(exc)

    def _iter_runtime_targets(self) -> Iterable[Tuple[str, ChatServiceRuntime]]:
        yield ANONYMOUS_OWNER_ID, self.service_runtime
        if self.user_access is None:
            return
        for username in self.user_access.list_usernames():
            user = self.user_access.get_user(username=username)
            if user is None:
                continue
            runtime = self.user_access.get_runtime(user=user)
            yield user.username, runtime

    def _wire_runtime_sink(self, runtime: ChatServiceRuntime) -> None:
        if self.thread_event_sink is not None:
            runtime.set_thread_event_sink(self.thread_event_sink)

    @staticmethod
    def _parse_iso_utc(raw_value: Optional[str]) -> Optional[datetime]:
        text = str(raw_value or "").strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).astimezone(timezone.utc)
        except ValueError:
            return None

    def _next_beat_due_at(self) -> Optional[str]:
        base_dt = self._parse_iso_utc(self._last_beat_finished_at)
        if base_dt is None:
            base_dt = self._parse_iso_utc(self._last_beat_started_at)
        if base_dt is None:
            base_dt = self._parse_iso_utc(self.created_at)
        if base_dt is None:
            return None
        return (base_dt + timedelta(seconds=self.beat_interval_seconds)).isoformat().replace("+00:00", "Z")

    def _emit_thread_event(self, thread_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        if self.thread_event_sink is None:
            return
        try:
            self.thread_event_sink(thread_id, event_type, payload)
        except Exception:
            logger.exception("Failed to emit schedule heartbeat event=%s thread_id=%s", event_type, thread_id)

    @staticmethod
    def _schedule_event_payload(schedule_item: Any, *, run_id: str = "", error: str = "") -> Dict[str, Any]:
        payload = {
            "thread_id": str(getattr(schedule_item, "thread_id", "") or "").strip(),
            "schedule_id": str(getattr(schedule_item, "schedule_id", "") or "").strip(),
            "title": str(getattr(schedule_item, "title", "") or "").strip(),
            "status": str(getattr(schedule_item, "status", "") or "").strip(),
            "due_at_utc": str(getattr(schedule_item, "due_at_utc", "") or "").strip(),
            "timezone_name": str(getattr(schedule_item, "timezone_name", "") or "").strip(),
        }
        if run_id:
            payload["run_id"] = run_id
        if error:
            payload["error"] = error
        return payload

    def beat_once(self) -> Dict[str, Any]:
        beat_started_at = _now_iso()
        total_leased = 0
        total_started = 0
        total_completed = 0
        total_failed = 0
        total_busy_retried = 0

        with self._stats_lock:
            self._beats_total += 1
            self._last_beat_started_at = beat_started_at

        for owner_id, runtime in self._iter_runtime_targets():
            self._wire_runtime_sink(runtime)
            schedule_agent = runtime.agent.get_schedule_agent()
            schedule_service = schedule_agent.service
            leased_items = schedule_service.lease_due_schedules(
                owner_id=owner_id,
                limit=self.batch_limit,
            )
            total_leased += len(leased_items)

            for schedule_item in leased_items:
                target_thread_id = str(getattr(schedule_item, "thread_id", "") or "").strip() or runtime.default_thread_id
                payload = self._schedule_event_payload(schedule_item)
                self._emit_thread_event(target_thread_id, "schedule_due", payload)

                thread_lock = _get_thread_lock(target_thread_id)
                if not thread_lock.acquire(blocking=False):
                    schedule_service.release_lease(
                        owner_id=str(getattr(schedule_item, "owner_id", "") or owner_id),
                        thread_id=target_thread_id,
                        schedule_id=str(getattr(schedule_item, "schedule_id", "") or ""),
                        reason="thread_busy",
                        retry_after_seconds=self.busy_retry_seconds,
                    )
                    total_busy_retried += 1
                    self._emit_thread_event(
                        target_thread_id,
                        "schedule_busy_retry",
                        {
                            **payload,
                            "status": "pending",
                            "retry_after_seconds": self.busy_retry_seconds,
                        },
                    )
                    continue

                run_id = f"schedule_run_{uuid.uuid4().hex}"
                try:
                    schedule_service.mark_running(
                        owner_id=str(getattr(schedule_item, "owner_id", "") or owner_id),
                        thread_id=target_thread_id,
                        schedule_id=str(getattr(schedule_item, "schedule_id", "") or ""),
                    )
                    total_started += 1
                    self._emit_thread_event(
                        target_thread_id,
                        "schedule_started",
                        self._schedule_event_payload(schedule_item, run_id=run_id),
                    )

                    result = runtime.run_schedule_trigger(schedule_item=schedule_item)
                    answer_text = str(result.get("answer", "") or "").strip()
                    schedule_service.mark_done(
                        owner_id=str(getattr(schedule_item, "owner_id", "") or owner_id),
                        thread_id=target_thread_id,
                        schedule_id=str(getattr(schedule_item, "schedule_id", "") or ""),
                        run_id=run_id,
                        result={
                            "answer": answer_text,
                            "memory_capture": result.get("memory_capture"),
                        },
                    )
                    total_completed += 1
                    self._emit_thread_event(
                        target_thread_id,
                        "schedule_completed",
                        {
                            **self._schedule_event_payload(schedule_item, run_id=run_id),
                            "status": "done",
                            "answer": answer_text,
                        },
                    )
                except Exception as exc:
                    logger.exception(
                        "Schedule trigger failed owner_id=%s thread_id=%s schedule_id=%s",
                        getattr(schedule_item, "owner_id", owner_id),
                        target_thread_id,
                        getattr(schedule_item, "schedule_id", ""),
                    )
                    error_text = str(exc or "schedule trigger failed").strip() or "schedule trigger failed"
                    schedule_service.mark_failed(
                        owner_id=str(getattr(schedule_item, "owner_id", "") or owner_id),
                        thread_id=target_thread_id,
                        schedule_id=str(getattr(schedule_item, "schedule_id", "") or ""),
                        error=error_text,
                    )
                    total_failed += 1
                    self._emit_thread_event(
                        target_thread_id,
                        "schedule_failed",
                        self._schedule_event_payload(schedule_item, run_id=run_id, error=error_text),
                    )
                finally:
                    thread_lock.release()

        beat_finished_at = _now_iso()
        with self._stats_lock:
            self._items_leased += total_leased
            self._items_started += total_started
            self._items_completed += total_completed
            self._items_failed += total_failed
            self._items_busy_retried += total_busy_retried
            self._last_beat_finished_at = beat_finished_at
            self._last_error = None

        return {
            "beat_started_at": beat_started_at,
            "beat_finished_at": beat_finished_at,
            "leased": total_leased,
            "started": total_started,
            "completed": total_completed,
            "failed": total_failed,
            "busy_retried": total_busy_retried,
        }

    def health_payload(self) -> Dict[str, Any]:
        with self._stats_lock:
            return {
                "enabled": True,
                "worker_alive": bool(self._worker is not None and self._worker.is_alive()),
                "created_at": self.created_at,
                "beat_interval_seconds": self.beat_interval_seconds,
                "interval_seconds": self.beat_interval_seconds,
                "batch_limit": self.batch_limit,
                "busy_retry_seconds": self.busy_retry_seconds,
                "beats_total": self._beats_total,
                "items_leased": self._items_leased,
                "items_started": self._items_started,
                "items_completed": self._items_completed,
                "items_failed": self._items_failed,
                "items_busy_retried": self._items_busy_retried,
                "last_beat_started_at": self._last_beat_started_at,
                "last_beat_finished_at": self._last_beat_finished_at,
                "next_beat_due_at": self._next_beat_due_at(),
                "last_error": self._last_error,
            }
