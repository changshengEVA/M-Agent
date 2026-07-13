from __future__ import annotations

from fastapi.testclient import TestClient

from tests.fixtures.app_factory import build_test_app, build_test_runtime


def test_healthz_includes_runtime_profile() -> None:
    runtime = build_test_runtime()
    client = TestClient(build_test_app(service_runtime=runtime))
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["runtime"]["runtime_profile"] == "think_life"
    assert body["runtime"]["think_life"]["pending_stimuli_total"] == 0


def test_stimuli_endpoint_returns_202_for_think_life() -> None:
    runtime = build_test_runtime()
    client = TestClient(build_test_app(service_runtime=runtime))
    response = client.post(
        "/v1/chat/threads/demo-thread/stimuli",
        json={"kind": "user_message", "text": "hello while busy"},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["stimulus_id"]
    assert payload["pending_count"] >= 1


def test_transactions_endpoint_for_think_life() -> None:
    runtime = build_test_runtime()
    client = TestClient(build_test_app(service_runtime=runtime))
    response = client.get("/v1/chat/threads/demo-thread/transactions")
    assert response.status_code == 200
    body = response.json()
    assert body["transaction_count"] == 0
    assert body["transactions"] == []


def test_schedule_heartbeat_includes_thread_runtime() -> None:
    runtime = build_test_runtime()
    client = TestClient(build_test_app(service_runtime=runtime))
    response = client.get("/v1/chat/threads/demo-thread/schedules/heartbeat")
    assert response.status_code == 200
    body = response.json()
    assert "thread_runtime" in body
    assert body["thread_runtime"]["runtime_profile"] == "think_life"
    assert "heartbeat" in body
    assert body["heartbeat"].get("status") in {"healthy", "degraded", "unhealthy"}
