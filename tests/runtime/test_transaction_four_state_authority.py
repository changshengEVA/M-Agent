from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3

from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.runtime.think_life.contracts import (
    ActivationStatus,
    PauseReason,
    StimulusEnvelope,
    TransactionKind,
    TransactionState,
)
from m_agent.runtime.think_life.perception.inbox import StimulusInbox
from m_agent.runtime.think_life.transaction.predicates import (
    is_runnable_record,
    project_compat_status,
)
from m_agent.runtime.think_life.transaction.store import SQLiteRuntimeStore
from m_agent.runtime.think_life.transaction_registry import TransactionRegistry


def test_failure_is_a_restorable_pause_and_closes_live_work() -> None:
    registry = TransactionRegistry()
    record = registry.create(thread_id="failure", kind=TransactionKind.USER_TASK)
    activation_id = str(record.current_activation_id)

    failed = registry.fail(record.transaction_id, error="boom")

    assert failed.state == TransactionState.PAUSE
    assert failed.pause_reason == PauseReason.RUNTIME_ERROR
    assert failed.last_error == "boom"
    assert failed.current_activation_id is None
    assert project_compat_status(failed) == "failed"
    activation = registry.store.load_activation(activation_id)
    assert activation is not None
    assert activation.status == ActivationStatus.INVALIDATED

    restored = registry.restore(failed.transaction_id, source="test")
    assert restored.state == TransactionState.CONTINUE
    assert restored.pause_reason is None
    assert is_runnable_record(
        restored,
        registry.store.load_activation(restored.current_activation_id),
    )


def test_complete_is_authoritative_before_flush() -> None:
    registry = TransactionRegistry()
    record = registry.create(thread_id="complete", kind=TransactionKind.USER_TASK)

    completed = registry.complete(record.transaction_id)

    assert completed.state == TransactionState.COMPLETE
    assert completed.current_activation_id is None
    assert project_compat_status(completed) == "completed"
    assert registry.get_active_user_transaction(completed.conversation_id) is None


def test_compat_status_is_not_stored_on_transaction_record() -> None:
    registry = TransactionRegistry()
    record = registry.create(thread_id="skew", kind=TransactionKind.USER_TASK)

    assert "status" not in record.to_dict()
    assert not hasattr(record, "status")
    assert is_runnable_record(record)
    waiting = registry.begin_delegate(record.transaction_id, "delegate-skew")
    assert waiting.state == TransactionState.CONTINUE
    assert project_compat_status(waiting) == "waiting_execution"


def test_durable_preempt_requeues_the_claim_in_place(tmp_path: Path) -> None:
    store = SQLiteRuntimeStore(tmp_path / "preempt.sqlite3")
    inbox = StimulusInbox(store=store)
    original = StimulusEnvelope(
        stimulus_id="stimulus-preempt",
        thread_id="preempt",
        conversation_id="preempt::0",
        stimulus=Stimulus(
            kind=StimulusKind.USER_MESSAGE,
            text="work",
            payload={},
        ),
        occurred_at="2026-01-01T00:00:00Z",
    )
    inbox.push(original, priority=10)
    claimed = inbox.pop_next("preempt")
    assert claimed is not None
    updated = replace(
        claimed,
        stimulus=replace(
            claimed.stimulus,
            payload={"_preempt_count": 1, "_checkpoint": {"phase": "think"}},
        ),
    )

    inbox.requeue_claimed(updated, priority=10)
    store.close()

    reopened = SQLiteRuntimeStore(tmp_path / "preempt.sqlite3")
    ready = reopened.load_stimulus(original.stimulus_id)
    assert ready is not None
    assert ready.disposition == "ready"
    assert ready.accepted_seq == claimed.accepted_seq
    assert ready.payload["_preempt_count"] == 1
    assert ready.payload["_checkpoint"]["phase"] == "think"
    reopened.close()


def test_v2_status_only_failure_is_migrated_before_readers_start(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    store = SQLiteRuntimeStore(database)
    registry = TransactionRegistry(store=store)
    record = registry.create(thread_id="legacy", kind=TransactionKind.USER_TASK)
    activation_id = str(record.current_activation_id)
    old_revision = int(record.revision)
    store.close()

    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT payload_json FROM transactions WHERE transaction_id = ?",
        (record.transaction_id,),
    ).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    payload["schema_version"] = 2
    payload["status"] = "failed"
    payload["state"] = "continue"
    payload["pause_reason"] = None
    connection.execute(
        "UPDATE transactions SET state = 'continue', payload_json = ? "
        "WHERE transaction_id = ?",
        (json.dumps(payload), record.transaction_id),
    )
    connection.commit()
    connection.close()

    migrated_store = SQLiteRuntimeStore(database)
    migrated = migrated_store.load_transaction(record.transaction_id)
    assert migrated is not None
    assert migrated.schema_version == 4
    assert migrated.revision == old_revision + 1
    assert migrated.state == TransactionState.PAUSE
    assert migrated.pause_reason == PauseReason.RUNTIME_ERROR
    assert migrated.current_activation_id is None
    activation = migrated_store.load_activation(activation_id)
    assert activation is not None
    assert activation.status == ActivationStatus.INVALIDATED
    assert migrated_store.legacy_status_migration_report["repaired"] == 1
    assert migrated_store.legacy_status_migration_report["replayed"] is True
    persisted_payload = json.loads(
        migrated_store._connection.execute(
            "SELECT payload_json FROM transactions WHERE transaction_id = ?",
            (record.transaction_id,),
        ).fetchone()[0]
    )
    assert "status" not in persisted_payload
    assert migrated_store.transaction_contract_migration_report["upgraded"] == 1
    migrated_store.close()


def test_v4_contract_cleanup_reaudits_late_legacy_payloads(
    tmp_path: Path,
) -> None:
    database = tmp_path / "late-legacy.sqlite3"
    store = SQLiteRuntimeStore(database)
    registry = TransactionRegistry(store=store)
    record = registry.create(thread_id="late", kind=TransactionKind.USER_TASK)
    store.close()

    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT payload_json FROM transactions WHERE transaction_id = ?",
        (record.transaction_id,),
    ).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    payload["schema_version"] = 3
    payload["status"] = "running"
    payload["correlation"].update(
        {
            "delegate_id": "legacy-delegate",
            "parent_transaction_id": "legacy-parent",
            "supersedes": "legacy-previous",
        }
    )
    connection.execute(
        "UPDATE transactions SET payload_json = ? WHERE transaction_id = ?",
        (json.dumps(payload), record.transaction_id),
    )
    connection.commit()
    connection.close()

    migrated_store = SQLiteRuntimeStore(database)
    migrated = migrated_store.load_transaction(record.transaction_id)
    assert migrated is not None
    assert migrated.schema_version == 4
    persisted = json.loads(
        migrated_store._connection.execute(
            "SELECT payload_json FROM transactions WHERE transaction_id = ?",
            (record.transaction_id,),
        ).fetchone()[0]
    )
    assert "status" not in persisted
    assert not {
        "delegate_id",
        "parent_transaction_id",
        "supersedes",
    }.intersection(persisted["correlation"])
    report = migrated_store.transaction_contract_migration_report
    assert report["replayed"] is True
    assert report["removed_status"] == 1
    assert report["removed_correlation_keys"] == 3
    migrated_store.close()
