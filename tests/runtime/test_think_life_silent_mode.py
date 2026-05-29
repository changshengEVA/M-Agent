"""Think-life silent planning mode."""
from __future__ import annotations

from m_agent.layers.thinking.state import (
    ThinkingDecision,
    is_execute_mode,
    is_reply_mode,
    is_silent_mode,
    normalize_thinking_mode,
    request_is_complete,
)
from m_agent.runtime.think_life.contracts import TransactionKind, TransactionRecord, TransactionStatus
from m_agent.runtime.think_life.scheduler.loop import ThinkLifeLoop


def test_normalize_thinking_mode_aliases() -> None:
    assert normalize_thinking_mode("silent") == "silent"
    assert normalize_thinking_mode("wait") == "silent"
    assert normalize_thinking_mode("defer") == "silent"
    assert normalize_thinking_mode("answer_directly") == "answer_directly"
    assert normalize_thinking_mode("reply") == "answer_directly"
    assert normalize_thinking_mode("execute") == "execute"


def test_mode_predicates() -> None:
    assert is_silent_mode("wait")
    assert is_execute_mode("execute")
    assert is_reply_mode("answer_directly")
    assert not is_silent_mode("execute")


def test_finish_silent_plan_turn_keeps_transaction_open() -> None:
    loop = ThinkLifeLoop.__new__(ThinkLifeLoop)
    loop.registry = type(
        "R",
        (),
        {
            "get": lambda _self, _tid: TransactionRecord(
                transaction_id="txn_1",
                thread_id="t1",
                status=TransactionStatus.RUNNING,
                kind=TransactionKind.USER_TASK,
            ),
            "transition": lambda *_a, **_k: None,
        },
    )()
    decision = ThinkingDecision(mode="silent", reasoning="user ack only", request_complete=False)
    result = ThinkLifeLoop._finish_silent_plan_turn(loop, loop.registry.get("txn_1"), decision)
    assert result["silent"] is True
    assert result["completed"] is False


def test_finish_silent_plan_turn_can_complete_when_requested() -> None:
    loop = ThinkLifeLoop.__new__(ThinkLifeLoop)
    completed: list[str] = []

    class _Registry:
        def get(self, _tid: str) -> TransactionRecord:
            return TransactionRecord(
                transaction_id="txn_2",
                thread_id="t1",
                status=TransactionStatus.RUNNING,
                kind=TransactionKind.SCHEDULE,
            )

        def transition(self, txn_id: str, _status: TransactionStatus) -> None:
            completed.append(txn_id)

    loop.registry = _Registry()
    loop._complete_transaction_after_turn = lambda record: completed.append(record.transaction_id)  # type: ignore[method-assign]
    decision = ThinkingDecision(mode="silent", request_complete=True)
    result = ThinkLifeLoop._finish_silent_plan_turn(loop, loop.registry.get("txn_2"), decision)
    assert result["completed"] is True
    assert request_is_complete(decision) is True
