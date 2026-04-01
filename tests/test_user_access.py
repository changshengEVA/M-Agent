from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from m_agent.api.user_access import UserAccessError, UserAccessService


class StubRuntime:
    def __init__(self, *, config_path: Path, username: str) -> None:
        self.config_path = Path(config_path)
        self.username = username
        self.shutdown_called = False

    def shutdown(self) -> None:
        self.shutdown_called = True


def _write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def _read_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}
    assert isinstance(payload, dict)
    return payload


def _build_base_config_chain(tmp_path: Path) -> Path:
    base_dir = tmp_path / "base"
    chat_path = base_dir / "chat_controller.yaml"
    memory_agent_path = base_dir / "memory_agent.yaml"
    memory_core_path = base_dir / "memory_core.yaml"

    _write_yaml(
        chat_path,
        {
            "memory_agent_config_path": "./memory_agent.yaml",
            "thread_id": "default-thread",
            "chat_user_name": "User",
            "chat_assistant_name": "Memory Assistant",
            "persist_memory": True,
            "enabled_tools": ["shallow_recall", "deep_recall"],
        },
    )
    _write_yaml(
        memory_agent_path,
        {
            "memory_core_config_path": "./memory_core.yaml",
            "model_name": "deepseek-chat",
            "agent_temperature": 0.0,
        },
    )
    _write_yaml(
        memory_core_path,
        {
            "workflow_id": "base_workflow",
            "memory_owner_name": "Memory Assistant",
            "memory_similarity_threshold": 0.88,
            "memory_top_k": 3,
        },
    )
    return chat_path


def test_register_login_and_runtime_reload_on_config_update(tmp_path: Path) -> None:
    base_chat_config_path = _build_base_config_chain(tmp_path)
    created_runtimes: list[StubRuntime] = []

    def runtime_factory(user) -> StubRuntime:
        runtime = StubRuntime(config_path=user.config_path, username=user.username)
        created_runtimes.append(runtime)
        return runtime

    service = UserAccessService(
        base_chat_config_path=base_chat_config_path,
        users_db_path=tmp_path / "users.json",
        users_root_dir=tmp_path / "users",
        runtime_factory=runtime_factory,
        session_ttl_seconds=3600,
    )

    register_payload = service.register_user(
        username="Alice",
        password="alice-password-123",
        role="basic",
        display_name="Alice",
        assistant_name="EVA",
        persona_prompt="You are Alice's memory assistant.",
    )
    user_payload = register_payload["user"]
    assert user_payload["username"] == "alice"
    assert Path(user_payload["config_path"]).exists()

    user_config_dir = Path(user_payload["config_path"]).parent
    user_chat_config = _read_yaml(user_config_dir / "chat_controller.yaml")
    user_memory_core_config = _read_yaml(user_config_dir / "memory_core.yaml")
    assert user_chat_config["chat_assistant_name"] == "EVA"
    assert user_chat_config["chat_persona_prompt"] == "You are Alice's memory assistant."
    assert user_memory_core_config["workflow_id"] == "user_alice"

    login_payload = service.login(username="alice", password="alice-password-123")
    token = str(login_payload["access_token"])
    assert token

    auth_user = service.authenticate(token)
    runtime_1 = service.get_runtime(user=auth_user)
    runtime_2 = service.get_runtime(user=auth_user)
    assert runtime_1 is runtime_2
    assert len(created_runtimes) == 1

    update_payload = service.update_user_config(
        user=auth_user,
        updates={"chat": {"chat_assistant_name": "Nova"}},
    )
    assert update_payload["user"]["username"] == "alice"
    assert runtime_1.shutdown_called is True

    auth_user_after_update = service.authenticate(token)
    runtime_3 = service.get_runtime(user=auth_user_after_update)
    assert runtime_3 is not runtime_1
    assert len(created_runtimes) == 2


def test_basic_user_cannot_edit_advanced_fields(tmp_path: Path) -> None:
    base_chat_config_path = _build_base_config_chain(tmp_path)
    service = UserAccessService(
        base_chat_config_path=base_chat_config_path,
        users_db_path=tmp_path / "users.json",
        users_root_dir=tmp_path / "users",
        runtime_factory=lambda user: StubRuntime(config_path=user.config_path, username=user.username),
    )
    service.register_user(username="basic-user", password="basic-pass-123", role="basic")
    login_payload = service.login(username="basic-user", password="basic-pass-123")
    user = service.authenticate(str(login_payload["access_token"]))

    with pytest.raises(UserAccessError) as exc_info:
        service.update_user_config(
            user=user,
            updates={"memory_core": {"workflow_id": "changed_workflow"}},
        )
    assert exc_info.value.status_code == 403


def test_advanced_user_can_edit_advanced_fields(tmp_path: Path) -> None:
    base_chat_config_path = _build_base_config_chain(tmp_path)
    service = UserAccessService(
        base_chat_config_path=base_chat_config_path,
        users_db_path=tmp_path / "users.json",
        users_root_dir=tmp_path / "users",
        runtime_factory=lambda user: StubRuntime(config_path=user.config_path, username=user.username),
    )
    register_payload = service.register_user(
        username="advanced-user",
        password="advanced-pass-123",
        role="advanced",
    )
    login_payload = service.login(username="advanced-user", password="advanced-pass-123")
    user = service.authenticate(str(login_payload["access_token"]))

    service.update_user_config(
        user=user,
        updates={
            "memory_core": {"workflow_id": "advanced_workflow"},
            "chat": {"enabled_tools": ["shallow_recall", "get_current_time"]},
        },
    )

    user_config_dir = Path(register_payload["user"]["config_path"]).parent
    memory_core_config = _read_yaml(user_config_dir / "memory_core.yaml")
    chat_config = _read_yaml(user_config_dir / "chat_controller.yaml")
    assert memory_core_config["workflow_id"] == "advanced_workflow"
    assert chat_config["enabled_tools"] == ["shallow_recall", "get_current_time"]

