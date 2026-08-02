from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import m_agent.runtime.langgraph.checkpointer as checkpointer_module
from m_agent.runtime.langgraph.checkpointer import (
    CHECKPOINT_METADATA_TABLE,
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointSchemaVersionError,
    PersistentCheckpointUnavailableError,
    close_checkpointer,
    create_checkpointer,
    describe_checkpointer,
)
from m_agent.runtime.langgraph.engine import TransactionGraphEngine
from m_agent.runtime.routing import (
    DEFAULT_RUNTIME_ENGINE,
    LANGGRAPH_RUNTIME_ENGINE,
    resolve_default_runtime_engine,
    resolve_runtime_engine,
)
from m_agent.runtime.domain.contracts import TransactionKind
from m_agent.runtime.transaction import SQLiteRuntimeStore
from m_agent.runtime.transaction.uow import RuntimeUnitOfWork
from m_agent.runtime.transaction.registry import TransactionRegistry


def test_runtime_engine_is_langgraph_only() -> None:
    assert DEFAULT_RUNTIME_ENGINE == LANGGRAPH_RUNTIME_ENGINE
    assert resolve_runtime_engine() == DEFAULT_RUNTIME_ENGINE
    assert resolve_runtime_engine(explicit=LANGGRAPH_RUNTIME_ENGINE) == (
        LANGGRAPH_RUNTIME_ENGINE
    )


def test_removed_runtime_engine_env_selector_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "M_AGENT_DEFAULT_RUNTIME_ENGINE",
        LANGGRAPH_RUNTIME_ENGINE,
    )
    with pytest.raises(ValueError, match="M_AGENT_DEFAULT_RUNTIME_ENGINE was removed"):
        resolve_default_runtime_engine()
    with pytest.raises(ValueError, match="M_AGENT_DEFAULT_RUNTIME_ENGINE was removed"):
        resolve_runtime_engine()


def test_transaction_create_persists_runtime_engine(tmp_path: Path) -> None:
    store = SQLiteRuntimeStore(tmp_path / "runtime.sqlite3")
    registry = TransactionRegistry(store=store)
    record = registry.create(
        thread_id="thread-a",
        kind=TransactionKind.USER_TASK,
        runtime_engine=LANGGRAPH_RUNTIME_ENGINE,
    )
    loaded = registry.get(record.transaction_id)
    assert loaded is not None
    assert loaded.runtime_engine == LANGGRAPH_RUNTIME_ENGINE


def test_persistent_checkpointer_creates_sqlite_file(tmp_path: Path) -> None:
    db_path = tmp_path / "checkpoints.sqlite3"
    checkpointer = create_checkpointer(db_path=db_path, persistent=True)
    try:
        metadata = describe_checkpointer(checkpointer)
        assert metadata == {
            "backend": "sqlite",
            "durable": True,
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
        }
        row = checkpointer.conn.execute(
            f"SELECT value FROM {CHECKPOINT_METADATA_TABLE} WHERE key = ?",
            ("schema_version",),
        ).fetchone()
        assert row == (CHECKPOINT_SCHEMA_VERSION,)
    finally:
        close_checkpointer(checkpointer)
    assert db_path.is_file()


def test_persistent_checkpointer_requires_database_path() -> None:
    with pytest.raises(ValueError, match="db_path is required"):
        create_checkpointer(persistent=True)


def test_persistent_checkpointer_fails_fast_without_sqlite_saver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(checkpointer_module, "SqliteSaver", None)

    with pytest.raises(
        PersistentCheckpointUnavailableError,
        match="langgraph-checkpoint-sqlite",
    ):
        create_checkpointer(
            db_path=tmp_path / "must-not-fallback.sqlite3",
            persistent=True,
        )


def test_in_memory_checkpointer_requires_explicit_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(checkpointer_module, "SqliteSaver", None)

    checkpointer = create_checkpointer(persistent=False)

    assert describe_checkpointer(checkpointer) == {
        "backend": "memory",
        "durable": False,
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
    }


def test_persistent_checkpointer_rejects_unknown_schema_version(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wrong-schema.sqlite3"
    checkpointer = create_checkpointer(db_path=db_path, persistent=True)
    close_checkpointer(checkpointer)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            f"UPDATE {CHECKPOINT_METADATA_TABLE} SET value = ? WHERE key = ?",
            ("future_checkpoint_v99", "schema_version"),
        )

    with pytest.raises(
        CheckpointSchemaVersionError,
        match="future_checkpoint_v99",
    ):
        create_checkpointer(db_path=db_path, persistent=True)


def test_checkpoint_metadata_is_reverified_for_health_checks(
    tmp_path: Path,
) -> None:
    checkpointer = create_checkpointer(
        db_path=tmp_path / "tampered-schema.sqlite3",
        persistent=True,
    )
    try:
        checkpointer.conn.execute(
            f"DELETE FROM {CHECKPOINT_METADATA_TABLE} WHERE key = ?",
            ("schema_version",),
        )
        checkpointer.conn.commit()

        with pytest.raises(
            CheckpointSchemaVersionError,
            match="version marker is missing",
        ):
            describe_checkpointer(checkpointer)
    finally:
        close_checkpointer(checkpointer)


def test_graph_engine_exposes_verified_checkpoint_metadata(
    tmp_path: Path,
) -> None:
    engine = TransactionGraphEngine(
        registry=object(),
        uow=object(),
        relay_feedback=lambda _intent: "",
        checkpoint_db_path=tmp_path / "engine-checkpoints.sqlite3",
        persistent_checkpoint=True,
    )
    try:
        assert engine.checkpoint_metadata == {
            "backend": "sqlite",
            "durable": True,
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
        }
    finally:
        engine.close()


def test_checkpoint_fault_suppresses_real_write_and_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    registry = TransactionRegistry(
        store=SQLiteRuntimeStore(tmp_path / "runtime.sqlite3")
    )
    transaction = registry.create(
        thread_id="thread-checkpoint-fault",
        conversation_id="conversation-checkpoint-fault",
        kind=TransactionKind.USER_TASK,
        runtime_engine=LANGGRAPH_RUNTIME_ENGINE,
    )
    engine = TransactionGraphEngine(
        registry=registry,
        uow=RuntimeUnitOfWork(registry),
        relay_feedback=lambda _intent: "",
        checkpoint_db_path=tmp_path / "fault-checkpoints.sqlite3",
        persistent_checkpoint=True,
    )
    script = [
        {
            "action": "record_progress",
            "transition_id": "checkpoint-fault-transition",
            "wm_entries": [{"fact": "committed-once"}],
            "goal": "recover from stale checkpoint",
        }
    ]
    try:
        engine.arm_checkpoint_fault()
        first = engine.run_once(
            conversation_id=transaction.conversation_id,
            transaction_id=transaction.transaction_id,
            script=script,
            thread_id=transaction.transaction_id,
        )

        assert first.checkpoint_saved is False
        durable = registry.get(transaction.transaction_id)
        assert durable is not None
        assert durable.wm_entries == [{"fact": "committed-once"}]
        stale_checkpoint = engine.get_checkpoint_state(
            thread_id=transaction.transaction_id
        )
        assert stale_checkpoint.get("wm_snapshot") == []

        replay = engine.resume_thread(
            thread_id=transaction.transaction_id,
            script=script,
        )
        assert replay.checkpoint_saved is True
        recovered = registry.get(transaction.transaction_id)
        assert recovered is not None
        assert recovered.wm_entries == [{"fact": "committed-once"}]
        assert engine.get_checkpoint_state(
            thread_id=transaction.transaction_id
        ).get("wm_snapshot") == [{"fact": "committed-once"}]
    finally:
        engine.close()
