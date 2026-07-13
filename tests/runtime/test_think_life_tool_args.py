"""Think-life param LLM tool-arg resolution."""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from m_agent.layers.execution.contracts import ParamFillResult
from m_agent.layers.execution.core import ExecutionAgent, _ParamFillOutcome
from m_agent.layers.execution.model_provider import ModelProvider
from m_agent.runtime.think_life.scheduler.delegate import (
    DelegateTarget,
    resolve_delegate_tool_input,
    uses_param_llm,
)
from m_agent.runtime.think_life.scheduler.tool_runner import (
    THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG,
    build_tool_input,
)
from m_agent.systems.episodic import EpisodeQueryModule


class _FakeStructuredModel:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.last_messages: List[Dict[str, str]] = []

    def invoke(self, messages: List[Dict[str, str]]) -> Any:
        self.last_messages = list(messages)
        return self._response


class _FakeChatModel:
    def __init__(self, response: Any) -> None:
        self._response = response

    def with_structured_output(self, schema: Any, **kwargs: Any) -> _FakeStructuredModel:
        return _FakeStructuredModel(self._response)


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
    assert uses_param_llm("shallow_recall") is False
    assert uses_param_llm("deep_recall") is False
    assert uses_param_llm("reply_to_user", for_user_reply=True) is False


def test_skip_param_instruction_arg_declares_recall_question() -> None:
    assert THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG["shallow_recall"] == "question"
    assert THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG["deep_recall"] == "question"


def test_build_tool_input_shallow_recall_maps_instruction_to_question() -> None:
    payload = build_tool_input(
        "shallow_recall",
        instruction="用户之前告诉我的邮箱地址",
    )
    assert payload == {"question": "用户之前告诉我的邮箱地址"}


def test_resolve_delegate_tool_input_skips_param_llm_for_get_current_time() -> None:
    agent = MagicMock()
    target = DelegateTarget(tool_name="get_current_time", instruction="现在几点")
    result = resolve_delegate_tool_input(agent, target, thread_id="t1")
    assert result.is_ready
    assert result.args == {}
    agent.fill_tool_args.assert_not_called()


def test_resolve_delegate_tool_input_skips_param_llm_for_shallow_recall() -> None:
    agent = MagicMock()
    target = DelegateTarget(
        tool_name="shallow_recall",
        instruction="用户之前提到的邮箱地址",
    )
    result = resolve_delegate_tool_input(agent, target, thread_id="t1")
    assert result.is_ready
    assert result.args == {"question": "用户之前提到的邮箱地址"}
    agent.fill_tool_args.assert_not_called()


def test_resolve_delegate_tool_input_calls_fill_tool_args() -> None:
    agent = MagicMock()
    agent.fill_tool_args.return_value = ParamFillResult(
        tool_name="email_ask",
        status="ready",
        args={"keywords": "", "mail_scope": "unread"},
    )
    target = DelegateTarget(
        tool_name="email_ask",
        instruction="用户想查看有没有新邮件，请检索未读邮件。",
    )
    result = resolve_delegate_tool_input(
        agent,
        target,
        thread_id="t1",
        correlation_id="dlg_x",
        pending_user_request="有什么新邮件吗",
    )
    assert result.is_ready
    assert result.args == {"keywords": "", "mail_scope": "unread"}
    agent.fill_tool_args.assert_called_once_with(
        tool_name="email_ask",
        instruction=target.instruction,
        thread_id="t1",
        pending_user_request="有什么新邮件吗",
        correlation_id="dlg_x",
    )


def test_resolve_delegate_tool_input_returns_clarify_from_fill_tool_args() -> None:
    agent = MagicMock()
    agent.fill_tool_args.return_value = ParamFillResult(
        tool_name="email_ask",
        status="needs_clarification",
        missing_fields=["keywords"],
        reason="missing_required_args",
    )
    target = DelegateTarget(tool_name="email_ask", instruction="查未读")
    result = resolve_delegate_tool_input(agent, target, thread_id="t1")
    assert result.needs_clarification
    assert result.missing_fields == ["keywords"]


def test_fill_tool_args_returns_ready_args() -> None:
    fake_model = _FakeChatModel(
        _ParamFillOutcome(
            outcome="invoke",
            args={"keywords": "", "mail_scope": "unread"},
        )
    )
    agent = _execution_agent(model=fake_model)
    fake_tool = MagicMock()
    fake_tool.name = "email_ask"
    agent._build_single_tool_object = MagicMock(return_value=fake_tool)  # type: ignore[method-assign]

    result = agent.fill_tool_args(
        tool_name="email_ask",
        instruction="用户想查看有没有新邮件，请检索未读邮件。",
        thread_id="thread-1",
        pending_user_request="有什么新邮件吗",
        correlation_id="dlg_test",
    )

    assert result.is_ready
    assert result.args == {"keywords": "", "mail_scope": "unread"}
    agent._build_single_tool_object.assert_called_once_with(  # type: ignore[attr-defined]
        tool_name="email_ask",
        thread_id="thread-1",
    )


def test_fill_tool_args_returns_clarify_outcome() -> None:
    fake_model = _FakeChatModel(
        _ParamFillOutcome(
            outcome="clarify",
            missing_fields=["event_time"],
            reason="no explicit time in request",
        )
    )
    agent = _execution_agent(model=fake_model)
    agent._build_single_tool_object = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]

    result = agent.fill_tool_args(
        tool_name="email_ask",
        instruction="提醒我起床",
        thread_id="thread-1",
    )

    assert result.needs_clarification
    assert result.missing_fields == ["event_time"]
    assert "time" in result.reason


def test_fill_tool_args_returns_clarify_on_model_failure() -> None:
    class _FailingModel:
        def with_structured_output(self, schema: Any, **kwargs: Any) -> _FakeStructuredModel:
            model = _FakeStructuredModel(None)

            def _fail(messages: List[Dict[str, str]]) -> Any:
                raise RuntimeError("model down")

            model.invoke = _fail  # type: ignore[method-assign]
            return model

    agent = _execution_agent(model=_FailingModel())
    agent._build_single_tool_object = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]

    result = agent.fill_tool_args(
        tool_name="email_ask",
        instruction="查未读",
        thread_id="thread-1",
    )

    assert result.needs_clarification
    assert result.reason == "param_llm_failed"
