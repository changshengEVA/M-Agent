from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from m_agent.agents.memory_agent.visual_api import MemoryRunRecord, MemoryTraceProjector
from m_agent.utils.logging_trace import TraceEvent


def _trace(raw_message: str) -> TraceEvent:
    return TraceEvent(
        timestamp="12:00:00",
        logger_name="m_agent.agents.memory_agent",
        level_name="INFO",
        phase=None,
        function_name=None,
        detail=raw_message,
        raw_message=raw_message,
    )


def test_memory_trace_projector_maps_tool_and_workspace_events() -> None:
    projected_tool = MemoryTraceProjector.project(
        _trace('TOOL CALL DETAIL: {"call_id": 1, "tool_name": "search_details", "status": "started"}')
    )
    assert projected_tool is not None
    assert projected_tool["type"] == "tool_call"
    assert projected_tool["payload"]["tool_name"] == "search_details"

    projected_ws = MemoryTraceProjector.project(
        _trace('WORKSPACE STATE: {"phase":"round_judged","round_id":1,"status":"SUFFICIENT"}')
    )
    assert projected_ws is not None
    assert projected_ws["type"] == "workspace_state"
    assert projected_ws["payload"]["status"] == "SUFFICIENT"


def test_memory_run_record_tracks_tool_calls_and_workspace_state() -> None:
    record = MemoryRunRecord(
        run_id="memory_run_test",
        question="What happened?",
        recall_mode="deep",
        config_path="config/agents/memory/locomo_eval_memory_agent.yaml",
        thread_id="ui-thread",
    )

    record.start()
    record.append_trace_event(
        _trace('TOOL CALL DETAIL: {"call_id": 1, "tool_name": "search_details", "status": "started"}')
    )
    record.append_trace_event(
        _trace('TOOL RESULT DETAIL: {"call_id": 1, "tool_name": "search_details", "status": "completed", "result":{"hit":true}}')
    )
    record.append_trace_event(
        _trace('WORKSPACE STATE: {"phase":"round_judged","round_id":1,"status":"INSUFFICIENT"}')
    )
    record.complete({"answer": "done", "evidence": "ok"})

    tool_snapshot = record.tool_calls_snapshot()
    assert tool_snapshot["tool_call_count"] == 1
    assert tool_snapshot["calls"][0]["status"] == "completed"

    workspace_snapshot = record.workspace_snapshot(limit=10)
    assert workspace_snapshot["workspace_event_count"] == 1
    assert workspace_snapshot["latest_workspace"]["status"] == "INSUFFICIENT"

    run_snapshot = record.snapshot()
    assert run_snapshot["status"] == "completed"
    assert run_snapshot["result"]["answer"] == "done"
