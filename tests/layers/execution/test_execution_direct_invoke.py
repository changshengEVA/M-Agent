"""Tests for single-capability direct invocation."""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from m_agent.layers.execution.core import ExecutionAgent
from m_agent.layers.execution.model_provider import ModelProvider


def _agent() -> ExecutionAgent:
    provider = ModelProvider(
        model=MagicMock(),
        network_retry_attempts=1,
        network_retry_backoff_seconds=0.0,
    )
    return ExecutionAgent(
        model_provider=provider,
        enabled_capability_names=["get_current_time"],
        capability_descriptions={"get_current_time": "time"},
        tool_defaults={"__controller__": {"max_calls_per_turn": 12}},
    )


def test_invoke_tool_direct_records_history() -> None:
    agent = _agent()
    captured: Dict[str, Any] = {}

    def _build_tools(**kwargs: Any) -> list[Any]:
        context = kwargs["context"]
        captured["context"] = context

        class _RecordingTool:
            def invoke(self, payload: Dict[str, Any]) -> Dict[str, Any]:
                result = {"success": True, "answer": "2026-05-29"}
                context.record_tool_use("get_current_time", payload, result)
                return result

        return [_RecordingTool()]

    with patch(
        "m_agent.layers.execution.core.build_controller_tools",
        side_effect=_build_tools,
    ):
        result = agent.invoke_tool_direct(
            tool_name="get_current_time",
            tool_input={},
            thread_id="t1",
            correlation_id="dlg_1",
        )
    assert result.success is True
    assert result.tool_call_count == 1
    assert result.tool_names == ["get_current_time"]
    assert result.raw["direct_invoke"] is True
    context = captured["context"]
    assert context.tool_defaults["__controller__"]["max_calls_per_turn"] == 1
    assert context.tool_defaults["get_current_time"]["max_calls_per_turn"] == 1
    assert agent.tool_defaults["__controller__"]["max_calls_per_turn"] == 12


def test_invoke_tool_direct_returns_structured_failure() -> None:
    agent = _agent()
    failing_tool = MagicMock()
    failing_tool.invoke.side_effect = RuntimeError("clock unavailable")

    with patch(
        "m_agent.layers.execution.core.build_controller_tools",
        return_value=[failing_tool],
    ):
        result = agent.invoke_tool_direct(
            tool_name="get_current_time",
            tool_input={"timezone_name": "UTC"},
            thread_id="t1",
            correlation_id="dlg_2",
        )

    assert result.success is False
    assert result.tool_call_count == 1
    assert result.tool_names == ["get_current_time"]
    assert "clock unavailable" in result.summary


def test_invoke_tool_direct_rejects_disabled_tool() -> None:
    agent = _agent()

    with pytest.raises(ValueError, match="Unknown or disabled tool"):
        agent.invoke_tool_direct(
            tool_name="deep_recall",
            tool_input={},
            thread_id="t1",
        )
