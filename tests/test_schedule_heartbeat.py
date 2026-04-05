from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from m_agent.agents.schedule_agent import ScheduleAgent
from m_agent.api.chat_api_shared import _get_thread_lock
from m_agent.api.schedule_heartbeat import ScheduleHeartbeatCoordinator
from m_agent.schedule.store import ANONYMOUS_OWNER_ID


class _DummyAgent:
    def __init__(self, schedule_agent: ScheduleAgent) -> None:
        self._schedule_agent = schedule_agent

    def get_schedule_agent(self) -> ScheduleAgent:
        return self._schedule_agent


class _HeartbeatRuntime:
    def __init__(self, schedule_agent: ScheduleAgent, *, config_path: Path) -> None:
        self.config_path = config_path
        self.default_thread_id = "demo-thread"
        self.agent = _DummyAgent(schedule_agent)
        self._thread_event_sink = None
        self.triggered_schedule_ids: list[str] = []

    def set_thread_event_sink(self, sink) -> None:
        self._thread_event_sink = sink

    def run_schedule_trigger(self, *, schedule_item):
        self.triggered_schedule_ids.append(schedule_item.schedule_id)
        if self._thread_event_sink is not None:
            self._thread_event_sink(
                schedule_item.thread_id,
                "assistant_message",
                {
                    "thread_id": schedule_item.thread_id,
                    "answer": f"triggered:{schedule_item.title}",
                    "source": "schedule",
                    "schedule_id": schedule_item.schedule_id,
                },
            )
        return {
            "answer": f"triggered:{schedule_item.title}",
            "memory_capture": {
                "status": "skipped",
            },
        }

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


def test_heartbeat_executes_due_schedule(tmp_path: Path) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    runtime = _HeartbeatRuntime(schedule_agent, config_path=tmp_path / "chat.yaml")
    events: list[tuple[str, str, dict]] = []
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
    assert updated.status == "done"
    assert runtime.triggered_schedule_ids == [created.schedule_id]
    assert result["completed"] == 1
    event_types = [event_type for _, event_type, _ in events]
    assert "schedule_due" in event_types
    assert "schedule_started" in event_types
    assert "schedule_completed" in event_types


def test_heartbeat_releases_lease_when_thread_is_busy(tmp_path: Path) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    runtime = _HeartbeatRuntime(schedule_agent, config_path=tmp_path / "chat.yaml")
    events: list[tuple[str, str, dict]] = []
    created = schedule_agent.service.create_schedule(
        owner_id=ANONYMOUS_OWNER_ID,
        thread_id="demo-thread",
        title="busy retry",
        due_at_utc=_past_due_iso(),
        timezone_name="Asia/Shanghai",
        original_time_text="now",
        action_type="chat_prompt",
        action_payload={"prompt": "busy retry"},
        source_text="busy retry",
        metadata={},
    )

    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        user_access=None,
        thread_event_sink=lambda thread_id, event_type, payload: events.append((thread_id, event_type, dict(payload))),
        autostart=False,
        busy_retry_seconds=7,
    )

    thread_lock = _get_thread_lock("demo-thread")
    assert thread_lock.acquire(blocking=False)
    try:
        result = coordinator.beat_once()
    finally:
        thread_lock.release()

    updated = schedule_agent.store.find_by_id(created.schedule_id, owner_id=ANONYMOUS_OWNER_ID)
    assert updated is not None
    assert updated.status == "pending"
    assert updated.metadata.get("last_release_reason") == "thread_busy"
    assert updated.metadata.get("retry_after_utc")
    assert runtime.triggered_schedule_ids == []
    assert result["busy_retried"] == 1
    event_types = [event_type for _, event_type, _ in events]
    assert "schedule_due" in event_types
    assert "schedule_busy_retry" in event_types
