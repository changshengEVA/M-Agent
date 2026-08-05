from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from m_agent import __version__
from tests.fixtures.app_factory import build_test_app


pytestmark = pytest.mark.contract


def test_openapi_contains_core_chat_api_paths() -> None:
    app = build_test_app(auth_enabled=False)

    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    payload = response.json()
    assert payload["info"]["version"] == __version__
    paths = payload["paths"]

    expected_paths = {
        "/healthz",
        "/v1/chat/runs",
        "/v1/chat/runs/{run_id}",
        "/v1/chat/runs/{run_id}/events",
        "/v1/chat/threads/{thread_id}/memory/state",
        "/v1/chat/threads/{thread_id}/memory/mode",
        "/v1/chat/threads/{thread_id}/memory/flush",
        "/v1/chat/threads/{thread_id}/transactions",
        "/v1/chat/threads/{thread_id}/transactions/{transaction_id}",
        "/v1/chat/threads/{thread_id}/schedules",
        "/v1/chat/threads/{thread_id}/schedules/{schedule_id}",
    }
    assert expected_paths.issubset(set(paths.keys()))
    assert (
        "delete"
        in paths[
            "/v1/chat/threads/{thread_id}/transactions/{transaction_id}"
        ]
    )
    delete_parameters = {
        item["name"]
        for item in paths[
            "/v1/chat/threads/{thread_id}/transactions/{transaction_id}"
        ]["delete"]["parameters"]
    }
    assert {"If-Match", "Idempotency-Key"}.issubset(delete_parameters)

    schemas = payload["components"]["schemas"]
    assert "ChatRunCreateRequest" in schemas
    run_schema_props = set(schemas["ChatRunCreateRequest"]["properties"].keys())
    assert {"thread_id", "message", "config"}.issubset(run_schema_props)
