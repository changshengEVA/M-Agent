"""Process-level Heartbeat monitor contracts and registry.

Heartbeat is a control-plane clock.  Monitors detect work, but all detected
stimuli still enter the runtime through the public ``Observation`` ingress.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import hashlib
import json
import logging
import threading
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Mapping,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
)

from m_agent.sdk.stimulus.contracts import (
    Disposition,
    IngestResult,
    Observation,
    validate_observation,
)

logger = logging.getLogger(__name__)


def observation_trigger_idempotency_key(
    *,
    monitor_id: str,
    owner_id: str,
    thread_id: str,
    source: str,
    source_event_id: str,
) -> str:
    """Build a runtime-global key for one external observation event."""

    identity = {
        "monitor_id": str(monitor_id or "").strip(),
        "owner_id": str(owner_id or "").strip(),
        "thread_id": str(thread_id or "").strip(),
        "source": str(source or "").strip(),
        "source_event_id": str(source_event_id or "").strip(),
    }
    missing = [key for key, value in identity.items() if not value]
    if missing:
        raise ValueError(
            "observation trigger identity missing: " + ", ".join(missing)
        )
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"observation_trigger:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class HeartbeatTarget:
    """One owner-scoped runtime visible to a process Heartbeat tick."""

    owner_id: str
    service_runtime: Any
    runtime_thread_id: str = ""
    public_thread_id: str = ""

    @property
    def runtime_host(self) -> Any:
        host = getattr(self.service_runtime, "runtime_host", None)
        if host is None:
            raise RuntimeError(
                f"heartbeat target {self.owner_id!r} has no runtime_host"
            )
        return host

    @property
    def default_thread_id(self) -> str:
        return (
            str(self.runtime_thread_id or "").strip()
            or str(
                getattr(self.service_runtime, "default_thread_id", "") or ""
            ).strip()
        )

    def resolve_scope(self, thread_id: str = "") -> Tuple[str, str]:
        """Resolve a runtime thread and its current conversation id."""

        target_thread_id = (
            str(thread_id or "").strip()
            or self.default_thread_id
        )
        if not target_thread_id:
            raise RuntimeError(
                f"heartbeat target {self.owner_id!r} has no default thread"
            )
        resolver = getattr(
            self.service_runtime,
            "conversation_id_for_thread",
            None,
        )
        if callable(resolver):
            conversation_id = str(resolver(target_thread_id) or "").strip()
        else:
            getter = getattr(
                self.service_runtime,
                "_get_or_create_thread",
                None,
            )
            session = getter(target_thread_id) if callable(getter) else None
            conversation_id = str(
                getattr(session, "conversation_id", "") or ""
            ).strip()
        return (
            target_thread_id,
            conversation_id or f"{target_thread_id}::0",
        )

    def build_observation_trigger(
        self,
        *,
        monitor_id: str,
        source: str,
        source_event_id: str,
        occurred_at: str,
        observed_at: str,
        subject: str = "",
        text: str = "",
        payload: Optional[Mapping[str, Any]] = None,
        causation_id: Optional[str] = None,
        confidence: Optional[float] = None,
        privacy_class: Optional[str] = None,
        payload_ref: Optional[str] = None,
        transaction_id: Optional[str] = None,
        expires_at: Optional[str] = None,
        stimulus_view: str = "",
    ) -> Observation:
        """Build a correctly scoped and collision-safe trigger Observation."""

        thread_id, conversation_id = self.resolve_scope()
        body = dict(payload or {})
        # ``source`` describes the external system; ``observation_name`` is
        # the stable Heartbeat registry identity required for Scene actor
        # attribution.  They are intentionally not assumed to be identical.
        body.setdefault("observation_name", str(monitor_id or "").strip())
        return Observation(
            source=str(source or "").strip(),
            type="observation_trigger",
            thread_id=thread_id,
            conversation_id=conversation_id,
            occurred_at=str(occurred_at or "").strip(),
            observed_at=str(observed_at or "").strip(),
            subject=str(subject or "").strip(),
            text=str(text or "").strip(),
            idempotency_key=observation_trigger_idempotency_key(
                monitor_id=monitor_id,
                owner_id=self.owner_id,
                thread_id=thread_id,
                source=source,
                source_event_id=source_event_id,
            ),
            causation_id=causation_id,
            confidence=confidence,
            privacy_class=privacy_class,
            payload_ref=payload_ref,
            transaction_id=transaction_id,
            payload=body,
            expires_at=expires_at,
            stimulus_view=str(stimulus_view or "").strip(),
        )


@dataclass(frozen=True)
class HeartbeatContext:
    """Immutable view handed to every monitor for one Heartbeat tick."""

    beat_started_at: str
    targets: Tuple[HeartbeatTarget, ...]


@dataclass(frozen=True)
class HeartbeatMonitorResult:
    """Small monitor-neutral result used for health and aggregate counters."""

    observed: int = 0
    admitted: int = 0
    merged: int = 0
    discarded: int = 0
    failed: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observed": max(0, int(self.observed)),
            "admitted": max(0, int(self.admitted)),
            "merged": max(0, int(self.merged)),
            "discarded": max(0, int(self.discarded)),
            "failed": max(0, int(self.failed)),
            "details": dict(self.details or {}),
        }


class HeartbeatMonitorError(RuntimeError):
    """A monitor beat failed after possibly producing a partial result."""

    def __init__(
        self,
        message: str,
        *,
        result: Optional[HeartbeatMonitorResult] = None,
    ) -> None:
        super().__init__(str(message or "heartbeat monitor failed"))
        self.result = result or HeartbeatMonitorResult(failed=1)


@runtime_checkable
class HeartbeatMonitor(Protocol):
    """Synchronous monitor run by the single process Heartbeat worker."""

    @property
    def monitor_id(self) -> str:
        """Stable registry key for this monitor."""

    def beat(self, context: HeartbeatContext) -> HeartbeatMonitorResult:
        """Inspect sources once and return a bounded result."""


@dataclass(frozen=True)
class ObservationTriggerDelivery:
    """One owner-targeted observation detected by a monitor.

    ``idempotency_key`` is mandatory for Heartbeat-produced observations: a
    tick is at-least-once and may be replayed after a crash or by another
    process.  Source cursors should be committed in ``on_ingested`` so a
    failed commit is safely retried against the same idempotency key.  Prefer
    ``HeartbeatTarget.build_observation_trigger`` to construct the scoped
    Observation and its runtime-global key.
    """

    target: HeartbeatTarget
    observation: Observation
    schedule_drainer: bool = True
    on_ingested: Optional[Callable[[IngestResult], None]] = None


class ObservationTriggerMonitor(ABC):
    """Base class for Heartbeat-driven ``observation_trigger`` producers."""

    def __init__(self, monitor_id: str) -> None:
        normalized = str(monitor_id or "").strip()
        if not normalized:
            raise ValueError("monitor_id must be a non-empty string")
        self._monitor_id = normalized

    @property
    def monitor_id(self) -> str:
        return self._monitor_id

    @abstractmethod
    def observe(
        self,
        context: HeartbeatContext,
    ) -> Iterable[ObservationTriggerDelivery]:
        """Return newly detected deliveries for this tick."""

    def beat(self, context: HeartbeatContext) -> HeartbeatMonitorResult:
        valid_targets = {
            (
                str(item.owner_id or "").strip(),
                id(item.service_runtime),
            ): item
            for item in context.targets
        }
        observed = 0
        admitted = 0
        merged = 0
        discarded = 0
        failed = 0
        stimulus_ids = []
        errors = []

        try:
            deliveries = self.observe(context)
            if deliveries is None:
                deliveries = ()
            for delivery in deliveries:
                observed += 1
                try:
                    if not isinstance(delivery, ObservationTriggerDelivery):
                        raise TypeError(
                            "observe() must yield ObservationTriggerDelivery"
                        )
                    target_key = (
                        str(delivery.target.owner_id or "").strip(),
                        id(delivery.target.service_runtime),
                    )
                    context_target = valid_targets.get(target_key)
                    if context_target is None:
                        raise ValueError(
                            "observation delivery targets a runtime outside "
                            "the current Heartbeat context"
                        )
                    observation = validate_observation(delivery.observation)
                    if str(observation.type or "").strip().lower() != "observation_trigger":
                        raise ValueError(
                            "ObservationTriggerMonitor only emits "
                            "Observation(type='observation_trigger')"
                        )
                    if not str(observation.idempotency_key or "").strip():
                        raise ValueError(
                            "Heartbeat observation_trigger requires a stable "
                            "idempotency_key"
                        )
                    expected_thread_id, expected_conversation_id = (
                        context_target.resolve_scope()
                    )
                    if observation.thread_id != expected_thread_id:
                        raise ValueError(
                            "observation thread_id is outside its Heartbeat "
                            f"target: expected {expected_thread_id!r}"
                        )
                    if observation.conversation_id != expected_conversation_id:
                        raise ValueError(
                            "observation conversation_id is outside its "
                            "Heartbeat target"
                        )
                    result = context_target.runtime_host.ingest(
                        observation,
                        schedule_drainer=bool(delivery.schedule_drainer),
                    )
                    if not isinstance(result, IngestResult):
                        raise TypeError(
                            "RuntimeHost.ingest() must return IngestResult"
                        )
                    stimulus_ids.append(result.stimulus_id)
                    disposition = str(result.disposition or "").strip()
                    if result.merged:
                        merged += 1
                    elif disposition in {
                        Disposition.DISCARDED.value,
                        Disposition.REJECTED.value,
                    }:
                        discarded += 1
                    else:
                        admitted += 1
                    if delivery.on_ingested is not None:
                        delivery.on_ingested(result)
                except Exception as exc:
                    failed += 1
                    errors.append(str(exc))
                    logger.exception(
                        "Observation trigger delivery failed monitor_id=%s",
                        self.monitor_id,
                    )
        except Exception as exc:
            failed += 1
            errors.append(str(exc))
            logger.exception(
                "Observation trigger polling failed monitor_id=%s",
                self.monitor_id,
            )

        result = HeartbeatMonitorResult(
            observed=observed,
            admitted=admitted,
            merged=merged,
            discarded=discarded,
            failed=failed,
            details={"stimulus_ids": stimulus_ids},
        )
        if errors:
            raise HeartbeatMonitorError(
                "; ".join(errors),
                result=result,
            )
        return result


@dataclass
class _MonitorHealth:
    beats_total: int = 0
    beats_succeeded: int = 0
    beats_failed: int = 0
    observed_total: int = 0
    admitted_total: int = 0
    merged_total: int = 0
    discarded_total: int = 0
    failed_total: int = 0
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    last_error: Optional[str] = None


@dataclass(frozen=True)
class _RegistryGroupSnapshot:
    managed_ids: frozenset[str]
    monitors: Tuple[Tuple[str, HeartbeatMonitor], ...]
    health: Tuple[Tuple[str, _MonitorHealth], ...]
    order: Tuple[str, ...]


class HeartbeatMonitorRegistry:
    """Thread-safe, ordered registry with per-monitor failure isolation."""

    def __init__(
        self,
        monitors: Optional[Iterable[HeartbeatMonitor]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._monitors: Dict[str, HeartbeatMonitor] = {}
        self._health: Dict[str, _MonitorHealth] = {}
        self._protected: set[str] = set()
        for monitor in monitors or ():
            self.register(monitor)

    @staticmethod
    def _monitor_id(monitor: HeartbeatMonitor) -> str:
        monitor_id = str(getattr(monitor, "monitor_id", "") or "").strip()
        if not monitor_id:
            raise ValueError("HeartbeatMonitor.monitor_id must be non-empty")
        if not callable(getattr(monitor, "beat", None)):
            raise TypeError("HeartbeatMonitor.beat must be callable")
        return monitor_id

    def register(
        self,
        monitor: HeartbeatMonitor,
        *,
        replace: bool = False,
    ) -> None:
        monitor_id = self._monitor_id(monitor)
        with self._lock:
            if monitor_id in self._protected:
                raise ValueError(
                    f"heartbeat monitor is protected: {monitor_id}"
                )
            if monitor_id in self._monitors and not replace:
                raise ValueError(f"heartbeat monitor already registered: {monitor_id}")
            self._monitors[monitor_id] = monitor
            if monitor_id not in self._health or replace:
                self._health[monitor_id] = _MonitorHealth()

    def unregister(self, monitor_id: str) -> Optional[HeartbeatMonitor]:
        key = str(monitor_id or "").strip()
        with self._lock:
            if key in self._protected:
                raise ValueError(f"heartbeat monitor is protected: {key}")
            monitor = self._monitors.pop(key, None)
            self._health.pop(key, None)
            return monitor

    def replace_group(
        self,
        *,
        managed_ids: Iterable[str],
        monitors: Iterable[HeartbeatMonitor],
    ) -> _RegistryGroupSnapshot:
        """Atomically replace one caller-owned monitor group.

        The returned token can be passed to :meth:`restore_group` if a later
        persistence step fails. Unrelated and protected monitors are left
        untouched.
        """

        managed = frozenset(
            key
            for key in (
                str(value or "").strip() for value in managed_ids
            )
            if key
        )
        if not managed:
            raise ValueError("managed heartbeat monitor ids must be non-empty")
        replacements: list[Tuple[str, HeartbeatMonitor]] = []
        replacement_ids: set[str] = set()
        for monitor in monitors:
            monitor_id = self._monitor_id(monitor)
            if monitor_id not in managed:
                raise ValueError(
                    f"heartbeat monitor is outside managed group: {monitor_id}"
                )
            if monitor_id in replacement_ids:
                raise ValueError(
                    f"duplicate heartbeat monitor in managed group: {monitor_id}"
                )
            replacement_ids.add(monitor_id)
            replacements.append((monitor_id, monitor))

        with self._lock:
            protected = managed.intersection(self._protected)
            if protected:
                raise ValueError(
                    "heartbeat monitor group contains protected ids: "
                    + ", ".join(sorted(protected))
                )
            snapshot = _RegistryGroupSnapshot(
                managed_ids=managed,
                monitors=tuple(
                    (key, monitor)
                    for key, monitor in self._monitors.items()
                    if key in managed
                ),
                health=tuple(
                    (key, health)
                    for key, health in self._health.items()
                    if key in managed
                ),
                order=tuple(self._monitors),
            )
            self._replace_group_locked(
                managed=managed,
                monitors=replacements,
                health=None,
            )
            return snapshot

    def restore_group(self, snapshot: _RegistryGroupSnapshot) -> None:
        """Restore a group snapshot returned by :meth:`replace_group`."""

        if not isinstance(snapshot, _RegistryGroupSnapshot):
            raise TypeError("invalid heartbeat registry group snapshot")
        with self._lock:
            self._replace_group_locked(
                managed=snapshot.managed_ids,
                monitors=snapshot.monitors,
                health=dict(snapshot.health),
            )
            restored_order = [
                key for key in snapshot.order if key in self._monitors
            ]
            restored_order.extend(
                key for key in self._monitors if key not in snapshot.order
            )
            self._monitors = {
                key: self._monitors[key] for key in restored_order
            }
            self._health = {
                key: self._health[key] for key in restored_order
            }

    def _replace_group_locked(
        self,
        *,
        managed: frozenset[str],
        monitors: Iterable[Tuple[str, HeartbeatMonitor]],
        health: Optional[Mapping[str, _MonitorHealth]],
    ) -> None:
        replacements = tuple(monitors)
        replacement_monitors = dict(replacements)
        replacement_health = {
            monitor_id: (
                health[monitor_id]
                if health is not None and monitor_id in health
                else _MonitorHealth()
            )
            for monitor_id, _ in replacements
        }

        # Keep the managed group at the position of its first old member. If
        # the group was absent, append it. restore_group additionally reapplies
        # the exact pre-swap order after a failed settings commit.
        ordered_monitors: Dict[str, HeartbeatMonitor] = {}
        ordered_health: Dict[str, _MonitorHealth] = {}
        inserted = False
        for monitor_id, monitor in self._monitors.items():
            if monitor_id in managed:
                if not inserted:
                    ordered_monitors.update(replacement_monitors)
                    ordered_health.update(replacement_health)
                    inserted = True
                continue
            ordered_monitors[monitor_id] = monitor
            ordered_health[monitor_id] = self._health[monitor_id]
        if not inserted:
            ordered_monitors.update(replacement_monitors)
            ordered_health.update(replacement_health)
        self._monitors = ordered_monitors
        self._health = ordered_health

    def protect(self, monitor_id: str) -> None:
        """Prevent a built-in monitor from being replaced or unregistered."""

        key = str(monitor_id or "").strip()
        with self._lock:
            if key not in self._monitors:
                raise ValueError(
                    f"cannot protect unregistered heartbeat monitor: {key}"
                )
            self._protected.add(key)

    def get(self, monitor_id: str) -> Optional[HeartbeatMonitor]:
        key = str(monitor_id or "").strip()
        with self._lock:
            return self._monitors.get(key)

    def names(self) -> list[str]:
        with self._lock:
            return list(self._monitors.keys())

    def snapshot(self) -> Tuple[HeartbeatMonitor, ...]:
        with self._lock:
            return tuple(self._monitors.values())

    def _snapshot_items(self) -> Tuple[Tuple[str, HeartbeatMonitor], ...]:
        with self._lock:
            return tuple(self._monitors.items())

    def beat(self, context: HeartbeatContext) -> Dict[str, Any]:
        """Run a registry snapshot without holding the registry lock."""

        results: Dict[str, Dict[str, Any]] = {}
        errors: Dict[str, str] = {}
        succeeded = 0
        failed = 0

        for monitor_id, monitor in self._snapshot_items():
            error: Optional[str] = None
            try:
                result = monitor.beat(context)
                if not isinstance(result, HeartbeatMonitorResult):
                    raise TypeError(
                        "HeartbeatMonitor.beat() must return "
                        "HeartbeatMonitorResult"
                    )
            except HeartbeatMonitorError as exc:
                result = exc.result
                error = str(exc)
            except Exception as exc:
                result = HeartbeatMonitorResult(failed=1)
                error = str(exc)
                logger.exception(
                    "Heartbeat monitor failed monitor_id=%s",
                    monitor_id,
                )

            try:
                if not isinstance(result, HeartbeatMonitorResult):
                    raise TypeError(
                        "HeartbeatMonitorError.result must be "
                        "HeartbeatMonitorResult"
                    )
                result_payload = result.to_dict()
            except Exception as exc:
                logger.exception(
                    "Heartbeat monitor result failed validation monitor_id=%s",
                    monitor_id,
                )
                validation_error = str(exc)
                error = (
                    f"{error}; {validation_error}"
                    if error
                    else validation_error
                )
                result = HeartbeatMonitorResult(failed=1)
                result_payload = result.to_dict()

            if not error and result_payload["failed"]:
                error = (
                    "monitor reported "
                    f"{result_payload['failed']} failed item(s)"
                )
            if error:
                failed += 1
            else:
                succeeded += 1
            results[monitor_id] = result_payload
            if error:
                errors[monitor_id] = error
            self._record_result(
                monitor_id,
                monitor=monitor,
                context=context,
                result=result,
                error=error,
            )

        return {
            "registered": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
            "errors": errors,
        }

    def _record_result(
        self,
        monitor_id: str,
        *,
        monitor: HeartbeatMonitor,
        context: HeartbeatContext,
        result: HeartbeatMonitorResult,
        error: Optional[str],
    ) -> None:
        from m_agent.api.chat_api_shared import _now_iso

        with self._lock:
            if self._monitors.get(monitor_id) is not monitor:
                # The snapshot entry was removed or replaced while it ran.
                return
            health = self._health.get(monitor_id)
            if health is None:
                return
            health.beats_total += 1
            if error:
                health.beats_failed += 1
            else:
                health.beats_succeeded += 1
            health.observed_total += max(0, int(result.observed))
            health.admitted_total += max(0, int(result.admitted))
            health.merged_total += max(0, int(result.merged))
            health.discarded_total += max(0, int(result.discarded))
            health.failed_total += max(0, int(result.failed))
            health.last_started_at = context.beat_started_at
            health.last_finished_at = _now_iso()
            health.last_error = error

    def health_payload(self) -> Dict[str, Any]:
        with self._lock:
            monitor_payload: Dict[str, Dict[str, Any]] = {}
            degraded = False
            for monitor_id in self._monitors:
                health = self._health[monitor_id]
                if health.last_error:
                    status = "degraded"
                    degraded = True
                elif health.beats_total:
                    status = "healthy"
                else:
                    status = "idle"
                monitor_payload[monitor_id] = {
                    "status": status,
                    "protected": monitor_id in self._protected,
                    "counters": {
                        "beats_total": health.beats_total,
                        "beats_succeeded": health.beats_succeeded,
                        "beats_failed": health.beats_failed,
                        "observed_total": health.observed_total,
                        "admitted_total": health.admitted_total,
                        "merged_total": health.merged_total,
                        "discarded_total": health.discarded_total,
                        "failed_total": health.failed_total,
                    },
                    "last_beat": {
                        "started_at": health.last_started_at,
                        "finished_at": health.last_finished_at,
                    },
                    "last_error": health.last_error,
                }
            return {
                "status": "degraded" if degraded else "healthy",
                "registered_count": len(self._monitors),
                "registered": list(self._monitors.keys()),
                "monitors": monitor_payload,
            }


__all__ = [
    "HeartbeatContext",
    "HeartbeatMonitor",
    "HeartbeatMonitorError",
    "HeartbeatMonitorRegistry",
    "HeartbeatMonitorResult",
    "HeartbeatTarget",
    "ObservationTriggerDelivery",
    "ObservationTriggerMonitor",
    "observation_trigger_idempotency_key",
]
