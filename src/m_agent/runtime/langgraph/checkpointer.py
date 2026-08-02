"""LangGraph checkpoint construction and deployment-time validation."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

from langgraph.checkpoint.memory import InMemorySaver

try:
    from langgraph.checkpoint.sqlite import SqliteSaver
except ImportError:  # pragma: no cover - exercised by monkeypatch in tests
    SqliteSaver = None  # type: ignore[assignment,misc]

CHECKPOINT_SCHEMA_VERSION = "langgraph_checkpoint_v1"
CHECKPOINT_METADATA_TABLE = "m_agent_checkpoint_metadata"
_SCHEMA_VERSION_KEY = "schema_version"
_METADATA_ATTRIBUTE = "m_agent_checkpoint_backend_metadata"

Checkpointer = Union[InMemorySaver, Any]


class PersistentCheckpointUnavailableError(RuntimeError):
    """Raised when durable checkpoints were requested but cannot be provided."""


class CheckpointSchemaVersionError(RuntimeError):
    """Raised when a checkpoint database has an unsupported app schema."""


@dataclass(frozen=True)
class CheckpointerBackendMetadata:
    """Operational metadata suitable for health and diagnostics endpoints."""

    backend: str
    durable: bool
    schema_version: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _attach_metadata(
    checkpointer: Checkpointer,
    metadata: CheckpointerBackendMetadata,
) -> Checkpointer:
    setattr(checkpointer, _METADATA_ATTRIBUTE, metadata)
    return checkpointer


def _initialize_sqlite_metadata(conn: sqlite3.Connection) -> None:
    """Create and validate the M-Agent-owned checkpoint schema marker."""

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CHECKPOINT_METADATA_TABLE} (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"INSERT OR IGNORE INTO {CHECKPOINT_METADATA_TABLE} (key, value) "
        "VALUES (?, ?)",
        (_SCHEMA_VERSION_KEY, CHECKPOINT_SCHEMA_VERSION),
    )
    conn.commit()
    _verify_sqlite_metadata(conn)


def _verify_sqlite_metadata(conn: sqlite3.Connection) -> None:
    try:
        row = conn.execute(
            f"SELECT value FROM {CHECKPOINT_METADATA_TABLE} WHERE key = ?",
            (_SCHEMA_VERSION_KEY,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise CheckpointSchemaVersionError(
            "LangGraph checkpoint schema metadata is missing or unreadable"
        ) from exc
    if row is None:
        raise CheckpointSchemaVersionError(
            "LangGraph checkpoint schema version marker is missing"
        )
    actual = str(row[0] or "").strip()
    if actual != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointSchemaVersionError(
            "unsupported LangGraph checkpoint schema: "
            f"expected {CHECKPOINT_SCHEMA_VERSION!r}, found {actual!r}"
        )


def verify_checkpointer(
    checkpointer: Checkpointer,
) -> CheckpointerBackendMetadata:
    """Return verified backend metadata, rejecting incompatible SQLite files."""

    metadata = getattr(checkpointer, _METADATA_ATTRIBUTE, None)
    if isinstance(metadata, CheckpointerBackendMetadata):
        if metadata.backend == "sqlite":
            conn = getattr(checkpointer, "conn", None)
            if not isinstance(conn, sqlite3.Connection):
                raise PersistentCheckpointUnavailableError(
                    "SQLite checkpointer does not expose a live sqlite3 connection"
                )
            _verify_sqlite_metadata(conn)
        return metadata

    if isinstance(checkpointer, InMemorySaver):
        return CheckpointerBackendMetadata(
            backend="memory",
            durable=False,
            schema_version=CHECKPOINT_SCHEMA_VERSION,
        )
    raise PersistentCheckpointUnavailableError(
        "checkpoint backend metadata is unavailable; construct it with "
        "create_checkpointer()"
    )


def describe_checkpointer(checkpointer: Checkpointer) -> Dict[str, Any]:
    """Return health-safe, serializable and schema-verified backend metadata."""

    return verify_checkpointer(checkpointer).to_dict()


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
    """Create either an explicitly ephemeral or verified durable checkpointer.

    ``persistent=True`` is a strict durability contract: a database path and
    the SQLite saver package must both be available. Only callers that
    explicitly pass ``persistent=False`` receive an in-memory saver.
    """

    if not persistent:
        return _attach_metadata(
            InMemorySaver(),
            CheckpointerBackendMetadata(
                backend="memory",
                durable=False,
                schema_version=CHECKPOINT_SCHEMA_VERSION,
            ),
        )
    if db_path is None:
        raise ValueError(
            "db_path is required when persistent checkpointing is enabled"
        )
    if SqliteSaver is None:
        raise PersistentCheckpointUnavailableError(
            "persistent LangGraph checkpoints require "
            "langgraph-checkpoint-sqlite; install the project's langgraph "
            "optional dependencies or set persistent=False explicitly for "
            "test-only in-memory checkpoints"
        )

    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        _initialize_sqlite_metadata(conn)
        saver = SqliteSaver(conn)
        if hasattr(saver, "setup"):
            saver.setup()
        return _attach_metadata(
            saver,
            CheckpointerBackendMetadata(
                backend="sqlite",
                durable=True,
                schema_version=CHECKPOINT_SCHEMA_VERSION,
            ),
        )
    except Exception:
        conn.close()
        raise


__all__ = [
    "CHECKPOINT_METADATA_TABLE",
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointSchemaVersionError",
    "Checkpointer",
    "CheckpointerBackendMetadata",
    "InMemorySaver",
    "PersistentCheckpointUnavailableError",
    "SqliteSaver",
    "close_checkpointer",
    "create_checkpointer",
    "describe_checkpointer",
    "verify_checkpointer",
]
