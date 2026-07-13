"""Plan-only flow tests for :class:`ThinkingAgent`.

The fake model returns deterministic task-state updates and decisions.  These
tests deliberately give the thinking layer a capability catalog that raises
if invocation is attempted: Think-life delegates execution from its scheduler,
never from ``ThinkingAgent.handle``.
"""
from __future__ import annotations

from typing import Any, Dict, List

from m_agent.layers.execution.model_provider import ModelProvider
from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.layers.thinking import (
    ConversationStateRegistry,
    PerceptionInput,
    TaskProgressUpdate,
    ThinkingAgent,
    ThinkingDecision,
)
from m_agent.systems.episodic import DefaultEpisodeRecorder


class _FakeStructuredModel:
    def __init__(self, responses: List[Any]) -> None:
        self._queue = list(responses)
        self.calls: List[List[Dict[str, str]]] = []

    def invoke(self, messages: List[Dict[str, str]]) -> Any:
        self.calls.append(list(messages))
        if not self._queue:
            raise AssertionError("FakeStructuredModel ran out of canned responses")
        return self._queue.pop(0)


class _FakeChatModel:
    def __init__(self, schema_to_queue: Dict[type, List[Any]]) -> None:
        self._schema_to_queue = schema_to_queue
        self._created: Dict[type, _FakeStructuredModel] = {}

    def with_structured_output(self, schema: type, **_: Any) -> _FakeStructuredModel:
        if schema not in self._created:
            self._created[schema] = _FakeStructuredModel(
                self._schema_to_queue.get(schema, [])
            )
        return self._created[schema]

    def structured_calls(self, schema: type) -> List[List[Dict[str, str]]]:
        bound = self._created.get(schema)
        return [] if bound is None else list(bound.calls)


class _CapabilityCatalog:
    def describe_capabilities_block(self) -> str:
        return "[Available Tools]\n- deep_recall"

    def fill_tool_args(self, **_: Any) -> None:
        raise AssertionError("ThinkingAgent must not fill tool arguments")

    def invoke_tool_direct(self, **_: Any) -> None:
        raise AssertionError("ThinkingAgent must not invoke tools")


def _make_perception(**overrides: Any) -> PerceptionInput:
    text = str(overrides.pop("user_message", "hello"))
    source = str(overrides.pop("source", "user"))
    payload = dict(overrides.pop("system_context", {}) or {})
    history = list(overrides.pop("history_messages", []) or [])
    kind = {
        "user": StimulusKind.USER_MESSAGE,
        "schedule": StimulusKind.SCHEDULED_PLAN,
        "execution_feedback": StimulusKind.EXECUTION_FEEDBACK,
    }.get(source, StimulusKind.OBSERVATION_TRIGGER)
    defaults: Dict[str, Any] = {
        "thread_id": "t1",
        "conversation_id": "t1::0",
        "transaction_id": "txn-1",
        "stimulus": Stimulus(kind=kind, text=text, payload=payload),
        "dialogue_history": history,
    }
    defaults.update(overrides)
    return PerceptionInput(**defaults)


def _make_agent(
    *,
    decisions: List[ThinkingDecision],
    task_updates: List[TaskProgressUpdate] | None = None,
    prompt_language: str = "en",
) -> tuple[ThinkingAgent, _FakeChatModel]:
    updates = (
        list(task_updates)
        if task_updates is not None
        else [TaskProgressUpdate() for _ in decisions]
    )
    fake_model = _FakeChatModel(
        {
            TaskProgressUpdate: updates,
            ThinkingDecision: decisions,
        }
    )
    agent = ThinkingAgent(
        execution_agent=_CapabilityCatalog(),  # type: ignore[arg-type]
        model_provider=ModelProvider(model=fake_model, network_retry_attempts=1),
        system_prompt="You are a memory assistant.",
        persona_prompt="",
        episode_recorder=DefaultEpisodeRecorder(),
        state_registry=ConversationStateRegistry(),
        prompt_language=prompt_language,
    )
    return agent, fake_model


def test_answer_directly_returns_answer_and_buffers_episode_note() -> None:
    agent, fake_model = _make_agent(
        decisions=[
            ThinkingDecision(
                mode="answer_directly",
                answer="Hello!",
                episode_note="user greeted the assistant",
            )
        ]
    )

    turn = agent.handle(_make_perception())

    assert turn.answer == "Hello!"
    assert turn.mode == "answer_directly"
    assert len(fake_model.structured_calls(TaskProgressUpdate)) == 1
    assert len(fake_model.structured_calls(ThinkingDecision)) == 1
    state = agent.snapshot_conversation("t1::0")
    assert state is not None
    assert state.turn_count == 1
    assert any(
        entry["note"] == "user greeted the assistant"
        for entry in state.episode_buffer
    )


def test_execute_decision_is_returned_without_inline_tool_work() -> None:
    decision = ThinkingDecision(
        mode="execute",
        tool_name="deep_recall",
        instruction="Find the relevant travel detail",
        request_complete=False,
    )
    agent, _ = _make_agent(decisions=[decision])

    turn = agent.handle(_make_perception(user_message="What did I plan?"))

    assert turn is decision
    assert turn.answer is None
    assert turn.tool_name == "deep_recall"
    assert turn.instruction == "Find the relevant travel detail"


def test_silent_decision_has_no_fallback_answer() -> None:
    agent, _ = _make_agent(
        decisions=[ThinkingDecision(mode="silent", reasoning="No reply needed")]
    )

    turn = agent.handle(_make_perception())

    assert turn.answer is None


def test_task_progress_update_is_rendered_in_next_plan_prompt() -> None:
    agent, fake_model = _make_agent(
        decisions=[
            ThinkingDecision(
                mode="execute",
                tool_name="deep_recall",
                instruction="Find the travel detail",
            ),
            ThinkingDecision(mode="answer_directly", answer="Done"),
        ],
        task_updates=[
            TaskProgressUpdate(
                goal="answer the travel question",
                completed=["identify the trip"],
                remaining=["find the departure time"],
            ),
            TaskProgressUpdate(),
        ],
    )

    first = agent.handle(_make_perception(user_message="Check my travel plan"))
    second = agent.handle(_make_perception(user_message="Continue"))

    assert first.answer is None
    assert second.answer == "Done"
    state = agent.snapshot_conversation("t1::0")
    assert state is not None
    assert state.task_progress.goal == "answer the travel question"
    assert state.task_progress.completed == ["identify the trip"]
    assert state.task_progress.remaining == ["find the departure time"]

    second_plan_prompt = fake_model.structured_calls(ThinkingDecision)[1][0]["content"]
    assert "[Task State]" in second_plan_prompt
    assert "goal: answer the travel question" in second_plan_prompt
    assert "identify the trip" in second_plan_prompt
    assert "find the departure time" in second_plan_prompt


def test_current_stimulus_is_rendered_in_both_thinking_prompts() -> None:
    agent, fake_model = _make_agent(
        decisions=[ThinkingDecision(mode="answer_directly", answer="OK")],
        task_updates=[TaskProgressUpdate(goal="handle schedule")],
    )

    agent.handle(
        _make_perception(
            user_message="Reminder fired",
            source="schedule",
            system_context={"schedule_id": "sch_1"},
        )
    )

    task_prompt = fake_model.structured_calls(TaskProgressUpdate)[0][0]["content"]
    decision_prompt = fake_model.structured_calls(ThinkingDecision)[0][0]["content"]
    for prompt in (task_prompt, decision_prompt):
        assert "[Current Stimulus]" in prompt
        assert "kind: scheduled_plan" in prompt
        assert "Reminder fired" in prompt
        assert '"schedule_id": "sch_1"' not in prompt


def test_on_flush_drops_standalone_state_and_returns_episode_notes() -> None:
    agent, _ = _make_agent(
        decisions=[
            ThinkingDecision(
                mode="answer_directly",
                answer="OK",
                episode_note="note-A",
            )
        ]
    )
    agent.handle(_make_perception())

    drained = agent.on_flush("t1::0", thread_id="t1")

    assert drained[0]["note"] == "note-A"
    assert agent.snapshot_conversation("t1::0") is None


def test_event_emitter_reports_plan_only_phases_for_execute_decision() -> None:
    agent, _ = _make_agent(
        decisions=[
            ThinkingDecision(
                mode="execute",
                tool_name="deep_recall",
                instruction="Find yesterday's plan",
            )
        ]
    )
    events: List[tuple[str, Dict[str, Any]]] = []

    agent.handle(
        _make_perception(),
        event_emitter=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
    )

    assert [event_type for event_type, _ in events] == [
        "thinking_started",
        "thinking_task_state",
        "thinking_plan",
        "thinking_completed",
    ]
    plan_payload = next(
        payload for event_type, payload in events if event_type == "thinking_plan"
    )
    assert plan_payload["mode"] == "execute"
    assert plan_payload["tool_name"] == "deep_recall"
    completed_payload = events[-1][1]
    assert completed_payload["executed"] is False
    assert completed_payload["phases"] == ["plan"]


def test_event_emitter_exception_does_not_break_handler() -> None:
    agent, _ = _make_agent(
        decisions=[ThinkingDecision(mode="answer_directly", answer="Hi")]
    )

    def _bad_emitter(_event_type: str, _payload: Dict[str, Any]) -> None:
        raise RuntimeError("client-side bug")

    turn = agent.handle(_make_perception(), event_emitter=_bad_emitter)

    assert turn.answer == "Hi"


def test_direct_answer_without_text_uses_configured_fallback() -> None:
    fake_model = _FakeChatModel(
        {
            TaskProgressUpdate: [TaskProgressUpdate()],
            ThinkingDecision: [ThinkingDecision(mode="answer_directly")],
        }
    )
    agent = ThinkingAgent(
        execution_agent=_CapabilityCatalog(),  # type: ignore[arg-type]
        model_provider=ModelProvider(model=fake_model, network_retry_attempts=1),
        system_prompt="System",
        episode_recorder=DefaultEpisodeRecorder(),
        state_registry=ConversationStateRegistry(),
        prompt_language="en",
        fallback_answer_prompt="Configured fallback",
    )

    turn = agent.handle(_make_perception())

    assert turn.answer == "Configured fallback"
