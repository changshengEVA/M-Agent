"""Form-A flow tests for :class:`ThinkingAgent`.

We swap the LangChain chat model with a deterministic fake that returns
pre-canned :class:`ThinkingDecision` / :class:`ThinkingSummary` objects so the
test stays hermetic. The execution agent is replaced with a stub that records
calls and returns a canned :class:`ExecutionResult`.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List

import pytest

from m_agent.layers.execution.contracts import ExecutionRequest, ExecutionResult
from m_agent.layers.execution.model_provider import ModelProvider
from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.layers.thinking import (
    ConversationStateRegistry,
    PerceptionInput,
    TaskProgressUpdate,
    ThinkingAgent,
    ThinkingDecision,
    ThinkingSummary,
)
from m_agent.systems.episodic import DefaultEpisodeRecorder
from m_agent.systems.wm import DefaultWMReader, DefaultWMWriter
from m_agent.chat.working_memory import WorkingMemoryConfig


class _FakeStructuredModel:
    """Stand-in for ``model.with_structured_output(Schema)``.

    Yields successive responses from a queue; each ``invoke()`` call records
    the messages it was given so tests can assert on prompt assembly.
    """

    def __init__(self, responses: List[Any]) -> None:
        self._queue = list(responses)
        self.calls: List[List[Dict[str, str]]] = []

    def invoke(self, messages: List[Dict[str, str]]) -> Any:
        self.calls.append(list(messages))
        if not self._queue:
            raise AssertionError("FakeStructuredModel ran out of canned responses")
        return self._queue.pop(0)


class _FakeChatModel:
    """Stand-in for a LangChain chat model that exposes ``with_structured_output``.

    The schemas are matched by class identity; multiple queues can be configured
    so plan + summarize calls produce different responses.
    """

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


class _StubExecutionAgent:
    """Minimal stub: records ``execute()`` calls and returns a canned result."""

    def __init__(self, *, result: ExecutionResult, capability_block: str = "[Available Tools]\n- shallow_recall") -> None:
        self._result = result
        self._capability_block = capability_block
        self.calls: List[ExecutionRequest] = []

    def describe_capabilities_block(self) -> str:
        return self._capability_block

    def execute(self, request: ExecutionRequest, *, wm_writer_callback=None) -> ExecutionResult:
        self.calls.append(request)
        if wm_writer_callback is not None:
            wm_writer_callback(self._result)
        return self._result


def _make_perception(**overrides: Any) -> PerceptionInput:
    text = str(overrides.pop("user_message", "你好"))
    source = str(overrides.pop("source", "user"))
    payload = dict(overrides.pop("system_context", {}) or {})
    history = list(overrides.pop("history_messages", []) or [])
    kind = {
        "user": StimulusKind.USER_MESSAGE,
        "schedule": StimulusKind.SCHEDULED_PLAN,
        "execution_feedback": StimulusKind.EXECUTION_FEEDBACK,
    }.get(source, StimulusKind.OBSERVATION_TRIGGER)
    defaults: Dict[str, Any] = dict(
        thread_id="t1",
        conversation_id="t1::0",
        transaction_id="txn-1",
        stimulus=Stimulus(kind=kind, text=text, payload=payload),
        dialogue_history=history,
    )
    defaults.update(overrides)
    return PerceptionInput(**defaults)


def _make_agent(
    *,
    decisions: List[ThinkingDecision],
    summaries: List[ThinkingSummary],
    execution_result: ExecutionResult,
    wm_config: WorkingMemoryConfig | None = None,
    max_executions_per_turn: int = 1,
    prompt_language: str = "zh",
    task_updates: List[TaskProgressUpdate] | None = None,
) -> tuple[ThinkingAgent, _StubExecutionAgent, _FakeChatModel]:
    wm_cfg = wm_config or WorkingMemoryConfig(enable=True)
    task_update_queue = task_updates or [TaskProgressUpdate() for _ in decisions]
    fake_model = _FakeChatModel(
        {
            TaskProgressUpdate: task_update_queue,
            ThinkingDecision: decisions,
            ThinkingSummary: summaries,
        }
    )
    model_provider = ModelProvider(model=fake_model, network_retry_attempts=1)
    stub_execution = _StubExecutionAgent(result=execution_result)
    agent = ThinkingAgent(
        execution_agent=stub_execution,
        model_provider=model_provider,
        system_prompt="你是一个记忆助手。",
        persona_prompt="",
        wm_reader=DefaultWMReader(wm_cfg),
        wm_writer=DefaultWMWriter(wm_cfg),
        episode_recorder=DefaultEpisodeRecorder(),
        state_registry=ConversationStateRegistry(),
        prompt_language=prompt_language,
        max_executions_per_turn=max_executions_per_turn,
        skip_summarize_on_direct_answer=True,
    )
    return agent, stub_execution, fake_model


def test_answer_directly_skips_execution_and_summarize() -> None:
    decisions = [ThinkingDecision(mode="answer_directly", answer="你好，很高兴见到你。", episode_note="user said hi")]
    summaries: List[ThinkingSummary] = []
    exec_result = ExecutionResult(summary="(unused)")

    agent, stub, fake_model = _make_agent(
        decisions=decisions, summaries=summaries, execution_result=exec_result
    )
    turn = agent.handle(_make_perception())

    assert turn.answer == "你好，很高兴见到你。"
    assert turn.execution_result is None
    assert turn.summary is None
    assert stub.calls == []
    # Task-state and decision LLM calls were made; summarize was skipped.
    assert len(fake_model.structured_calls(TaskProgressUpdate)) == 1
    assert len(fake_model.structured_calls(ThinkingDecision)) == 1
    assert fake_model.structured_calls(ThinkingSummary) == []

    state = agent.snapshot_conversation("t1::0")
    assert state is not None
    assert state.turn_count == 1
    # episode_note should be buffered for later flush.
    assert any(entry["note"] == "user said hi" for entry in state.episode_buffer)


def test_silent_mode_skips_fallback_answer() -> None:
    decisions = [ThinkingDecision(mode="silent", episode_note="ack only", reasoning="no reply needed")]
    agent, stub, fake_model = _make_agent(
        decisions=decisions,
        summaries=[],
        execution_result=ExecutionResult(summary="(unused)"),
        max_executions_per_turn=0,
    )
    turn = agent.handle(_make_perception())

    assert turn.answer == ""
    assert turn.execution_result is None
    assert stub.calls == []
    assert len(fake_model.structured_calls(TaskProgressUpdate)) == 1
    assert len(fake_model.structured_calls(ThinkingDecision)) == 1


def test_execute_then_summarize_writes_wm_and_returns_summary_answer() -> None:
    decisions = [ThinkingDecision(mode="execute", instruction="查一下昨天的事")]
    summaries = [ThinkingSummary(answer="昨天你和朋友讨论了旅行。", episode_note="topic: travel plans")]
    exec_result = ExecutionResult(
        summary="raw recall: travel discussion",
        tool_history=[
            {
                "tool_name": "deep_recall",
                "params": {"question": "昨天发生了什么"},
                "result": {"answer": "讨论旅行计划"},
            }
        ],
    )

    agent, stub, fake_model = _make_agent(
        decisions=decisions, summaries=summaries, execution_result=exec_result
    )
    turn = agent.handle(_make_perception(user_message="昨天发生了什么"))

    assert turn.answer == "昨天你和朋友讨论了旅行。"
    assert turn.execution_result is exec_result
    assert turn.summary is not None and turn.summary.episode_note == "topic: travel plans"
    assert len(stub.calls) == 1
    assert "查一下昨天的事" in stub.calls[0].instruction
    assert stub.calls[0].thread_id == "t1"
    # Task-state, decision, and legacy summarize LLM calls happened.
    assert len(fake_model.structured_calls(TaskProgressUpdate)) == 1
    assert len(fake_model.structured_calls(ThinkingDecision)) == 1
    assert len(fake_model.structured_calls(ThinkingSummary)) == 1

    # WM should have one entry projected from the tool history.
    state = agent.snapshot_conversation("t1::0")
    assert state is not None
    assert len(state.wm_entries) == 1
    assert state.wm_entries[0]["tool"] == "deep_recall"


def test_task_progress_update_is_rendered_in_next_plan_prompt() -> None:
    decisions = [
        ThinkingDecision(
            mode="execute",
            tool_name="deep_recall",
            instruction="find the relevant travel detail",
        ),
        ThinkingDecision(mode="answer_directly", answer="done"),
    ]
    summaries = [
        ThinkingSummary(
            answer="I found the travel detail.",
            task_progress_update=TaskProgressUpdate(
                completed=["find the relevant travel detail"],
                remaining=["answer the user"],
            ),
        )
    ]
    exec_result = ExecutionResult(
        summary="found travel detail",
        tool_history=[
            {
                "tool_name": "deep_recall",
                "params": {"question": "travel detail"},
                "result": {"answer": "train at 9"},
            }
        ],
    )
    agent, _, fake_model = _make_agent(
        decisions=decisions,
        summaries=summaries,
        execution_result=exec_result,
        prompt_language="en",
        task_updates=[
            TaskProgressUpdate(
                goal="answer the travel question",
                remaining=["find the relevant travel detail", "answer the user"],
            ),
            TaskProgressUpdate(),
        ],
    )

    first = agent.handle(_make_perception(user_message="check my travel detail"))
    second = agent.handle(_make_perception(user_message="continue"))

    assert first.answer == "I found the travel detail."
    assert second.answer == "done"
    state = agent.snapshot_conversation("t1::0")
    assert state is not None
    assert state.task_progress.goal == "answer the travel question"
    assert state.task_progress.completed == ["find the relevant travel detail"]
    assert state.task_progress.remaining == ["answer the user"]

    second_plan_prompt = fake_model.structured_calls(ThinkingDecision)[1][0]["content"]
    assert "[Task State]" in second_plan_prompt
    assert "goal: answer the travel question" in second_plan_prompt
    assert "find the relevant travel detail" in second_plan_prompt
    assert "answer the user" in second_plan_prompt


def test_current_input_is_rendered_in_task_state_and_decision_prompts() -> None:
    decisions = [ThinkingDecision(mode="answer_directly", answer="ok")]
    agent, _, fake_model = _make_agent(
        decisions=decisions,
        summaries=[],
        execution_result=ExecutionResult(summary=""),
        prompt_language="en",
        task_updates=[TaskProgressUpdate(goal="handle schedule", remaining=["reply"])],
    )

    agent.handle(
        _make_perception(
            user_message="Reminder fired",
            source="schedule",
            system_context={"schedule_id": "sch_1", "trigger_source": "schedule"},
        )
    )

    task_prompt = fake_model.structured_calls(TaskProgressUpdate)[0][0]["content"]
    decision_prompt = fake_model.structured_calls(ThinkingDecision)[0][0]["content"]
    for prompt in (task_prompt, decision_prompt):
        assert "[Current Stimulus]" in prompt
        assert "kind: scheduled_plan" in prompt
        assert "Reminder fired" in prompt
        assert '"schedule_id": "sch_1"' not in prompt


def test_execute_with_empty_instruction_falls_back_to_direct_answer() -> None:
    decisions = [ThinkingDecision(mode="execute", instruction="", answer="备用回答")]
    summaries: List[ThinkingSummary] = []
    exec_result = ExecutionResult(summary="(unused)")

    agent, stub, _ = _make_agent(
        decisions=decisions, summaries=summaries, execution_result=exec_result
    )
    turn = agent.handle(_make_perception(user_message="random"))

    # Empty instruction is not a valid execution call -> fall back to answer.
    assert stub.calls == []
    assert turn.answer == "备用回答"


def test_on_flush_drops_conversation_state() -> None:
    decisions = [ThinkingDecision(mode="answer_directly", answer="ok", episode_note="note-A")]
    summaries: List[ThinkingSummary] = []
    exec_result = ExecutionResult(summary="")

    agent, _, _ = _make_agent(
        decisions=decisions, summaries=summaries, execution_result=exec_result
    )
    agent.handle(_make_perception())
    state = agent.snapshot_conversation("t1::0")
    assert state is not None
    assert state.episode_buffer

    drained = agent.on_flush("t1::0", thread_id="t1")
    assert drained
    assert drained[0]["note"] == "note-A"
    # State is gone after flush; a fresh conversation starts empty.
    assert agent.snapshot_conversation("t1::0") is None


def test_event_emitter_receives_plan_only_on_direct_answer() -> None:
    decisions = [ThinkingDecision(mode="answer_directly", answer="hi", episode_note="n")]
    summaries: List[ThinkingSummary] = []
    exec_result = ExecutionResult(summary="")

    agent, _, _ = _make_agent(
        decisions=decisions, summaries=summaries, execution_result=exec_result
    )

    events: List[tuple[str, Dict[str, Any]]] = []
    agent.handle(_make_perception(), event_emitter=lambda et, p: events.append((et, p)))

    types = [t for t, _ in events]
    # Direct-answer flow: started -> task_state -> plan -> completed (no execution_*, no summary)
    assert types == [
        "thinking_started",
        "thinking_task_state",
        "thinking_plan",
        "thinking_completed",
    ]
    task_payload = next(p for t, p in events if t == "thinking_task_state")
    assert task_payload["task_progress"] == {"goal": "", "completed": [], "remaining": []}
    # plan payload exposes mode and reasoning fields
    plan_payload = next(p for t, p in events if t == "thinking_plan")
    assert plan_payload["mode"] == "answer_directly"
    assert plan_payload["episode_note"] == "n"
    completed_payload = next(p for t, p in events if t == "thinking_completed")
    assert completed_payload["executed"] is False
    assert completed_payload["phases"] == ["plan"]


def test_event_emitter_receives_full_phases_on_execute() -> None:
    decisions = [ThinkingDecision(mode="execute", instruction="查询昨天的旅行计划")]
    summaries = [ThinkingSummary(answer="昨天讨论了旅行", episode_note="note-sum")]
    exec_result = ExecutionResult(
        summary="recall: travel",
        tool_history=[
            {"tool_name": "deep_recall", "result": {"answer": "..."}, "params": {}},
            {"tool_name": "deep_recall", "result": {"answer": "..."}, "params": {}},
        ],
        insufficient=False,
    )

    agent, stub, _ = _make_agent(
        decisions=decisions, summaries=summaries, execution_result=exec_result
    )

    events: List[tuple[str, Dict[str, Any]]] = []
    agent.handle(_make_perception(), event_emitter=lambda et, p: events.append((et, p)))

    types = [t for t, _ in events]
    assert types == [
        "thinking_started",
        "thinking_task_state",
        "thinking_plan",
        "execution_started",
        "execution_completed",
        "thinking_summary",
        "thinking_completed",
    ]
    exec_completed = next(p for t, p in events if t == "execution_completed")
    assert exec_completed["tool_call_count"] == 2
    assert exec_completed["tool_names"] == ["deep_recall"]
    summary_event = next(p for t, p in events if t == "thinking_summary")
    assert "昨天讨论了旅行" in summary_event["answer_excerpt"]
    assert summary_event["episode_note"] == "note-sum"
    final_event = next(p for t, p in events if t == "thinking_completed")
    assert final_event["executed"] is True
    assert final_event["phases"] == ["plan", "execute", "summarize"]


def test_event_emitter_exception_does_not_break_handler() -> None:
    """Emitter callback must be quarantined: a buggy UI client cannot break the chat path."""
    decisions = [ThinkingDecision(mode="answer_directly", answer="hi")]
    summaries: List[ThinkingSummary] = []
    exec_result = ExecutionResult(summary="")
    agent, _, _ = _make_agent(
        decisions=decisions, summaries=summaries, execution_result=exec_result
    )

    def _bad_emitter(_et: str, _p: Dict[str, Any]) -> None:
        raise RuntimeError("client-side bug")

    # Should not raise; the answer is still returned.
    turn = agent.handle(_make_perception(), event_emitter=_bad_emitter)
    assert turn.answer == "hi"


def test_max_executions_zero_forces_direct_answer() -> None:
    decisions = [ThinkingDecision(mode="execute", instruction="x", answer=None)]
    summaries: List[ThinkingSummary] = []
    exec_result = ExecutionResult(summary="")

    agent, stub, _ = _make_agent(
        decisions=decisions, summaries=summaries, execution_result=exec_result
    )
    agent.max_executions_per_turn = 0
    turn = agent.handle(_make_perception())

    assert stub.calls == []
    # answer was None; ThinkingAgent must produce a non-empty fallback string.
    assert turn.answer
