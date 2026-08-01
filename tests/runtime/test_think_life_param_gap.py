"""Think-life param gap short-circuit in delegate loop."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from m_agent.layers.execution.contracts import ParamFillResult
from m_agent.layers.perception.contracts import PerceptionInput, Stimulus
from m_agent.runtime.think_life.config import ThinkLifeConfig
from m_agent.runtime.think_life.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    StimulusKind,
    PauseReason,
    TransactionCorrelation,
    TransactionKind,
    TransactionState,
)
from m_agent.runtime.think_life.perception.inbox import StimulusInbox
from m_agent.runtime.think_life.scheduler.delegate import DelegateTarget
from m_agent.runtime.think_life.scheduler.loop import ThinkLifeLoop
from m_agent.runtime.think_life.transaction_registry import TransactionRegistry


def _minimal_loop(*, execution_agent: MagicMock) -> ThinkLifeLoop:
    registry = TransactionRegistry()
    gateway = MagicMock()
    gateway.submit_execution_feedback.return_value = "stim_feedback"
    loop = ThinkLifeLoop(
        config=ThinkLifeConfig(),
        registry=registry,
        inbox=StimulusInbox(),
        gateway=gateway,
        attributor=MagicMock(priority_for=MagicMock(return_value=0)),
        thinking_agent=MagicMock(),
        execution_agent=execution_agent,
        wm_system=MagicMock(),
        scene_writer=MagicMock(),
        scene_reader=MagicMock(),
    )
    loop._should_yield_to_inbox = MagicMock(return_value=False)  # type: ignore[method-assign]
    loop._append_tool_scene = MagicMock()  # type: ignore[method-assign]
    return loop


def test_delegate_and_wait_submits_feedback_without_invoke_on_param_gap() -> None:
    execution_agent = MagicMock()
    execution_agent.fill_tool_args.return_value = ParamFillResult(
        tool_name="schedule_create",
        status="needs_clarification",
        missing_fields=["due_at"],
        reason="no explicit time",
    )
    loop = _minimal_loop(execution_agent=execution_agent)

    record = loop.registry.create(thread_id="t1", kind=TransactionKind.USER_TASK)
    record = loop.registry.get(record.transaction_id) or record

    target = DelegateTarget(tool_name="schedule_create", instruction="提醒我起床")
    stimulus = StimulusEnvelope(
        stimulus_id="s1",
        thread_id="t1",
        conversation_id=record.conversation_id,
        stimulus=Stimulus(
            kind=StimulusKind.USER_MESSAGE,
            text="帮我设提醒",
            payload={},
        ),
        occurred_at="2026-01-01T00:00:00Z",
    )
    perception = PerceptionInput(
        thread_id="t1",
        conversation_id=record.conversation_id,
        transaction_id=record.transaction_id,
        stimulus=Stimulus(kind=StimulusKind.USER_MESSAGE, text="帮我设提醒"),
    )

    result = loop._delegate_and_wait(
        record,
        target=target,
        pending_user_request="帮我设提醒",
        perception=perception,
        stimulus=stimulus,
    )

    assert result.get("param_gap") is True
    assert result.get("waiting_feedback") is True
    execution_agent.invoke_tool_direct.assert_not_called()
    loop.gateway.submit_execution_feedback.assert_called_once()
    call_kwargs = loop.gateway.submit_execution_feedback.call_args.kwargs
    assert call_kwargs["thread_id"] == "t1"
    assert call_kwargs["transaction_id"] == record.transaction_id
    history = call_kwargs["tool_history"]
    assert history[0]["result"]["stage"] == "param_fill"
    assert history[0]["result"]["tool_invoked"] is False


def test_delegate_passes_scene_conversation_id_to_reply_tool() -> None:
    execution_agent = MagicMock()
    execution_agent.invoke_tool_direct.return_value.tool_history = []
    execution_agent.invoke_tool_direct.return_value.summary = ""
    loop = _minimal_loop(execution_agent=execution_agent)

    record = loop.registry.create(
        thread_id="owner::thread",
        conversation_id="owner::thread::7",
        kind=TransactionKind.USER_TASK,
    )
    record = loop.registry.get(record.transaction_id) or record
    stimulus = StimulusEnvelope(
        stimulus_id="s-reply",
        thread_id=record.thread_id,
        conversation_id=record.conversation_id,
        stimulus=Stimulus(kind=StimulusKind.USER_MESSAGE, text="hello", payload={}),
        occurred_at="2026-01-01T00:00:00Z",
    )
    perception = PerceptionInput(
        thread_id=record.thread_id,
        conversation_id=record.conversation_id,
        transaction_id=record.transaction_id,
        stimulus=Stimulus(kind=StimulusKind.USER_MESSAGE, text="hello"),
    )

    loop._delegate_and_wait(
        record,
        target=DelegateTarget(
            tool_name="reply_to_user",
            for_user_reply=True,
            user_reply_text="Hello back",
        ),
        pending_user_request="hello",
        perception=perception,
        stimulus=stimulus,
    )

    hooks = execution_agent.invoke_tool_direct.call_args.kwargs["think_life_hooks"]
    assert hooks["conversation_id"] == "owner::thread::7"


def test_finalized_reply_submits_feedback_instead_of_completing_task() -> None:
    execution_agent = MagicMock()

    def _invoke_reply(**kwargs):
        kwargs["think_life_hooks"]["on_reply"]("The answer", finalize=True)
        return SimpleNamespace(
            tool_history=[
                {
                    "tool_name": "reply_to_user",
                    "result": {
                        "success": True,
                        "message": "The answer",
                        "finalize": True,
                    },
                }
            ],
            summary="",
        )

    execution_agent.invoke_tool_direct.side_effect = _invoke_reply
    loop = _minimal_loop(execution_agent=execution_agent)
    record = loop.registry.create(thread_id="t1", kind=TransactionKind.USER_TASK)
    record = loop.registry.get(record.transaction_id) or record
    stimulus = StimulusEnvelope(
        stimulus_id="s-reply-feedback",
        thread_id="t1",
        conversation_id=record.conversation_id,
        stimulus=Stimulus(kind=StimulusKind.USER_MESSAGE, text="answer me", payload={}),
        occurred_at="2026-01-01T00:00:00Z",
    )
    perception = PerceptionInput(
        thread_id="t1",
        conversation_id=record.conversation_id,
        transaction_id=record.transaction_id,
        stimulus=Stimulus(kind=StimulusKind.USER_MESSAGE, text="answer me"),
    )

    result = loop._delegate_and_wait(
        record,
        target=DelegateTarget(
            tool_name="reply_to_user",
            for_user_reply=True,
            user_reply_text="The answer",
        ),
        pending_user_request="answer me",
        perception=perception,
        stimulus=stimulus,
    )

    assert result["waiting_feedback"] is True
    assert result["reply_finalized"] is True
    assert result["replies"] == ["The answer"]
    loop.gateway.submit_execution_feedback.assert_called_once()
    feedback = loop.gateway.submit_execution_feedback.call_args.kwargs
    assert feedback["tool_history"][0]["tool_name"] == "reply_to_user"
    assert "finalize=true" in feedback["summary"]
    current = loop.registry.get(record.transaction_id)
    assert current is not None
    assert current.state == TransactionState.CONTINUE
    assert current.active_delegate_id is not None


def test_schedule_feedback_completion_recovers_previously_emitted_reply() -> None:
    loop = _minimal_loop(execution_agent=MagicMock())
    lifecycle = MagicMock()
    loop._schedule_lifecycle = lifecycle
    record = loop.registry.create(
        thread_id="t1",
        conversation_id="t1::0",
        kind=TransactionKind.SCHEDULE,
    )
    record.correlation.schedule_owner_id = "owner"
    record.correlation.schedule_id = "schedule-1"
    record.correlation.schedule_run_id = "run-1"
    loop.scene_reader.entries_since_flush.return_value = [
        SceneEntry(
            seq=1,
            occurred_at="2026-01-01T00:00:00Z",
            entry_type=SceneEntryType.REPLY,
            actor=SceneActor.ASSISTANT,
            text="Reminder delivered",
            transaction_id=record.transaction_id,
        )
    ]

    loop._notify_schedule_finished(
        record,
        {"success": True, "completed": True, "silent": True},
    )

    call = lifecycle.on_schedule_processing_finished.call_args.kwargs
    assert call["answer"] == "Reminder delivered"


def test_processing_failure_marks_transaction_failed_and_emits_terminal_event() -> None:
    loop = _minimal_loop(execution_agent=MagicMock())
    events: list[tuple[str, dict]] = []
    loop._event_emitter = lambda event_type, payload: events.append(
        (event_type, payload)
    )
    record = loop.registry.create(thread_id="t1", kind=TransactionKind.USER_TASK)
    stimulus = StimulusEnvelope(
        stimulus_id="s-failed",
        thread_id="t1",
        conversation_id=record.conversation_id,
        stimulus=Stimulus(kind=StimulusKind.USER_MESSAGE, text="hello", payload={}),
        occurred_at="2026-01-01T00:00:00Z",
    )

    loop._handle_processing_failure(stimulus, record, RuntimeError("boom"))

    failed = loop.registry.get(record.transaction_id)
    assert failed is not None
    assert failed.state == TransactionState.PAUSE
    assert failed.pause_reason == PauseReason.RUNTIME_ERROR
    assert failed.last_error == "boom"
    assert events == [
        (
            "turn_failed",
            {
                "thread_id": "t1",
                "conversation_id": record.conversation_id,
                "transaction_id": record.transaction_id,
                "stimulus_id": "s-failed",
                "error": "boom",
                "retryable": False,
            },
        )
    ]


def _schedule_delete_loop() -> tuple[
    ThinkLifeLoop,
    object,
    StimulusEnvelope,
    MagicMock,
]:
    loop = _minimal_loop(execution_agent=MagicMock())
    # Use the same durable Store for inbox claims so deletion can atomically
    # abort the in-flight stimulus and the drainer can observe the tombstone.
    loop.inbox = StimulusInbox(store=loop.registry.store)
    lifecycle = MagicMock()
    loop._schedule_lifecycle = lifecycle
    record = loop.registry.create(
        thread_id="schedule-delete-thread",
        conversation_id="schedule-delete-thread::0",
        kind=TransactionKind.SCHEDULE,
        correlation=TransactionCorrelation(
            schedule_id="schedule-delete-1",
            schedule_owner_id="owner-1",
            schedule_run_id="run-delete-1",
        ),
    )
    stimulus = StimulusEnvelope(
        stimulus_id="stimulus-schedule-delete",
        thread_id=record.thread_id,
        conversation_id=record.conversation_id,
        transaction_id=record.transaction_id,
        schedule_id="schedule-delete-1",
        schedule_run_id="run-delete-1",
        stimulus=Stimulus(
            kind=StimulusKind.SCHEDULED_PLAN,
            text="deliver reminder",
            payload={
                "owner_id": "owner-1",
                "schedule_id": "schedule-delete-1",
                "run_id": "run-delete-1",
            },
        ),
        occurred_at="2026-01-01T00:00:00Z",
    )
    loop.inbox.push(stimulus, priority=0)
    loop.attributor.resolve.return_value = (record, False)
    return loop, record, stimulus, lifecycle


def test_schedule_delete_after_normal_turn_result_finishes_as_deleted() -> None:
    loop, record, stimulus, lifecycle = _schedule_delete_loop()

    def _run_then_delete(*_args, **_kwargs) -> dict:
        current = loop.registry.store.load_transaction(record.transaction_id)
        assert current is not None
        loop.registry.delete_with_cleanup(
            record.transaction_id,
            expected_revision=current.revision,
            transition_id="delete-after-normal-result",
            idempotency_key="delete-after-normal-result",
        )
        # Simulate a planner/tool result that completed just as delete won.
        return {"success": True, "answer": "must not report success"}

    loop._run_transaction_turn = MagicMock(  # type: ignore[method-assign]
        side_effect=_run_then_delete
    )

    results = loop.drain_thread(record.thread_id)

    assert results[0]["deleted"] is True
    lifecycle.on_schedule_processing_started.assert_called_once()
    finished = lifecycle.on_schedule_processing_finished.call_args.kwargs
    assert finished["success"] is False
    assert finished["error"] == "transaction_deleted"
    stored_stimulus = loop.registry.store.load_stimulus(stimulus.stimulus_id)
    assert stored_stimulus is not None
    assert stored_stimulus.disposition == "aborted"
    assert stored_stimulus.disposition_stage == "transaction_delete"


def test_schedule_exception_after_delete_is_deleted_unwind_not_turn_failed() -> None:
    loop, record, stimulus, lifecycle = _schedule_delete_loop()
    events: list[tuple[str, dict]] = []
    loop._event_emitter = lambda event_type, payload: events.append(
        (event_type, payload)
    )

    def _delete_then_raise(*_args, **_kwargs) -> dict:
        current = loop.registry.store.load_transaction(record.transaction_id)
        assert current is not None
        loop.registry.delete_with_cleanup(
            record.transaction_id,
            expected_revision=current.revision,
            transition_id="delete-before-tool-error",
            idempotency_key="delete-before-tool-error",
        )
        raise RuntimeError("late tool error")

    loop._run_transaction_turn = MagicMock(  # type: ignore[method-assign]
        side_effect=_delete_then_raise
    )

    results = loop.drain_thread(record.thread_id)

    assert results[0]["deleted"] is True
    assert all(event_type != "turn_failed" for event_type, _ in events)
    lifecycle.on_schedule_processing_started.assert_called_once()
    finished = lifecycle.on_schedule_processing_finished.call_args.kwargs
    assert finished["success"] is False
    assert finished["error"] == "transaction_deleted"
    stored_stimulus = loop.registry.store.load_stimulus(stimulus.stimulus_id)
    assert stored_stimulus is not None
    assert stored_stimulus.disposition == "aborted"
