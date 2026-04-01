from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from m_agent.agents.chat_controller_agent import ChatControllerAgent


def _build_test_agent() -> ChatControllerAgent:
    stub_memory_agent = SimpleNamespace(
        model="fake-model",
        recursion_limit=8,
        retry_recursion_limit=16,
        network_retry_attempts=1,
        _compute_network_retry_delay=lambda attempt: 0.0,
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
    }

    with mock.patch.object(ChatControllerAgent, "_load_config", return_value=chat_config):
        with mock.patch("m_agent.agents.chat_controller_agent.MemoryAgent", return_value=stub_memory_agent):
            return ChatControllerAgent(config_path="config/agents/chat/chat_controller.yaml")


def test_build_chat_controller_registers_current_time_tool() -> None:
    agent = _build_test_agent()
    captured: dict[str, object] = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    with mock.patch("m_agent.agents.chat_controller_agent.create_agent", side_effect=fake_create_agent):
        agent._build_chat_controller(
            active_thread_id="demo-thread",
            recall_state={"mode": None, "result": None, "history": []},
            controller_state={"history": [], "call_seq": 0},
        )

    tools = captured["tools"]
    tool_names = [tool.name for tool in tools]

    assert "get_current_time" in tool_names

    time_tool = next(tool for tool in tools if tool.name == "get_current_time")
    result = time_tool.invoke({})

    assert result["ok"] is True
    assert "local_datetime" in result


def test_chat_reports_current_time_tool_usage_without_recall() -> None:
    agent = _build_test_agent()

    def fake_build_chat_controller(*, active_thread_id, recall_state, controller_state):
        controller_state["history"].append(
            {
                "tool_name": "get_current_time",
                "params": {"timezone_name": "UTC"},
                "result": {"ok": True, "timezone_name": "UTC"},
            }
        )
        return SimpleNamespace()

    with mock.patch.object(agent, "_build_chat_controller", side_effect=fake_build_chat_controller):
        with mock.patch.object(
            agent,
            "_invoke_chat_controller",
            return_value={"structured_response": {"answer": "It is 2026-03-31 09:30 UTC."}},
        ):
            result = agent.chat("What time is it now?")

    agent_result = result["agent_result"]

    assert result["answer"] == "It is 2026-03-31 09:30 UTC."
    assert agent_result["tool_call_count"] == 1
    assert agent_result["controller_tool_count"] == 1
    assert agent_result["controller_tool_names"] == ["get_current_time"]
    assert agent_result["plan_summary"] == "This chat turn used the current-time tool."
