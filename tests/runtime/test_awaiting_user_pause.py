"""Awaiting-user pause semantics shared by runtime orchestration."""

from __future__ import annotations

from pathlib import Path

from m_agent.runtime.config import RuntimeConfig
from m_agent.runtime.domain.contracts import (
    Stimulus,
    StimulusEnvelope,
    StimulusKind,
    TransactionKind,
    TransactionState,
)
from m_agent.runtime.perception.attributor import (
    TransactionAttributor,
    is_match_candidate,
)
from m_agent.runtime.turn_support.awaiting_user_pause import (
    pause_for_user_collaboration,
)
from m_agent.runtime.transaction import SQLiteRuntimeStore
from m_agent.runtime.transaction.registry import TransactionRegistry


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
            config=RuntimeConfig(),
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
