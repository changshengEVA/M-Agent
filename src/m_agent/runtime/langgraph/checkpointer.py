"""Persistent LangGraph checkpoint factory for P8."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional, Union

from langgraph.checkpoint.memory import InMemorySaver

try:
    from langgraph.checkpoint.sqlite import SqliteSaver
except ImportError:  # pragma: no cover - optional dependency
    SqliteSaver = None  # type: ignore[assignment,misc]

CHECKPOINT_SCHEMA_VERSION = "langgraph_checkpoint_v1"

Checkpointer = Union[InMemorySaver, Any]


def close_checkpointer(checkpointer: Optional[Checkpointer]) -> None:
    """Release SQLite connections so temp harness dirs can be cleaned up."""

    if checkpointer is None:
        return
    conn = getattr(checkpointer, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def create_checkpointer(
    *,
    db_path: Optional[Path | str] = None,
    persistent: bool = True,
) -> Checkpointer:
    """Create a LangGraph checkpointer.

    When ``langgraph-checkpoint-sqlite`` is installed and ``persistent`` is
    true, use a SQLite-backed saver at ``db_path``. Otherwise fall back to an
    in-memory saver (P6 PoC behavior).
    """

    if persistent and db_path is not None and SqliteSaver is not None:
        path = Path(db_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        saver = SqliteSaver(conn)
        if hasattr(saver, "setup"):
            saver.setup()
        return saver
    return InMemorySaver()


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "Checkpointer",
    "InMemorySaver",
    "SqliteSaver",
    "close_checkpointer",
    "create_checkpointer",
]
