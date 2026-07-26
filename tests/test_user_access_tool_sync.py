from __future__ import annotations

from pathlib import Path

import yaml

from m_agent.api.user_access import UserAccountStore
from m_agent.config_paths import resolve_related_config_path


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)


def _build_base_configs(tmp_path: Path) -> Path:
    config_root = tmp_path / "config"
    chat_path = config_root / "agents" / "chat" / "chat_controller.yaml"
    model_path = config_root / "agents" / "chat" / "chat_model.yaml"
    runtime_path = config_root / "agents" / "chat" / "runtime" / "chat_controller_runtime.yaml"
    email_path = config_root / "agents" / "email" / "gmail_email_agent.yaml"
    schedule_path = config_root / "agents" / "schedule" / "schedule_agent.yaml"

    _write_yaml(
        chat_path,
        {
            "model_config_path": "./chat_model.yaml",
            "runtime_prompt_config_path": "./runtime/chat_controller_runtime.yaml",
            "email_agent_config_path": "../email/gmail_email_agent.yaml",
            "schedule_agent_config_path": "../schedule/schedule_agent.yaml",
            "enabled_tools": [
                "shallow_recall",
                "deep_recall",
                "get_current_time",
                "schedule_create",
                "schedule_query",
                "schedule_delete",
                "email_ask",
                "email_read",
                "email_send",
            ],
            "tool_defaults": {
                "get_current_time": {"timezone_name": "Asia/Shanghai"},
                "schedule_create": {"timezone_name": "Asia/Shanghai"},
                "schedule_query": {"timezone_name": "Asia/Shanghai", "limit": 10},
                "email_ask": {"mail_scope": "unread"},
            },
        },
    )
    _write_yaml(model_path, {"model_name": "fake-model", "agent_temperature": 0.0})
    _write_yaml(
        runtime_path,
        {
            "chat_controller": {
                "tools": {
                    "shallow_recall": {"description": {"zh": "A", "en": "A"}},
                    "deep_recall": {"description": {"zh": "B", "en": "B"}},
                    "get_current_time": {"description": {"zh": "C", "en": "C"}},
                    "schedule_create": {"description": {"zh": "SC", "en": "SC"}},
                    "schedule_delete": {"description": {"zh": "SD", "en": "SD"}},
                    "schedule_query": {"description": {"zh": "SQ", "en": "SQ"}},
                    "email_ask": {"description": {"zh": "D", "en": "D"}},
                    "email_read": {"description": {"zh": "E", "en": "E"}},
                    "email_send": {"description": {"zh": "E", "en": "E"}},
                }
            }
        },
    )
    _write_yaml(
        email_path,
        {
            "provider": "gmail",
            "gmail": {
                "user_id": "me",
                "credentials_path": "./dummy_client_secret.json",
                "token_path": "./dummy_token.json",
            },
        },
    )
    _write_yaml(
        schedule_path,
        {
            "provider": "local_schedule",
            "default_timezone_name": "Asia/Shanghai",
            "storage_dir": "../../../data/schedules",
        },
    )
    return chat_path


def test_verify_credentials_syncs_tool_related_user_configs(tmp_path: Path) -> None:
    base_chat_config_path = _build_base_configs(tmp_path)
    users_root = tmp_path / "users"
    users_db = users_root / "users.json"
    store = UserAccountStore(
        base_chat_config_path=base_chat_config_path,
        users_root_dir=users_root,
        users_db_path=users_db,
    )

    created_user = store.register_user(
        username="alice",
        password="password123",
        role="advanced",
    )
    assert created_user.canonical_thread_id == "alice-thread"
    assert created_user.to_payload()["canonical_thread_id"] == "alice-thread"
    user_chat_path = created_user.config_path
    user_runtime_path = user_chat_path.parent / "runtime" / "chat_runtime.yaml"

    stale_chat = yaml.safe_load(user_chat_path.read_text(encoding="utf-8"))
    stale_chat["enabled_tools"] = ["shallow_recall", "deep_recall", "get_current_time"]
    stale_chat["tool_defaults"] = {"get_current_time": {"timezone_name": "Asia/Shanghai"}}
    stale_chat.pop("email_agent_config_path", None)
    stale_chat.pop("schedule_agent_config_path", None)
    _write_yaml(user_chat_path, stale_chat)

    stale_runtime = yaml.safe_load(user_runtime_path.read_text(encoding="utf-8"))
    stale_runtime["chat_controller"]["tools"] = {
        "shallow_recall": {"description": {"zh": "A", "en": "A"}},
        "deep_recall": {"description": {"zh": "B", "en": "B"}},
        "get_current_time": {"description": {"zh": "C", "en": "C"}},
    }
    _write_yaml(user_runtime_path, stale_runtime)

    refreshed_user = store.verify_credentials(username="alice", password="password123")

    refreshed_chat = yaml.safe_load(user_chat_path.read_text(encoding="utf-8"))
    assert refreshed_chat["enabled_tools"] == [
        "shallow_recall",
        "deep_recall",
        "get_current_time",
    ]
    assert refreshed_chat["tool_defaults"]["email_ask"]["mail_scope"] == "unread"
    assert refreshed_chat["tool_defaults"]["schedule_query"]["limit"] == 10
    resolved_email_path = resolve_related_config_path(
        user_chat_path,
        refreshed_chat.get("email_agent_config_path"),
    )
    assert resolved_email_path.exists()
    resolved_schedule_path = resolve_related_config_path(
        user_chat_path,
        refreshed_chat.get("schedule_agent_config_path"),
    )
    assert resolved_schedule_path.exists()

    refreshed_runtime = yaml.safe_load(user_runtime_path.read_text(encoding="utf-8"))
    runtime_tools = refreshed_runtime["chat_controller"]["tools"]
    assert "schedule_create" in runtime_tools
    assert "schedule_delete" in runtime_tools
    assert "schedule_query" in runtime_tools
    assert "email_ask" in runtime_tools
    assert "email_read" in runtime_tools
    assert "email_send" in runtime_tools
    assert refreshed_user.updated_at != created_user.updated_at
    assert refreshed_user.canonical_thread_id == "alice-thread"


def test_register_user_rewrites_email_agent_config_path_for_user_dir(tmp_path: Path) -> None:
    base_chat_config_path = _build_base_configs(tmp_path)
    users_root = tmp_path / "users"
    users_db = users_root / "users.json"
    store = UserAccountStore(
        base_chat_config_path=base_chat_config_path,
        users_root_dir=users_root,
        users_db_path=users_db,
    )

    user = store.register_user(
        username="bob",
        password="password123",
        role="basic",
    )
    user_chat = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    assert user.canonical_thread_id == "bob-thread"
    assert user_chat["thread_id"] == "bob-thread"
    resolved_email_path = resolve_related_config_path(
        user.config_path,
        user_chat.get("email_agent_config_path"),
    )
    assert resolved_email_path.exists()
    resolved_schedule_path = resolve_related_config_path(
        user.config_path,
        user_chat.get("schedule_agent_config_path"),
    )
    assert resolved_schedule_path.exists()

    user_chat["thread_id"] = "configured-public-thread"
    _write_yaml(user.config_path, user_chat)
    refreshed_user = store.get_user(username="bob")
    assert refreshed_user is not None
    assert refreshed_user.canonical_thread_id == "configured-public-thread"


def test_get_user_config_schema_exposes_field_metadata(tmp_path: Path) -> None:
    base_chat_config_path = _build_base_configs(tmp_path)
    users_root = tmp_path / "users"
    users_db = users_root / "users.json"
    store = UserAccountStore(
        base_chat_config_path=base_chat_config_path,
        users_root_dir=users_root,
        users_db_path=users_db,
    )

    store.register_user(
        username="carol",
        password="password123",
        role="basic",
    )
    schema = store.get_user_config_schema(username="carol")

    assert schema["user"]["username"] == "carol"
    assert schema["user"]["role"] == "basic"

    chat_section = schema["sections"]["chat"]
    assert set(chat_section["editable_fields"]) == {"chat_assistant_name", "chat_persona_prompt"}
    assert "chat_assistant_name" in chat_section["fields"]
    assert chat_section["fields"]["chat_assistant_name"]["type"] == "string"
    assert chat_section["fields"]["chat_assistant_name"]["editable"] is True
    assert chat_section["fields"]["persist_memory"]["editable"] is False
    assert chat_section["fields"]["persist_memory"]["type"] == "boolean"

    model_section = schema["sections"]["model"]
    assert model_section["fields"]["model_name"]["editable"] is False
    assert model_section["fields"]["model_name"]["present"] is True
