"""Protocol coverage for Think-life planning events and summaries.

These tests guard the frontend-facing allow-list and the concise operator-log
projection for each event emitted by the plan-only thinking layer.
"""
from __future__ import annotations

import pytest

from m_agent.api.chat_api_protocol import (
    _PROTOCOL_SSE_EVENTS,
    _summarize_event_payload,
)


@pytest.mark.parametrize(
    "event_type",
    [
        "thinking_started",
        "thinking_task_state",
        "thinking_plan",
        "thinking_completed",
    ],
)
def test_think_life_planning_events_are_in_protocol_whitelist(
    event_type: str,
) -> None:
    assert event_type in _PROTOCOL_SSE_EVENTS, (
        f"{event_type!r} missing from _PROTOCOL_SSE_EVENTS; FastAPI protocol "
        f"channel will drop it from operator logs"
    )


def test_summarize_thinking_plan_returns_mode_and_instruction_excerpt() -> None:
    payload = {
        "mode": "execute",
        "instruction": "Find yesterday's travel plan",
        "capability_hint": ["deep_recall", "shallow_recall"],
    }

    text = _summarize_event_payload("thinking_plan", payload)

    assert "mode=execute" in text
    assert "yesterday's travel" in text


def test_summarize_thinking_task_state_returns_goal_and_step_counts() -> None:
    payload = {
        "task_progress": {
            "goal": "Plan a trip",
            "completed": ["pick a city"],
            "remaining": ["book train", "book hotel"],
        }
    }

    text = _summarize_event_payload("thinking_task_state", payload)

    assert "goal=Plan a trip" in text
    assert "completed=1" in text
    assert "remaining=2" in text


def test_summarize_thinking_completed_lists_plan_phase() -> None:
    payload = {"executed": False, "phases": ["plan"]}

    text = _summarize_event_payload("thinking_completed", payload)

    assert "executed=False" in text
    assert "phases=plan" in text
