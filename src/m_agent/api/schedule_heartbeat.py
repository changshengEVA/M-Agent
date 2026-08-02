from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import threading
from typing import Any, Dict, Iterable, Optional, Tuple

from m_agent.api.user_access import UserAccessService
from m_agent.runtime.transaction import (
    stable_schedule_delivery_id,
    stable_schedule_run_id,
)
from m_agent.schedule.store import ANONYMOUS_OWNER_ID

from .chat_api_runtime import ChatServiceRuntime, ThreadEventSink
from .chat_api_shared import _now_iso, _scoped_thread_id

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
        thread_event_sink: ThreadEventSink = None,
        autostart: bool = True,
    ) -> None:
        self.service_runtime = service_runtime
        self.user_access = user_access
        self.beat_interval_seconds = max(1, int(beat_interval_seconds or 10))
        self.batch_limit = max(1, min(200, int(batch_limit or 20)))
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
            try:
                runtime = self.user_access.get_runtime(user=user)
            except Exception as exc:
                logger.warning(
                    "Schedule heartbeat skipped user=%s (runtime unavailable): %s",
                    username,
                    exc,
                )
                continue
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
    def _schedule_event_payload(
        schedule_item: Any,
        *,
        public_thread_id: str = "",
        run_id: str = "",
        error: str = "",
    ) -> Dict[str, Any]:
        raw_objective = getattr(schedule_item, "deferred_objective", None)
        if hasattr(raw_objective, "to_dict"):
            deferred_objective = dict(raw_objective.to_dict())
        elif isinstance(raw_objective, dict):
            deferred_objective = dict(raw_objective)
        else:
            deferred_objective = {
                "description": str(
                    getattr(schedule_item, "text", "") or ""
                ).strip(),
                "encoding": "legacy_text",
            }
        payload = {
            "thread_id": (
                str(public_thread_id or "").strip()
                or str(getattr(schedule_item, "thread_id", "") or "").strip()
            ),
            "schedule_id": str(getattr(schedule_item, "schedule_id", "") or "").strip(),
            "text": str(getattr(schedule_item, "text", "") or "").strip(),
            "deferred_objective": deferred_objective,
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
                stored_thread_id = (
                    str(getattr(schedule_item, "thread_id", "") or "").strip()
                    or runtime.default_thread_id
                )
                target_thread_id = stored_thread_id
                public_thread_id = stored_thread_id
                if owner_id != ANONYMOUS_OWNER_ID and self.user_access is not None:
                    user = self.user_access.get_user(username=owner_id)
                    if user is not None:
                        configured_thread_id = str(
                            getattr(user, "canonical_thread_id", "") or ""
                        ).strip()
                        if configured_thread_id:
                            public_thread_id = configured_thread_id
                            target_thread_id = _scoped_thread_id(user, configured_thread_id)
                payload = self._schedule_event_payload(
                    schedule_item,
                    public_thread_id=public_thread_id,
                )
                self._emit_thread_event(target_thread_id, "schedule_due", payload)

                item_owner_id = owner_id
                schedule_id = str(getattr(schedule_item, "schedule_id", "") or "")
                due_at = str(
                    getattr(schedule_item, "due_at_utc", "") or ""
                ).strip()
                raw_origin = getattr(schedule_item, "origin", None)
                origin = dict(raw_origin) if isinstance(raw_origin, dict) else {}
                origin_transaction_id = str(
                    origin.get("transaction_id", "") or ""
                ).strip()
                origin_conversation_id = str(
                    origin.get("conversation_id", "") or ""
                ).strip()
                run_binding = origin_transaction_id or (
                    f"external:{item_owner_id}:{stored_thread_id}"
                )
                run_id = stable_schedule_run_id(
                    schedule_id,
                    run_binding,
                    due_at,
                )
                delivery_id = stable_schedule_delivery_id(run_id, 1)
                schedule_prompt = runtime._schedule_prompt(schedule_item)
                system_context = runtime._schedule_system_context(schedule_item)
                system_context["schedule_run_id"] = run_id
                system_context["schedule_delivery_id"] = delivery_id
                if origin_transaction_id:
                    system_context["transaction_id"] = origin_transaction_id
                if origin_conversation_id:
                    system_context["origin_conversation_id"] = (
                        origin_conversation_id
                    )
                try:
                    queued = runtime.runtime_host.enqueue_schedule(
                        thread_id=target_thread_id,
                        conversation_id=(
                            origin_conversation_id
                            or runtime._get_or_create_thread(
                                target_thread_id
                            ).conversation_id
                        ),
                        schedule_id=schedule_id,
                        text=schedule_prompt,
                        payload=system_context,
                        run_id=run_id,
                        owner_id=item_owner_id,
                    )
                    total_started += 1
                    self._emit_thread_event(
                        target_thread_id,
                        "schedule_queued",
                        {
                            **payload,
                            "status": "queued",
                            "run_id": run_id,
                            "stimulus_id": queued.get("stimulus_id"),
                            "pending_count": queued.get("pending_count"),
                            "runtime_phase": queued.get("runtime_phase"),
                        },
                    )
                except Exception as exc:
                    logger.exception(
                        "Runtime schedule enqueue failed owner_id=%s thread_id=%s schedule_id=%s",
                        item_owner_id,
                        target_thread_id,
                        schedule_id,
                    )
                    error_text = str(exc or "schedule enqueue failed").strip() or "schedule enqueue failed"
                    schedule_service.mark_failed(
                        owner_id=item_owner_id,
                        thread_id=stored_thread_id,
                        schedule_id=schedule_id,
                        error=error_text,
                    )
                    total_failed += 1
                    self._emit_thread_event(
                        target_thread_id,
                        "schedule_failed",
                        self._schedule_event_payload(
                            schedule_item,
                            public_thread_id=public_thread_id,
                            run_id=run_id,
                            error=error_text,
                        ),
                    )

        beat_finished_at = _now_iso()
        with self._stats_lock:
            self._items_leased += total_leased
            self._items_started += total_started
            self._items_completed += total_completed
            self._items_failed += total_failed
            self._last_beat_finished_at = beat_finished_at
            self._last_error = None

        return {
            "beat_started_at": beat_started_at,
            "beat_finished_at": beat_finished_at,
            "leased": total_leased,
            "started": total_started,
            "completed": total_completed,
            "failed": total_failed,
        }

    def health_payload(self) -> Dict[str, Any]:
        with self._stats_lock:
            worker_alive = bool(self._worker is not None and self._worker.is_alive())
            last_error = self._last_error
            if not worker_alive:
                status = "unhealthy"
            elif last_error:
                status = "degraded"
            else:
                status = "healthy"
            return {
                "enabled": True,
                "status": status,
                "worker": {
                    "alive": worker_alive,
                    "created_at": self.created_at,
                },
                "scheduler": {
                    "beat_interval_seconds": self.beat_interval_seconds,
                    "interval_seconds": self.beat_interval_seconds,
                    "batch_limit": self.batch_limit,
                    "next_beat_due_at": self._next_beat_due_at(),
                },
                "counters": {
                    "beats_total": self._beats_total,
                    "schedule_leased_total": self._items_leased,
                    "schedule_started_total": self._items_started,
                    "schedule_completed_total": self._items_completed,
                    "schedule_failed_total": self._items_failed,
                },
                "last_beat": {
                    "started_at": self._last_beat_started_at,
                    "finished_at": self._last_beat_finished_at,
                },
                "last_error": last_error,
            }
