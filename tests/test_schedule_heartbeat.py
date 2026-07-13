from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from m_agent.agents.schedule_agent import ScheduleAgent
from m_agent.api.chat_api_shared import _get_thread_lock
from m_agent.api.schedule_heartbeat import ScheduleHeartbeatCoordinator
from m_agent.schedule.models import ScheduleItem
from m_agent.schedule.store import ANONYMOUS_OWNER_ID


class _DummyAgent:
    def __init__(self, schedule_agent: ScheduleAgent) -> None:
        self._schedule_agent = schedule_agent

    def get_schedule_agent(self) -> ScheduleAgent:
        return self._schedule_agent


class _ThinkLifeFake:
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
        self.think_life = _ThinkLifeFake()
        self._thread_event_sink = None

    def set_thread_event_sink(self, sink) -> None:
        self._thread_event_sink = sink

    @staticmethod
    def _schedule_prompt(schedule_item: ScheduleItem) -> str:
        return str(schedule_item.action_payload.get("prompt") or schedule_item.title)

    @staticmethod
    def _schedule_system_context(schedule_item: ScheduleItem) -> dict[str, Any]:
        return {
            "trigger_source": "schedule",
            "schedule_id": schedule_item.schedule_id,
            "action_payload": dict(schedule_item.action_payload),
        }

    @staticmethod
    def _get_or_create_thread(thread_id: str) -> SimpleNamespace:
        return SimpleNamespace(conversation_id=f"conversation-{thread_id}")

    def shutdown(self) -> None:
        return None


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
                    "target_candidate_limit": 5,
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
        title="team sync",
        due_at_utc=_past_due_iso(),
        timezone_name="Asia/Shanghai",
        original_time_text="now",
        action_type="chat_prompt",
        action_payload={"prompt": "team sync"},
        source_text="team sync",
        metadata={},
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
    assert len(runtime.think_life.enqueued_schedules) == 1
    queued = runtime.think_life.enqueued_schedules[0]
    assert queued["thread_id"] == "demo-thread"
    assert queued["conversation_id"] == "conversation-demo-thread"
    assert queued["schedule_id"] == created.schedule_id
    assert queued["text"] == "team sync"
    assert queued["payload"]["trigger_source"] == "schedule"
    assert result["leased"] == 1
    assert result["started"] == 1
    assert result["completed"] == 0
    assert result["failed"] == 0
    event_types = [event_type for _, event_type, _ in events]
    assert "schedule_due" in event_types
    assert "schedule_queued" in event_types
    assert "schedule_started" not in event_types
    assert "schedule_completed" not in event_types


def test_heartbeat_enqueues_while_thread_lock_is_held(tmp_path: Path) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    runtime = _HeartbeatRuntime(schedule_agent, config_path=tmp_path / "chat.yaml")
    events: list[tuple[str, str, dict[str, Any]]] = []
    created = schedule_agent.service.create_schedule(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        title="lock-safe enqueue",
        due_at_utc=_past_due_iso(),
        timezone_name="Asia/Shanghai",
        original_time_text="now",
        action_type="chat_prompt",
        action_payload={"prompt": "lock-safe enqueue"},
        source_text="lock-safe enqueue",
        metadata={},
    )

    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        user_access=None,
        thread_event_sink=lambda thread_id, event_type, payload: events.append((thread_id, event_type, dict(payload))),
        autostart=False,
        enqueue_retry_seconds=7,
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
    assert len(runtime.think_life.enqueued_schedules) == 1
    assert runtime.think_life.enqueued_schedules[0]["schedule_id"] == created.schedule_id
    assert result["started"] == 1
    assert result["completed"] == 0
    event_types = [event_type for _, event_type, _ in events]
    assert "schedule_due" in event_types
    assert "schedule_queued" in event_types
