from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from m_agent.agents.chat_controller_agent import ChatControllerAgent


def test_chat_persona_prompt_can_still_be_loaded_from_chat_agent_config() -> None:
    stub_memory_agent = SimpleNamespace(
        config_path="config/agents/memory/chat_memory_agent.yaml",
        config={"thread_id": "memory-thread"},
        memory_core_config={},
    )

    chat_config = {
        "thread_id": "chat-thread",
        "prompt_language": "zh",
        "runtime_prompt_config_path": "../../prompts/runtime/agent_runtime.yaml",
        "memory_agent_config_path": "../memory/chat_memory_agent.yaml",
        "chat_persona_prompt": {
            "zh": "persona from chat agent config",
            "en": "persona from chat agent config",
        },
        "chat_system_prompt": {
            "zh": "chat system prompt",
            "en": "chat system prompt",
        },
    }

    with mock.patch.object(ChatControllerAgent, "_load_config", return_value=chat_config):
        with mock.patch("m_agent.agents.chat_controller_agent.MemoryAgent", return_value=stub_memory_agent):
            agent = ChatControllerAgent(config_path="config/agents/chat/chat_controller.yaml")

    assert agent.config == chat_config
    assert str(agent.memory_agent_config_path).endswith("config\\agents\\memory\\chat_memory_agent.yaml")
    assert agent.default_thread_id == "chat-thread"
    assert agent.chat_persona_prompt == "persona from chat agent config"
    assert "persona from chat agent config" in agent.chat_system_prompt


def test_legacy_current_time_timezone_is_merged_into_tool_defaults() -> None:
    stub_memory_agent = SimpleNamespace(
        config_path="config/agents/memory/chat_memory_agent.yaml",
        config={"thread_id": "memory-thread"},
        memory_core_config={},
    )
    chat_config = {
        "thread_id": "chat-thread",
        "prompt_language": "en",
        "runtime_prompt_config_path": "../../prompts/runtime/agent_runtime.yaml",
        "memory_agent_config_path": "../memory/chat_memory_agent.yaml",
        "chat_persona_prompt": {
            "zh": "persona zh",
            "en": "persona en",
        },
        "chat_system_prompt": {
            "zh": "system zh",
            "en": "system en",
        },
        "current_time_tool_timezone": "UTC",
    }

    with mock.patch.object(ChatControllerAgent, "_load_config", return_value=chat_config):
        with mock.patch("m_agent.agents.chat_controller_agent.MemoryAgent", return_value=stub_memory_agent):
            agent = ChatControllerAgent(config_path="config/agents/chat/chat_controller.yaml")

    assert agent.tool_defaults["get_current_time"]["timezone_name"] == "UTC"
