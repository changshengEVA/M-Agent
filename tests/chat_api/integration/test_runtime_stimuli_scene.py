from __future__ import annotations

from fastapi.testclient import TestClient

from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE
from tests.fixtures.app_factory import build_test_app, build_test_runtime


def test_healthz_includes_langgraph_runtime_metrics() -> None:
    runtime = build_test_runtime()
    client = TestClient(build_test_app(service_runtime=runtime))
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["runtime"]["runtime_profile"] == LANGGRAPH_RUNTIME_ENGINE
    assert body["runtime"]["runtime_engine_id"] == LANGGRAPH_RUNTIME_ENGINE
    assert body["runtime"]["runtime"]["runtime_engine_id"] == (
        LANGGRAPH_RUNTIME_ENGINE
    )
    assert body["runtime"]["runtime"]["pending_stimuli_total"] == 0


def test_stimuli_endpoint_returns_202_for_runtime() -> None:
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
    assert payload["runtime_engine_id"] == LANGGRAPH_RUNTIME_ENGINE


def test_transactions_endpoint_returns_runtime_snapshot() -> None:
    runtime = build_test_runtime()
    client = TestClient(build_test_app(service_runtime=runtime))
    response = client.get("/v1/chat/threads/demo-thread/transactions")
    assert response.status_code == 200
    body = response.json()
    assert body["transaction_count"] == 0
    assert body["transactions"] == []
    assert body["conversation_id"] == "demo-thread::0"
    assert body["runtime_phase"] == "ready"
    assert body["effective_depth"] == 0


def test_transactions_default_to_current_conversation_with_audit_opt_in() -> None:
    runtime = build_test_runtime()
    runtime.seed_transaction(
        thread_id="demo-thread",
        transaction_id="tx-current",
    )
    runtime.seed_transaction(
        thread_id="demo-thread",
        transaction_id="tx-history",
        conversation_id="demo-thread::9",
    )
    client = TestClient(build_test_app(service_runtime=runtime))

    current = client.get("/v1/chat/threads/demo-thread/transactions")
    audit = client.get(
        "/v1/chat/threads/demo-thread/transactions?include_history=true"
    )

    assert current.status_code == 200
    assert current.json()["conversation_id"] == "demo-thread::0"
    assert {
        item["transaction_id"] for item in current.json()["transactions"]
    } == {"tx-current"}
    assert current.json()["audit_transaction_count"] == 2
    assert {
        item["transaction_id"] for item in audit.json()["transactions"]
    } == {"tx-current", "tx-history"}


def test_delete_transaction_requires_fences_and_replays_idempotently() -> None:
    runtime = build_test_runtime()
    runtime.seed_transaction(
        thread_id="demo-thread",
        transaction_id="tx-delete",
        revision=4,
    )
    client = TestClient(build_test_app(service_runtime=runtime))
    path = "/v1/chat/threads/demo-thread/transactions/tx-delete"

    assert client.delete(path).status_code == 428
    assert client.delete(path, headers={"If-Match": 'W/"4"'}).status_code == 400

    headers = {
        "If-Match": 'W/"4"',
        "Idempotency-Key": "delete-request-1",
    }
    deleted = client.delete(path, headers=headers)
    replay = client.delete(path, headers=headers)

    assert deleted.status_code == 200
    assert deleted.headers["etag"] == 'W/"5"'
    body = deleted.json()
    assert body["success"] is True
    assert body["outcome"] == "deleted"
    assert body["conversation_id"] == "demo-thread::0"
    assert body["transaction"]["lifecycle_status"] == "deleted"
    assert body["transaction"]["deleted_at"]
    assert body["transaction"]["revision"] == 5
    assert body["cleanup"]["cancelled_in_flight"] is False
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True

    stale = client.delete(
        path,
        headers={
            "If-Match": "4",
            "Idempotency-Key": "delete-request-2",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "revision_conflict"
    assert stale.json()["actual_revision"] == 5


def test_delete_transaction_hides_cross_thread_targets() -> None:
    runtime = build_test_runtime()
    runtime.seed_transaction(
        thread_id="other-thread",
        transaction_id="tx-other-thread",
        revision=2,
    )
    client = TestClient(build_test_app(service_runtime=runtime))

    response = client.delete(
        "/v1/chat/threads/demo-thread/transactions/tx-other-thread",
        headers={
            "If-Match": "2",
            "Idempotency-Key": "delete-cross-thread",
        },
    )

    assert response.status_code == 404


def test_delete_transaction_is_limited_to_current_conversation() -> None:
    runtime = build_test_runtime()
    runtime.seed_transaction(
        thread_id="demo-thread",
        conversation_id="demo-thread::9",
        transaction_id="tx-old-conversation",
        revision=2,
    )
    client = TestClient(build_test_app(service_runtime=runtime))

    response = client.delete(
        "/v1/chat/threads/demo-thread/transactions/tx-old-conversation",
        headers={
            "If-Match": "2",
            "Idempotency-Key": "delete-old-conversation",
        },
    )

    assert response.status_code == 404


def test_schedule_heartbeat_includes_thread_runtime() -> None:
    runtime = build_test_runtime()
    client = TestClient(build_test_app(service_runtime=runtime))
    response = client.get("/v1/chat/threads/demo-thread/schedules/heartbeat")
    assert response.status_code == 200
    body = response.json()
    assert "thread_runtime" in body
    assert body["thread_runtime"]["runtime_profile"] == (
        LANGGRAPH_RUNTIME_ENGINE
    )
    assert "heartbeat" in body
    assert body["heartbeat"].get("status") in {"healthy", "degraded", "unhealthy"}
