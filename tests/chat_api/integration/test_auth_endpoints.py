from __future__ import annotations

import base64
import time

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.app_factory import build_test_app, build_test_user_access
from tests.fixtures.payload_builders import run_payload
from tests.fixtures.sse_helpers import parse_sse_events


pytestmark = pytest.mark.integration


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF9sAAAAASUVORK5CYII="
)


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
    app.state.image_store.root_dir = tmp_path / "uploads"
    app.state.image_store.root_dir.mkdir(parents=True, exist_ok=True)
    app.state.image_store.captioner.caption_image = lambda _path: "authenticated test image"

    with TestClient(app) as client:
        registered = client.post(
            "/v1/auth/register",
            json={"username": "alice", "password": "password123", "role": "basic"},
        )
        assert registered.status_code == 201
        assert registered.json()["user"]["username"] == "alice"
        assert registered.json()["user"]["canonical_thread_id"] == "alice-thread"

        login_alice = client.post("/v1/auth/login", json={"username": "alice", "password": "password123"})
        assert login_alice.status_code == 200
        assert login_alice.json()["user"]["canonical_thread_id"] == "alice-thread"
        alice_token = login_alice.json()["access_token"]
        alice_headers = _auth_headers(alice_token)

        me = client.get("/v1/auth/me", headers=alice_headers)
        assert me.status_code == 200
        assert me.json()["user"]["canonical_thread_id"] == "alice-thread"

        upload = client.post(
            "/v1/chat/uploads/images",
            headers=alice_headers,
            files={"file": ("tiny.png", _PNG_1X1, "image/png")},
            data={"thread_id": "upload-client-thread"},
        )
        assert upload.status_code == 200
        assert upload.json()["thread_id"] == "alice-thread"
        stored_upload = app.state.image_store.get_upload_metadata(upload.json()["upload_id"])
        assert stored_upload is not None
        assert stored_upload["thread_id"] == "alice::alice-thread"

        scheduled = client.post(
            "/v1/chat/threads/schedule-client-thread/schedules",
            headers=alice_headers,
            json={
                "due_at": "2099-01-02T03:04:00Z",
                "timezone_name": "UTC",
                "text": "Canonical routing check",
            },
        )
        assert scheduled.status_code == 201
        assert scheduled.json()["thread_id"] == "alice-thread"
        assert scheduled.json()["item"]["thread_id"] == "alice-thread"
        schedule_id = scheduled.json()["item"]["schedule_id"]

        created = client.post(
            "/v1/chat/runs",
            headers=alice_headers,
            json=run_payload(message="alice says hi", thread_id="work-thread"),
        )
        assert created.status_code == 201
        assert created.json()["thread_id"] == "alice-thread"
        run_id = created.json()["run_id"]

        snapshot = _wait_run_completed(client, run_id, headers=alice_headers)
        assert snapshot["status"] == "completed"
        assert snapshot["thread_id"] == "alice-thread"
        assert snapshot["user_id"] == "alice"

        stream_response = client.get(f"/v1/chat/runs/{run_id}/events?after_seq=0", headers=alice_headers)
        assert stream_response.status_code == 200
        events = parse_sse_events(stream_response.text.splitlines())
        assert events
        assert {
            event["data"].get("thread_id")
            for event in events
            if isinstance(event.get("data"), dict)
        } == {"alice-thread"}
        thread_ids = [
            event["data"]["payload"].get("thread_id")
            for event in events
            if isinstance(event.get("data"), dict)
            and isinstance(event["data"].get("payload"), dict)
            and event["data"]["payload"].get("thread_id")
        ]
        assert thread_ids
        assert set(thread_ids) == {"alice-thread"}

        state = client.get("/v1/chat/threads/another-client-thread/memory/state", headers=alice_headers)
        assert state.status_code == 200
        assert state.json()["thread_id"] == "alice-thread"
        alice_user = user_access.get_user(username="alice")
        assert alice_user is not None
        alice_runtime = user_access.get_runtime(user=alice_user)
        assert set(alice_runtime._threads) == {"alice::alice-thread"}
        stored_schedule = alice_runtime.agent.get_schedule_agent().store.find_by_id(
            schedule_id,
            owner_id="alice",
        )
        assert stored_schedule is not None
        assert stored_schedule.thread_id == "alice::alice-thread"

        schedules = client.get(
            "/v1/chat/threads/list-client-thread/schedules",
            headers=alice_headers,
        )
        assert schedules.status_code == 200
        assert schedules.json()["thread_id"] == "alice-thread"
        assert {item["thread_id"] for item in schedules.json()["items"]} == {"alice-thread"}

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
