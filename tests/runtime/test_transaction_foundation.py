"""P2 Transaction and Scene foundation gate.

These tests exercise only deterministic domain APIs.  Stimulus admission,
semantic attribution, durable Inbox coordination, and real effect delivery
belong to later phase gates.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any

import pytest

from m_agent.runtime.domain.contracts import (
    ActivationRecord,
    DelegateRecord,
    SceneActor,
    SceneEntry,
    SceneEntryType,
    TransactionKind,
    TransactionLifecycle,
    TransactionState,
)
from m_agent.runtime.transaction.store import (
    IdempotencyConflictError,
    RevisionConflictError,
    SQLiteRuntimeStore,
    StoreInvariantError,
)
from m_agent.runtime.transaction.flush import FlushCoordinator
from m_agent.runtime.transaction.uow import RuntimeUnitOfWork
from m_agent.runtime.transaction.registry import (
    TransactionRegistry,
    TransactionTransitionError,
)
from m_agent.systems.scene.default.jsonl_store import (
    SceneLogStore,
    scene_persist_file_stem,
)


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


def test_runtime_scene_store_mirrors_and_repairs_jsonl_without_duplicates(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    scene_dir = tmp_path / "scene"
    conversation_id = "p2-scene-mirror::0"
    append_id = "append-p2-feedback-1"
    jsonl_path = (
        scene_dir / f"{scene_persist_file_stem(conversation_id)}.jsonl"
    )
    entry = SceneEntry(
        seq=0,
        occurred_at="2026-08-11T08:00:01Z",
        entry_type=SceneEntryType.STIMULUS_EXECUTION_FEEDBACK,
        actor=SceneActor.WORK,
        actor_name="web_search",
        text=(
            "web_search completed successfully and returned 3 results "
            "for the requested query."
        ),
        transaction_id="txn-p2-feedback",
        tool_name="web_search",
    )

    runtime_store = SQLiteRuntimeStore(database_path)
    scene = SceneLogStore(
        runtime_store=runtime_store,
        persist_dir=scene_dir,
        persist_enabled=True,
    )
    stored = scene.append(
        conversation_id,
        entry,
        append_id=append_id,
    )
    replay = scene.append(
        conversation_id,
        entry,
        append_id=append_id,
    )

    assert replay.to_dict() == stored.to_dict()
    rows = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["append_id"] == append_id
    assert rows[0]["entry_type"] == "Stimulus_EXECUTION_FEEDBACK"
    assert rows[0]["actor"] == "web_search"
    assert rows[0]["actor_role"] == "work"
    assert rows[0]["text"] == entry.text
    runtime_store.close()

    # Simulate a lost audit mirror while the authoritative SQLite row survives.
    jsonl_path.unlink()
    reopened_runtime_store = SQLiteRuntimeStore(database_path)
    reopened_scene = SceneLogStore(
        runtime_store=reopened_runtime_store,
        persist_dir=scene_dir,
        persist_enabled=True,
    )

    restored = reopened_scene.tail(conversation_id)
    assert len(restored) == 1
    assert restored[0].entry_type is SceneEntryType.STIMULUS_EXECUTION_FEEDBACK
    assert restored[0].actor is SceneActor.WORK
    assert restored[0].actor_name == "web_search"

    repaired_rows = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert repaired_rows == rows

    reopened_scene.append(
        conversation_id,
        entry,
        append_id=append_id,
    )
    assert len(jsonl_path.read_text(encoding="utf-8").splitlines()) == 1
    reopened_runtime_store.close()


@pytest.mark.parametrize("corruption", ["missing-middle", "conflicting-row"])
def test_runtime_scene_store_atomically_repairs_non_prefix_jsonl_from_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    scene_dir = tmp_path / "scene"
    conversation_id = "p2-scene-canonical-repair::0"
    jsonl_path = (
        scene_dir / f"{scene_persist_file_stem(conversation_id)}.jsonl"
    )
    temporary_path = jsonl_path.with_suffix(jsonl_path.suffix + ".tmp")

    runtime_store = SQLiteRuntimeStore(database_path)
    scene = SceneLogStore(
        runtime_store=runtime_store,
        persist_dir=scene_dir,
        persist_enabled=True,
    )
    for index in range(1, 4):
        scene.append(
            conversation_id,
            _scene_entry(
                text=f"canonical-{index}",
                occurred_at=f"2026-08-11T08:00:0{index}Z",
            ),
            append_id=f"append-p2-canonical-{index}",
        )

    def read_rows(path: Path) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    canonical_rows = read_rows(jsonl_path)
    runtime_store.close()

    if corruption == "missing-middle":
        corrupted_rows = [canonical_rows[0], canonical_rows[2]]
    else:
        corrupted_rows = [dict(row) for row in canonical_rows]
        corrupted_rows[1]["text"] = "conflicting JSONL text"
    jsonl_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in corrupted_rows
        ),
        encoding="utf-8",
    )

    reopened_runtime_store = SQLiteRuntimeStore(database_path)
    reopened_scene = SceneLogStore(
        runtime_store=reopened_runtime_store,
        persist_dir=scene_dir,
        persist_enabled=True,
    )
    replacement_snapshots: list[
        tuple[list[dict[str, Any]], list[dict[str, Any]]]
    ] = []
    original_replace = Path.replace

    def observe_replace(source: Path, target: Path) -> Path:
        if source == temporary_path and Path(target) == jsonl_path:
            replacement_snapshots.append(
                (read_rows(jsonl_path), read_rows(temporary_path))
            )
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", observe_replace)

    restored = reopened_scene.tail(conversation_id)

    assert [entry.append_id for entry in restored] == [
        "append-p2-canonical-1",
        "append-p2-canonical-2",
        "append-p2-canonical-3",
    ]
    assert replacement_snapshots == [(corrupted_rows, canonical_rows)]
    assert read_rows(jsonl_path) == canonical_rows
    assert not temporary_path.exists()
    reopened_runtime_store.close()


def test_runtime_scene_initial_load_serializes_mirror_sync_and_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    scene_dir = tmp_path / "scene"
    conversation_id = "p2-scene-load-race::0"
    jsonl_path = (
        scene_dir / f"{scene_persist_file_stem(conversation_id)}.jsonl"
    )
    runtime_store = SQLiteRuntimeStore(database_path)
    runtime_store.append_scene_entry(
        conversation_id,
        _scene_entry(text="canonical-before-load"),
        append_id="append-before-load",
    )
    scene = SceneLogStore(
        runtime_store=runtime_store,
        persist_dir=scene_dir,
        persist_enabled=True,
    )

    sync_started = threading.Event()
    allow_sync = threading.Event()
    append_done = threading.Event()
    failures: list[BaseException] = []
    original_sync = scene._sync_runtime_jsonl

    def paused_sync(*args: Any, **kwargs: Any) -> None:
        sync_started.set()
        assert allow_sync.wait(timeout=5)
        original_sync(*args, **kwargs)

    monkeypatch.setattr(scene, "_sync_runtime_jsonl", paused_sync)

    def load_scene() -> None:
        try:
            scene.tail(conversation_id)
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    def append_during_load() -> None:
        try:
            scene.append(
                conversation_id,
                _scene_entry(
                    text="canonical-after-load",
                    occurred_at="2026-08-11T08:00:02Z",
                ),
                append_id="append-after-load",
            )
            append_done.set()
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    loader = threading.Thread(target=load_scene)
    appender = threading.Thread(target=append_during_load)
    loader.start()
    assert sync_started.wait(timeout=5)
    appender.start()
    assert append_done.wait(timeout=0.1) is False
    allow_sync.set()
    loader.join(timeout=5)
    appender.join(timeout=5)

    assert not loader.is_alive()
    assert not appender.is_alive()
    assert failures == []
    rows = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["append_id"] for row in rows] == [
        "append-before-load",
        "append-after-load",
    ]
    runtime_store.close()


def test_runtime_scene_store_rewrites_a_torn_jsonl_tail(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    scene_dir = tmp_path / "scene"
    conversation_id = "p2-scene-torn-tail::0"
    jsonl_path = (
        scene_dir / f"{scene_persist_file_stem(conversation_id)}.jsonl"
    )
    runtime_store = SQLiteRuntimeStore(database_path)
    scene = SceneLogStore(
        runtime_store=runtime_store,
        persist_dir=scene_dir,
        persist_enabled=True,
    )
    for index in range(1, 3):
        scene.append(
            conversation_id,
            _scene_entry(
                text=f"canonical-{index}",
                occurred_at=f"2026-08-11T08:00:0{index}Z",
            ),
            append_id=f"append-torn-{index}",
        )
    canonical_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    runtime_store.close()

    jsonl_path.write_text(
        canonical_lines[0] + "\n" + '{"partial":',
        encoding="utf-8",
    )
    reopened_runtime_store = SQLiteRuntimeStore(database_path)
    reopened_scene = SceneLogStore(
        runtime_store=reopened_runtime_store,
        persist_dir=scene_dir,
        persist_enabled=True,
    )

    restored = reopened_scene.tail(conversation_id)

    assert [entry.append_id for entry in restored] == [
        "append-torn-1",
        "append-torn-2",
    ]
    repaired_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert repaired_lines == canonical_lines
    assert all(json.loads(line) for line in repaired_lines)
    reopened_runtime_store.close()


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


def test_flush_snapshot_excludes_scene_and_completion_after_boundary(
    tmp_path: Path,
) -> None:
    store = SQLiteRuntimeStore(tmp_path / "flush-snapshot.sqlite3")
    registry = TransactionRegistry(store=store)
    conversation_id = "p2-flush-snapshot::0"
    completed_before = _create_transaction(
        registry,
        thread_id="p2-flush-snapshot",
        conversation_id=conversation_id,
    )
    completed_before = registry.complete(
        completed_before.transaction_id,
        expected_revision=completed_before.revision,
    )
    completed_after = _create_transaction(
        registry,
        thread_id="p2-flush-snapshot",
        conversation_id=conversation_id,
    )
    already_flushed = store.append_scene_entry(
        conversation_id,
        _scene_entry(
            text="already below the watermark",
            transaction_id=completed_before.transaction_id,
        ),
        append_id="append-below-flush-snapshot-watermark",
    )
    store.mark_scene_flushed(
        conversation_id,
        through_seq=already_flushed.seq,
    )
    first_entry = store.append_scene_entry(
        conversation_id,
        _scene_entry(
            text="present at snapshot",
            transaction_id=completed_before.transaction_id,
        ),
        append_id="append-before-flush-snapshot",
    )

    snapshot = store.capture_flush_snapshot(conversation_id)

    second_entry = store.append_scene_entry(
        conversation_id,
        _scene_entry(
            text="written after snapshot",
            transaction_id=completed_after.transaction_id,
        ),
        append_id="append-after-flush-snapshot",
    )
    completed_after = registry.complete(
        completed_after.transaction_id,
        expected_revision=completed_after.revision,
    )

    assert snapshot == {
        "conversation_id": conversation_id,
        "flush_watermark": already_flushed.seq,
        "through_seq": first_entry.seq,
        "scene_entries": [first_entry.to_dict()],
        "eligible_revisions": {
            completed_before.transaction_id: completed_before.revision,
        },
    }
    json.dumps(snapshot)
    current = store.capture_flush_snapshot(conversation_id)
    assert current["through_seq"] == second_entry.seq
    assert current["scene_entries"] == [
        first_entry.to_dict(),
        second_entry.to_dict(),
    ]
    assert current["eligible_revisions"] == {
        completed_before.transaction_id: completed_before.revision,
        completed_after.transaction_id: completed_after.revision,
    }
    registry.close()


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
