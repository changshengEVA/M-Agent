from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.app_factory import build_test_app
from tests.fixtures.payload_builders import run_payload
from tests.fixtures.sse_helpers import parse_sse_events


pytestmark = pytest.mark.integration


def _wait_run_completed(client: TestClient, run_id: str, *, timeout_seconds: float = 2.0) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/v1/chat/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload.get("status") in {"completed", "failed"}:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"run not completed within {timeout_seconds} seconds: {run_id}")


def test_create_run_rejects_empty_message() -> None:
    app = build_test_app(auth_enabled=False)

    with TestClient(app) as client:
        response = client.post("/v1/chat/runs", json=run_payload(message="", thread_id="demo-thread"))

    assert response.status_code == 400
    assert response.json()["error"] == "message is empty"


def test_run_lifecycle_snapshot_and_event_stream() -> None:
    app = build_test_app(auth_enabled=False)

    with TestClient(app) as client:
        created = client.post("/v1/chat/runs", json=run_payload(message="hello", thread_id="demo-thread"))
        assert created.status_code == 201
        created_payload = created.json()
        run_id = created_payload["run_id"]
        assert created_payload["status"] in {"queued", "running", "completed"}
        assert created_payload["thread_id"] == "demo-thread"

        snapshot = _wait_run_completed(client, run_id)
        assert snapshot["status"] == "completed"
        assert snapshot["result"]["answer"] == "echo:hello"

        stream_response = client.get(f"/v1/chat/runs/{run_id}/events?after_seq=0")
        assert stream_response.status_code == 200
        assert stream_response.headers["content-type"].startswith("text/event-stream")
        events = parse_sse_events(stream_response.text.splitlines())
        event_types = [event["event"] for event in events]
        assert "run_started" in event_types
        assert "assistant_message" in event_types
        assert "run_completed" in event_types

        run_completed_event = [event for event in events if event["event"] == "run_completed"][-1]
        assert run_completed_event["data"]["payload"]["answer"] == "echo:hello"
