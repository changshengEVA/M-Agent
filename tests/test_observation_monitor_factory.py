from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from m_agent.api.observation_monitor_factory import (
    build_observation_monitors,
    load_observation_monitor_config,
)
from m_agent.api import chat_api_cli


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "observation-monitors.yaml"
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    return path


def test_missing_implicit_config_is_safely_empty(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    assert load_observation_monitor_config(missing, missing_ok=True) == {}
    assert build_observation_monitors(missing, missing_ok=True) == ()
    with pytest.raises(FileNotFoundError):
        build_observation_monitors(missing)


def test_disabled_sections_construct_no_external_clients(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "gmail_email": {"enabled": False},
            "arxiv": {"enabled": False},
        },
    )

    assert build_observation_monitors(path) == ()


def test_gmail_requires_exactly_one_explicit_owner(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {"gmail_email": {"enabled": True, "owner_ids": []}},
    )

    with pytest.raises(ValueError, match="exactly one explicit owner_id"):
        build_observation_monitors(path)


def test_factory_builds_gmail_and_arxiv_without_network_calls(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        {
            "gmail_email": {
                "enabled": True,
                "owner_ids": ["alice"],
                "state_path": str(tmp_path / "gmail.json"),
            },
            "arxiv": {
                "enabled": True,
                "state_path": str(tmp_path / "arxiv.json"),
                "max_pending_per_owner": 37,
                "subscriptions": [
                    {
                        "subscription_id": "memory",
                        "owner_id": "alice",
                        "categories": ["cs.AI"],
                        "keywords": ["agent memory"],
                    }
                ],
            },
        },
    )

    monitors = build_observation_monitors(path)

    assert [monitor.monitor_id for monitor in monitors] == [
        "gmail_email",
        "arxiv",
    ]
    assert monitors[1].config.max_pending_per_owner == 37


def test_cli_injects_configured_monitors_into_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_monitor = SimpleNamespace(monitor_id="configured")
    runtime = SimpleNamespace(
        idle_flush_seconds=30,
        history_max_rounds=4,
    )
    captured = {}
    args = SimpleNamespace(
        host="127.0.0.1",
        port=8777,
        config="config/agents/chat/chat_controller.yaml",
        idle_flush_seconds=30,
        history_max_rounds=4,
        schedule_beat_seconds=10,
        observation_monitors_config=str(tmp_path / "monitors.yaml"),
        disable_observation_monitors=False,
        users_db="config/users/users.json",
        session_ttl_seconds=60,
        disable_auth=True,
        debug=False,
    )

    monkeypatch.setattr(chat_api_cli, "parse_args", lambda: args)
    monkeypatch.setattr(
        chat_api_cli,
        "ENV_PATH",
        tmp_path / "missing.env",
    )
    monkeypatch.setattr(
        chat_api_cli,
        "_configure_windows_event_loop_policy",
        lambda: None,
    )
    monkeypatch.setattr(
        chat_api_cli,
        "_configure_logging",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        chat_api_cli,
        "build_observation_monitors",
        lambda path, **kwargs: (
            captured.update(config_path=path, config_kwargs=kwargs)
            or (configured_monitor,)
        ),
    )
    monkeypatch.setattr(
        chat_api_cli,
        "ChatServiceRuntime",
        lambda **_kwargs: runtime,
    )
    monkeypatch.setattr(
        chat_api_cli,
        "create_app",
        lambda **kwargs: captured.update(app_kwargs=kwargs) or "app",
    )
    monkeypatch.setattr(
        chat_api_cli.uvicorn,
        "run",
        lambda app, **kwargs: captured.update(uvicorn=(app, kwargs)),
    )

    chat_api_cli.main()

    assert captured["config_path"] == args.observation_monitors_config
    assert captured["config_kwargs"] == {"missing_ok": False}
    assert captured["app_kwargs"]["heartbeat_monitors"] == (
        configured_monitor,
    )
    assert captured["uvicorn"][0] == "app"
