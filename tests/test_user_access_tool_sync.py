from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from m_agent.api import user_access as user_access_module
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
            "runtime": {
                "common": {"scene_context_max_entries": 40},
                "langgraph": {
                    "turn_loop": True,
                    "thinking_mode": "single_call",
                },
            },
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
                "thinking": {
                    "persona_tone_prompt": {
                        "zh": "SERVER DEFAULT PERSONA",
                        "en": "SERVER DEFAULT PERSONA",
                    },
                    "persona_merge_template": {
                        "zh": "<base_prompt>\n\n[SERVER PERSONA]\n<persona_prompt>",
                        "en": "<base_prompt>\n\n[SERVER PERSONA]\n<persona_prompt>",
                    },
                    "thinking_turn": {
                        "base_prompt": {
                            "zh": "SERVER THINKING BASE",
                            "en": "SERVER THINKING BASE",
                        },
                        "instructions": {
                            "zh": "SERVER THINKING INSTRUCTIONS",
                            "en": "SERVER THINKING INSTRUCTIONS",
                        },
                    },
                },
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


def test_verify_credentials_syncs_user_configs_and_shared_runtime(tmp_path: Path) -> None:
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

    stale_chat = yaml.safe_load(user_chat_path.read_text(encoding="utf-8"))
    stale_chat["enabled_tools"] = ["shallow_recall", "deep_recall", "get_current_time"]
    stale_chat["tool_defaults"] = {"get_current_time": {"timezone_name": "Asia/Shanghai"}}
    stale_chat.pop("email_agent_config_path", None)
    stale_chat.pop("schedule_agent_config_path", None)
    _write_yaml(user_chat_path, stale_chat)

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

    base_chat = yaml.safe_load(base_chat_config_path.read_text(encoding="utf-8"))
    base_runtime_path = resolve_related_config_path(
        base_chat_config_path,
        base_chat.get("runtime_prompt_config_path"),
    )
    user_runtime_path = resolve_related_config_path(
        user_chat_path,
        refreshed_chat.get("runtime_prompt_config_path"),
    )
    assert user_runtime_path == base_runtime_path
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


def test_register_user_stores_only_persona_and_reuses_server_runtime(tmp_path: Path) -> None:
    base_chat_config_path = _build_base_configs(tmp_path)
    users_root = tmp_path / "users"
    store = UserAccountStore(
        base_chat_config_path=base_chat_config_path,
        users_root_dir=users_root,
        users_db_path=users_root / "users.json",
    )

    user = store.register_user(
        username="persona-user",
        password="password123",
        display_name="展示名",
        persona_prompt="A concise custom persona.",
    )

    user_chat = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    assert user_chat["chat_owner_id"] == "persona-user"
    assert user_chat["chat_user_name"] == "展示名"
    assert user_chat["chat_persona_prompt"] == "A concise custom persona."
    base_chat = yaml.safe_load(base_chat_config_path.read_text(encoding="utf-8"))
    assert resolve_related_config_path(
        user.config_path,
        user_chat.get("runtime_prompt_config_path"),
    ) == resolve_related_config_path(
        base_chat_config_path,
        base_chat.get("runtime_prompt_config_path"),
    )
    assert not (user.config_path.parent / "runtime" / "chat_runtime.yaml").exists()


@pytest.mark.parametrize(
    ("persona_fragment", "expected_persona", "seed_null_override"),
    [
        (
            {
                "thinking": {
                    "persona_tone_prompt": {
                        "zh": "NESTED LEGACY PERSONA",
                        "en": "IGNORED ENGLISH PERSONA",
                    }
                }
            },
            "NESTED LEGACY PERSONA",
            False,
        ),
        (
            {
                "persona_prompt": {
                    "zh": "TOP-LEVEL LEGACY PERSONA",
                    "en": "IGNORED ENGLISH PERSONA",
                }
            },
            "TOP-LEVEL LEGACY PERSONA",
            False,
        ),
        (
            {
                "persona_prompt": {
                    "zh": "PERSONA RECOVERED AFTER NULL",
                    "en": "IGNORED ENGLISH PERSONA",
                }
            },
            "PERSONA RECOVERED AFTER NULL",
            True,
        ),
    ],
)
def test_verify_credentials_migrates_legacy_runtime_persona_only(
    tmp_path: Path,
    persona_fragment: dict,
    expected_persona: str,
    seed_null_override: bool,
) -> None:
    base_chat_config_path = _build_base_configs(tmp_path)
    users_root = tmp_path / "users"
    store = UserAccountStore(
        base_chat_config_path=base_chat_config_path,
        users_root_dir=users_root,
        users_db_path=users_root / "users.json",
    )
    user = store.register_user(
        username="legacy-user",
        password="password123",
    )

    legacy_runtime_path = user.config_path.parent / "runtime" / "chat_runtime.yaml"
    legacy_controller = {
        "system_prompt": {"zh": "UNTRUSTED SYSTEM", "en": "UNTRUSTED SYSTEM"},
        "merge_system_with_persona": {
            "zh": "UNTRUSTED MERGE <persona_prompt>",
            "en": "UNTRUSTED MERGE <persona_prompt>",
        },
        "thinking": {
            "thinking_turn": {
                "base_prompt": {"zh": "UNTRUSTED THINKING", "en": "UNTRUSTED THINKING"},
                "instructions": {"zh": "UNTRUSTED INSTRUCTIONS", "en": "UNTRUSTED INSTRUCTIONS"},
            }
        },
    }
    for key, value in persona_fragment.items():
        if key == "thinking" and isinstance(value, dict):
            legacy_controller["thinking"].update(value)
        else:
            legacy_controller[key] = value
    _write_yaml(legacy_runtime_path, {"chat_controller": legacy_controller})

    legacy_chat = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    if seed_null_override:
        legacy_chat["chat_persona_prompt"] = None
    else:
        legacy_chat.pop("chat_persona_prompt", None)
    legacy_chat["prompt_language"] = "zh"
    legacy_chat["runtime_prompt_config_path"] = "./runtime/chat_runtime.yaml"
    _write_yaml(user.config_path, legacy_chat)

    store.verify_credentials(username="legacy-user", password="password123")

    migrated_chat = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    assert migrated_chat["chat_persona_prompt"] == expected_persona
    base_chat = yaml.safe_load(base_chat_config_path.read_text(encoding="utf-8"))
    assert resolve_related_config_path(
        user.config_path,
        migrated_chat.get("runtime_prompt_config_path"),
    ) == resolve_related_config_path(
        base_chat_config_path,
        base_chat.get("runtime_prompt_config_path"),
    )


def test_migration_does_not_freeze_copied_server_persona(tmp_path: Path) -> None:
    base_chat_config_path = _build_base_configs(tmp_path)
    users_root = tmp_path / "users"
    store = UserAccountStore(
        base_chat_config_path=base_chat_config_path,
        users_root_dir=users_root,
        users_db_path=users_root / "users.json",
    )
    user = store.register_user(
        username="copied-default-user",
        password="password123",
    )

    base_chat = yaml.safe_load(base_chat_config_path.read_text(encoding="utf-8"))
    base_runtime_path = resolve_related_config_path(
        base_chat_config_path,
        base_chat.get("runtime_prompt_config_path"),
    )
    legacy_runtime_path = user.config_path.parent / "runtime" / "chat_runtime.yaml"
    _write_yaml(
        legacy_runtime_path,
        yaml.safe_load(base_runtime_path.read_text(encoding="utf-8")),
    )
    legacy_chat = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    legacy_chat["runtime_prompt_config_path"] = "./runtime/chat_runtime.yaml"
    _write_yaml(user.config_path, legacy_chat)

    store.verify_credentials(
        username="copied-default-user",
        password="password123",
    )

    migrated_chat = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    assert "chat_persona_prompt" not in migrated_chat
    assert resolve_related_config_path(
        user.config_path,
        migrated_chat.get("runtime_prompt_config_path"),
    ) == base_runtime_path


def test_migration_does_not_freeze_historical_server_persona(tmp_path: Path) -> None:
    base_chat_config_path = _build_base_configs(tmp_path)
    users_root = tmp_path / "users"
    store = UserAccountStore(
        base_chat_config_path=base_chat_config_path,
        users_root_dir=users_root,
        users_db_path=users_root / "users.json",
    )
    user = store.register_user(
        username="historical-default-user",
        password="password123",
    )

    historical_default = (
        "你是一个长期陪伴型记忆助手，风格温和、可靠、克制。\n"
        "你像一个真正帮助用户整理生活细节的聊天伙伴，而不是机械问答器。\n"
        "默认简洁回答，先回应用户真正关心的问题，再补充必要细节。"
    )
    legacy_runtime_path = user.config_path.parent / "runtime" / "chat_runtime.yaml"
    _write_yaml(
        legacy_runtime_path,
        {
            "chat_controller": {
                "persona_prompt": {
                    "zh": historical_default,
                    "en": "unused",
                }
            }
        },
    )
    legacy_chat = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    legacy_chat["runtime_prompt_config_path"] = "./runtime/chat_runtime.yaml"
    _write_yaml(user.config_path, legacy_chat)

    store.verify_credentials(
        username="historical-default-user",
        password="password123",
    )

    migrated_chat = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    assert "chat_persona_prompt" not in migrated_chat


def test_agent_uses_server_prompts_and_keeps_user_persona(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent
    from m_agent.layers.execution.model_provider import ModelProvider

    base_chat_config_path = _build_base_configs(tmp_path)
    users_root = tmp_path / "users"
    store = UserAccountStore(
        base_chat_config_path=base_chat_config_path,
        users_root_dir=users_root,
        users_db_path=users_root / "users.json",
    )
    user = store.register_user(
        username="agent-user",
        password="password123",
        persona_prompt="TRUSTED USER PERSONA",
    )

    legacy_runtime_path = user.config_path.parent / "runtime" / "chat_runtime.yaml"
    _write_yaml(
        legacy_runtime_path,
        {
            "chat_controller": {
                "system_prompt": {"zh": "UNTRUSTED SYSTEM"},
                "persona_prompt": {"zh": "UNTRUSTED PERSONA"},
                "merge_system_with_persona": {
                    "zh": "UNTRUSTED MERGE <persona_prompt>"
                },
                "thinking": {
                    "thinking_turn": {
                        "base_prompt": {"zh": "UNTRUSTED THINKING"},
                        "instructions": {"zh": "UNTRUSTED INSTRUCTIONS"},
                    }
                },
            }
        },
    )
    legacy_chat = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    legacy_chat["runtime_prompt_config_path"] = "./runtime/chat_runtime.yaml"
    _write_yaml(user.config_path, legacy_chat)

    refreshed_user = store.verify_credentials(
        username="agent-user",
        password="password123",
    )
    refreshed_chat = yaml.safe_load(refreshed_user.config_path.read_text(encoding="utf-8"))
    assert refreshed_chat["chat_persona_prompt"] == "TRUSTED USER PERSONA"

    fake_provider = ModelProvider(
        model=object(),
        model_name="fake-model",
        network_retry_attempts=1,
        network_retry_backoff_seconds=0,
    )
    monkeypatch.setattr(
        "m_agent.chat.three_layer_chat_agent.build_model_provider_from_config",
        lambda *_args, **_kwargs: fake_provider,
    )

    agent = ThreeLayerChatAgent(config_path=refreshed_user.config_path)

    assert agent.runtime_prompts["thinking"]["thinking_turn"]["base_prompt"] == (
        "SERVER THINKING BASE"
    )
    assert agent.thinking_agent._thinking_turn_instructions_block() == (
        "SERVER THINKING INSTRUCTIONS"
    )
    assert "SERVER THINKING BASE" in agent.thinking_agent.system_prompt
    assert "[SERVER PERSONA]" in agent.thinking_agent.system_prompt
    assert "TRUSTED USER PERSONA" in agent.thinking_agent.system_prompt
    assert "UNTRUSTED" not in agent.thinking_agent.system_prompt


def test_explicit_empty_persona_disables_server_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent
    from m_agent.layers.execution.model_provider import ModelProvider

    base_chat_config_path = _build_base_configs(tmp_path)
    users_root = tmp_path / "users"
    store = UserAccountStore(
        base_chat_config_path=base_chat_config_path,
        users_root_dir=users_root,
        users_db_path=users_root / "users.json",
    )
    user = store.register_user(
        username="no-persona-user",
        password="password123",
        persona_prompt="",
    )

    user_chat = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    assert user_chat["chat_persona_prompt"] == ""

    monkeypatch.setattr(
        "m_agent.chat.three_layer_chat_agent.build_model_provider_from_config",
        lambda *_args, **_kwargs: ModelProvider(model=object(), model_name="fake-model"),
    )
    agent = ThreeLayerChatAgent(config_path=user.config_path)

    assert agent.thinking_agent.system_prompt.startswith("SERVER THINKING BASE")
    assert "SERVER DEFAULT PERSONA" not in agent.thinking_agent.system_prompt
    assert "[SERVER PERSONA]" not in agent.thinking_agent.system_prompt


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


def test_verify_credentials_syncs_single_runtime_settings(
    tmp_path: Path,
) -> None:
    base_chat_config_path = _build_base_configs(tmp_path)
    users_root = tmp_path / "users"
    store = UserAccountStore(
        base_chat_config_path=base_chat_config_path,
        users_root_dir=users_root,
        users_db_path=users_root / "users.json",
    )
    user = store.register_user(
        username="rollout-user",
        password="password123",
        role="advanced",
    )
    initial = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    assert "default_engine" not in initial["runtime"]
    assert initial["runtime"]["common"]["scene_context_max_entries"] == 40
    initial["runtime"]["default_engine"] = "obsolete_selector"
    _write_yaml(user.config_path, initial)

    base = yaml.safe_load(base_chat_config_path.read_text(encoding="utf-8"))
    base["runtime"]["common"]["scheduler"] = {"preempt_enabled": False}
    base["runtime"]["langgraph"]["turn_loop"] = False
    _write_yaml(base_chat_config_path, base)

    store.verify_credentials(username="rollout-user", password="password123")
    migrated = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    assert "default_engine" not in migrated["runtime"]
    assert migrated["runtime"]["common"]["scheduler"] == {
        "preempt_enabled": False
    }
    assert migrated["runtime"]["langgraph"]["turn_loop"] is False
    assert migrated["runtime"]["langgraph"]["thinking_mode"] == "single_call"


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


def test_user_config_yaml_write_is_atomic_and_leaves_no_temp_file(
    tmp_path: Path,
) -> None:
    base_chat_config_path = _build_base_configs(tmp_path)
    users_root = tmp_path / "users"
    store = UserAccountStore(
        base_chat_config_path=base_chat_config_path,
        users_root_dir=users_root,
        users_db_path=users_root / "users.json",
    )
    user = store.register_user(
        username="atomic-user",
        password="password123",
        role="advanced",
    )

    updated = store.update_user_config(
        username=user.username,
        updates={
            "chat": {"chat_assistant_name": "Nova"},
            "model": {"agent_temperature": 0.25},
        },
    )

    chat_payload = yaml.safe_load(user.config_path.read_text(encoding="utf-8"))
    model_path = user.config_path.parent / "chat_model.params.yaml"
    model_payload = yaml.safe_load(model_path.read_text(encoding="utf-8"))
    assert chat_payload["chat_assistant_name"] == "Nova"
    assert model_payload["agent_temperature"] == 0.25
    assert updated.updated_at != user.updated_at
    assert list(user.config_path.parent.glob(".*.tmp")) == []


def test_user_config_rolls_back_first_section_when_second_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_chat_config_path = _build_base_configs(tmp_path)
    users_root = tmp_path / "users"
    store = UserAccountStore(
        base_chat_config_path=base_chat_config_path,
        users_root_dir=users_root,
        users_db_path=users_root / "users.json",
    )
    user = store.register_user(
        username="rollback-user",
        password="password123",
        role="advanced",
    )
    model_path = user.config_path.parent / "chat_model.params.yaml"
    original_chat = user.config_path.read_bytes()
    original_model = model_path.read_bytes()
    original_writer = user_access_module._write_yaml

    def _fail_model_write(path: Path, payload: dict) -> None:
        if Path(path) == model_path:
            raise OSError("simulated model settings write failure")
        original_writer(path, payload)

    monkeypatch.setattr(user_access_module, "_write_yaml", _fail_model_write)
    with pytest.raises(OSError, match="simulated model settings write failure"):
        store.update_user_config(
            username=user.username,
            updates={
                "chat": {"chat_assistant_name": "Should roll back"},
                "model": {"agent_temperature": 0.75},
            },
        )

    assert user.config_path.read_bytes() == original_chat
    assert model_path.read_bytes() == original_model
    refreshed = store.get_user(username=user.username)
    assert refreshed is not None
    assert refreshed.updated_at == user.updated_at
