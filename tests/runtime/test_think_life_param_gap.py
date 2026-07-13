"""Think-life param gap short-circuit in delegate loop."""
from __future__ import annotations

from unittest.mock import MagicMock

from m_agent.layers.execution.contracts import ParamFillResult
from m_agent.layers.perception.contracts import PerceptionInput, Stimulus
from m_agent.runtime.think_life.config import ThinkLifeConfig
from m_agent.runtime.think_life.contracts import (
    StimulusEnvelope,
    StimulusKind,
    TransactionKind,
    TransactionStatus,
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
    loop.registry.transition(record.transaction_id, TransactionStatus.RUNNING)
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
