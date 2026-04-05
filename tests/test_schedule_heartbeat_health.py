from __future__ import annotations

from pathlib import Path

import yaml

from m_agent.agents.schedule_agent import ScheduleAgent
from m_agent.api.schedule_heartbeat import ScheduleHeartbeatCoordinator


class _DummyAgent:
    def __init__(self, schedule_agent: ScheduleAgent) -> None:
        self._schedule_agent = schedule_agent

    def get_schedule_agent(self) -> ScheduleAgent:
        return self._schedule_agent


class _DummyRuntime:
    def __init__(self, schedule_agent: ScheduleAgent, *, config_path: Path) -> None:
        self.config_path = config_path
        self.default_thread_id = "demo-thread"
        self.agent = _DummyAgent(schedule_agent)

    def set_thread_event_sink(self, sink) -> None:
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
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return ScheduleAgent(config_path=config_path)


def test_schedule_heartbeat_next_due_falls_back_to_last_started_and_clears_error(tmp_path: Path) -> None:
    runtime = _DummyRuntime(_build_schedule_agent(tmp_path), config_path=tmp_path / "chat.yaml")
    coordinator = ScheduleHeartbeatCoordinator(
        service_runtime=runtime,
        user_access=None,
        autostart=False,
        beat_interval_seconds=10,
    )
    coordinator._last_beat_started_at = "2026-04-05T05:33:15Z"
    coordinator._last_error = "example"

    assert coordinator._next_beat_due_at() == "2026-04-05T05:33:25Z"

    result = coordinator.beat_once()
    health = coordinator.health_payload()

    assert result["beat_finished_at"]
    assert health["last_error"] is None
    assert health["next_beat_due_at"]
