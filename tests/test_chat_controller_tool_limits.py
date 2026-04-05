from __future__ import annotations

import logging

from m_agent.chat.capabilities.base import ControllerCapabilityContext
from m_agent.chat.capabilities.recall import (
    _build_deep_recall_tool,
    _build_shallow_recall_tool,
)
from m_agent.chat.capabilities.time_context import _build_get_current_time_tool


class _FakeMemoryAgent:
    def __init__(self) -> None:
        self.shallow_calls: list[dict] = []
        self.deep_calls: list[dict] = []

    def shallow_recall(self, question: str, thread_id: str | None = None) -> dict:
        self.shallow_calls.append({"question": question, "thread_id": thread_id})
        return {"answer": f"shallow:{question}"}

    def deep_recall(self, question: str, thread_id: str | None = None) -> dict:
        self.deep_calls.append({"question": question, "thread_id": thread_id})
        return {"answer": f"deep:{question}"}


def _build_context(*, memory_agent: _FakeMemoryAgent, tool_defaults: dict) -> ControllerCapabilityContext:
    return ControllerCapabilityContext(
        active_thread_id="thread-1",
        memory_agent=memory_agent,
        recall_state={"mode": None, "result": None, "history": []},
        controller_state={"history": [], "call_seq": 0},
        tool_defaults=tool_defaults,
        logger=logging.getLogger("test.chat_controller_tool_limits"),
    )


def test_memory_recall_tools_share_group_limit() -> None:
    memory_agent = _FakeMemoryAgent()
    context = _build_context(
        memory_agent=memory_agent,
        tool_defaults={"memory_recall": {"max_calls_per_turn": 8}},
    )
    shallow_tool = _build_shallow_recall_tool(context, "shallow recall")
    deep_tool = _build_deep_recall_tool(context, "deep recall")

    for idx in range(4):
        result = shallow_tool.invoke({"question": f"shallow-{idx}"})
        assert result["answer"] == f"shallow:shallow-{idx}"

    for idx in range(4):
        result = deep_tool.invoke({"question": f"deep-{idx}"})
        assert result["answer"] == f"deep:deep-{idx}"

    blocked = shallow_tool.invoke({"question": "shallow-over-limit"})

    assert blocked["limit_reached"] is True
    assert blocked["limit_scope"] == "group"
    assert blocked["group_name"] == "memory_recall"
    assert blocked["max_calls_per_turn"] == 8
    assert len(memory_agent.shallow_calls) == 4
    assert len(memory_agent.deep_calls) == 4
    assert len(context.controller_state["history"]) == 8


def test_controller_total_limit_blocks_extra_top_level_calls() -> None:
    memory_agent = _FakeMemoryAgent()
    context = _build_context(
        memory_agent=memory_agent,
        tool_defaults={"__controller__": {"max_calls_per_turn": 2}},
    )
    time_tool = _build_get_current_time_tool(context, "current time")

    first = time_tool.invoke({})
    second = time_tool.invoke({})
    blocked = time_tool.invoke({})

    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert blocked["limit_reached"] is True
    assert blocked["limit_scope"] == "controller"
    assert blocked["max_calls_per_turn"] == 2
    assert len(context.controller_state["history"]) == 2
