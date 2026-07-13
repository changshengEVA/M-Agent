"""Capability traces emitted during direct invocation reach the SSE projector.

``ExecutionAgent`` uses ``m_agent.layers.execution.core``. For ``tool_call``
and ``tool_result`` SSE events to keep working, the parent execution namespace
must be in the trace logger list so child records reach the attached handler.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from m_agent.api.chat_api_records import TRACE_LOGGER_NAMES, _attach_trace_handler, _detach_trace_handler
from m_agent.api.chat_api_records import TraceEventProjector
from m_agent.utils.logging_trace import FunctionTraceHandler, TraceEvent


def test_execution_namespace_is_in_trace_logger_names() -> None:
    assert "m_agent.layers.execution" in TRACE_LOGGER_NAMES
    assert "m_agent.layers.thinking" in TRACE_LOGGER_NAMES


def test_child_logger_emit_is_captured_and_projects_to_tool_call_event() -> None:
    """End-to-end: emit a TOOL CALL DETAIL log on the execution child logger,
    expect the FunctionTraceHandler to receive it and the projector to map it
    to a ``tool_call`` SSE event payload."""
    received: List[TraceEvent] = []

    def _callback(event: TraceEvent) -> None:
        received.append(event)

    handler = FunctionTraceHandler(callback=_callback, include_non_api=True)
    attached = _attach_trace_handler(handler)
    try:
        # ControllerCapabilityContext receives this module logger from
        # ExecutionAgent's direct-invocation path.
        child = logging.getLogger("m_agent.layers.execution.core")
        payload = {
            "call_id": 1,
            "tool_name": "shallow_recall",
            "params": {"question": "hi"},
            "thread_id": "t1",
        }
        child.info("TOOL CALL DETAIL: %s", json.dumps(payload, ensure_ascii=False))
    finally:
        _detach_trace_handler(handler, attached)

    assert received, "FunctionTraceHandler did not receive any trace events"
    # Find the TOOL CALL one
    tool_call_events = [
        event for event in received if "TOOL CALL DETAIL" in str(event.raw_message or "")
    ]
    assert tool_call_events, "expected TOOL CALL DETAIL trace to be captured"

    projected = TraceEventProjector.project(tool_call_events[0])
    assert projected is not None
    assert projected["type"] == "tool_call"
    assert projected["payload"]["tool_name"] == "shallow_recall"
    assert projected["payload"]["call_id"] == 1
