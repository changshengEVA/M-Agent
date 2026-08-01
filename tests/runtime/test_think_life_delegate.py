"""Think-life single-tool delegate resolution."""
from __future__ import annotations

from m_agent.layers.thinking.contracts import ThinkingDecision
from m_agent.runtime.think_life.scheduler.delegate import (
    REPLY_TOOL_NAME,
    plan_delegate,
    resolve_tool_name,
)
from m_agent.runtime.think_life.scheduler.tool_runner import build_tool_input


def test_resolve_tool_name_from_decision() -> None:
    decision = ThinkingDecision(mode="execute", tool_name="shallow_recall", instruction="查昨天")
    assert resolve_tool_name(decision, enabled_tools=["shallow_recall", "email_send"]) == "shallow_recall"


def test_resolve_tool_name_requires_tool_name() -> None:
    decision = ThinkingDecision(
        mode="execute",
        instruction="现在几点",
    )
    assert (
        resolve_tool_name(decision, enabled_tools=["get_current_time", "shallow_recall"])
        is None
    )


def test_plan_delegate_reply() -> None:
    decision = ThinkingDecision(mode="answer_directly", answer="你好")
    planned = plan_delegate(
        decision,
        enabled_tools=[REPLY_TOOL_NAME, "shallow_recall"],
        for_user_reply=True,
        user_reply_text="你好",
    )
    assert planned is not None
    tool_name, tool_input = planned
    assert tool_name == REPLY_TOOL_NAME
    assert tool_input.get("message") == "你好"
    assert tool_input.get("finalize") is True


def test_build_tool_input_schedule_create() -> None:
    payload = build_tool_input(
        "schedule_create",
        instruction="2026-05-30T06:00:00+08:00",
    )
    assert payload == {"due_at": "2026-05-30T06:00:00+08:00"}


def test_build_tool_input_schedule_delete() -> None:
    payload = build_tool_input(
        "schedule_delete",
        instruction="sch_abc123def456",
    )
    assert payload == {"schedule_id": "sch_abc123def456"}
