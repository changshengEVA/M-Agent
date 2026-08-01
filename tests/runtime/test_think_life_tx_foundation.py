"""P2 Transaction and Scene foundation gate.

These tests exercise only deterministic domain APIs.  Stimulus admission,
semantic attribution, durable Inbox coordination, and real effect delivery
belong to later phase gates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from m_agent.runtime.think_life.contracts import (
    ActivationRecord,
    DelegateRecord,
    SceneActor,
    SceneEntry,
    SceneEntryType,
    TransactionKind,
    TransactionLifecycle,
    TransactionState,
)
from m_agent.runtime.think_life.transaction.store import (
    IdempotencyConflictError,
    RevisionConflictError,
    SQLiteRuntimeStore,
    StoreInvariantError,
)
from m_agent.runtime.think_life.transaction.flush import FlushCoordinator
from m_agent.runtime.think_life.transaction.uow import RuntimeUnitOfWork
from m_agent.runtime.think_life.transaction_registry import (
    TransactionRegistry,
    TransactionTransitionError,
)
from m_agent.systems.scene.default.jsonl_store import SceneLogStore


pytestmark = [pytest.mark.unit, pytest.mark.p2_foundation]


def _new_registry(
    tmp_path: Path,
) -> tuple[TransactionRegistry, Path]:
    persist_path = tmp_path / "transactions.sqlite3"
    return TransactionRegistry(persist_path=persist_path), persist_path


def _create_transaction(
    registry: TransactionRegistry,
    *,
    thread_id: str = "p2-thread",
    conversation_id: str = "p2-thread::0",
):
    return registry.create(
        thread_id=thread_id,
        conversation_id=conversation_id,
        kind=TransactionKind.USER_TASK,
    )


def _required_transaction(
    registry: TransactionRegistry,
    transaction_id: str,
):
    record = registry.get(transaction_id)
    assert record is not None
    return record


def _scene_entry(
    *,
    text: str,
    transaction_id: str = "txn_scene",
    occurred_at: str = "2026-01-01T00:00:01Z",
) -> SceneEntry:
    return SceneEntry(
        seq=0,
        occurred_at=occurred_at,
        entry_type=SceneEntryType.ACTION,
        actor=SceneActor.WORK,
        text=text,
        transaction_id=transaction_id,
    )


def test_create_starts_continue_with_activation_revision_and_persists(
    tmp_path: Path,
) -> None:
    registry, persist_path = _new_registry(tmp_path)

    created = _create_transaction(registry)

    assert created.state == TransactionState.CONTINUE
    assert created.lifecycle_status == TransactionLifecycle.ACTIVE
    assert created.current_activation_id
    assert isinstance(created.revision, int)
    assert created.revision > 0
    created_id = created.transaction_id
    activation_id = created.current_activation_id
    revision = created.revision
    registry.close()

    reopened = TransactionRegistry(persist_path=persist_path)
    restored = _required_transaction(reopened, created_id)
    assert restored.transaction_id == created_id
    assert restored.state == TransactionState.CONTINUE
    assert restored.current_activation_id == activation_id
    assert restored.revision == revision
    reopened.close()


def test_pause_restore_invalidates_old_activation_and_delegate(
    tmp_path: Path,
) -> None:
    registry, _persist_path = _new_registry(tmp_path)
    created = _create_transaction(registry)
    original_activation_id = created.current_activation_id
    assert original_activation_id

    delegated = registry.begin_delegate(
        created.transaction_id,
        "delegate-p2-1",
    )
    assert delegated.current_activation_id == original_activation_id
    assert registry.validate_feedback_source(
        created.transaction_id,
        original_activation_id,
        "delegate-p2-1",
    )

    paused = registry.pause(created.transaction_id, expected_revision=delegated.revision)
    assert paused.state == TransactionState.PAUSE
    assert not registry.validate_feedback_source(
        created.transaction_id,
        original_activation_id,
        "delegate-p2-1",
    )

    restored = registry.restore(
        created.transaction_id,
        source="ui",
        expected_revision=paused.revision,
    )
    assert restored.transaction_id == created.transaction_id
    assert restored.state == TransactionState.CONTINUE
    assert restored.current_activation_id
    assert restored.current_activation_id != original_activation_id
    assert restored.active_delegate_id is None
    assert not registry.validate_feedback_source(
        created.transaction_id,
        original_activation_id,
        "delegate-p2-1",
    )
    registry.close()


def test_delete_is_permanent_and_does_not_mutate_another_transaction(
    tmp_path: Path,
) -> None:
    registry, persist_path = _new_registry(tmp_path)
    first = _create_transaction(registry)
    second = _create_transaction(registry)
    second_before = second.to_dict()
    first_activation_id = first.current_activation_id
    assert first_activation_id
    delegated = registry.begin_delegate(first.transaction_id, "delegate-a")

    deleted = registry.delete(
        first.transaction_id,
        expected_revision=delegated.revision,
    )

    assert deleted.lifecycle_status == TransactionLifecycle.DELETED
    assert deleted.deleted
    assert not registry.validate_feedback_source(
        first.transaction_id,
        first_activation_id,
        "delegate-a",
    )
    with pytest.raises(TransactionTransitionError):
        registry.restore(
            first.transaction_id,
            source="ui",
            expected_revision=deleted.revision,
        )
    assert _required_transaction(
        registry,
        second.transaction_id,
    ).to_dict() == second_before
    registry.close()

    reopened = TransactionRegistry(persist_path=persist_path)
    persisted = _required_transaction(reopened, first.transaction_id)
    assert persisted.lifecycle_status == TransactionLifecycle.DELETED
    with pytest.raises(TransactionTransitionError):
        reopened.restore(
            first.transaction_id,
            source="ui",
            expected_revision=persisted.revision,
        )
    reopened.close()


def test_flush_archives_only_completed_transactions(
    tmp_path: Path,
) -> None:
    registry, persist_path = _new_registry(tmp_path)
    conversation_id = "p2-flush::0"
    complete = _create_transaction(
        registry,
        thread_id="p2-flush",
        conversation_id=conversation_id,
    )
    paused = _create_transaction(
        registry,
        thread_id="p2-flush",
        conversation_id=conversation_id,
    )
    continuing = _create_transaction(
        registry,
        thread_id="p2-flush",
        conversation_id=conversation_id,
    )

    completed_activation_id = complete.current_activation_id
    assert completed_activation_id
    completed = registry.complete(
        complete.transaction_id,
        expected_revision=complete.revision,
    )
    assert completed.state == TransactionState.COMPLETE
    held = registry.pause(paused.transaction_id, expected_revision=paused.revision)
    registry.archive_completed_for_flush(
        conversation_id,
        flush_id="flush-p2-1",
    )

    assert _required_transaction(
        registry,
        completed.transaction_id,
    ).state == TransactionState.ARCHIVE
    assert _required_transaction(
        registry,
        held.transaction_id,
    ).state == TransactionState.PAUSE
    assert _required_transaction(
        registry,
        continuing.transaction_id,
    ).state == TransactionState.CONTINUE
    registry.close()

    reopened = TransactionRegistry(persist_path=persist_path)
    assert _required_transaction(
        reopened,
        completed.transaction_id,
    ).state == TransactionState.ARCHIVE
    assert _required_transaction(
        reopened,
        held.transaction_id,
    ).state == TransactionState.PAUSE
    assert _required_transaction(
        reopened,
        continuing.transaction_id,
    ).state == TransactionState.CONTINUE

    archived = _required_transaction(reopened, completed.transaction_id)
    restored = reopened.restore(
        archived.transaction_id,
        source="ui",
        expected_revision=archived.revision,
    )
    assert restored.state == TransactionState.CONTINUE
    assert restored.transaction_id == completed.transaction_id
    assert restored.current_activation_id
    assert restored.current_activation_id != completed_activation_id
    reopened.close()


def test_stale_expected_revision_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    registry, _persist_path = _new_registry(tmp_path)
    created = _create_transaction(registry)
    stale_revision = created.revision
    paused = registry.pause(created.transaction_id, expected_revision=stale_revision)

    with pytest.raises(RevisionConflictError):
        registry.restore(
            created.transaction_id,
            source="ui",
            expected_revision=stale_revision,
        )

    current = _required_transaction(registry, created.transaction_id)
    assert current.state == TransactionState.PAUSE
    assert current.revision == paused.revision
    registry.close()


def test_uow_replays_complete_result_and_rejects_digest_conflict(
    tmp_path: Path,
) -> None:
    registry, persist_path = _new_registry(tmp_path)
    created = _create_transaction(registry)
    initial_revision = created.revision
    calls: list[str] = []
    expected_result = {
        "outcome": "committed",
        "transaction_id": created.transaction_id,
        "payload_ref": "result-p2-1",
    }

    def mutate(record: Any) -> dict[str, Any]:
        calls.append(record.transaction_id)
        record.wm_entries.append({"fact": "uow-committed"})
        record.task_state.goal = "preserve the first committed result"
        return dict(expected_result)

    uow = RuntimeUnitOfWork(registry)
    first = uow.apply_transition(
        "transition-p2-1",
        {
            "action": "record_progress",
            "goal": "preserve the first committed result",
        },
        created.transaction_id,
        initial_revision,
        mutate,
    )
    replay = uow.apply_transition(
        "transition-p2-1",
        {
            "goal": "preserve the first committed result",
            "action": "record_progress",
        },
        created.transaction_id,
        initial_revision,
        lambda _record: pytest.fail("replay called mutate"),
    )

    assert first == expected_result
    assert replay == first
    assert calls == [created.transaction_id]
    assert _required_transaction(
        registry,
        created.transaction_id,
    ).revision == initial_revision + 1

    with pytest.raises(IdempotencyConflictError):
        uow.apply_transition(
            "transition-p2-1",
            {"action": "different-command"},
            created.transaction_id,
            initial_revision,
            lambda _record: {"outcome": "must-not-run"},
        )
    registry.close()

    reopened = TransactionRegistry(persist_path=persist_path)
    replay_after_reopen = RuntimeUnitOfWork(reopened).apply_transition(
        "transition-p2-1",
        {
            "action": "record_progress",
            "goal": "preserve the first committed result",
        },
        created.transaction_id,
        initial_revision,
        lambda _record: pytest.fail("persisted replay called mutate"),
    )
    assert replay_after_reopen == first
    persisted = _required_transaction(reopened, created.transaction_id)
    assert persisted.wm_entries == [{"fact": "uow-committed"}]
    assert persisted.task_state.goal == "preserve the first committed result"
    reopened.close()


def test_uow_rejects_stale_revision_before_calling_mutate(
    tmp_path: Path,
) -> None:
    registry, _persist_path = _new_registry(tmp_path)
    created = _create_transaction(registry)
    stale_revision = created.revision
    registry.pause(created.transaction_id, expected_revision=stale_revision)
    mutate_called = False

    def mutate(_record: Any) -> dict[str, Any]:
        nonlocal mutate_called
        mutate_called = True
        return {"outcome": "must-not-commit"}

    with pytest.raises(RevisionConflictError):
        RuntimeUnitOfWork(registry).apply_transition(
            "transition-p2-stale",
            {"action": "stale-write"},
            created.transaction_id,
            stale_revision,
            mutate,
        )
    assert mutate_called is False
    registry.close()


def test_scene_append_id_is_idempotent_and_watermark_persists(
    tmp_path: Path,
) -> None:
    scene_dir = tmp_path / "scene"
    conversation_id = "p2-scene::0"
    store = SceneLogStore(
        persist_dir=scene_dir,
        persist_enabled=True,
    )

    first = store.append(
        conversation_id,
        _scene_entry(text="first"),
        append_id="append-p2-1",
    )
    replay = store.append(
        conversation_id,
        _scene_entry(text="must not replace first"),
        append_id="append-p2-1",
    )
    second = store.append(
        conversation_id,
        _scene_entry(
            text="second",
            occurred_at="2026-01-01T00:00:02Z",
        ),
        append_id="append-p2-2",
    )

    assert replay.to_dict() == first.to_dict()
    assert [item.seq for item in store.tail(conversation_id)] == [1, 2]
    assert [item.append_id for item in store.tail(conversation_id)] == [
        "append-p2-1",
        "append-p2-2",
    ]
    store.mark_flushed(conversation_id, through_seq=first.seq)
    assert store.flush_watermark(conversation_id) == first.seq
    assert [
        item.append_id
        for item in store.entries_since_flush(conversation_id)
    ] == [second.append_id]

    reopened = SceneLogStore(
        persist_dir=scene_dir,
        persist_enabled=True,
    )
    persisted_replay = reopened.append(
        conversation_id,
        _scene_entry(text="must still not replace first"),
        append_id="append-p2-1",
    )
    assert persisted_replay.to_dict() == first.to_dict()
    assert len(reopened.tail(conversation_id)) == 2
    assert reopened.flush_watermark(conversation_id) == first.seq


def test_transaction_owned_state_is_isolated_and_persists(
    tmp_path: Path,
) -> None:
    registry, persist_path = _new_registry(tmp_path)
    first = _create_transaction(registry)
    second = _create_transaction(registry)
    first_revision = first.revision

    def mutate_first(record: Any) -> dict[str, Any]:
        record.wm_entries.append({"owner": "first"})
        record.task_state.goal = "first goal"
        return {"transaction_id": record.transaction_id}

    RuntimeUnitOfWork(registry).apply_transition(
        "transition-p2-isolation",
        {"action": "write-owned-state", "owner": "first"},
        first.transaction_id,
        first_revision,
        mutate_first,
    )

    first_after = _required_transaction(registry, first.transaction_id)
    second_after = _required_transaction(registry, second.transaction_id)
    assert first_after.wm_entries == [{"owner": "first"}]
    assert first_after.task_state.goal == "first goal"
    assert second_after.wm_entries == []
    assert second_after.task_state.goal == ""
    registry.close()

    reopened = TransactionRegistry(persist_path=persist_path)
    assert _required_transaction(
        reopened,
        first.transaction_id,
    ).wm_entries == [{"owner": "first"}]
    assert _required_transaction(
        reopened,
        second.transaction_id,
    ).wm_entries == []
    reopened.close()


def test_shared_store_flush_is_atomic_idempotent_and_recovers_outbox(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore(database_path)
    registry = TransactionRegistry(store=store)
    scene = SceneLogStore(
        runtime_store=store,
        persist_dir=tmp_path / "legacy-scene",
        persist_enabled=True,
    )
    coordinator = FlushCoordinator(store=store, registry=registry)
    conversation_id = "p2-atomic-flush::0"
    complete = _create_transaction(
        registry,
        thread_id="p2-atomic-flush",
        conversation_id=conversation_id,
    )
    continuing = _create_transaction(
        registry,
        thread_id="p2-atomic-flush",
        conversation_id=conversation_id,
    )
    completed = registry.complete(
        complete.transaction_id,
        expected_revision=complete.revision,
    )
    scene.append(
        conversation_id,
        _scene_entry(
            text="atomic flush entry",
            transaction_id=completed.transaction_id,
        ),
        append_id="append-p2-atomic-flush",
    )

    coordinator.inject_fault("after_flush_commit_before_materialize")
    first = coordinator.trigger(
        conversation_id=conversation_id,
        flush_id="flush-p2-atomic",
        payload={"reason": "foundation-gate"},
    )
    replay = coordinator.trigger(
        conversation_id=conversation_id,
        flush_id="flush-p2-atomic",
        payload={"reason": "foundation-gate"},
    )

    assert first == replay
    assert first["materialization_status"] == "pending"
    assert first["watermark"] == 1
    assert first["archived_transaction_ids"] == [
        completed.transaction_id
    ]
    assert _required_transaction(
        registry,
        completed.transaction_id,
    ).state == TransactionState.ARCHIVE
    assert _required_transaction(
        registry,
        continuing.transaction_id,
    ).state == TransactionState.CONTINUE
    registry.close()

    reopened_store = SQLiteRuntimeStore(database_path)
    reopened_registry = TransactionRegistry(store=reopened_store)
    reopened_coordinator = FlushCoordinator(
        store=reopened_store,
        registry=reopened_registry,
    )
    recovery = reopened_coordinator.recover_materializations()
    assert recovery["recovered_count"] == 1
    after_recovery = reopened_coordinator.trigger(
        conversation_id=conversation_id,
        flush_id="flush-p2-atomic",
        payload={"reason": "foundation-gate"},
    )
    assert after_recovery["materialization_status"] == "pending"
    assert (
        after_recovery["current_materialization_status"]
        == "materialized"
    )
    assert reopened_store.load_conversation_state(
        conversation_id
    )["flush_watermark"] == 1
    assert _required_transaction(
        reopened_registry,
        completed.transaction_id,
    ).state == TransactionState.ARCHIVE
    with pytest.raises(IdempotencyConflictError):
        reopened_coordinator.trigger(
            conversation_id="another-conversation::0",
            flush_id="flush-p2-atomic",
            payload={"reason": "different-command"},
        )
    reopened_registry.close()


def test_store_enforces_activation_and_transition_idempotency_invariants(
    tmp_path: Path,
) -> None:
    store = SQLiteRuntimeStore(tmp_path / "invariants.sqlite3")
    registry = TransactionRegistry(store=store)
    created = _create_transaction(registry)

    with pytest.raises(StoreInvariantError):
        store.create_activation(
            ActivationRecord(
                activation_id="second-active-activation",
                transaction_id=created.transaction_id,
                source="invalid-concurrent-activation",
                from_revision=created.revision,
            )
        )

    completed = registry.complete(
        created.transaction_id,
        expected_revision=created.revision,
    )
    original_activation = store.list_activations(
        completed.transaction_id
    )[0]
    store.create_delegate(
        DelegateRecord(
            delegate_id="orphan-pending-delegate",
            transaction_id=completed.transaction_id,
            activation_id=original_activation.activation_id,
        )
    )
    flush = FlushCoordinator(store=store, registry=registry).trigger(
        conversation_id=completed.conversation_id,
        flush_id="flush-must-skip-pending-delegate",
    )
    assert flush["archived_transaction_ids"] == []
    assert _required_transaction(
        registry,
        completed.transaction_id,
    ).state == TransactionState.COMPLETE

    immutable_guard = _create_transaction(
        registry,
        thread_id="immutable-thread",
        conversation_id="immutable-thread::0",
    )
    detached = registry.checkout(immutable_guard.transaction_id)
    detached.conversation_id = "another-conversation::0"
    detached.revision += 1
    with pytest.raises(StoreInvariantError):
        store.cas_transaction(
            detached,
            expected_revision=immutable_guard.revision,
        )
    assert _required_transaction(
        registry,
        immutable_guard.transaction_id,
    ).conversation_id == "immutable-thread::0"

    activation = store.list_activations(
        immutable_guard.transaction_id
    )[0]
    activation.transaction_id = completed.transaction_id
    with pytest.raises(StoreInvariantError):
        with store.unit_of_work() as store_uow:
            store_uow.save_activation(activation)

    no_revision_change = _create_transaction(
        registry,
        thread_id="transition-guard",
        conversation_id="transition-guard::0",
    )
    with pytest.raises(StoreInvariantError):
        store.execute_transition(
            transition_id="transition-without-revision-change",
            command={"action": "must-advance-once"},
            transaction_id=no_revision_change.transaction_id,
            expected_revision=no_revision_change.revision,
            operation=lambda _uow: {"outcome": "invalid"},
        )
    assert (
        store.get_transition("transition-without-revision-change")
        is None
    )

    with pytest.raises(StoreInvariantError):
        store.execute_transition(
            transition_id="transition-invalid-digest",
            command={"action": "canonical"},
            command_digest="not-the-canonical-digest",
            operation=lambda _uow: {"outcome": "must-not-commit"},
        )

    first = store.execute_transition(
        transition_id="transition-json-stable-result",
        command={"action": "json-stable-result"},
        operation=lambda _uow: {"values": ("first", "second")},
    )
    replay = store.execute_transition(
        transition_id="transition-json-stable-result",
        command={"action": "json-stable-result"},
        operation=lambda _uow: pytest.fail("replay called operation"),
    )
    assert first.result == {"values": ["first", "second"]}
    assert replay.result == first.result
    registry.close()
