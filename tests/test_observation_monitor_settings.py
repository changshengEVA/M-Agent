from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest
import yaml

from m_agent.api.chat_api_web import create_app
from m_agent.api.heartbeat import HeartbeatMonitorRegistry
from m_agent.api.observation_monitor_settings import (
    MANAGED_MONITOR_IDS,
    ObservationMonitorSettingsError,
    ObservationMonitorSettingsManager,
)
from tests.fixtures.app_factory import build_test_runtime, build_test_user_access


class _Coordinator:
    def __init__(self, monitors=()) -> None:
        self.monitor_registry = HeartbeatMonitorRegistry(monitors)
        self.reconcile_calls = 0

    def reconcile_monitor_group(self, *, managed_ids, monitors, commit):
        self.reconcile_calls += 1
        candidate = tuple(monitors)
        snapshot = self.monitor_registry.replace_group(
            managed_ids=managed_ids,
            monitors=candidate,
        )
        try:
            commit()
        except Exception:
            self.monitor_registry.restore_group(snapshot)
            raise
        return tuple(monitor.monitor_id for monitor in candidate)


class _Monitor:
    def __init__(self, monitor_id: str) -> None:
        self.monitor_id = monitor_id

    def beat(self, _context):  # pragma: no cover - settings never run a beat
        raise AssertionError("unexpected beat")


def _write(path: Path, payload: object) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _base_payload(tmp_path: Path, *, owner: str = "alice") -> dict:
    return {
        "gmail_email": {
            "enabled": False,
            "owner_ids": [owner],
            "state_path": str(tmp_path / "gmail-state.json"),
            "poll_interval_seconds": 60,
            "subject_max_chars": 321,
        },
        "arxiv": {
            "enabled": False,
            "state_path": str(tmp_path / "arxiv-state.json"),
            "feed_base_url": "https://example.invalid/atom",
            "max_feed_items": 2000,
            "seen_revision_limit": 4000,
            "subscriptions": [
                {
                    "subscription_id": "memory",
                    "owner_id": owner,
                    "categories": ["cs.AI"],
                    "keywords": ["memory"],
                }
            ],
        },
    }


def test_put_merges_hidden_fields_injects_owner_and_noop_does_not_reconcile(
    tmp_path: Path,
) -> None:
    path = tmp_path / "monitors.yaml"
    _write(path, _base_payload(tmp_path))
    coordinator = _Coordinator()
    manager = ObservationMonitorSettingsManager(
        config_path=path,
        coordinator=coordinator,
        live_apply_enabled=True,
        initial_config_applied=True,
    )
    before = manager.get()

    updated = manager.put(
        patch={"gmail_email": {"label_id": "IMPORTANT"}},
        expected_revision=before["revision"],
        owner_id="alice",
    )

    assert updated["changed"] is True
    assert coordinator.reconcile_calls == 1
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored["gmail_email"]["owner_ids"] == ["alice"]
    assert stored["gmail_email"]["state_path"].endswith("gmail-state.json")
    assert stored["gmail_email"]["subject_max_chars"] == 321
    assert stored["arxiv"]["feed_base_url"] == "https://example.invalid/atom"
    assert stored["arxiv"]["subscriptions"][0]["owner_id"] == "alice"

    public = manager.get()["config"]
    assert "owner_ids" not in public["gmail_email"]
    assert "state_path" not in public["gmail_email"]
    assert "state_path" not in public["arxiv"]
    assert "feed_base_url" not in public["arxiv"]
    assert "max_feed_items" not in public["arxiv"]
    assert "seen_revision_limit" not in public["arxiv"]
    assert "owner_id" not in public["arxiv"]["subscriptions"][0]

    unchanged = manager.put(
        patch={"gmail_email": {"label_id": "IMPORTANT"}},
        expected_revision=updated["revision"],
        owner_id="alice",
    )
    assert unchanged["changed"] is False
    assert coordinator.reconcile_calls == 1


def test_section_patch_only_rebinds_the_section_being_applied(
    tmp_path: Path,
) -> None:
    path = tmp_path / "monitors.yaml"
    _write(path, _base_payload(tmp_path, owner="existing-owner"))
    manager = ObservationMonitorSettingsManager(
        config_path=path,
        coordinator=_Coordinator(),
        live_apply_enabled=True,
    )

    manager.put(
        patch={"gmail_email": {"label_id": "IMPORTANT"}},
        expected_revision=manager.get()["revision"],
        owner_id="alice",
    )

    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored["gmail_email"]["owner_ids"] == ["alice"]
    assert stored["arxiv"]["subscriptions"][0]["owner_id"] == (
        "existing-owner"
    )


def test_put_rejects_hidden_fields_ranges_and_stale_revision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "monitors.yaml"
    _write(path, _base_payload(tmp_path))
    manager = ObservationMonitorSettingsManager(
        config_path=path,
        coordinator=_Coordinator(),
        live_apply_enabled=True,
        initial_config_applied=True,
    )
    revision = manager.get()["revision"]

    with pytest.raises(ObservationMonitorSettingsError, match="state_path"):
        manager.put(
            patch={"gmail_email": {"state_path": "C:/elsewhere.json"}},
            expected_revision=revision,
            owner_id="alice",
        )
    with pytest.raises(
        ObservationMonitorSettingsError,
        match="poll_interval_seconds",
    ):
        manager.put(
            patch={"arxiv": {"poll_interval_seconds": 0}},
            expected_revision=revision,
            owner_id="alice",
        )
    with pytest.raises(ObservationMonitorSettingsError) as conflict:
        manager.put(
            patch={"gmail_email": {"enabled": True}},
            expected_revision="stale",
            owner_id="alice",
        )
    assert conflict.value.status_code == 409
    assert conflict.value.extra["current_revision"] == revision


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        (
            {"arxiv": {"request_timeout_seconds": float("nan")}},
            "request_timeout_seconds",
        ),
        ({"gmail_email": {"label_id": "  "}}, "label_id"),
        (
            {
                "arxiv": {
                    "subscriptions": [
                        {"subscription_id": "", "categories": ["cs.AI"]}
                    ]
                }
            },
            "subscription_id",
        ),
        (
            {
                "arxiv": {
                    "subscriptions": [
                        {"subscription_id": "one", "categories": ["  "]}
                    ]
                }
            },
            "blank strings",
        ),
        (
            {
                "arxiv": {
                    "subscriptions": [
                        {"subscription_id": "same", "categories": ["cs.AI"]},
                        {"subscription_id": "same", "categories": ["cs.CL"]},
                    ]
                }
            },
            "duplicate arxiv subscription_id",
        ),
    ],
)
def test_put_rejects_values_that_runtime_would_normalize(
    tmp_path: Path,
    patch: dict,
    message: str,
) -> None:
    path = tmp_path / "monitors.yaml"
    _write(path, _base_payload(tmp_path))
    manager = ObservationMonitorSettingsManager(
        config_path=path,
        coordinator=_Coordinator(),
        live_apply_enabled=True,
        initial_config_applied=True,
    )

    with pytest.raises(ObservationMonitorSettingsError, match=message):
        manager.put(
            patch=patch,
            expected_revision=manager.get()["revision"],
            owner_id="alice",
        )


def test_live_reconcile_rolls_back_registry_when_atomic_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "monitors.yaml"
    _write(path, _base_payload(tmp_path))
    previous = _Monitor("arxiv")
    coordinator = _Coordinator((previous,))
    manager = ObservationMonitorSettingsManager(
        config_path=path,
        coordinator=coordinator,
        live_apply_enabled=True,
        initial_config_applied=True,
    )
    revision = manager.get()["revision"]
    original_text = path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        manager,
        "_atomic_write",
        lambda _payload: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(ObservationMonitorSettingsError, match="feed_base_url"):
        manager.put(
            patch={
                "arxiv": {
                    "enabled": True,
                    "feed_base_url": "forbidden",
                }
            },
            expected_revision=revision,
            owner_id="alice",
        )

    # The invalid hidden field is rejected before reconciliation. Exercise a
    # valid candidate next so persistence failure must restore the old object.
    with pytest.raises(ObservationMonitorSettingsError, match="disk full"):
        manager.put(
            patch={"arxiv": {"enabled": True}},
            expected_revision=revision,
            owner_id="alice",
        )
    assert coordinator.monitor_registry.get("arxiv") is previous
    assert path.read_text(encoding="utf-8") == original_text


def test_cli_disabled_persists_without_changing_live_registry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "monitors.yaml"
    _write(path, _base_payload(tmp_path))
    coordinator = _Coordinator()
    manager = ObservationMonitorSettingsManager(
        config_path=path,
        coordinator=coordinator,
        live_apply_enabled=False,
    )

    result = manager.put(
        patch={"gmail_email": {"enabled": True}},
        expected_revision=manager.get()["revision"],
        owner_id="alice",
    )

    assert result["persisted"] is True
    assert result["application"]["status"] == "restart_required"
    assert result["application"]["restart_required"] is True
    assert coordinator.reconcile_calls == 0
    assert not MANAGED_MONITOR_IDS.intersection(
        coordinator.monitor_registry.names()
    )


def test_api_is_advanced_only_uses_cas_and_never_exposes_owner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "monitors.yaml"
    _write(path, _base_payload(tmp_path))
    access = build_test_user_access(users_root=tmp_path / "users")
    app = create_app(
        service_runtime=build_test_runtime(),
        user_access=access,
        schedule_beat_seconds=3600,
        observation_monitors_config_path=path,
    )

    with TestClient(app) as client:
        for username, role in (("alice", "advanced"), ("basic", "basic")):
            assert client.post(
                "/v1/auth/register",
                json={
                    "username": username,
                    "password": "password123",
                    "role": role,
                },
            ).status_code == 201
        alice_token = client.post(
            "/v1/auth/login",
            json={"username": "alice", "password": "password123"},
        ).json()["access_token"]
        basic_token = client.post(
            "/v1/auth/login",
            json={"username": "basic", "password": "password123"},
        ).json()["access_token"]
        alice_headers = {"Authorization": f"Bearer {alice_token}"}
        basic_headers = {"Authorization": f"Bearer {basic_token}"}

        assert client.get(
            "/v1/users/me/settings/observation-monitors",
            headers=basic_headers,
        ).status_code == 403
        fetched = client.get(
            "/v1/users/me/settings/observation-monitors",
            headers=alice_headers,
        )
        assert fetched.status_code == 200
        assert fetched.headers["etag"].startswith('W/"')
        assert "owner_ids" not in fetched.json()["config"]["gmail_email"]

        rejected = client.put(
            "/v1/users/me/settings/observation-monitors",
            headers=alice_headers,
            json={
                "expected_revision": fetched.json()["revision"],
                "config": {"gmail_email": {"owner_ids": ["basic"]}},
            },
        )
        assert rejected.status_code == 400

        applied = client.put(
            "/v1/users/me/settings/observation-monitors",
            headers={
                **alice_headers,
                "If-Match": fetched.headers["etag"],
            },
            json={"config": {"gmail_email": {"label_id": "IMPORTANT"}}},
        )
        assert applied.status_code == 200
        assert applied.json()["application"]["status"] == "applied"
        stored = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert stored["gmail_email"]["owner_ids"] == ["alice"]
