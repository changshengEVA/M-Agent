from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from m_agent.api.heartbeat import (
    HeartbeatContext,
    HeartbeatMonitorError,
    HeartbeatMonitorRegistry,
    HeartbeatMonitorResult,
    HeartbeatTarget,
    ObservationTriggerDelivery,
    ObservationTriggerMonitor,
)
from m_agent.api.schedule_heartbeat import ScheduleHeartbeatCoordinator
from m_agent.schedule.store import ANONYMOUS_OWNER_ID
from m_agent.sdk.stimulus.contracts import IngestResult, Observation


class _RecordingMonitor:
    def __init__(
        self,
        monitor_id: str,
        calls: list[str],
        *,
        result: HeartbeatMonitorResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.monitor_id = monitor_id
        self.calls = calls
        self.result = result or HeartbeatMonitorResult()
        self.error = error
        self.contexts: list[HeartbeatContext] = []

    def beat(self, context: HeartbeatContext) -> HeartbeatMonitorResult:
        self.calls.append(self.monitor_id)
        self.contexts.append(context)
        if self.error is not None:
            raise self.error
        return self.result


class _BlockingMonitor:
    def __init__(self, monitor_id: str = "blocking") -> None:
        self.monitor_id = monitor_id
        self.started = threading.Event()
        self.release = threading.Event()

    def beat(self, context: HeartbeatContext) -> HeartbeatMonitorResult:
        del context
        self.started.set()
        if not self.release.wait(timeout=2.0):
            raise RuntimeError("blocking monitor test timed out")
        return HeartbeatMonitorResult()


class _RuntimeHostFake:
    def __init__(self) -> None:
        self.calls: list[tuple[Observation, bool]] = []

    def ingest(
        self,
        observation: Observation,
        *,
        schedule_drainer: bool = True,
    ) -> IngestResult:
        self.calls.append((observation, schedule_drainer))
        return IngestResult(
            stimulus_id=f"stimulus-{len(self.calls)}",
            pool_state="ready",
            created=True,
        )


class _StaticObservationMonitor(ObservationTriggerMonitor):
    def __init__(
        self,
        monitor_id: str,
        deliveries: list[ObservationTriggerDelivery],
    ) -> None:
        super().__init__(monitor_id)
        self.deliveries = deliveries

    def observe(
        self,
        context: HeartbeatContext,
    ) -> list[ObservationTriggerDelivery]:
        return list(self.deliveries)


class _NoopScheduleService:
    def lease_due_schedules(self, **kwargs: object) -> list[object]:
        return []


class _NoopAgentFacade:
    def __init__(self) -> None:
        self.schedule_agent = SimpleNamespace(service=_NoopScheduleService())

    def get_schedule_agent(self) -> object:
        return self.schedule_agent


class _CoordinatorRuntime:
    def __init__(self) -> None:
        self.agent = _NoopAgentFacade()
        self.default_thread_id = "demo-thread"

    def set_thread_event_sink(self, sink: object) -> None:
        return None


class _FailingUserAccess:
    def list_usernames(self) -> list[str]:
        raise RuntimeError("account store unavailable")


def _context(*targets: HeartbeatTarget) -> HeartbeatContext:
    return HeartbeatContext(
        beat_started_at="2026-08-11T00:00:00Z",
        targets=tuple(targets),
    )


def _observation(*, idempotency_key: str | None) -> Observation:
    return Observation(
        source="mailbox",
        type="observation_trigger",
        thread_id="thread-1",
        conversation_id="thread-1::0",
        occurred_at="2026-08-11T00:00:00Z",
        observed_at="2026-08-11T00:00:01Z",
        idempotency_key=idempotency_key,
        text="new mail",
    )


def test_monitor_registry_preserves_order_rejects_duplicates_and_unregisters() -> None:
    calls: list[str] = []
    first = _RecordingMonitor("first", calls)
    second = _RecordingMonitor("second", calls)
    registry = HeartbeatMonitorRegistry([first, second])

    assert registry.names() == ["first", "second"]
    assert registry.snapshot() == (first, second)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_RecordingMonitor("first", calls))

    assert registry.names() == ["first", "second"]
    assert registry.unregister("first") is first
    assert registry.names() == ["second"]
    assert registry.unregister("missing") is None

    registry.protect("second")
    with pytest.raises(ValueError, match="protected"):
        registry.unregister("second")
    with pytest.raises(ValueError, match="protected"):
        registry.register(_RecordingMonitor("second", calls), replace=True)


def test_observation_trigger_ingests_via_runtime_host_and_requires_idempotency_key() -> None:
    runtime_host = _RuntimeHostFake()
    target = HeartbeatTarget(
        owner_id="alice",
        service_runtime=SimpleNamespace(
            runtime_host=runtime_host,
            default_thread_id="thread-1",
        ),
    )
    valid_observation = target.build_observation_trigger(
        monitor_id="mailbox",
        source="mailbox",
        source_event_id="event-1",
        occurred_at="2026-08-11T00:00:00Z",
        observed_at="2026-08-11T00:00:01Z",
        text="new mail",
    )
    replay_observation = target.build_observation_trigger(
        monitor_id="mailbox",
        source="mailbox",
        source_event_id="event-1",
        occurred_at="2026-08-11T00:00:00Z",
        observed_at="2026-08-11T00:00:01Z",
    )
    assert replay_observation.idempotency_key == valid_observation.idempotency_key
    other_thread_target = HeartbeatTarget(
        owner_id="alice",
        service_runtime=target.service_runtime,
        runtime_thread_id="thread-2",
    )
    other_thread_observation = other_thread_target.build_observation_trigger(
        monitor_id="mailbox",
        source="mailbox",
        source_event_id="event-1",
        occurred_at="2026-08-11T00:00:00Z",
        observed_at="2026-08-11T00:00:01Z",
    )
    assert (
        other_thread_observation.idempotency_key
        != valid_observation.idempotency_key
    )
    acknowledgements: list[IngestResult] = []
    monitor = _StaticObservationMonitor(
        "mailbox",
        [
            ObservationTriggerDelivery(
                target=target,
                observation=valid_observation,
                schedule_drainer=False,
                on_ingested=acknowledgements.append,
            )
        ],
    )

    result = monitor.beat(_context(target))

    assert runtime_host.calls == [(valid_observation, False)]
    assert [item.stimulus_id for item in acknowledgements] == ["stimulus-1"]
    assert result == HeartbeatMonitorResult(
        observed=1,
        admitted=1,
        details={"stimulus_ids": ["stimulus-1"]},
    )

    missing_key_monitor = _StaticObservationMonitor(
        "missing-key",
        [
            ObservationTriggerDelivery(
                target=target,
                observation=_observation(idempotency_key=None),
            )
        ],
    )
    with pytest.raises(HeartbeatMonitorError, match="idempotency_key") as raised:
        missing_key_monitor.beat(_context(target))

    assert runtime_host.calls == [(valid_observation, False)]
    assert raised.value.result.observed == 1
    assert raised.value.result.failed == 1

    wrong_scope = Observation(
        **{
            **_observation(idempotency_key="mailbox:event-2").to_dict(),
            "thread_id": "alice::other-thread",
            "conversation_id": "alice::other-thread::0",
        }
    )
    wrong_scope_monitor = _StaticObservationMonitor(
        "wrong-scope",
        [ObservationTriggerDelivery(target=target, observation=wrong_scope)],
    )
    with pytest.raises(HeartbeatMonitorError, match="outside"):
        wrong_scope_monitor.beat(_context(target))

    assert runtime_host.calls == [(valid_observation, False)]


def test_monitor_failure_does_not_block_later_monitors() -> None:
    calls: list[str] = []
    registry = HeartbeatMonitorRegistry(
        [
            _RecordingMonitor(
                "failing",
                calls,
                error=RuntimeError("injected failure"),
            ),
            _RecordingMonitor(
                "following",
                calls,
                result=HeartbeatMonitorResult(observed=2, admitted=1),
            ),
        ]
    )

    report = registry.beat(_context())

    assert calls == ["failing", "following"]
    assert report["succeeded"] == 1
    assert report["failed"] == 1
    assert report["errors"] == {"failing": "injected failure"}
    assert report["results"]["following"]["observed"] == 2


def test_invalid_monitor_result_does_not_escape_registry_isolation() -> None:
    calls: list[str] = []
    malformed = HeartbeatMonitorResult(observed="not-an-int")  # type: ignore[arg-type]
    registry = HeartbeatMonitorRegistry(
        [
            _RecordingMonitor("malformed", calls, result=malformed),
            _RecordingMonitor("following", calls),
        ]
    )

    report = registry.beat(_context())

    assert calls == ["malformed", "following"]
    assert report["failed"] == 1
    assert report["succeeded"] == 1
    assert "malformed" in report["errors"]


def test_schedule_coordinator_runs_custom_monitor_and_exposes_monitor_health() -> None:
    runtime = _CoordinatorRuntime()
    calls: list[str] = []
    custom = _RecordingMonitor(
        "custom",
        calls,
        result=HeartbeatMonitorResult(observed=2, admitted=1),
    )
    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        monitors=[custom],
        autostart=False,
    )

    result = coordinator.beat_once()
    health = coordinator.health_payload()["monitors"]

    assert calls == ["custom"]
    assert len(custom.contexts) == 1
    assert custom.contexts[0].targets[0].owner_id == ANONYMOUS_OWNER_ID
    assert custom.contexts[0].targets[0].service_runtime is runtime
    assert result["monitors"]["results"]["custom"]["observed"] == 2
    assert health["registered"] == ["schedule", "custom"]
    assert health["monitors"]["custom"]["status"] == "healthy"
    assert health["monitors"]["custom"]["counters"]["beats_total"] == 1
    assert health["monitors"]["custom"]["counters"]["observed_total"] == 2


def test_monitor_group_reconcile_waits_for_in_flight_beat_and_swaps_as_one_group() -> None:
    runtime = _CoordinatorRuntime()
    blocking = _BlockingMonitor("gmail_email")
    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        monitors=[blocking],
        autostart=False,
    )
    calls: list[str] = []
    replacement_gmail = _RecordingMonitor("gmail_email", calls)
    replacement_arxiv = _RecordingMonitor("arxiv", calls)
    committed = threading.Event()
    reconciled = threading.Event()

    beat_thread = threading.Thread(target=coordinator.beat_once)
    beat_thread.start()
    assert blocking.started.wait(timeout=1.0)

    def reconcile() -> None:
        coordinator.reconcile_monitor_group(
            managed_ids={"gmail_email", "arxiv"},
            monitors=[replacement_gmail, replacement_arxiv],
            commit=committed.set,
        )
        reconciled.set()

    reconcile_thread = threading.Thread(target=reconcile)
    reconcile_thread.start()
    assert not committed.wait(timeout=0.05)
    assert coordinator.monitor_registry.get("gmail_email") is blocking

    blocking.release.set()
    beat_thread.join(timeout=1.0)
    assert committed.wait(timeout=1.0)
    assert reconciled.wait(timeout=1.0)
    reconcile_thread.join(timeout=1.0)
    assert coordinator.monitor_registry.get("gmail_email") is replacement_gmail
    assert coordinator.monitor_registry.get("arxiv") is replacement_arxiv
    assert coordinator.monitor_registry.names() == [
        "schedule",
        "gmail_email",
        "arxiv",
    ]


def test_monitor_group_reconcile_restores_old_group_when_commit_fails() -> None:
    runtime = _CoordinatorRuntime()
    calls: list[str] = []
    previous = _RecordingMonitor("gmail_email", calls)
    replacement = _RecordingMonitor("gmail_email", calls)
    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        monitors=[previous],
        autostart=False,
    )

    def fail_commit() -> None:
        raise OSError("settings disk is full")

    with pytest.raises(OSError, match="settings disk is full"):
        coordinator.reconcile_monitor_group(
            managed_ids={"gmail_email", "arxiv"},
            monitors=[replacement],
            commit=fail_commit,
        )

    assert coordinator.monitor_registry.get("gmail_email") is previous
    assert coordinator.monitor_registry.get("arxiv") is None


def test_registry_group_restore_preserves_interleaved_monitor_order() -> None:
    calls: list[str] = []
    gmail = _RecordingMonitor("gmail_email", calls)
    custom = _RecordingMonitor("custom", calls)
    arxiv = _RecordingMonitor("arxiv", calls)
    registry = HeartbeatMonitorRegistry([gmail, custom, arxiv])

    snapshot = registry.replace_group(
        managed_ids={"gmail_email", "arxiv"},
        monitors=[_RecordingMonitor("gmail_email", calls)],
    )
    registry.restore_group(snapshot)

    assert registry.names() == ["gmail_email", "custom", "arxiv"]
    assert registry.get("gmail_email") is gmail
    assert registry.get("arxiv") is arxiv


def test_shutdown_waits_for_manual_in_flight_beat() -> None:
    runtime = _CoordinatorRuntime()
    blocking = _BlockingMonitor()
    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        monitors=[blocking],
        autostart=False,
    )
    beat_errors: list[Exception] = []
    shutdown_results: list[bool] = []
    shutdown_done = threading.Event()

    def run_beat() -> None:
        try:
            coordinator.beat_once()
        except Exception as exc:  # pragma: no cover - assertion captures it
            beat_errors.append(exc)

    def run_shutdown() -> None:
        shutdown_results.append(coordinator.shutdown())
        shutdown_done.set()

    beat_thread = threading.Thread(target=run_beat)
    beat_thread.start()
    assert blocking.started.wait(timeout=1.0)
    shutdown_thread = threading.Thread(target=run_shutdown)
    shutdown_thread.start()

    assert not shutdown_done.wait(timeout=0.05)
    blocking.release.set()
    assert shutdown_done.wait(timeout=1.0)
    beat_thread.join(timeout=1.0)
    shutdown_thread.join(timeout=1.0)

    assert beat_errors == []
    assert shutdown_results == [True]
    with pytest.raises(RuntimeError, match="not accepting beats"):
        coordinator.beat_once()


def test_user_target_failure_keeps_anonymous_monitors_running() -> None:
    runtime = _CoordinatorRuntime()
    calls: list[str] = []
    custom = _RecordingMonitor("custom", calls)
    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        user_access=_FailingUserAccess(),  # type: ignore[arg-type]
        monitors=[custom],
        autostart=False,
    )

    result = coordinator.beat_once()
    health = coordinator.health_payload()

    assert calls == ["custom"]
    assert result["monitors"]["target_errors"] == [
        "user target enumeration failed: account store unavailable"
    ]
    assert health["last_error"] == (
        "user target enumeration failed: account store unavailable"
    )
