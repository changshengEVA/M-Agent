from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from m_agent.agents.schedule_agent import ScheduleAgent
from m_agent.api.chat_api_web import create_app


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
        self._thread_event_sink = None

    def set_thread_event_sink(self, sink):
        self._thread_event_sink = sink

    def shutdown(self) -> None:
        return None

    def health_payload(self):
        return {
            "config_path": str(self.config_path),
            "default_thread_id": self.default_thread_id,
        }


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


def test_schedule_crud_endpoints(tmp_path: Path) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    runtime = _DummyRuntime(schedule_agent, config_path=tmp_path / "chat.yaml")
    app = create_app(service_runtime=runtime, user_access=None)

    with TestClient(app) as client:
        response = client.get("/v1/chat/threads/demo-thread/schedules")
        assert response.status_code == 200
        assert response.json()["count"] == 0

        created = client.post(
            "/v1/chat/threads/demo-thread/schedules",
            json={
                "title": "交周报",
                "prompt": "提醒我交周报",
                "due_at": "2026-04-06T09:30",
                "timezone_name": "Asia/Shanghai",
            },
        )
        assert created.status_code == 201
        created_item = created.json()["item"]
        assert created_item["thread_id"] == "demo-thread"
        assert created_item["title"] == "交周报"
        assert created_item["status"] == "pending"

        listed = client.get("/v1/chat/threads/demo-thread/schedules")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1

        schedule_id = created_item["schedule_id"]
        updated = client.patch(
            f"/v1/chat/threads/demo-thread/schedules/{schedule_id}",
            json={
                "title": "交项目周报",
                "due_at": "2026-04-06T10:15",
                "timezone_name": "Asia/Shanghai",
            },
        )
        assert updated.status_code == 200
        updated_item = updated.json()["item"]
        assert updated_item["title"] == "交项目周报"
        assert updated_item["due_display"] == "2026-04-06 10:15"

        detail = client.get(f"/v1/chat/threads/demo-thread/schedules/{schedule_id}")
        assert detail.status_code == 200
        assert detail.json()["item"]["schedule_id"] == schedule_id

        canceled = client.delete(f"/v1/chat/threads/demo-thread/schedules/{schedule_id}")
        assert canceled.status_code == 200
        assert canceled.json()["item"]["status"] == "canceled"

        active_after_cancel = client.get("/v1/chat/threads/demo-thread/schedules")
        assert active_after_cancel.status_code == 200
        assert active_after_cancel.json()["count"] == 0

        archived = client.get("/v1/chat/threads/demo-thread/schedules?include_completed=true")
        assert archived.status_code == 200
        assert archived.json()["count"] == 1
        assert archived.json()["items"][0]["status"] == "canceled"


def test_schedule_endpoints_share_items_across_threads_for_same_owner(tmp_path: Path) -> None:
    schedule_agent = _build_schedule_agent(tmp_path)
    runtime = _DummyRuntime(schedule_agent, config_path=tmp_path / "chat.yaml")
    app = create_app(service_runtime=runtime, user_access=None)

    with TestClient(app) as client:
        created = client.post(
            "/v1/chat/threads/work-thread/schedules",
            json={
                "title": "开组会",
                "prompt": "提醒我开组会",
                "due_at": "2026-04-06T14:00",
                "timezone_name": "Asia/Shanghai",
            },
        )
        assert created.status_code == 201
        schedule_id = created.json()["item"]["schedule_id"]

        listed_elsewhere = client.get("/v1/chat/threads/life-thread/schedules")
        assert listed_elsewhere.status_code == 200
        assert listed_elsewhere.json()["count"] == 1
        assert listed_elsewhere.json()["items"][0]["thread_id"] == "work-thread"

        canceled_elsewhere = client.delete(f"/v1/chat/threads/life-thread/schedules/{schedule_id}")
        assert canceled_elsewhere.status_code == 200
        assert canceled_elsewhere.json()["item"]["status"] == "canceled"
        assert canceled_elsewhere.json()["item"]["thread_id"] == "work-thread"
