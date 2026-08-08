"""Adapter-authored stimulus_view coverage for Chat / Feedback / Schedule."""

from __future__ import annotations

from m_agent.runtime.perception.chat_adapter import ChatSignal, ChatSourceAdapter
from m_agent.runtime.perception.feedback_adapter import (
    FeedbackSignal,
    FeedbackSourceAdapter,
    render_feedback_stimulus_view,
)
from m_agent.runtime.perception.observation import (
    CHAT_STIMULUS_VIEW,
    observation_to_envelope,
)
from m_agent.runtime.perception.schedule_adapter import (
    ScheduleSignal,
    ScheduleSourceAdapter,
    render_schedule_stimulus_view,
)
from m_agent.sdk.stimulus.contracts import IngestResult


class _FakeRuntime:
    def __init__(self) -> None:
        self.observations = []

    def ingest(self, observation, *, schedule_drainer: bool = True):
        self.observations.append((observation, schedule_drainer))
        return IngestResult(
            stimulus_id="stim_test",
            pool_state="ready",
            created=True,
        )


def test_chat_adapter_stimulus_view_is_utterance_pointer() -> None:
    runtime = _FakeRuntime()
    adapter = ChatSourceAdapter(runtime)
    observation = adapter.to_observation(
        ChatSignal(text="hello", message_id="m1", occurred_at="2026-08-01T00:00:00Z"),
        thread_id="t1",
        conversation_id="t1::0",
    )
    assert observation.stimulus_view == CHAT_STIMULUS_VIEW
    assert "hello" not in observation.stimulus_view
    envelope = observation_to_envelope(observation)
    assert envelope.payload["stimulus_view"] == CHAT_STIMULUS_VIEW


def test_feedback_adapter_view_contains_provable_tool_results() -> None:
    view = render_feedback_stimulus_view(
        tool_history=[
            {
                "tool_name": "schedule_create",
                "result": {
                    "success": True,
                    "count": 1,
                    "partial": False,
                    "summary": "created one item",
                },
            }
        ],
        summary="ok",
    )
    assert "kind: execution_feedback" in view
    assert "semantic_role: provable_tool_results" in view
    assert "tool_name: schedule_create" in view
    assert "success: True" in view
    assert "count: 1" in view
    assert "created one item" in view

    runtime = _FakeRuntime()
    adapter = FeedbackSourceAdapter(runtime)
    result = adapter.handle_feedback(
        FeedbackSignal(
            tool_history=[
                {
                    "tool_name": "schedule_create",
                    "result": {"success": True, "count": 1, "summary": "created"},
                }
            ],
            summary="created",
            delegate_id="dlg-1",
            activation_id="act-1",
            effect_id="eff-1",
            ingress_key="effect:eff-1",
        ),
        thread_id="t1",
        conversation_id="t1::0",
        transaction_id="txn-1",
        schedule_drainer=False,
    )
    assert result.stimulus_id == "stim_test"
    observation, schedule_drainer = runtime.observations[0]
    assert schedule_drainer is False
    assert observation.type == "execution_feedback"
    assert "provable_tool_results" in observation.stimulus_view
    envelope = observation_to_envelope(observation)
    assert envelope.delegate_id == "dlg-1"
    assert envelope.activation_id == "act-1"
    assert envelope.ingress_key == "effect:eff-1"
    assert envelope.stimulus_id == "stim_eff-1"


def test_schedule_adapter_view_is_todo_not_completion_proof() -> None:
    view = render_schedule_stimulus_view(
        schedule_id="sch-1",
        deferred_objective="Remind the user to cook",
        due_at_utc="2026-08-01T09:25:29Z",
    )
    assert "kind: scheduled_plan" in view
    assert "semantic_role: schedule_due_todo" in view
    assert "Remind the user to cook" in view
    assert "not proof that the work was completed" in view

    runtime = _FakeRuntime()
    adapter = ScheduleSourceAdapter(runtime)
    result = adapter.handle_due(
        ScheduleSignal(
            schedule_id="sch-1",
            text="Remind the user to cook",
            run_id="run-1",
            delivery_id="del-1",
            payload={
                "due_at_utc": "2026-08-01T09:25:29Z",
                "timezone_name": "UTC",
            },
        ),
        thread_id="t1",
        conversation_id="t1::0",
    )
    assert result.created is True
    observation, _ = runtime.observations[0]
    assert observation.type == "scheduled_plan"
    assert observation.idempotency_key == "schedule_delivery:del-1"
    assert "schedule_due_todo" in observation.stimulus_view
    assert "Remind the user to cook" in observation.stimulus_view
    envelope = observation_to_envelope(observation)
    assert envelope.schedule_id == "sch-1"
    assert envelope.schedule_run_id == "run-1"
    assert envelope.payload["stimulus_view"] == observation.stimulus_view
