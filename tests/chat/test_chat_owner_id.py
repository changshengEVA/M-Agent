from __future__ import annotations

from pathlib import Path

import yaml

from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent
from m_agent.paths import chat_memory_workflow_id, chat_user_slug


def _agent_for_config(config_path: Path, payload: dict) -> ThreeLayerChatAgent:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    agent = ThreeLayerChatAgent.__new__(ThreeLayerChatAgent)
    agent.config_path = config_path
    agent.config = payload
    agent.user_name = str(payload.get("chat_user_name", "User") or "User")
    agent.owner_id = ThreeLayerChatAgent._resolve_chat_owner_id(agent)
    return agent


def test_owner_id_prefers_chat_owner_id_over_chinese_display_name(tmp_path: Path) -> None:
    agent = _agent_for_config(
        tmp_path / "users" / "tester" / "chat.yaml",
        {
            "chat_owner_id": "tester",
            "chat_user_name": "测试用户甲",
        },
    )
    assert agent.user_name == "测试用户甲"
    assert agent.owner_id == "tester"
    assert chat_user_slug(agent.user_name) == "user"
    assert chat_memory_workflow_id(agent.owner_id) == "chat-api/tester"


def test_owner_id_infers_username_from_users_config_dir(tmp_path: Path) -> None:
    agent = _agent_for_config(
        tmp_path / "users" / "alice" / "chat.yaml",
        {"chat_user_name": "爱丽丝"},
    )
    assert agent.owner_id == "alice"


def test_owner_id_falls_back_to_display_slug_for_cli_configs(tmp_path: Path) -> None:
    agent = _agent_for_config(
        tmp_path / "agents" / "chat" / "chat_controller.yaml",
        {"chat_user_name": "Bob"},
    )
    assert agent.owner_id == "bob"
