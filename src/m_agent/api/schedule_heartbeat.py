from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from m_agent.api.user_access import UserAccessService
from m_agent.runtime.transaction import (
    stable_schedule_delivery_id,
    stable_schedule_run_id,
)
from m_agent.schedule.store import ANONYMOUS_OWNER_ID
from m_agent.schedule.service import (
    DEFAULT_SCHEDULE_LEASE_SECONDS,
    ScheduleLeaseConflictError,
)

from .chat_api_runtime import ChatServiceRuntime, ThreadEventSink
from .chat_api_shared import _now_iso, _scoped_thread_id
from .heartbeat import (
    HeartbeatContext,
    HeartbeatMonitor,
    HeartbeatMonitorRegistry,
    HeartbeatMonitorResult,
    HeartbeatTarget,
)

logger = logging.getLogger(__name__)


class _ScheduleHeartbeatMonitor:
    """Registry adapter that keeps Schedule's lease state machine private."""

    monitor_id = "schedule"

    def __init__(self, coordinator: "ScheduleHeartbeatCoordinator") -> None:
        self._coordinator = coordinator

    def beat(self, context: HeartbeatContext) -> HeartbeatMonitorResult:
        return self._coordinator._beat_schedules(context)


class ScheduleHeartbeatCoordinator:
    """Process Heartbeat coordinator with a protected Schedule monitor."""

    def __init__(
        self,
        *,
        service_runtime: ChatServiceRuntime,
        user_access: Optional[UserAccessService] = None,
        beat_interval_seconds: int = 10,
        batch_limit: int = 20,
        lease_duration_seconds: float = DEFAULT_SCHEDULE_LEASE_SECONDS,
        lease_owner: str = "",
        thread_event_sink: ThreadEventSink = None,
        monitors: Optional[Iterable[HeartbeatMonitor]] = None,
        autostart: bool = True,
    ) -> None:
        self.service_runtime = service_runtime
        self.user_access = user_access
        self.beat_interval_seconds = max(1, int(beat_interval_seconds or 10))
        self.batch_limit = max(1, min(200, int(batch_limit or 20)))
        self.lease_duration_seconds = max(
            1.0,
            float(lease_duration_seconds or DEFAULT_SCHEDULE_LEASE_SECONDS),
        )
        self.lease_owner = (
            str(lease_owner or "").strip()
            or f"schedule-heartbeat:{uuid.uuid4().hex}"
        )
        self.thread_event_sink = thread_event_sink
        self.created_at = _now_iso()
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._lifecycle_lock = threading.RLock()
        self._lifecycle_condition = threading.Condition(self._lifecycle_lock)
        self._shutting_down = False
        self._accepting_beats = True
        self._active_beats = 0
        self._beat_local = threading.local()
        self._beat_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._beats_total = 0
        self._items_leased = 0
        self._items_started = 0
        self._items_completed = 0
        self._items_failed = 0
        self._last_beat_started_at: Optional[str] = None
        self._last_beat_finished_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self.monitor_registry = HeartbeatMonitorRegistry()
        self.monitor_registry.register(_ScheduleHeartbeatMonitor(self))
        self.monitor_registry.protect(_ScheduleHeartbeatMonitor.monitor_id)
        for monitor in monitors or ():
            self.register_monitor(monitor)
        if autostart:
            self.start()

    def register_monitor(
        self,
        monitor: HeartbeatMonitor,
        *,
        replace: bool = False,
    ) -> None:
        """Register a monitor that will run on subsequent Heartbeat ticks."""

        if str(getattr(monitor, "monitor_id", "") or "").strip() == "schedule":
            raise ValueError("the built-in schedule monitor cannot be replaced")
        self.monitor_registry.register(monitor, replace=replace)

    def unregister_monitor(self, monitor_id: str) -> Optional[HeartbeatMonitor]:
        """Remove a monitor; the built-in Schedule monitor may not be removed."""

        key = str(monitor_id or "").strip()
        if key == _ScheduleHeartbeatMonitor.monitor_id:
            raise ValueError("the built-in schedule monitor cannot be unregistered")
        return self.monitor_registry.unregister(key)

    def reconcile_monitor_group(
        self,
        *,
        managed_ids: Iterable[str],
        monitors: Iterable[HeartbeatMonitor],
        commit: Callable[[], None],
    ) -> Tuple[str, ...]:
        """Swap a managed monitor group and commit its config as one unit.

        Beats cannot observe the candidate group until ``commit`` succeeds.
        If persistence raises, the previous monitor objects and health records
        are restored before the beat lock is released.
        """

        candidate = tuple(monitors)
        with self._beat_lock:
            snapshot = self.monitor_registry.replace_group(
                managed_ids=managed_ids,
                monitors=candidate,
            )
            try:
                commit()
            except Exception:
                self.monitor_registry.restore_group(snapshot)
                raise
        return tuple(
            str(getattr(monitor, "monitor_id", "") or "").strip()
            for monitor in candidate
        )

    def start(self) -> None:
        with self._lifecycle_condition:
            if self._shutting_down:
                raise RuntimeError("heartbeat coordinator is shutting down")
            if self._worker is not None and self._worker.is_alive():
                if not self._accepting_beats:
                    raise RuntimeError("heartbeat worker is still stopping")
                return
            self._accepting_beats = True
            self._stop_event.clear()
            worker = threading.Thread(
                target=self._run_loop,
                name="schedule-heartbeat",
                daemon=True,
            )
            self._worker = worker
            try:
                worker.start()
            except Exception:
                self._worker = None
                self._stop_event.set()
                raise

    def shutdown(self, *, timeout: Optional[float] = None) -> bool:
        """Stop the worker before its target runtimes are closed.

        The default waits for an in-flight monitor to finish.  Monitor
        implementations therefore must keep ``beat`` bounded.
        """

        join_timeout = (
            None
            if timeout is None
            else max(0.0, float(timeout))
        )
        with self._lifecycle_condition:
            self._shutting_down = True
            self._accepting_beats = False
            self._stop_event.set()
            worker = self._worker
        deadline = (
            None
            if join_timeout is None
            else time.monotonic() + join_timeout
        )
        try:
            if (
                worker is not None
                and worker.is_alive()
                and worker is not threading.current_thread()
            ):
                remaining = (
                    None
                    if deadline is None
                    else max(0.0, deadline - time.monotonic())
                )
                worker.join(timeout=remaining)
            if not bool(getattr(self._beat_local, "active", False)):
                with self._lifecycle_condition:
                    while self._active_beats > 0:
                        remaining = (
                            None
                            if deadline is None
                            else max(0.0, deadline - time.monotonic())
                        )
                        if remaining is not None and remaining <= 0:
                            break
                        self._lifecycle_condition.wait(timeout=remaining)
            with self._lifecycle_condition:
                return bool(
                    (worker is None or not worker.is_alive())
                    and self._active_beats == 0
                )
        finally:
            with self._lifecycle_condition:
                stopped = bool(
                    (worker is None or not worker.is_alive())
                    and self._active_beats == 0
                )
                if self._worker is worker and stopped:
                    self._worker = None
                self._shutting_down = False
                self._lifecycle_condition.notify_all()

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self.beat_interval_seconds):
            if self._stop_event.is_set():
                return
            try:
                self.beat_once()
            except Exception as exc:
                if self._stop_event.is_set():
                    return
                logger.exception("Schedule heartbeat beat failed")
                with self._stats_lock:
                    self._last_error = str(exc)

    def _collect_runtime_targets(
        self,
    ) -> Tuple[Tuple[HeartbeatTarget, ...], Tuple[str, ...]]:
        targets = [
            HeartbeatTarget(
                owner_id=ANONYMOUS_OWNER_ID,
                service_runtime=self.service_runtime,
                runtime_thread_id=self.service_runtime.default_thread_id,
                public_thread_id=self.service_runtime.default_thread_id,
            )
        ]
        errors = []
        if self.user_access is None:
            return tuple(targets), tuple(errors)
        try:
            usernames = list(self.user_access.list_usernames())
        except Exception as exc:
            logger.exception("Heartbeat failed to list user runtime targets")
            errors.append(f"user target enumeration failed: {exc}")
            return tuple(targets), tuple(errors)
        for username in usernames:
            try:
                user = self.user_access.get_user(username=username)
            except Exception as exc:
                logger.warning(
                    "Heartbeat skipped user=%s (account lookup failed): %s",
                    username,
                    exc,
                )
                errors.append(f"user {username!r} lookup failed: {exc}")
                continue
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
                errors.append(f"user {username!r} runtime unavailable: {exc}")
                continue
            public_thread_id = (
                str(getattr(user, "canonical_thread_id", "") or "").strip()
                or runtime.default_thread_id
            )
            targets.append(
                HeartbeatTarget(
                    owner_id=user.username,
                    service_runtime=runtime,
                    runtime_thread_id=_scoped_thread_id(
                        user,
                        public_thread_id,
                    ),
                    public_thread_id=public_thread_id,
                )
            )
        return tuple(targets), tuple(errors)

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
        """Run one non-reentrant tick across a stable monitor snapshot."""

        if bool(getattr(self._beat_local, "active", False)):
            raise RuntimeError("heartbeat beat_once is not re-entrant")
        with self._lifecycle_condition:
            if self._shutting_down or not self._accepting_beats:
                raise RuntimeError("heartbeat coordinator is not accepting beats")
            self._active_beats += 1
        self._beat_local.active = True
        try:
            with self._beat_lock:
                with self._lifecycle_condition:
                    if self._shutting_down or not self._accepting_beats:
                        raise RuntimeError(
                            "heartbeat coordinator stopped before beat started"
                        )
                try:
                    return self._beat_once_locked()
                except Exception as exc:
                    with self._stats_lock:
                        self._last_beat_finished_at = _now_iso()
                        self._last_error = str(exc)
                    raise
        finally:
            self._beat_local.active = False
            with self._lifecycle_condition:
                self._active_beats = max(0, self._active_beats - 1)
                self._lifecycle_condition.notify_all()

    def _beat_once_locked(self) -> Dict[str, Any]:
        beat_started_at = _now_iso()
        with self._stats_lock:
            self._beats_total += 1
            self._last_beat_started_at = beat_started_at

        targets, target_errors = self._collect_runtime_targets()
        report = self.monitor_registry.beat(
            HeartbeatContext(
                beat_started_at=beat_started_at,
                targets=targets,
            )
        )
        schedule_result = dict(
            report.get("results", {}).get("schedule", {}).get("details", {})
        )
        total_leased = max(0, int(schedule_result.get("leased", 0) or 0))
        total_started = max(0, int(schedule_result.get("started", 0) or 0))
        total_completed = max(
            0,
            int(schedule_result.get("completed", 0) or 0),
        )
        total_failed = max(0, int(schedule_result.get("failed", 0) or 0))
        beat_finished_at = _now_iso()
        monitor_errors = dict(report.get("errors") or {})
        if target_errors:
            report["target_errors"] = list(target_errors)
        last_error = (
            "; ".join(
                [
                    *(
                        f"{monitor_id}: {error}"
                        for monitor_id, error in monitor_errors.items()
                    ),
                    *target_errors,
                ]
            )
            or None
        )

        with self._stats_lock:
            self._items_leased += total_leased
            self._items_started += total_started
            self._items_completed += total_completed
            self._items_failed += total_failed
            self._last_beat_finished_at = beat_finished_at
            self._last_error = last_error

        return {
            "beat_started_at": beat_started_at,
            "beat_finished_at": beat_finished_at,
            "leased": total_leased,
            "started": total_started,
            "completed": total_completed,
            "failed": total_failed,
            "monitors": report,
        }

    def _beat_schedules(
        self,
        context: HeartbeatContext,
    ) -> HeartbeatMonitorResult:
        total_leased = 0
        total_started = 0
        total_completed = 0
        total_failed = 0

        for target in context.targets:
            owner_id = target.owner_id
            runtime = target.service_runtime
            self._wire_runtime_sink(runtime)
            schedule_agent = runtime.agent.get_schedule_agent()
            schedule_service = schedule_agent.service
            leased_items = schedule_service.lease_due_schedules(
                owner_id=owner_id,
                limit=self.batch_limit,
                lease_owner=self.lease_owner,
                lease_seconds=self.lease_duration_seconds,
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
                system_context["schedule_lease_token"] = str(
                    getattr(schedule_item, "lease_token", "") or ""
                ).strip()
                system_context["schedule_attempt"] = max(
                    0,
                    int(getattr(schedule_item, "attempt", 0) or 0),
                )
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
                    failed_item = schedule_item
                    try:
                        failed_item = schedule_service.release_lease(
                            owner_id=item_owner_id,
                            thread_id=stored_thread_id,
                            schedule_id=schedule_id,
                            lease_token=str(
                                getattr(schedule_item, "lease_token", "") or ""
                            ).strip(),
                            error=error_text,
                        )
                    except ScheduleLeaseConflictError:
                        # A newer heartbeat already reclaimed the schedule.  A
                        # stale enqueue failure must not reset that newer lease.
                        logger.warning(
                            "Ignored stale schedule lease release owner_id=%s schedule_id=%s",
                            item_owner_id,
                            schedule_id,
                        )
                    total_failed += 1
                    self._emit_thread_event(
                        target_thread_id,
                        "schedule_failed",
                        self._schedule_event_payload(
                            failed_item,
                            public_thread_id=public_thread_id,
                            run_id=run_id,
                            error=error_text,
                        ),
                    )

        return HeartbeatMonitorResult(
            observed=total_leased,
            admitted=total_started,
            failed=total_failed,
            details={
                "leased": total_leased,
                "started": total_started,
                "completed": total_completed,
                "failed": total_failed,
            },
        )

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
                    "lease_duration_seconds": self.lease_duration_seconds,
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
                "monitors": self.monitor_registry.health_payload(),
                "last_error": last_error,
            }
