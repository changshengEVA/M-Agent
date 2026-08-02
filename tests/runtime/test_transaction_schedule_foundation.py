"""P2 gate for transaction-side one-shot Schedule primitives.

These tests deliberately stop at the SQLite transaction boundary.  Durable
Inbox admission, consumer claim/disposition, and lease fencing belong to P3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from m_agent.runtime.domain.contracts import (
    ActivationStatus,
    PauseReason,
    TransactionKind,
    TransactionLifecycle,
    TransactionRecord,
    TransactionState,
)
from m_agent.runtime.transaction import (
    IdempotencyConflictError,
    OneShotScheduleRun,
    RevisionConflictError,
    ScheduleDeliveryStatus,
    ScheduleRunStatus,
    StoreInvariantError,
    TransactionScheduleCoordinator,
    stable_schedule_delivery_id,
    stable_schedule_run_id,
)
from m_agent.runtime.transaction.domain import open_activation
from m_agent.runtime.transaction.store import (
    RuntimeStoreUnitOfWork,
    SQLiteRuntimeStore,
)


pytestmark = [pytest.mark.unit, pytest.mark.p2_foundation]

_NOW = "2026-01-01T00:00:00Z"
_DUE = "2026-01-02T09:30:00Z"


def _seed_transaction(
    store: SQLiteRuntimeStore,
    *,
    suffix: str,
) -> TransactionRecord:
    record = TransactionRecord(
        transaction_id=f"txn-schedule-{suffix}",
        thread_id=f"thread-schedule-{suffix}",
        conversation_id=f"thread-schedule-{suffix}::0",
        state=TransactionState.CONTINUE,
        revision=0,
        kind=TransactionKind.USER_TASK,
        created_at=_NOW,
        updated_at=_NOW,
    )
    activation = open_activation(
        record,
        source="test",
        now=_NOW,
        activation_id=f"act-schedule-initial-{suffix}",
    )
    record.revision = 1
    with store.unit_of_work() as uow:
        uow.create_transaction(record)
        uow.create_activation(activation)
    return record


def _coordinator(
    path: Path,
    *,
    suffix: str,
) -> tuple[
    SQLiteRuntimeStore,
    TransactionScheduleCoordinator,
    TransactionRecord,
]:
    store = SQLiteRuntimeStore(path)
    transaction = _seed_transaction(store, suffix=suffix)
    coordinator = TransactionScheduleCoordinator(
        store,
        clock=lambda: _NOW,
    )
    return store, coordinator, transaction


def test_register_enters_scheduled_wait_atomically_and_replays(
    tmp_path: Path,
) -> None:
    database = tmp_path / "schedule-register.sqlite3"
    store, coordinator, transaction = _coordinator(
        database,
        suffix="register",
    )
    schedule_id = "schedule-register"
    run_id = stable_schedule_run_id(
        schedule_id,
        transaction.transaction_id,
        _DUE,
    )

    first = coordinator.register(
        schedule_id=schedule_id,
        transaction_id=transaction.transaction_id,
        due_at=_DUE,
        expected_transaction_revision=1,
        transition_id="transition-register",
    )
    replay = coordinator.register(
        schedule_id=schedule_id,
        transaction_id=transaction.transaction_id,
        due_at=_DUE,
        expected_transaction_revision=1,
        transition_id="transition-register",
    )

    assert first.run.schedule_run_id == run_id
    assert first.run.status == ScheduleRunStatus.SCHEDULED
    assert first.run.revision == 1
    assert first.transaction.state == TransactionState.PAUSE
    assert first.transaction.pause_reason == PauseReason.SCHEDULED_WAIT
    assert first.transaction.current_activation_id is None
    assert first.transaction.revision == 2
    assert replay.replayed is True
    assert replay.run.to_dict() == first.run.to_dict()
    assert replay.transaction.to_dict() == first.transaction.to_dict()
    assert store.load_activation(
        "act-schedule-initial-register"
    ).status == ActivationStatus.COMPLETED

    with pytest.raises(IdempotencyConflictError):
        coordinator.register(
            schedule_id=schedule_id,
            transaction_id=transaction.transaction_id,
            due_at="2026-01-03T09:30:00Z",
            expected_transaction_revision=1,
            transition_id="transition-register",
        )
    store.close()

    reopened = SQLiteRuntimeStore(database)
    persisted = TransactionScheduleCoordinator(reopened).load(run_id)
    assert persisted is not None
    assert persisted.to_dict() == first.run.to_dict()
    reopened_transaction = reopened.load_transaction(
        transaction.transaction_id
    )
    assert reopened_transaction is not None
    assert reopened_transaction.state == TransactionState.PAUSE
    assert reopened_transaction.pause_reason == PauseReason.SCHEDULED_WAIT
    reopened.close()


def test_claim_opens_one_stable_activation_and_rejects_stale_cas(
    tmp_path: Path,
) -> None:
    store, coordinator, transaction = _coordinator(
        tmp_path / "schedule-claim.sqlite3",
        suffix="claim",
    )
    registered = coordinator.register(
        schedule_id="schedule-claim",
        transaction_id=transaction.transaction_id,
        due_at=_DUE,
        expected_transaction_revision=1,
        transition_id="transition-claim-register",
    )

    claimed = coordinator.claim(
        registered.run.schedule_run_id,
        expected_run_revision=1,
        expected_transaction_revision=2,
        transition_id="transition-claim",
    )
    replay = coordinator.claim(
        registered.run.schedule_run_id,
        expected_run_revision=1,
        expected_transaction_revision=2,
        transition_id="transition-claim",
    )

    expected_delivery = stable_schedule_delivery_id(
        registered.run.schedule_run_id,
        1,
    )
    assert claimed.run.status == ScheduleRunStatus.CLAIMED
    assert claimed.run.revision == 2
    assert claimed.run.delivery_generation == 1
    assert claimed.delivery_id == expected_delivery
    assert (
        claimed.run.delivery_status
        == ScheduleDeliveryStatus.CLAIMED
    )
    assert claimed.activation_id
    assert claimed.transaction.transaction_id == transaction.transaction_id
    assert claimed.transaction.state == TransactionState.CONTINUE
    assert (
        claimed.transaction.current_activation_id
        == claimed.activation_id
    )
    assert claimed.transaction.revision == 3
    assert replay.replayed is True
    assert replay.activation_id == claimed.activation_id
    assert replay.delivery_id == expected_delivery

    before_run = coordinator.load(registered.run.schedule_run_id)
    before_transaction = store.load_transaction(
        transaction.transaction_id
    )
    with pytest.raises(RevisionConflictError):
        coordinator.claim(
            registered.run.schedule_run_id,
            expected_run_revision=1,
            expected_transaction_revision=2,
            transition_id="transition-claim-stale",
        )
    assert coordinator.load(
        registered.run.schedule_run_id
    ).to_dict() == before_run.to_dict()
    assert store.load_transaction(
        transaction.transaction_id
    ).to_dict() == before_transaction.to_dict()
    store.close()


def test_complete_and_consumed_share_one_rollback_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, coordinator, transaction = _coordinator(
        tmp_path / "schedule-complete.sqlite3",
        suffix="complete",
    )
    registered = coordinator.register(
        schedule_id="schedule-complete",
        transaction_id=transaction.transaction_id,
        due_at=_DUE,
        expected_transaction_revision=1,
    )
    claimed = coordinator.claim(
        registered.run.schedule_run_id,
        expected_run_revision=1,
        expected_transaction_revision=2,
    )
    original_cas = RuntimeStoreUnitOfWork.cas_schedule_run

    def fail_consumed_run(
        uow: RuntimeStoreUnitOfWork,
        schedule_run_id: str,
        payload: dict[str, object],
        *,
        expected_revision: int,
    ) -> dict[str, object]:
        if payload.get("status") == ScheduleRunStatus.CONSUMED.value:
            raise RuntimeError("injected schedule CAS failure")
        return original_cas(
            uow,
            schedule_run_id,
            payload,
            expected_revision=expected_revision,
        )

    monkeypatch.setattr(
        RuntimeStoreUnitOfWork,
        "cas_schedule_run",
        fail_consumed_run,
    )
    with pytest.raises(RuntimeError, match="injected schedule CAS"):
        coordinator.complete(
            registered.run.schedule_run_id,
            expected_run_revision=2,
            expected_transaction_revision=3,
            transition_id="transition-complete-fault",
        )

    after_failure_run = coordinator.load(
        registered.run.schedule_run_id
    )
    after_failure_transaction = store.load_transaction(
        transaction.transaction_id
    )
    assert after_failure_run.status == ScheduleRunStatus.CLAIMED
    assert after_failure_run.revision == 2
    assert (
        after_failure_transaction.state
        == TransactionState.CONTINUE
    )
    assert after_failure_transaction.revision == 3
    assert (
        after_failure_transaction.current_activation_id
        == claimed.activation_id
    )

    monkeypatch.setattr(
        RuntimeStoreUnitOfWork,
        "cas_schedule_run",
        original_cas,
    )
    completed = coordinator.complete(
        registered.run.schedule_run_id,
        expected_run_revision=2,
        expected_transaction_revision=3,
        transition_id="transition-complete",
    )
    replay = coordinator.complete(
        registered.run.schedule_run_id,
        expected_run_revision=2,
        expected_transaction_revision=3,
        transition_id="transition-complete",
    )
    assert completed.transaction.state == TransactionState.COMPLETE
    assert completed.transaction.current_activation_id is None
    assert completed.transaction.revision == 4
    assert completed.run.status == ScheduleRunStatus.CONSUMED
    assert completed.run.revision == 3
    assert (
        completed.run.delivery_status
        == ScheduleDeliveryStatus.CONSUMED
    )
    assert replay.replayed is True
    store.close()


def test_only_one_run_can_claim_a_transaction(
    tmp_path: Path,
) -> None:
    store, coordinator, transaction = _coordinator(
        tmp_path / "schedule-single-claim.sqlite3",
        suffix="single-claim",
    )
    first = coordinator.register(
        schedule_id="schedule-first",
        transaction_id=transaction.transaction_id,
        due_at=_DUE,
        expected_transaction_revision=1,
    )
    claimed = coordinator.claim(
        first.run.schedule_run_id,
        expected_run_revision=1,
        expected_transaction_revision=2,
    )
    second_run_id = stable_schedule_run_id(
        "schedule-second",
        transaction.transaction_id,
        "2026-01-03T09:30:00Z",
    )
    second = OneShotScheduleRun(
        schedule_id="schedule-second",
        schedule_run_id=second_run_id,
        transaction_id=transaction.transaction_id,
        conversation_id=transaction.conversation_id,
        due_at="2026-01-03T09:30:00Z",
        created_at=_NOW,
        updated_at=_NOW,
    )
    store.create_schedule_run(second_run_id, second.to_dict())

    illegal_claim = second.to_dict()
    illegal_claim["status"] = ScheduleRunStatus.CLAIMED.value
    illegal_claim["revision"] = 2
    with pytest.raises(StoreInvariantError):
        store.cas_schedule_run(
            second_run_id,
            illegal_claim,
            expected_revision=1,
        )

    blocked = coordinator.claim(
        second_run_id,
        expected_run_revision=1,
        expected_transaction_revision=3,
        transition_id="transition-second-run-blocked",
    )
    assert (
        blocked.run.status
        == ScheduleRunStatus.BLOCKED_ON_ACTIVATION
    )
    assert blocked.run.blocked_reason == "another_claimed_run"
    assert blocked.run.revision == 2
    assert blocked.transaction.revision == 3
    assert blocked.transaction.current_activation_id == claimed.activation_id
    assert coordinator.load(
        first.run.schedule_run_id
    ).status == ScheduleRunStatus.CLAIMED
    store.close()


def test_manual_pause_invalidates_activation_and_blocks_run(
    tmp_path: Path,
) -> None:
    store, coordinator, transaction = _coordinator(
        tmp_path / "schedule-pause.sqlite3",
        suffix="pause",
    )
    registered = coordinator.register(
        schedule_id="schedule-pause",
        transaction_id=transaction.transaction_id,
        due_at=_DUE,
        expected_transaction_revision=1,
    )
    claimed = coordinator.claim(
        registered.run.schedule_run_id,
        expected_run_revision=1,
        expected_transaction_revision=2,
    )

    paused = coordinator.manual_pause(
        registered.run.schedule_run_id,
        expected_run_revision=2,
        expected_transaction_revision=3,
        transition_id="transition-manual-pause",
    )
    replay = coordinator.manual_pause(
        registered.run.schedule_run_id,
        expected_run_revision=2,
        expected_transaction_revision=3,
        transition_id="transition-manual-pause",
    )

    assert paused.transaction.state == TransactionState.PAUSE
    assert paused.transaction.current_activation_id is None
    assert paused.transaction.revision == 4
    assert (
        store.load_activation(claimed.activation_id).status
        == ActivationStatus.INVALIDATED
    )
    assert (
        paused.run.status
        == ScheduleRunStatus.BLOCKED_ON_ACTIVATION
    )
    assert paused.run.blocked_reason == "pause"
    assert paused.run.claimed_activation_id is None
    assert (
        paused.run.delivery_status
        == ScheduleDeliveryStatus.ABORTED
    )
    assert paused.run.revision == 3
    assert replay.replayed is True
    store.close()


def test_delete_tombstones_transaction_and_cancels_claimed_run(
    tmp_path: Path,
) -> None:
    store, coordinator, transaction = _coordinator(
        tmp_path / "schedule-delete.sqlite3",
        suffix="delete",
    )
    registered = coordinator.register(
        schedule_id="schedule-delete",
        transaction_id=transaction.transaction_id,
        due_at=_DUE,
        expected_transaction_revision=1,
    )
    claimed = coordinator.claim(
        registered.run.schedule_run_id,
        expected_run_revision=1,
        expected_transaction_revision=2,
    )

    deleted = coordinator.delete(
        registered.run.schedule_run_id,
        expected_run_revision=2,
        expected_transaction_revision=3,
        transition_id="transition-delete",
    )
    replay = coordinator.delete(
        registered.run.schedule_run_id,
        expected_run_revision=2,
        expected_transaction_revision=3,
        transition_id="transition-delete",
    )

    assert (
        deleted.transaction.lifecycle_status
        == TransactionLifecycle.DELETED
    )
    assert deleted.transaction.deleted_at == _NOW
    assert deleted.transaction.current_activation_id is None
    assert deleted.transaction.revision == 4
    assert (
        store.load_activation(claimed.activation_id).status
        == ActivationStatus.INVALIDATED
    )
    assert deleted.run.status == ScheduleRunStatus.CANCELLED
    assert deleted.run.claimed_activation_id is None
    assert (
        deleted.run.delivery_status
        == ScheduleDeliveryStatus.ABORTED
    )
    assert deleted.run.revision == 3
    assert deleted.cancelled_run_ids == (
        registered.run.schedule_run_id,
    )
    assert replay.replayed is True
    store.close()
