from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.app_factory import build_test_app
from tests.fixtures.payload_builders import memory_flush_payload, run_payload
from tests.fixtures.sse_helpers import parse_sse_events


pytestmark = pytest.mark.e2e


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


def test_anonymous_chat_run_memory_flush_and_schedule_flow() -> None:
    app = build_test_app(auth_enabled=False)

    with TestClient(app) as client:
        created = client.post("/v1/chat/runs", json=run_payload(message="hello e2e", thread_id="e2e-thread"))
        assert created.status_code == 201
        run_id = created.json()["run_id"]
        snapshot = _wait_run_completed(client, run_id)
        assert snapshot["status"] == "completed"
        assert snapshot["result"]["answer"] == "echo:hello e2e"

        run_events_response = client.get(f"/v1/chat/runs/{run_id}/events?after_seq=0")
        assert run_events_response.status_code == 200
        events = parse_sse_events(run_events_response.text.splitlines())
        event_types = [event.get("event") for event in events]
        assert event_types[0] == "run_started"
        assert "assistant_message" in event_types
        assert event_types[-1] == "run_completed"

        state_before = client.get("/v1/chat/threads/e2e-thread/memory/state")
        assert state_before.status_code == 200
        assert state_before.json()["pending_rounds"] == 1

        flushed = client.post(
            "/v1/chat/threads/e2e-thread/memory/flush",
            json=memory_flush_payload(reason="e2e_manual_flush"),
        )
        assert flushed.status_code == 200
        assert flushed.json()["success"] is True

        schedule_created = client.post(
            "/v1/chat/threads/e2e-thread/schedules",
            json={
                "title": "E2E reminder",
                "prompt": "Check e2e flow",
                "due_at": "2026-04-12T11:00",
                "timezone_name": "Asia/Shanghai",
            },
        )
        assert schedule_created.status_code == 201

        listed = client.get("/v1/chat/threads/e2e-thread/schedules")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
