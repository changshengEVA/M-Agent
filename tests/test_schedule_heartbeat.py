from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
from m_agent.schedule.store import ANONYMOUS_OWNER_ID


class _DummyAgent:
    def __init__(self, schedule_agent: ScheduleAgent) -> None:
        self._schedule_agent = schedule_agent

    def get_schedule_agent(self) -> ScheduleAgent:
        return self._schedule_agent


class _RuntimeHostFake:
    def __init__(self) -> None:
        self.enqueued_schedules: list[dict[str, Any]] = []

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
        return {
            "accepted": True,
            "stimulus_id": f"stimulus-{len(self.enqueued_schedules)}",
            "pending_count": len(self.enqueued_schedules),
            "runtime_phase": "ready",
        }


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
