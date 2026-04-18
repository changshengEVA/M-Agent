from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.app_factory import build_test_app, build_test_user_access
from tests.fixtures.payload_builders import run_payload


pytestmark = pytest.mark.integration


def _wait_run_completed(client: TestClient, run_id: str, *, headers: dict[str, str]) -> dict:
    deadline = time.time() + 2.0
    while time.time() < deadline:
        response = client.get(f"/v1/chat/runs/{run_id}", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"run not completed within timeout: {run_id}")


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_chat_endpoints_require_token_when_auth_is_enabled(tmp_path) -> None:
    user_access = build_test_user_access(users_root=tmp_path / "users")
    app = build_test_app(auth_enabled=True, user_access=user_access)

    with TestClient(app) as client:
        response = client.post("/v1/chat/runs", json=run_payload(message="hello"))

    assert response.status_code == 401
    assert "missing bearer token" in response.json()["error"]


def test_auth_register_login_and_run_visibility_isolated_by_user(tmp_path) -> None:
    user_access = build_test_user_access(users_root=tmp_path / "users")
    app = build_test_app(auth_enabled=True, user_access=user_access)

    with TestClient(app) as client:
        registered = client.post(
            "/v1/auth/register",
            json={"username": "alice", "password": "password123", "role": "basic"},
        )
        assert registered.status_code == 201
        assert registered.json()["user"]["username"] == "alice"

        login_alice = client.post("/v1/auth/login", json={"username": "alice", "password": "password123"})
        assert login_alice.status_code == 200
        alice_token = login_alice.json()["access_token"]
        alice_headers = _auth_headers(alice_token)

        created = client.post(
            "/v1/chat/runs",
            headers=alice_headers,
            json=run_payload(message="alice says hi", thread_id="work-thread"),
        )
        assert created.status_code == 201
        run_id = created.json()["run_id"]

        snapshot = _wait_run_completed(client, run_id, headers=alice_headers)
        assert snapshot["status"] == "completed"
        assert snapshot["thread_id"] == "work-thread"
        assert snapshot["user_id"] == "alice"

        client.post(
            "/v1/auth/register",
            json={"username": "bob", "password": "password123", "role": "basic"},
        )
        login_bob = client.post("/v1/auth/login", json={"username": "bob", "password": "password123"})
        bob_headers = _auth_headers(login_bob.json()["access_token"])

        bob_view = client.get(f"/v1/chat/runs/{run_id}", headers=bob_headers)
        assert bob_view.status_code == 404

        alice_view = client.get(f"/v1/chat/runs/{run_id}", headers=alice_headers)
        assert alice_view.status_code == 200
