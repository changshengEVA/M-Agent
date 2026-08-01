from __future__ import annotations

from pathlib import Path

import pytest

from m_agent.runtime.langgraph.checkpointer import create_checkpointer
from m_agent.runtime.routing import (
    DEFAULT_RUNTIME_ENGINE,
    LANGGRAPH_RUNTIME_ENGINE,
    resolve_default_runtime_engine,
    resolve_runtime_engine,
)
from m_agent.runtime.think_life.contracts import TransactionKind
from m_agent.runtime.think_life.transaction import SQLiteRuntimeStore
from m_agent.runtime.think_life.transaction_registry import TransactionRegistry


def test_resolve_runtime_engine_defaults_to_think_life() -> None:
    assert resolve_runtime_engine() == DEFAULT_RUNTIME_ENGINE
    assert resolve_runtime_engine(explicit=LANGGRAPH_RUNTIME_ENGINE) == (
        LANGGRAPH_RUNTIME_ENGINE
    )


def test_default_runtime_engine_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "M_AGENT_DEFAULT_RUNTIME_ENGINE",
        LANGGRAPH_RUNTIME_ENGINE,
    )
    assert resolve_default_runtime_engine() == LANGGRAPH_RUNTIME_ENGINE
    assert resolve_runtime_engine() == LANGGRAPH_RUNTIME_ENGINE


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
    create_checkpointer(db_path=db_path, persistent=True)
    assert db_path.is_file()
