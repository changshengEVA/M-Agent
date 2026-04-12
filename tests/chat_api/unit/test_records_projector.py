from __future__ import annotations

import pytest

from m_agent.api.chat_api_records import TraceEventProjector
from m_agent.utils.logging_trace import TraceEvent


pytestmark = pytest.mark.unit


def _trace(raw_message: str) -> TraceEvent:
    return TraceEvent(
        timestamp="00:00:00",
        logger_name="test.logger",
        level_name="INFO",
        phase=None,
        function_name=None,
        detail="",
        raw_message=raw_message,
    )


def test_trace_projector_maps_tool_call_payload() -> None:
    event = _trace('TOOL CALL DETAIL: {"call_id":"1","tool_name":"search_details","status":"started"}')

    projected = TraceEventProjector.project(event)

    assert projected is not None
    assert projected["type"] == "tool_call"
    assert projected["payload"]["tool_name"] == "search_details"


def test_trace_projector_ignores_unrecognized_messages() -> None:
    event = _trace("some plain log line")

    projected = TraceEventProjector.project(event)

    assert projected is None
