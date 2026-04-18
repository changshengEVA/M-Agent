from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.app_factory import build_test_app
from tests.fixtures.payload_builders import memory_flush_payload, memory_mode_payload, run_payload


pytestmark = pytest.mark.integration


def _wait_run_completed(client: TestClient, run_id: str) -> dict:
    deadline = time.time() + 2.0
    while time.time() < deadline:
        response = client.get(f"/v1/chat/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"run not completed within timeout: {run_id}")


def test_thread_memory_mode_and_flush_flow() -> None:
    app = build_test_app(auth_enabled=False)

    with TestClient(app) as client:
        created = client.post("/v1/chat/runs", json=run_payload(message="first message", thread_id="memory-thread"))
        assert created.status_code == 201
        run_id = created.json()["run_id"]
        _wait_run_completed(client, run_id)

        state_before = client.get("/v1/chat/threads/memory-thread/memory/state")
        assert state_before.status_code == 200
        assert state_before.json()["pending_rounds"] == 1

        switched = client.post(
            "/v1/chat/threads/memory-thread/memory/mode",
            json=memory_mode_payload(mode="off", discard_pending=True),
        )
        assert switched.status_code == 200
        switched_payload = switched.json()
        assert switched_payload["mode"] == "off"
        assert switched_payload["thread_state"]["pending_rounds"] == 0

        flushed = client.post(
            "/v1/chat/threads/memory-thread/memory/flush",
            json=memory_flush_payload(reason="test_flush"),
        )
        assert flushed.status_code == 200
        assert flushed.json()["status"] == "noop"


def test_schedule_crud_endpoints_with_fake_runtime() -> None:
    app = build_test_app(auth_enabled=False)

    with TestClient(app) as client:
        created = client.post(
            "/v1/chat/threads/schedule-thread/schedules",
            json={
                "title": "Weekly report",
                "prompt": "Remind me to submit weekly report",
                "due_at": "2026-04-12T09:30",
                "timezone_name": "Asia/Shanghai",
            },
        )
        assert created.status_code == 201
        created_item = created.json()["item"]
        schedule_id = created_item["schedule_id"]
        assert created_item["title"] == "Weekly report"

        listed = client.get("/v1/chat/threads/schedule-thread/schedules")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1

        updated = client.patch(
            f"/v1/chat/threads/schedule-thread/schedules/{schedule_id}",
            json={
                "title": "Project weekly report",
                "prompt": "Remind me project report",
                "due_at": "2026-04-12T10:30",
                "timezone_name": "Asia/Shanghai",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["item"]["title"] == "Project weekly report"

        detail = client.get(f"/v1/chat/threads/schedule-thread/schedules/{schedule_id}")
        assert detail.status_code == 200
        assert detail.json()["item"]["schedule_id"] == schedule_id

        canceled = client.delete(f"/v1/chat/threads/schedule-thread/schedules/{schedule_id}")
        assert canceled.status_code == 200
        assert canceled.json()["item"]["status"] == "canceled"

        archived = client.get("/v1/chat/threads/schedule-thread/schedules?include_completed=true")
        assert archived.status_code == 200
        assert archived.json()["count"] == 1
