"""Macro awaiting_user maps to TX pause; silent alone does not."""

from __future__ import annotations

from pathlib import Path

from m_agent.layers.thinking.contracts import (
    TASK_COMPLETION_AWAITING_USER,
    ThinkingDecision,
)
from m_agent.runtime.think_life.config import ThinkLifeConfig
from m_agent.runtime.think_life.contracts import (
    Stimulus,
    StimulusEnvelope,
    StimulusKind,
    TransactionKind,
    TransactionRecord,
    TransactionState,
)
from m_agent.runtime.think_life.perception.attributor import (
    TransactionAttributor,
    is_match_candidate,
)
from m_agent.runtime.think_life.scheduler.awaiting_user_pause import (
    pause_for_user_collaboration,
)
from m_agent.runtime.think_life.scheduler.loop import ThinkLifeLoop
from m_agent.runtime.think_life.transaction import SQLiteRuntimeStore
from m_agent.runtime.think_life.transaction_registry import TransactionRegistry


def _registry(tmp_path: Path) -> TransactionRegistry:
    store = SQLiteRuntimeStore(tmp_path / "awaiting_user.sqlite3")
    return TransactionRegistry(store=store)


def test_pause_requires_finalized_reply_and_explicit_call(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    try:
        record = registry.create(
            thread_id="t-await",
            conversation_id="t-await::0",
            kind=TransactionKind.USER_TASK,
        )
        live = registry.get(record.transaction_id)
        assert live is not None
        live.task_state.goal = "check mailbox"

        assert pause_for_user_collaboration(registry, live) is False

        registry.mark_reply_finalized(record.transaction_id)
        live = registry.get(record.transaction_id)
        assert live is not None
        assert pause_for_user_collaboration(registry, live) is True
        paused = registry.get(record.transaction_id)
        assert paused is not None
        assert paused.state == TransactionState.PAUSE
        assert is_match_candidate(paused)
        assert pause_for_user_collaboration(registry, paused) is False
    finally:
        registry.close()


def test_finish_silent_pauses_when_macro_awaits_user(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    try:
        record = registry.create(
            thread_id="t-silent",
            conversation_id="t-silent::0",
            kind=TransactionKind.USER_TASK,
        )
        registry.mark_reply_finalized(record.transaction_id)
        live = registry.get(record.transaction_id)
        assert live is not None
        live.task_state.completion_status = TASK_COMPLETION_AWAITING_USER

        loop = ThinkLifeLoop.__new__(ThinkLifeLoop)
        loop.registry = registry
        decision = ThinkingDecision(
            mode="silent",
            request_complete=False,
            reasoning="Waiting for the user after clarification.",
        )
        result = ThinkLifeLoop._finish_silent_plan_turn(loop, live, decision)
        assert result["silent"] is True
        assert result["completed"] is False
        assert result["paused_awaiting_user"] is True
        paused = registry.get(record.transaction_id)
        assert paused is not None
        assert paused.state == TransactionState.PAUSE
    finally:
        registry.close()


def test_finish_silent_without_awaiting_user_does_not_pause(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    try:
        record = registry.create(
            thread_id="t-no-auto",
            conversation_id="t-no-auto::0",
            kind=TransactionKind.USER_TASK,
        )
        registry.mark_reply_finalized(record.transaction_id)
        live = registry.get(record.transaction_id)
        assert live is not None
        # processing: silent alone must not pause
        live.task_state.completion_status = "processing"

        loop = ThinkLifeLoop.__new__(ThinkLifeLoop)
        loop.registry = registry
        decision = ThinkingDecision(
            mode="silent",
            request_complete=False,
            reasoning="Ack only; not waiting on user.",
        )
        result = ThinkLifeLoop._finish_silent_plan_turn(loop, live, decision)
        assert result["silent"] is True
        assert result["paused_awaiting_user"] is False
        current = registry.get(record.transaction_id)
        assert current is not None
        assert current.state == TransactionState.CONTINUE
    finally:
        registry.close()


def test_clarification_pause_is_rematchable_by_sourceless_attributor(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    try:
        record = registry.create(
            thread_id="t-match",
            conversation_id="t-match::0",
            kind=TransactionKind.USER_TASK,
        )
        registry.mark_reply_finalized(record.transaction_id)
        live = registry.get(record.transaction_id)
        assert live is not None
        live.task_state.goal = "inspect suspended account email"
        assert pause_for_user_collaboration(registry, live) is True

        def _resolver(_stimulus, _candidates, **_kwargs):
            return "candidate_1"

        attributor = TransactionAttributor(
            registry=registry,
            config=ThinkLifeConfig(),
            semantic_resolver=_resolver,
        )
        stimulus = StimulusEnvelope(
            stimulus_id="stim_followup",
            thread_id="t-match",
            conversation_id="t-match::0",
            stimulus=Stimulus(
                kind=StimulusKind.USER_MESSAGE,
                text="账户暂停是怎么回事",
            ),
            occurred_at="2026-07-30T08:00:00Z",
        )
        matched, created = attributor.resolve(stimulus)
        assert created is False
        assert matched.transaction_id == record.transaction_id
        assert matched.state == TransactionState.CONTINUE
    finally:
        registry.close()


def test_finish_awaiting_user_without_reply_does_not_pause() -> None:
    loop = ThinkLifeLoop.__new__(ThinkLifeLoop)
    paused: list[str] = []

    class _Registry:
        def get(self, _tid: str) -> TransactionRecord:
            record = TransactionRecord(
                transaction_id="txn_open",
                thread_id="t1",
                kind=TransactionKind.USER_TASK,
                reply_finalized_in_activation=False,
            )
            record.task_state.completion_status = TASK_COMPLETION_AWAITING_USER
            return record

        def transition(self, *_a, **_k) -> None:
            return None

        def pause(self, txn_id: str, **_k) -> None:
            paused.append(txn_id)

    loop.registry = _Registry()
    decision = ThinkingDecision(
        mode="silent",
        reasoning="want user input but no reply yet",
        request_complete=False,
    )
    result = ThinkLifeLoop._finish_silent_plan_turn(
        loop,
        loop.registry.get("txn_open"),
        decision,
    )
    assert result["silent"] is True
    assert result.get("paused_awaiting_user") is False
    assert paused == []
