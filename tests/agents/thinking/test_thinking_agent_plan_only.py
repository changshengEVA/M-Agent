"""Single-call and rollback coverage for :class:`ThinkingAgent`.

The production path must obtain task state and the current action from one
``ThinkingTurnOutput`` invocation.  The fake model below deliberately owns one
global response queue so a test cannot accidentally hide a second model call
behind a schema-specific queue.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest
from pydantic import ValidationError

from m_agent.layers.execution.model_provider import ModelProvider, StructuredOutputError
from m_agent.layers.perception.contracts import (
    ActivationFrame,
    EventFrame,
    ObjectiveFrame,
    Stimulus,
    StimulusKind,
)
from m_agent.layers.thinking import (
    ConversationStateRegistry,
    DecisionOutput,
    PerceptionInput,
    TaskProgressUpdate,
    TaskStateOutput,
    ThinkingAgent,
    ThinkingDecision,
    ThinkingTurnOutput,
    TransactionResolution,
)
from m_agent.runtime.domain.contracts import TransactionRecord, TransactionState
from m_agent.systems.episodic import DefaultEpisodeRecorder


class _FakeStructuredModel:
    def __init__(self, owner: "_FakeChatModel", schema: type) -> None:
        self._owner = owner
        self._schema = schema

    def invoke(self, messages: List[Dict[str, str]]) -> Any:
        self._owner.calls.append((self._schema, list(messages)))
        if not self._owner.responses:
            raise AssertionError("FakeChatModel ran out of canned responses")
        return self._owner.responses.pop(0)


class _FakeChatModel:
    """Record schema bindings while consuming exactly one shared queue."""

    def __init__(self, responses: List[Any]) -> None:
        self.responses = list(responses)
        self.calls: List[tuple[type, List[Dict[str, str]]]] = []
        self.bindings: List[tuple[type, Dict[str, Any]]] = []
        self.call_names: List[str] = []

    def with_structured_output(
        self,
        schema: type,
        **kwargs: Any,
    ) -> _FakeStructuredModel:
        self.bindings.append((schema, dict(kwargs)))
        return _FakeStructuredModel(self, schema)

    def structured_calls(self, schema: type) -> List[List[Dict[str, str]]]:
        return [messages for bound, messages in self.calls if bound is schema]

    @property
    def called_schemas(self) -> List[type]:
        return [schema for schema, _messages in self.calls]


class _CapabilityCatalog:
    enabled_capability_names = ["deep_recall", "email_ask"]

    def describe_capabilities_block(self) -> str:
        return "[Available Tools]\n- deep_recall\n- email_ask"

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


def _turn(
    *,
    mode: str = "silent",
    reason: str = "The current state supports this action.",
    goal: str = "handle the request",
    completion_status: str = "processing",
    completed: List[str] | None = None,
    remaining: List[str] | None = None,
    tool_name: str | None = None,
    instruction: str | None = None,
    answer: str | None = None,
    episode_note: str | None = None,
) -> ThinkingTurnOutput:
    if mode == "execute":
        tool_name = tool_name or "deep_recall"
        instruction = instruction or "Find the relevant detail"
    elif mode == "answer_directly":
        answer = answer or "OK"
    return ThinkingTurnOutput(
        reason=reason,
        task_state=TaskStateOutput(
            goal=goal,
            completion_status=completion_status,  # type: ignore[arg-type]
            completed=list(completed or []),
            remaining=list(
                ["finish the request"] if remaining is None else remaining
            ),
        ),
        decision=DecisionOutput(
            mode=mode,  # type: ignore[arg-type]
            tool_name=tool_name,
            instruction=instruction,
            answer=answer,
            episode_note=episode_note,
        ),
    )


def _make_agent(
    responses: List[Any],
    *,
    thinking_mode: str = "single_call",
    prompt_language: str = "en",
    structured_retry_attempts: int = 2,
) -> tuple[ThinkingAgent, _FakeChatModel]:
    fake_model = _FakeChatModel(responses)
    provider = ModelProvider(
        model=fake_model,
        network_retry_attempts=1,
        structured_retry_attempts=structured_retry_attempts,
    )
    invoke_with_retry = provider.invoke_with_network_retry

    def _record_call_name(fn: Any, *, call_name: str) -> Any:
        fake_model.call_names.append(call_name)
        return invoke_with_retry(fn, call_name=call_name)

    provider.invoke_with_network_retry = _record_call_name  # type: ignore[method-assign]
    agent = ThinkingAgent(
        execution_agent=_CapabilityCatalog(),  # type: ignore[arg-type]
        model_provider=provider,
        system_prompt="You are a memory assistant.",
        persona_prompt="",
        episode_recorder=DefaultEpisodeRecorder(),
        state_registry=ConversationStateRegistry(),
        prompt_language=prompt_language,
        thinking_mode=thinking_mode,
    )
    return agent, fake_model


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            _turn(
                mode="execute",
                tool_name="deep_recall",
                instruction="Find the travel detail",
            ),
            ("execute", "deep_recall", "Find the travel detail", None),
        ),
        (
            _turn(mode="answer_directly", answer="Hello!"),
            ("answer_directly", None, None, "Hello!"),
        ),
        (
            _turn(mode="silent"),
            ("silent", None, None, None),
        ),
    ],
    ids=["execute", "answer-directly", "silent"],
)
def test_single_call_projects_all_three_decision_modes(
    output: ThinkingTurnOutput,
    expected: tuple[str, str | None, str | None, str | None],
) -> None:
    agent, fake_model = _make_agent([output])

    decision = agent.handle(_make_perception())

    assert (
        decision.mode,
        decision.tool_name,
        decision.instruction,
        decision.answer,
    ) == expected
    assert decision.request_complete is False
    assert fake_model.called_schemas == [ThinkingTurnOutput]
    assert len(fake_model.structured_calls(ThinkingTurnOutput)) == 1
    assert fake_model.call_names == ["thinking.turn"]
    assert fake_model.responses == []


def test_single_call_accepts_a_raw_dict_and_validates_it_as_joint_output() -> None:
    raw = _turn(mode="answer_directly", answer="Validated").model_dump()
    agent, fake_model = _make_agent([raw])

    decision = agent.handle(_make_perception())

    assert decision.answer == "Validated"
    assert fake_model.called_schemas == [ThinkingTurnOutput]


def test_single_call_retries_an_empty_mapping_then_accepts_valid_output() -> None:
    agent, fake_model = _make_agent(
        [{}, _turn(mode="answer_directly", answer="Recovered")],
        structured_retry_attempts=2,
    )

    decision = agent.handle(_make_perception())

    assert decision.answer == "Recovered"
    assert fake_model.called_schemas == [ThinkingTurnOutput, ThinkingTurnOutput]
    assert fake_model.responses == []
    thinking_bindings = [
        kwargs
        for schema, kwargs in fake_model.bindings
        if schema is ThinkingTurnOutput
    ]
    assert thinking_bindings
    assert all(
        kwargs.get("method") == "function_calling"
        for kwargs in thinking_bindings
    )


def test_single_call_exhausts_empty_mappings_and_restores_completion_status() -> None:
    agent, fake_model = _make_agent([{}, {}], structured_retry_attempts=2)
    state = agent.state_registry.get_or_create("t1::0", thread_id="t1")
    state.task_progress.goal = "the already completed request"
    state.task_progress.completion_status = "completed"
    state.task_progress.completed = ["deliver the requested result"]
    state.task_progress.remaining = []

    with pytest.raises(StructuredOutputError) as exc_info:
        agent.handle(_make_perception(user_message="One more thing"))

    assert isinstance(exc_info.value.__cause__, ValidationError)
    assert {
        tuple(error["loc"])
        for error in exc_info.value.__cause__.errors()
        if error["type"] == "missing"
    } == {("reason",), ("task_state",), ("decision",)}
    assert state.task_progress.completion_status == "completed"
    assert state.task_progress.goal == "the already completed request"
    assert state.task_progress.completed == ["deliver the requested result"]
    assert state.task_progress.remaining == []
    assert fake_model.called_schemas == [ThinkingTurnOutput, ThinkingTurnOutput]


def test_current_user_text_appears_once_across_joint_prompt_messages() -> None:
    marker = "UNIQUE-CURRENT-UTTERANCE-7F2A"
    agent, fake_model = _make_agent(
        [_turn(mode="answer_directly", answer="Acknowledged")]
    )

    agent.handle(_make_perception(user_message=marker))

    messages = fake_model.structured_calls(ThinkingTurnOutput)[0]
    assert sum(message["content"].count(marker) for message in messages) == 1
    assert messages[-1] == {"role": "user", "content": marker}


def test_full_task_state_preserves_goal_and_completed_history_across_turns() -> None:
    agent, fake_model = _make_agent(
        [
            _turn(
                mode="execute",
                goal="plan the trip",
                completed=["choose a city"],
                remaining=["book train", "book hotel"],
                instruction="Book the train first",
            ),
            _turn(
                mode="execute",
                goal="",
                completed=["book train", "book train"],
                remaining=["book train", "book hotel", "book hotel"],
                instruction="Book the hotel next",
            ),
        ]
    )

    first = agent.handle(_make_perception(user_message="Plan my trip"))
    second = agent.handle(_make_perception(user_message="Continue"))

    assert first.instruction == "Book the train first"
    assert second.instruction == "Book the hotel next"
    state = agent.snapshot_conversation("t1::0")
    assert state is not None
    assert state.task_progress.goal == "plan the trip"
    assert state.task_progress.completed == ["choose a city", "book train"]
    assert state.task_progress.remaining == ["book hotel"]
    assert fake_model.called_schemas == [ThinkingTurnOutput, ThinkingTurnOutput]

    second_prompt = fake_model.structured_calls(ThinkingTurnOutput)[1][0]["content"]
    assert "[Previous Task State]" in second_prompt
    assert "goal: plan the trip" in second_prompt
    assert "choose a city" in second_prompt
    assert "book train" in second_prompt
    assert "book hotel" in second_prompt


def test_new_user_stimulus_reopens_completed_state_before_joint_prompt() -> None:
    agent, fake_model = _make_agent(
        [_turn(mode="answer_directly", answer="One more answer")]
    )
    state = agent.state_registry.get_or_create("t1::0", thread_id="t1")
    state.task_progress.goal = "the original request"
    state.task_progress.completion_status = "completed"

    decision = agent.handle(_make_perception(user_message="One more thing"))

    assert decision.answer == "One more answer"
    assert state.task_progress.completion_status == "processing"
    prompt = fake_model.structured_calls(ThinkingTurnOutput)[0][0]["content"]
    assert "completion_status: processing" in prompt
    assert "completion_status: completed" not in prompt


def test_param_gap_requires_and_keeps_processing_in_joint_output() -> None:
    agent, fake_model = _make_agent(
        [
            _turn(
                mode="execute",
                completion_status="processing",
                remaining=["find the missing message id"],
                tool_name="email_ask",
                instruction="List recent mail to recover the message id",
            )
        ]
    )
    perception = _make_perception(
        source="execution_feedback",
        user_message="email_read parameter fill failed",
        system_context={
            "tool_history": [
                {
                    "tool_name": "email_read",
                    "result": {
                        "stage": "param_fill",
                        "tool_invoked": False,
                        "needs_clarification": True,
                    },
                }
            ]
        },
    )

    decision = agent.handle(perception)

    state = agent.snapshot_conversation("t1::0")
    assert state is not None
    assert state.task_progress.completion_status == "processing"
    assert decision.mode == "execute"
    assert decision.tool_name == "email_ask"
    assert decision.request_complete is False
    assert fake_model.called_schemas == [ThinkingTurnOutput]


def test_awaiting_user_without_finalized_reply_is_corrected_before_action() -> None:
    agent, fake_model = _make_agent(
        [
            _turn(
                mode="execute",
                completion_status="awaiting_user",
                remaining=["obtain a message id"],
                tool_name="email_ask",
                instruction="Find a usable message id",
            )
        ]
    )

    decision = agent.handle(
        _make_perception(
            source="execution_feedback",
            user_message="More information is required",
        )
    )

    state = agent.snapshot_conversation("t1::0")
    assert state is not None
    assert state.task_progress.completion_status == "processing"
    assert state.task_progress.goal == "handle the request"
    assert state.task_progress.completed == []
    assert state.task_progress.remaining == ["obtain a message id"]
    assert decision.mode == "execute"
    assert decision.tool_name == "email_ask"
    assert decision.request_complete is False
    assert fake_model.called_schemas == [ThinkingTurnOutput]


def test_finalized_awaiting_user_state_forces_silent_decision() -> None:
    agent, fake_model = _make_agent(
        [
            _turn(
                mode="answer_directly",
                completion_status="awaiting_user",
                completed=["ask for the missing time"],
                remaining=["wait for the user's time"],
                answer="This candidate answer must be suppressed",
            )
        ]
    )
    state = agent.state_registry.get_or_create("t1::0", thread_id="t1")
    state.reply_finalized_in_activation = True  # type: ignore[attr-defined]

    decision = agent.handle(
        _make_perception(
            source="execution_feedback",
            user_message="clarification reply delivered",
        )
    )

    assert state.task_progress.completion_status == "awaiting_user"
    assert decision.mode == "silent"
    assert decision.answer is None
    assert decision.request_complete is False
    assert fake_model.called_schemas == [ThinkingTurnOutput]


def test_completed_state_derives_completion_and_suppresses_candidate_action() -> None:
    agent, fake_model = _make_agent(
        [
            _turn(
                mode="answer_directly",
                completion_status="completed",
                completed=["deliver the final answer"],
                remaining=[],
                answer="This duplicate reply must not be sent",
            )
        ]
    )

    decision = agent.handle(
        _make_perception(
            source="execution_feedback",
            user_message="final reply delivery succeeded",
        )
    )

    assert decision.mode == "silent"
    assert decision.answer is None
    assert decision.request_complete is True
    assert fake_model.called_schemas == [ThinkingTurnOutput]


def test_paused_transaction_forces_silent_after_one_joint_call() -> None:
    agent, fake_model = _make_agent([_turn(mode="execute")])
    record = TransactionRecord(
        transaction_id="txn-paused",
        thread_id="t1",
        conversation_id="t1::0",
        state=TransactionState.PAUSE,
    )

    decision = agent.handle(
        _make_perception(transaction_id=record.transaction_id),
        transaction_state=record,
    )

    assert decision.mode == "silent"
    assert decision.request_complete is False
    assert fake_model.called_schemas == [ThinkingTurnOutput]


def test_reason_is_not_copied_into_episode_memory_or_user_fields() -> None:
    reason = "TRANSIENT-REASON-MUST-NOT-BECOME-MEMORY"
    agent, _ = _make_agent(
        [
            _turn(
                mode="answer_directly",
                reason=reason,
                answer="Visible answer",
                episode_note="Durable note",
            )
        ]
    )

    decision = agent.handle(_make_perception())
    state = agent.snapshot_conversation("t1::0")

    assert decision.answer == "Visible answer"
    assert decision.episode_note == "Durable note"
    assert decision.reasoning is None
    assert reason not in str(decision.answer)
    assert reason not in str(decision.episode_note)
    assert state is not None
    assert reason not in repr(state.episode_buffer)
    assert any(item["note"] == "Durable note" for item in state.episode_buffer)

    drained = agent.on_flush("t1::0", thread_id="t1")
    assert [item["note"] for item in drained] == ["Durable note"]
    assert reason not in repr(drained)


def test_transaction_bound_episode_notes_wait_for_runtime_commit() -> None:
    agent, _ = _make_agent(
        [
            _turn(
                mode="answer_directly",
                answer="Visible answer",
                episode_note="Runtime-owned durable note",
            )
        ]
    )
    record = TransactionRecord(
        transaction_id="txn-runtime-note",
        thread_id="t1",
        conversation_id="t1::runtime",
        state=TransactionState.CONTINUE,
    )

    decision = agent.handle(
        _make_perception(
            conversation_id=record.conversation_id,
            transaction_id=record.transaction_id,
        ),
        transaction_state=record,
    )

    assert decision.episode_note == "Runtime-owned durable note"
    # The graph's commit node will persist the note as an idempotent Scene
    # marker. Until then, neither snapshot nor flush may expose it.
    assert agent.snapshot_episode_notes(record.conversation_id) == []
    assert agent.on_flush(record.conversation_id, thread_id=record.thread_id) == []
    assert agent.snapshot_episode_notes(record.conversation_id) == []


def test_single_call_preserves_compatible_sse_event_sequence_and_payloads() -> None:
    agent, fake_model = _make_agent(
        [
            _turn(
                mode="execute",
                reason="A retrieval step is needed.",
                goal="answer the travel question",
                completed=["identify the trip"],
                remaining=["find the departure time"],
                tool_name="deep_recall",
                instruction="Find the departure time",
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

    assert [event_type for event_type, _payload in events] == [
        "thinking_started",
        "thinking_task_state",
        "thinking_plan",
        "thinking_completed",
    ]
    task_payload = events[1][1]
    assert task_payload["task_progress"] == {
        "goal": "answer the travel question",
        "completion_status": "processing",
        "completed": ["identify the trip"],
        "remaining": ["find the departure time"],
    }
    assert task_payload["task_progress_update"] == task_payload["task_progress"]
    plan_payload = events[2][1]
    assert plan_payload["mode"] == "execute"
    assert plan_payload["tool_name"] == "deep_recall"
    assert plan_payload["instruction"] == "Find the departure time"
    assert plan_payload["request_complete"] is False
    assert plan_payload["reasoning"] is None
    assert "A retrieval step is needed." not in repr(events)
    assert events[3][1]["executed"] is False
    assert events[3][1]["phases"] == ["plan"]
    assert fake_model.called_schemas == [ThinkingTurnOutput]


def test_event_emitter_exception_does_not_break_single_call_handler() -> None:
    agent, fake_model = _make_agent(
        [_turn(mode="answer_directly", answer="Hi")]
    )

    def _bad_emitter(_event_type: str, _payload: Dict[str, Any]) -> None:
        raise RuntimeError("client-side bug")

    decision = agent.handle(_make_perception(), event_emitter=_bad_emitter)

    assert decision.answer == "Hi"
    assert fake_model.called_schemas == [ThinkingTurnOutput]


def test_runtime_activation_is_rendered_once_for_the_joint_schema() -> None:
    agent, fake_model = _make_agent(
        [
            _turn(
                mode="answer_directly",
                goal="handle schedule",
                answer="Reminder ready",
            )
        ]
    )

    agent.handle(
        _make_perception(
            user_message="schedule_due",
            source="schedule",
            system_context={"schedule_id": "sch_1"},
            activation=ActivationFrame(
                event=EventFrame(
                    event_type="schedule_due",
                    source="heartbeat",
                    occurred_at="2026-08-01T09:25:33Z",
                    facts={"due_at_utc": "2026-08-01T09:25:29Z"},
                ),
                objective=ObjectiveFrame(
                    description="Remind the user to cook",
                    encoding="native",
                ),
                evidence=[],
            ),
        )
    )

    assert fake_model.called_schemas == [ThinkingTurnOutput]
    messages = fake_model.structured_calls(ThinkingTurnOutput)[0]
    system_prompt = messages[0]["content"]
    assert "[Output Example: structure and state semantics only]" in system_prompt
    assert '"mode":"answer_directly"' in system_prompt
    assert '"request_complete":' not in system_prompt
    assert "[Current Stimulus]" in system_prompt
    assert "kind: scheduled_plan" in system_prompt
    assert "semantic_role: runtime_activation" in system_prompt
    assert "[Activation Event]" in system_prompt
    assert "type: schedule_due" in system_prompt
    assert "[Current Objective]" in system_prompt
    assert "Remind the user to cook" in system_prompt
    assert "[Observed Evidence]" in system_prompt
    assert "[Previous Task State]" in system_prompt
    assert "[Thinking Turn Requirements]" in system_prompt
    assert "thread_id:" not in system_prompt
    assert "conversation_id:" not in system_prompt
    assert "transaction_id:" not in system_prompt
    assert "t1::0" not in system_prompt
    assert "txn-1" not in system_prompt
    assert '"schedule_id": "sch_1"' not in system_prompt
    assert messages[1]["content"].startswith(
        "[Runtime semantic input — not a user utterance]"
    )


def test_legacy_two_call_mode_retains_rollback_sequence() -> None:
    agent, fake_model = _make_agent(
        [
            TaskProgressUpdate(
                goal="answer the travel question",
                completion_status="processing",
                completed=["identify the trip"],
                remaining=["find the departure time"],
            ),
            ThinkingDecision(
                mode="execute",
                tool_name="deep_recall",
                instruction="Find the departure time",
                request_complete=True,
            ),
        ],
        thinking_mode="legacy_two_call",
    )

    decision = agent.handle(_make_perception())

    assert decision.mode == "execute"
    assert decision.tool_name == "deep_recall"
    assert decision.request_complete is False
    assert fake_model.called_schemas == [TaskProgressUpdate, ThinkingDecision]
    assert fake_model.structured_calls(ThinkingTurnOutput) == []


def test_transaction_resolver_remains_a_separate_structured_call() -> None:
    agent, fake_model = _make_agent(
        [TransactionResolution(action="continue", transaction_id="candidate_2")]
    )
    candidates = [
        TransactionRecord(
            transaction_id="txn-private-alpha",
            thread_id="account::canonical-thread",
            conversation_id="account::canonical-thread::4",
            state=TransactionState.CONTINUE,
        ),
        TransactionRecord(
            transaction_id="txn-private-beta",
            thread_id="account::canonical-thread",
            conversation_id="account::canonical-thread::4",
            state=TransactionState.PAUSE,
        ),
    ]

    selected = agent.resolve_transaction(
        Stimulus(
            kind=StimulusKind.USER_MESSAGE,
            text="continue the second task",
        ),
        candidates,
    )

    assert selected == "txn-private-beta"
    assert fake_model.called_schemas == [TransactionResolution]
    prompt = fake_model.structured_calls(TransactionResolution)[0][0]["content"]
    assert "candidate_1" in prompt
    assert "candidate_2" in prompt
    assert "txn-private-alpha" not in prompt
    assert "txn-private-beta" not in prompt
