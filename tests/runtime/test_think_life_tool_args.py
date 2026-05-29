"""Think-life param LLM tool-arg resolution."""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from m_agent.layers.execution.core import ExecutionAgent
from m_agent.layers.execution.model_provider import ModelProvider
from m_agent.runtime.think_life.scheduler.delegate import (
    DelegateTarget,
    resolve_delegate_tool_input,
    uses_param_llm,
)
from m_agent.runtime.think_life.scheduler.tool_runner import build_tool_input
from m_agent.systems.episodic import EpisodeQueryModule


class _FakeBoundModel:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.last_messages: List[Dict[str, str]] = []

    def invoke(self, messages: List[Dict[str, str]]) -> Any:
        self.last_messages = list(messages)
        return self._response


class _FakeChatModel:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.bound_tools: List[Any] = []
        self.tool_choice: Any = None

    def bind_tools(self, tools: List[Any], tool_choice: Any = None) -> _FakeBoundModel:
        self.bound_tools = list(tools)
        self.tool_choice = tool_choice
        return _FakeBoundModel(self._response)


class _FakeToolCallMessage:
    def __init__(self, tool_calls: List[Dict[str, Any]]) -> None:
        self.tool_calls = tool_calls


def _execution_agent(*, model: Any) -> ExecutionAgent:
    provider = ModelProvider(
        model=model,
        network_retry_attempts=1,
        network_retry_backoff_seconds=0.0,
    )
    return ExecutionAgent(
        model_provider=provider,
        enabled_capability_names=["email_ask", "get_current_time", "reply_to_user"],
        capability_descriptions={
            "email_ask": "Search mail",
            "get_current_time": "Current time",
            "reply_to_user": "Reply",
        },
        tool_defaults={"email_ask": {"mail_scope": "unread"}},
        episode_query_module=EpisodeQueryModule(enabled=False),
        email_agent_provider=lambda: MagicMock(),
    )


def test_uses_param_llm_skips_reply_and_time() -> None:
    assert uses_param_llm("email_ask") is True
    assert uses_param_llm("get_current_time") is False
    assert uses_param_llm("reply_to_user", for_user_reply=True) is False


def test_resolve_delegate_tool_input_skips_param_llm_for_get_current_time() -> None:
    agent = MagicMock()
    target = DelegateTarget(tool_name="get_current_time", instruction="现在几点")
    payload = resolve_delegate_tool_input(agent, target, thread_id="t1")
    assert payload == {}
    agent.fill_tool_args.assert_not_called()


def test_resolve_delegate_tool_input_calls_fill_tool_args() -> None:
    agent = MagicMock()
    agent.fill_tool_args.return_value = {"keywords": "", "mail_scope": "unread"}
    target = DelegateTarget(
        tool_name="email_ask",
        instruction="用户想查看有没有新邮件，请检索未读邮件。",
    )
    payload = resolve_delegate_tool_input(
        agent,
        target,
        thread_id="t1",
        correlation_id="dlg_x",
        pending_user_request="有什么新邮件吗",
    )
    assert payload == {"keywords": "", "mail_scope": "unread"}
    agent.fill_tool_args.assert_called_once_with(
        tool_name="email_ask",
        instruction=target.instruction,
        thread_id="t1",
        pending_user_request="有什么新邮件吗",
        correlation_id="dlg_x",
    )


def test_resolve_delegate_tool_input_falls_back_on_param_failure() -> None:
    agent = MagicMock()
    agent.fill_tool_args.side_effect = RuntimeError("no tool call")
    target = DelegateTarget(tool_name="email_ask", instruction="查未读")
    payload = resolve_delegate_tool_input(agent, target, thread_id="t1")
    assert payload == build_tool_input("email_ask", instruction="查未读")


def test_extract_tool_call_args_from_tool_calls() -> None:
    message = _FakeToolCallMessage(
        [{"name": "email_ask", "args": {"keywords": "", "mail_scope": "unread"}}]
    )
    args = ExecutionAgent._extract_tool_call_args(message, expected_name="email_ask")
    assert args == {"keywords": "", "mail_scope": "unread"}


def test_fill_tool_args_binds_single_tool_and_returns_args() -> None:
    fake_model = _FakeChatModel(
        _FakeToolCallMessage(
            [{"name": "email_ask", "args": {"keywords": "", "mail_scope": "unread"}}]
        )
    )
    agent = _execution_agent(model=fake_model)
    fake_tool = MagicMock()
    fake_tool.name = "email_ask"
    agent._build_single_tool_object = MagicMock(return_value=fake_tool)  # type: ignore[method-assign]

    args = agent.fill_tool_args(
        tool_name="email_ask",
        instruction="用户想查看有没有新邮件，请检索未读邮件。",
        thread_id="thread-1",
        pending_user_request="有什么新邮件吗",
        correlation_id="dlg_test",
    )

    assert args == {"keywords": "", "mail_scope": "unread"}
    assert fake_model.tool_choice == "email_ask"
    assert fake_model.bound_tools == [fake_tool]
    agent._build_single_tool_object.assert_called_once_with(  # type: ignore[attr-defined]
        tool_name="email_ask",
        thread_id="thread-1",
    )


def test_fill_tool_args_raises_when_no_tool_call() -> None:
    fake_model = _FakeChatModel(_FakeToolCallMessage([]))
    agent = _execution_agent(model=fake_model)
    agent._build_single_tool_object = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="no tool call"):
        agent.fill_tool_args(
            tool_name="email_ask",
            instruction="查未读",
            thread_id="thread-1",
        )
