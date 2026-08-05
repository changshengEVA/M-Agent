from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from m_agent.agents.schedule_agent import ScheduleAgent
from m_agent.api.chat_api_shared import _get_thread_lock
from m_agent.api.chat_api_runtime import ChatServiceRuntime
from m_agent.api.schedule_heartbeat import ScheduleHeartbeatCoordinator
from m_agent.runtime.transaction import (
    stable_schedule_delivery_id,
    stable_schedule_run_id,
)
from m_agent.schedule.models import ScheduleItem
from m_agent.schedule.service import ScheduleLeaseConflictError
from m_agent.schedule.store import ANONYMOUS_OWNER_ID


class _DummyAgent:
    def __init__(self, schedule_agent: ScheduleAgent) -> None:
        self._schedule_agent = schedule_agent

    def get_schedule_agent(self) -> ScheduleAgent:
        return self._schedule_agent


class _RuntimeHostFake:
    def __init__(
        self,
        *,
        failures_before_success: int = 0,
        dedupe_deliveries: bool = False,
    ) -> None:
        self.enqueued_schedules: list[dict[str, Any]] = []
        self.enqueue_attempts = 0
        self.failures_before_success = max(0, int(failures_before_success))
        self.dedupe_deliveries = bool(dedupe_deliveries)
        self._responses_by_delivery: dict[str, dict[str, Any]] = {}

    def enqueue_schedule(
        self,
        *,
        thread_id: str,
        conversation_id: str,
        schedule_id: str,
        text: str,
        payload: dict[str, Any],
        run_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        self.enqueue_attempts += 1
        if self.enqueue_attempts <= self.failures_before_success:
            raise RuntimeError("injected enqueue failure")
        delivery_id = str(payload.get("schedule_delivery_id", "") or "")
        if self.dedupe_deliveries and delivery_id in self._responses_by_delivery:
            return dict(self._responses_by_delivery[delivery_id])
        queued = {
            "thread_id": thread_id,
            "conversation_id": conversation_id,
            "schedule_id": schedule_id,
            "text": text,
            "payload": dict(payload),
            "run_id": run_id,
            "owner_id": owner_id,
        }
        self.enqueued_schedules.append(queued)
        response = {
            "accepted": True,
            "stimulus_id": f"stimulus-{len(self.enqueued_schedules)}",
            "pending_count": len(self.enqueued_schedules),
            "runtime_phase": "ready",
        }
        if delivery_id:
            self._responses_by_delivery[delivery_id] = dict(response)
        return response


class _HeartbeatRuntime:
    def __init__(self, schedule_agent: ScheduleAgent, *, config_path: Path) -> None:
        self.config_path = config_path
        self.default_thread_id = "demo-thread"
        self.agent = _DummyAgent(schedule_agent)
        self.runtime_host = _RuntimeHostFake()
        self._thread_event_sink = None

    def set_thread_event_sink(self, sink) -> None:
        self._thread_event_sink = sink

    @staticmethod
    def _schedule_prompt(schedule_item: ScheduleItem) -> str:
        return ChatServiceRuntime._schedule_prompt(schedule_item)

    @staticmethod
    def _schedule_system_context(schedule_item: ScheduleItem) -> dict[str, Any]:
        return ChatServiceRuntime._schedule_system_context(schedule_item)

    @staticmethod
    def _get_or_create_thread(thread_id: str) -> SimpleNamespace:
        return SimpleNamespace(conversation_id=f"conversation-{thread_id}")

    def shutdown(self) -> None:
        return None


class _HeartbeatUserAccess:
    def __init__(self, runtime: _HeartbeatRuntime) -> None:
        self.runtime = runtime
        self.user = SimpleNamespace(
            username="alice",
            canonical_thread_id="alice-thread",
        )

    def list_usernames(self) -> list[str]:
        return ["alice"]

    def get_user(self, *, username: str):
        return self.user if username == "alice" else None

    def get_runtime(self, *, user):
        assert user is self.user
        return self.runtime


def _build_schedule_agent(tmp_path: Path) -> ScheduleAgent:
    config_path = tmp_path / "schedule_agent.yaml"
    storage_dir = tmp_path / "schedule-data"
    config_path.write_text(
        yaml.safe_dump(
            {
                "provider": "local_schedule",
                "default_timezone_name": "Asia/Shanghai",
                "storage_dir": str(storage_dir),
                "execution": {
                    "query_limit_default": 10,
                    "query_limit_max": 50,
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return ScheduleAgent(config_path=config_path)


def _past_due_iso(minutes: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_heartbeat_queues_due_schedule(tmp_path: Path) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    runtime = _HeartbeatRuntime(schedule_agent, config_path=tmp_path / "chat.yaml")
    events: list[tuple[str, str, dict[str, Any]]] = []
    created = schedule_agent.service.create_schedule(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        due_at_utc=_past_due_iso(),
        timezone_name="Asia/Shanghai",
        deferred_objective="Remind the user to prepare for the team sync.",
    )

    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        user_access=None,
        thread_event_sink=lambda thread_id, event_type, payload: events.append((thread_id, event_type, dict(payload))),
        autostart=False,
    )

    result = coordinator.beat_once()

    updated = schedule_agent.store.find_by_id(created.schedule_id, owner_id=ANONYMOUS_OWNER_ID)
    assert updated is not None
    assert updated.status == "leased"
    assert len(runtime.runtime_host.enqueued_schedules) == 1
    queued = runtime.runtime_host.enqueued_schedules[0]
    assert queued["thread_id"] == "demo-thread"
    assert queued["conversation_id"] == "conversation-demo-thread"
    assert queued["schedule_id"] == created.schedule_id
    assert queued["text"] == "schedule_due"
    assert queued["payload"]["trigger_source"] == "schedule"
    assert queued["payload"]["semantic_frame_version"] == 1
    activation = queued["payload"]["activation"]
    assert activation["event"]["role"] == "activation_event"
    assert activation["event"]["type"] == "schedule_due"
    assert activation["event"]["facts"]["due_at_utc"] == created.due_at_utc
    assert activation["objective"] == {
        "description": "Remind the user to prepare for the team sync.",
        "encoding": "native",
        "role": "deferred_objective",
    }
    assert activation["evidence"] == []
    assert queued["text"] != activation["objective"]["description"]
    assert result["leased"] == 1
    assert result["started"] == 1
    assert result["completed"] == 0
    assert result["failed"] == 0
    event_types = [event_type for _, event_type, _ in events]
    assert "schedule_due" in event_types
    assert "schedule_queued" in event_types
    assert "schedule_started" not in event_types
    assert "schedule_completed" not in event_types


def test_heartbeat_reenters_origin_transaction_with_stable_delivery(
    tmp_path: Path,
) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    runtime = _HeartbeatRuntime(
        schedule_agent,
        config_path=tmp_path / "chat.yaml",
    )
    created = schedule_agent.service.create_schedule(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        due_at_utc=_past_due_iso(),
        timezone_name="Asia/Shanghai",
        deferred_objective="Resume the original task.",
        origin={
            "transaction_id": "txn-origin",
            "conversation_id": "demo-thread::7",
        },
    )
    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        user_access=None,
        autostart=False,
    )

    result = coordinator.beat_once()

    assert result["started"] == 1
    queued = runtime.runtime_host.enqueued_schedules[0]
    expected_run_id = stable_schedule_run_id(
        created.schedule_id,
        "txn-origin",
        created.due_at_utc,
    )
    assert queued["conversation_id"] == "demo-thread::7"
    assert queued["run_id"] == expected_run_id
    assert queued["payload"]["transaction_id"] == "txn-origin"
    assert queued["payload"]["schedule_run_id"] == expected_run_id
    assert queued["payload"]["schedule_delivery_id"] == (
        stable_schedule_delivery_id(expected_run_id, 1)
    )


def test_authenticated_heartbeat_routes_legacy_schedule_to_canonical_thread(tmp_path: Path) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    runtime = _HeartbeatRuntime(schedule_agent, config_path=tmp_path / "chat.yaml")
    user_access = _HeartbeatUserAccess(runtime)
    events: list[tuple[str, str, dict[str, Any]]] = []
    created = schedule_agent.service.create_schedule(
        owner_id="alice",
        thread_id="alice::client-supplied-thread",
        due_at_utc=_past_due_iso(),
        timezone_name="Asia/Shanghai",
        text="Route this legacy reminder through the account's canonical thread.",
    )
    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        user_access=user_access,
        thread_event_sink=lambda thread_id, event_type, payload: events.append(
            (thread_id, event_type, dict(payload))
        ),
        autostart=False,
    )

    result = coordinator.beat_once()

    assert result["leased"] == 1
    assert result["started"] == 1
    assert len(runtime.runtime_host.enqueued_schedules) == 1
    queued = runtime.runtime_host.enqueued_schedules[0]
    assert queued["thread_id"] == "alice::alice-thread"
    assert queued["conversation_id"] == "conversation-alice::alice-thread"
    assert {thread_id for thread_id, _, _ in events} == {"alice::alice-thread"}
    assert {payload["thread_id"] for _, _, payload in events} == {"alice-thread"}
    stored = schedule_agent.store.find_by_id(created.schedule_id, owner_id="alice")
    assert stored is not None
    assert stored.thread_id == "alice::client-supplied-thread"
    assert stored.status == "leased"
    queued_activation = queued["payload"]["activation"]
    assert queued_activation["objective"]["encoding"] == "legacy_text"
    assert queued_activation["evidence"] == []


def test_heartbeat_enqueues_while_thread_lock_is_held(tmp_path: Path) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    runtime = _HeartbeatRuntime(schedule_agent, config_path=tmp_path / "chat.yaml")
    events: list[tuple[str, str, dict[str, Any]]] = []
    created = schedule_agent.service.create_schedule(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        due_at_utc=_past_due_iso(),
        timezone_name="Asia/Shanghai",
        text="The scheduled lock-safe enqueue check is now due.",
    )

    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        user_access=None,
        thread_event_sink=lambda thread_id, event_type, payload: events.append((thread_id, event_type, dict(payload))),
        autostart=False,
    )

    thread_lock = _get_thread_lock("demo-thread")
    assert thread_lock.acquire(blocking=False)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        result = executor.submit(coordinator.beat_once).result(timeout=2.0)
    finally:
        thread_lock.release()
        executor.shutdown(wait=True)

    updated = schedule_agent.store.find_by_id(created.schedule_id, owner_id=ANONYMOUS_OWNER_ID)
    assert updated is not None
    assert updated.status == "leased"
    assert len(runtime.runtime_host.enqueued_schedules) == 1
    assert runtime.runtime_host.enqueued_schedules[0]["schedule_id"] == created.schedule_id
    assert result["started"] == 1
    assert result["completed"] == 0
    event_types = [event_type for _, event_type, _ in events]
    assert "schedule_due" in event_types
    assert "schedule_queued" in event_types


def test_lease_metadata_persists_and_expired_lease_is_reclaimed(
    tmp_path: Path,
) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    now = datetime.now(timezone.utc)
    created = schedule_agent.service.create_schedule(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        due_at_utc=_utc_iso(now - timedelta(minutes=1)),
        timezone_name="UTC",
        deferred_objective="Recover this schedule after a worker crash.",
    )

    first = schedule_agent.service.lease_due_schedules(
        owner_id=ANONYMOUS_OWNER_ID,
        now_utc=_utc_iso(now - timedelta(seconds=5)),
        lease_owner="worker-before-crash",
        lease_seconds=1,
    )
    assert [item.schedule_id for item in first] == [created.schedule_id]
    first_token = first[0].lease_token
    assert first_token.startswith("lease_")
    assert first[0].lease_owner == "worker-before-crash"
    assert first[0].attempt == 1

    persisted = schedule_agent.store.find_by_id(
        created.schedule_id,
        owner_id=ANONYMOUS_OWNER_ID,
    )
    assert persisted is not None
    assert persisted.lease_token == first_token
    assert persisted.lease_until == _utc_iso(now - timedelta(seconds=4))

    second = schedule_agent.service.lease_due_schedules(
        owner_id=ANONYMOUS_OWNER_ID,
        now_utc=_utc_iso(now),
        lease_owner="worker-after-restart",
        lease_seconds=30,
    )
    assert [item.schedule_id for item in second] == [created.schedule_id]
    assert second[0].lease_token != first_token
    assert second[0].lease_owner == "worker-after-restart"
    assert second[0].attempt == 2

    not_expired = schedule_agent.service.lease_due_schedules(
        owner_id=ANONYMOUS_OWNER_ID,
        now_utc=_utc_iso(now + timedelta(seconds=1)),
        lease_owner="worker-three",
        lease_seconds=30,
    )
    assert not_expired == []


def test_concurrent_store_instances_only_grant_one_live_lease(
    tmp_path: Path,
) -> None:
    first_agent = _build_schedule_agent(tmp_path)
    second_agent = _build_schedule_agent(tmp_path)
    now = datetime.now(timezone.utc)
    created = first_agent.service.create_schedule(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        due_at_utc=_utc_iso(now - timedelta(minutes=1)),
        timezone_name="UTC",
        deferred_objective="Grant exactly one concurrent lease.",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                agent.service.lease_due_schedules,
                owner_id=ANONYMOUS_OWNER_ID,
                now_utc=_utc_iso(now),
                lease_owner=f"worker-{index}",
                lease_seconds=30,
            )
            for index, agent in enumerate((first_agent, second_agent), start=1)
        ]
        claims = [future.result(timeout=2.0) for future in futures]

    granted = [item for claim in claims for item in claim]
    assert [item.schedule_id for item in granted] == [created.schedule_id]
    assert granted[0].lease_owner in {"worker-1", "worker-2"}


def test_legacy_leased_json_without_metadata_is_reclaimed_on_upgrade(
    tmp_path: Path,
) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    created = schedule_agent.service.create_schedule(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        due_at_utc=_past_due_iso(),
        timezone_name="UTC",
        deferred_objective="Recover a v2 lease.",
    )
    schedule_path = next(
        (schedule_agent.store.storage_root / "by_user").rglob("schedules.json")
    )
    payload = json.loads(schedule_path.read_text(encoding="utf-8"))
    legacy_item = payload["items"][0]
    legacy_item["schema_version"] = 2
    legacy_item["status"] = "leased"
    for field_name in (
        "lease_token",
        "lease_owner",
        "lease_until",
        "attempt",
        "last_error",
    ):
        legacy_item.pop(field_name, None)
    schedule_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    claimed = schedule_agent.service.lease_due_schedules(
        owner_id=ANONYMOUS_OWNER_ID,
        lease_owner="upgrade-worker",
        lease_seconds=30,
    )

    assert [item.schedule_id for item in claimed] == [created.schedule_id]
    assert claimed[0].schema_version == 2
    assert claimed[0].lease_owner == "upgrade-worker"
    assert claimed[0].lease_token.startswith("lease_")
    assert claimed[0].attempt == 1


def test_stale_lease_token_cannot_release_a_reclaimed_schedule(
    tmp_path: Path,
) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    now = datetime.now(timezone.utc)
    created = schedule_agent.service.create_schedule(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        due_at_utc=_utc_iso(now - timedelta(minutes=1)),
        timezone_name="UTC",
        deferred_objective="Keep the newer claim authoritative.",
    )
    first = schedule_agent.service.lease_due_schedules(
        owner_id=ANONYMOUS_OWNER_ID,
        now_utc=_utc_iso(now - timedelta(seconds=2)),
        lease_owner="stale-worker",
        lease_seconds=1,
    )[0]
    second = schedule_agent.service.lease_due_schedules(
        owner_id=ANONYMOUS_OWNER_ID,
        now_utc=_utc_iso(now),
        lease_owner="current-worker",
        lease_seconds=30,
    )[0]

    with pytest.raises(ScheduleLeaseConflictError):
        schedule_agent.service.release_lease(
            owner_id=ANONYMOUS_OWNER_ID,
            thread_id="demo-thread",
            schedule_id=created.schedule_id,
            lease_token=first.lease_token,
            error="late failure from stale worker",
        )

    stored = schedule_agent.store.find_by_id(
        created.schedule_id,
        owner_id=ANONYMOUS_OWNER_ID,
    )
    assert stored is not None
    assert stored.status == "leased"
    assert stored.lease_token == second.lease_token
    assert stored.lease_owner == "current-worker"
    assert stored.last_error == ""


def test_runtime_status_transitions_clear_lease_and_persist_last_error(
    tmp_path: Path,
) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    created = schedule_agent.service.create_schedule(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        due_at_utc=_past_due_iso(),
        timezone_name="UTC",
        deferred_objective="Persist the processing failure for diagnostics.",
    )
    claimed = schedule_agent.service.lease_due_schedules(
        owner_id=ANONYMOUS_OWNER_ID,
        lease_owner="runtime-worker",
        lease_seconds=30,
    )[0]

    running = schedule_agent.service.mark_running(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        schedule_id=created.schedule_id,
        lease_token=claimed.lease_token,
    )
    assert running.status == "running"
    assert running.lease_token == ""
    assert running.lease_owner == ""
    assert running.lease_until == ""
    assert running.attempt == 1

    failed = schedule_agent.service.mark_failed(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        schedule_id=created.schedule_id,
        error="runtime crashed while processing",
    )
    assert failed.status == "failed"
    assert failed.last_error == "runtime crashed while processing"
    persisted = schedule_agent.store.find_by_id(
        created.schedule_id,
        owner_id=ANONYMOUS_OWNER_ID,
    )
    assert persisted is not None
    assert persisted.last_error == "runtime crashed while processing"


def test_enqueue_failure_releases_lease_for_retry_and_records_error(
    tmp_path: Path,
) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    runtime = _HeartbeatRuntime(schedule_agent, config_path=tmp_path / "chat.yaml")
    runtime.runtime_host = _RuntimeHostFake(failures_before_success=1)
    created = schedule_agent.service.create_schedule(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        due_at_utc=_past_due_iso(),
        timezone_name="UTC",
        deferred_objective="Retry an interrupted runtime admission.",
    )
    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        user_access=None,
        lease_owner="failure-injection-worker",
        autostart=False,
    )

    failed = coordinator.beat_once()
    after_failure = schedule_agent.store.find_by_id(
        created.schedule_id,
        owner_id=ANONYMOUS_OWNER_ID,
    )
    assert failed["leased"] == 1
    assert failed["started"] == 0
    assert failed["failed"] == 1
    assert after_failure is not None
    assert after_failure.status == "pending"
    assert after_failure.lease_token == ""
    assert after_failure.lease_owner == ""
    assert after_failure.lease_until == ""
    assert after_failure.attempt == 1
    assert after_failure.last_error == "injected enqueue failure"

    retried = coordinator.beat_once()
    after_retry = schedule_agent.store.find_by_id(
        created.schedule_id,
        owner_id=ANONYMOUS_OWNER_ID,
    )
    assert retried["started"] == 1
    assert retried["failed"] == 0
    assert after_retry is not None
    assert after_retry.status == "leased"
    assert after_retry.attempt == 2
    assert after_retry.last_error == "injected enqueue failure"
    assert len(runtime.runtime_host.enqueued_schedules) == 1
    assert runtime.runtime_host.enqueued_schedules[0]["payload"][
        "schedule_attempt"
    ] == 2


def test_restart_replay_uses_stable_runtime_ingress_identity(
    tmp_path: Path,
) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    runtime = _HeartbeatRuntime(schedule_agent, config_path=tmp_path / "chat.yaml")
    runtime.runtime_host = _RuntimeHostFake(dedupe_deliveries=True)
    created = schedule_agent.service.create_schedule(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        due_at_utc=_past_due_iso(),
        timezone_name="UTC",
        deferred_objective="Do not duplicate this schedule after restart.",
    )
    first_coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        user_access=None,
        lease_duration_seconds=60,
        lease_owner="heartbeat-before-restart",
        autostart=False,
    )
    assert first_coordinator.beat_once()["started"] == 1
    first_stored = schedule_agent.store.find_by_id(
        created.schedule_id,
        owner_id=ANONYMOUS_OWNER_ID,
    )
    assert first_stored is not None
    first_token = first_stored.lease_token

    def expire_claim(items: list[ScheduleItem]) -> None:
        for item in items:
            if item.schedule_id == created.schedule_id:
                item.lease_until = _past_due_iso()

    schedule_agent.store.mutate_thread_items(
        ANONYMOUS_OWNER_ID,
        "demo-thread",
        expire_claim,
    )
    restarted_coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        user_access=None,
        lease_duration_seconds=60,
        lease_owner="heartbeat-after-restart",
        autostart=False,
    )
    replayed = restarted_coordinator.beat_once()

    assert replayed["leased"] == 1
    assert replayed["started"] == 1
    assert runtime.runtime_host.enqueue_attempts == 2
    assert len(runtime.runtime_host.enqueued_schedules) == 1
    queued = runtime.runtime_host.enqueued_schedules[0]
    expected_run_id = stable_schedule_run_id(
        created.schedule_id,
        f"external:{ANONYMOUS_OWNER_ID}:demo-thread",
        created.due_at_utc,
    )
    assert queued["run_id"] == expected_run_id
    assert queued["payload"]["schedule_delivery_id"] == (
        stable_schedule_delivery_id(expected_run_id, 1)
    )
    replayed_stored = schedule_agent.store.find_by_id(
        created.schedule_id,
        owner_id=ANONYMOUS_OWNER_ID,
    )
    assert replayed_stored is not None
    assert replayed_stored.lease_token != first_token
    assert replayed_stored.lease_owner == "heartbeat-after-restart"
    assert replayed_stored.attempt == 2
