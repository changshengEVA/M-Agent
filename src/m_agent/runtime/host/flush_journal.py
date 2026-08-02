"""Durable journal for runtime commit and product materialization.

The journal deliberately knows nothing about engine selection or routing.  It
tracks one or more named runtime participants so the same state machine can be
used by the single LangGraph host and by offline migration tooling.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Union


JOURNAL_PENDING = "pending"
JOURNAL_COMPLETED = "completed"
RUNTIME_PENDING = "pending"
RUNTIME_COMMITTED = "committed"
MATERIALIZATION_PENDING = "pending"
MATERIALIZATION_DELIVERED = "delivered"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required_text(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _canonical_json(value: Any, field_name: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be JSON serializable") from exc


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _runtime_ids(snapshot: Mapping[str, Any]) -> List[str]:
    """Extract the ordered, unique target-runtime set from a flush snapshot."""

    raw: Any = None
    for key in ("runtimes", "runtime_ids", "targets"):
        if key in snapshot:
            raw = snapshot[key]
            break
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        values = raw.keys()
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        values = raw
    else:
        raise TypeError(
            "snapshot runtimes must be a sequence of runtime ids or a mapping"
        )
    normalized: List[str] = []
    seen = set()
    for value in values:
        runtime_id = _required_text(value, "runtime_id")
        if runtime_id not in seen:
            normalized.append(runtime_id)
            seen.add(runtime_id)
    return normalized


class FlushIdempotencyConflict(RuntimeError):
    """A stable flush identity was reused with different immutable input."""


class FlushJournalStateError(RuntimeError):
    """A journal transition violated the flush saga state machine."""


@dataclass(frozen=True)
class FlushRuntimeState:
    runtime_id: str
    status: str
    result: Any
    committed_at: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "status": self.status,
            "result": self.result,
            "committed_at": self.committed_at,
        }


@dataclass(frozen=True)
class FlushMaterializationState:
    destination: str
    status: str
    payload: Any
    result: Any
    delivered_at: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "destination": self.destination,
            "status": self.status,
            "payload": self.payload,
            "result": self.result,
            "delivered_at": self.delivered_at,
        }


@dataclass(frozen=True)
class FlushRecord:
    conversation_id: str
    thread_id: str
    flush_id: str
    snapshot: Dict[str, Any]
    snapshot_digest: str
    status: str
    runtimes: Dict[str, FlushRuntimeState]
    materializations: Dict[str, FlushMaterializationState]
    created_at: str
    updated_at: str
    completed_at: Optional[str]

    @property
    def pending_runtimes(self) -> List[str]:
        return [
            runtime_id
            for runtime_id, state in self.runtimes.items()
            if state.status == RUNTIME_PENDING
        ]

    @property
    def committed_runtimes(self) -> List[str]:
        return [
            runtime_id
            for runtime_id, state in self.runtimes.items()
            if state.status == RUNTIME_COMMITTED
        ]

    @property
    def pending_materializations(self) -> List[str]:
        return [
            destination
            for destination, state in self.materializations.items()
            if state.status == MATERIALIZATION_PENDING
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "thread_id": self.thread_id,
            "flush_id": self.flush_id,
            "snapshot": dict(self.snapshot),
            "snapshot_digest": self.snapshot_digest,
            "status": self.status,
            "runtimes": {
                runtime_id: state.to_dict()
                for runtime_id, state in self.runtimes.items()
            },
            "materializations": {
                destination: state.to_dict()
                for destination, state in self.materializations.items()
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_flush_journal (
    flush_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'completed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_runtime_flush_pending
    ON runtime_flush_journal(status, updated_at, flush_id);

CREATE TABLE IF NOT EXISTS runtime_flush_runtime_state (
    flush_id TEXT NOT NULL,
    runtime_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'committed')),
    result_json TEXT,
    result_digest TEXT,
    committed_at TEXT,
    PRIMARY KEY (flush_id, runtime_id),
    FOREIGN KEY (flush_id) REFERENCES runtime_flush_journal(flush_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS runtime_flush_materialization (
    flush_id TEXT NOT NULL,
    destination TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'delivered')),
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    result_json TEXT,
    result_digest TEXT,
    delivered_at TEXT,
    PRIMARY KEY (flush_id, destination),
    FOREIGN KEY (flush_id) REFERENCES runtime_flush_journal(flush_id)
        ON DELETE CASCADE
);
"""


class FlushJournal:
    """SQLite-backed authority for a durable runtime flush saga.

    ``path=None`` uses one connection-scoped in-memory database, which is
    useful for tests or deliberately non-durable runtimes.  A filesystem path
    survives ``close`` and can be reopened to resume pending runtime commits.
    """

    def __init__(self, path: Optional[Union[str, Path]]) -> None:
        self._lock = RLock()
        self._closed = False
        self._path = Path(path) if path is not None else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            database = str(self._path)
        else:
            database = ":memory:"
        self._connection = sqlite3.connect(
            database,
            check_same_thread=False,
            isolation_level=None,
            timeout=30.0,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 30000")
        if self._path is not None:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
        with self._transaction():
            self._connection.executescript(_SCHEMA)

    @property
    def path(self) -> Optional[Path]:
        return self._path

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("runtime flush journal is closed")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._ensure_open()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    @contextmanager
    def _read_transaction(self) -> Iterator[None]:
        """Keep the journal row and its runtime rows on one SQLite snapshot."""

        with self._lock:
            self._ensure_open()
            self._connection.execute("BEGIN")
            try:
                yield
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def _load_record_locked(self, flush_id: str) -> Optional[FlushRecord]:
        row = self._connection.execute(
            """
            SELECT flush_id, conversation_id, thread_id, snapshot_json,
                   snapshot_digest, status, created_at, updated_at, completed_at
              FROM runtime_flush_journal
             WHERE flush_id = ?
            """,
            (flush_id,),
        ).fetchone()
        if row is None:
            return None
        runtime_rows = self._connection.execute(
            """
            SELECT runtime_id, status, result_json, committed_at
              FROM runtime_flush_runtime_state
             WHERE flush_id = ?
             ORDER BY runtime_id
            """,
            (flush_id,),
        ).fetchall()
        runtimes = {
            str(runtime_row["runtime_id"]): FlushRuntimeState(
                runtime_id=str(runtime_row["runtime_id"]),
                status=str(runtime_row["status"]),
                result=(
                    json.loads(str(runtime_row["result_json"]))
                    if runtime_row["result_json"] is not None
                    else None
                ),
                committed_at=(
                    str(runtime_row["committed_at"])
                    if runtime_row["committed_at"] is not None
                    else None
                ),
            )
            for runtime_row in runtime_rows
        }
        materialization_rows = self._connection.execute(
            """
            SELECT destination, status, payload_json, result_json,
                   delivered_at
              FROM runtime_flush_materialization
             WHERE flush_id = ?
             ORDER BY destination
            """,
            (flush_id,),
        ).fetchall()
        materializations = {
            str(item["destination"]): FlushMaterializationState(
                destination=str(item["destination"]),
                status=str(item["status"]),
                payload=json.loads(str(item["payload_json"])),
                result=(
                    json.loads(str(item["result_json"]))
                    if item["result_json"] is not None
                    else None
                ),
                delivered_at=(
                    str(item["delivered_at"])
                    if item["delivered_at"] is not None
                    else None
                ),
            )
            for item in materialization_rows
        }
        return FlushRecord(
            conversation_id=str(row["conversation_id"]),
            thread_id=str(row["thread_id"]),
            flush_id=str(row["flush_id"]),
            snapshot=dict(json.loads(str(row["snapshot_json"]))),
            snapshot_digest=str(row["snapshot_digest"]),
            status=str(row["status"]),
            runtimes=runtimes,
            materializations=materializations,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=(
                str(row["completed_at"])
                if row["completed_at"] is not None
                else None
            ),
        )

    def create_or_get_pending(
        self,
        conversation_id: str,
        thread_id: str,
        flush_id: str,
        snapshot: Mapping[str, Any],
    ) -> FlushRecord:
        """Create the pending saga or return an identical prior attempt.

        ``flush_id`` is the idempotency identity.  Reusing it with a different
        conversation, thread, or canonical snapshot is rejected even if the
        prior saga has already completed.
        """

        cid = _required_text(conversation_id, "conversation_id")
        tid = _required_text(thread_id, "thread_id")
        fid = _required_text(flush_id, "flush_id")
        if not isinstance(snapshot, Mapping):
            raise TypeError("snapshot must be a mapping")
        canonical_snapshot = _canonical_json(dict(snapshot), "snapshot")
        snapshot_digest = _digest(canonical_snapshot)
        runtimes = _runtime_ids(snapshot)
        with self._transaction():
            existing = self._load_record_locked(fid)
            if existing is not None:
                if (
                    existing.conversation_id != cid
                    or existing.thread_id != tid
                    or existing.snapshot_digest != snapshot_digest
                    or _canonical_json(existing.snapshot, "snapshot")
                    != canonical_snapshot
                ):
                    raise FlushIdempotencyConflict(
                        f"flush_id {fid!r} was already used with different input"
                    )
                return existing

            now = _now_iso()
            self._connection.execute(
                """
                INSERT INTO runtime_flush_journal (
                    flush_id, conversation_id, thread_id, snapshot_json,
                    snapshot_digest, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fid,
                    cid,
                    tid,
                    canonical_snapshot,
                    snapshot_digest,
                    JOURNAL_PENDING,
                    now,
                    now,
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO runtime_flush_runtime_state (
                    flush_id, runtime_id, status
                ) VALUES (?, ?, ?)
                """,
                [(fid, runtime_id, RUNTIME_PENDING) for runtime_id in runtimes],
            )
            created = self._load_record_locked(fid)
            assert created is not None
            return created

    def get(self, flush_id: str) -> Optional[FlushRecord]:
        fid = _required_text(flush_id, "flush_id")
        with self._read_transaction():
            return self._load_record_locked(fid)

    def get_pending(self, flush_id: str) -> Optional[FlushRecord]:
        record = self.get(flush_id)
        if record is None or record.status != JOURNAL_PENDING:
            return None
        return record

    def list_pending(
        self,
        *,
        conversation_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> List[FlushRecord]:
        """List recoverable sagas, oldest first, with optional scope filters."""

        clauses = ["status = ?"]
        params: List[str] = [JOURNAL_PENDING]
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            params.append(_required_text(conversation_id, "conversation_id"))
        if thread_id is not None:
            clauses.append("thread_id = ?")
            params.append(_required_text(thread_id, "thread_id"))
        sql = (
            "SELECT flush_id FROM runtime_flush_journal WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at, flush_id"
        )
        with self._read_transaction():
            rows = self._connection.execute(sql, params).fetchall()
            records = [
                self._load_record_locked(str(row["flush_id"])) for row in rows
            ]
        return [record for record in records if record is not None]

    def mark_runtime_committed(
        self,
        flush_id: str,
        runtime_id: str,
        result: Any,
    ) -> FlushRecord:
        fid = _required_text(flush_id, "flush_id")
        runtime = _required_text(runtime_id, "runtime_id")
        canonical_result = _canonical_json(result, "result")
        result_digest = _digest(canonical_result)
        with self._transaction():
            record = self._load_record_locked(fid)
            if record is None:
                raise KeyError(f"unknown flush_id: {fid}")
            state = record.runtimes.get(runtime)
            if state is None:
                raise KeyError(
                    f"runtime {runtime!r} is not a target of flush {fid!r}"
                )
            row = self._connection.execute(
                """
                SELECT status, result_json, result_digest
                  FROM runtime_flush_runtime_state
                 WHERE flush_id = ? AND runtime_id = ?
                """,
                (fid, runtime),
            ).fetchone()
            assert row is not None
            if str(row["status"]) == RUNTIME_COMMITTED:
                if (
                    str(row["result_digest"] or "") != result_digest
                    or str(row["result_json"] or "") != canonical_result
                ):
                    raise FlushIdempotencyConflict(
                        f"runtime {runtime!r} of flush {fid!r} was already "
                        "committed with a different result"
                    )
                return record
            if record.status != JOURNAL_PENDING:
                raise FlushJournalStateError(
                    f"flush {fid!r} is already {record.status}"
                )
            now = _now_iso()
            self._connection.execute(
                """
                UPDATE runtime_flush_runtime_state
                   SET status = ?, result_json = ?, result_digest = ?,
                       committed_at = ?
                 WHERE flush_id = ? AND runtime_id = ? AND status = ?
                """,
                (
                    RUNTIME_COMMITTED,
                    canonical_result,
                    result_digest,
                    now,
                    fid,
                    runtime,
                    RUNTIME_PENDING,
                ),
            )
            self._connection.execute(
                """
                UPDATE runtime_flush_journal
                   SET updated_at = ?
                 WHERE flush_id = ?
                """,
                (now, fid),
            )
            updated = self._load_record_locked(fid)
            assert updated is not None
            return updated

    def stage_materialization(
        self,
        flush_id: str,
        destination: str,
        payload: Any,
    ) -> FlushRecord:
        """Durably freeze an idempotent external delivery before runtime commit."""

        fid = _required_text(flush_id, "flush_id")
        target = _required_text(destination, "destination")
        canonical_payload = _canonical_json(payload, "payload")
        payload_digest = _digest(canonical_payload)
        with self._transaction():
            record = self._load_record_locked(fid)
            if record is None:
                raise KeyError(f"unknown flush_id: {fid}")
            existing = self._connection.execute(
                """
                SELECT payload_json, payload_digest
                  FROM runtime_flush_materialization
                 WHERE flush_id = ? AND destination = ?
                """,
                (fid, target),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["payload_digest"] or "") != payload_digest
                    or str(existing["payload_json"] or "")
                    != canonical_payload
                ):
                    raise FlushIdempotencyConflict(
                        f"materialization {target!r} of flush {fid!r} "
                        "was already staged with a different payload"
                    )
                replay = self._load_record_locked(fid)
                assert replay is not None
                return replay
            if record.status != JOURNAL_PENDING:
                raise FlushJournalStateError(
                    f"flush {fid!r} is already {record.status}"
                )
            now = _now_iso()
            self._connection.execute(
                """
                INSERT INTO runtime_flush_materialization(
                    flush_id, destination, status,
                    payload_json, payload_digest
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    fid,
                    target,
                    MATERIALIZATION_PENDING,
                    canonical_payload,
                    payload_digest,
                ),
            )
            self._connection.execute(
                """
                UPDATE runtime_flush_journal
                   SET updated_at = ?
                 WHERE flush_id = ?
                """,
                (now, fid),
            )
            updated = self._load_record_locked(fid)
            assert updated is not None
            return updated

    def mark_materialization_delivered(
        self,
        flush_id: str,
        destination: str,
        result: Any,
    ) -> FlushRecord:
        fid = _required_text(flush_id, "flush_id")
        target = _required_text(destination, "destination")
        canonical_result = _canonical_json(result, "result")
        result_digest = _digest(canonical_result)
        with self._transaction():
            record = self._load_record_locked(fid)
            if record is None:
                raise KeyError(f"unknown flush_id: {fid}")
            row = self._connection.execute(
                """
                SELECT status, result_json, result_digest
                  FROM runtime_flush_materialization
                 WHERE flush_id = ? AND destination = ?
                """,
                (fid, target),
            ).fetchone()
            if row is None:
                raise KeyError(
                    f"materialization {target!r} is not staged for {fid!r}"
                )
            if str(row["status"]) == MATERIALIZATION_DELIVERED:
                if (
                    str(row["result_digest"] or "") != result_digest
                    or str(row["result_json"] or "") != canonical_result
                ):
                    raise FlushIdempotencyConflict(
                        f"materialization {target!r} of flush {fid!r} "
                        "was already delivered with a different result"
                    )
                return record
            if record.pending_runtimes:
                raise FlushJournalStateError(
                    f"flush {fid!r} still has pending runtimes"
                )
            now = _now_iso()
            self._connection.execute(
                """
                UPDATE runtime_flush_materialization
                   SET status = ?, result_json = ?, result_digest = ?,
                       delivered_at = ?
                 WHERE flush_id = ? AND destination = ? AND status = ?
                """,
                (
                    MATERIALIZATION_DELIVERED,
                    canonical_result,
                    result_digest,
                    now,
                    fid,
                    target,
                    MATERIALIZATION_PENDING,
                ),
            )
            self._connection.execute(
                """
                UPDATE runtime_flush_journal
                   SET updated_at = ?
                 WHERE flush_id = ?
                """,
                (now, fid),
            )
            updated = self._load_record_locked(fid)
            assert updated is not None
            return updated

    def mark_completed(self, flush_id: str) -> FlushRecord:
        fid = _required_text(flush_id, "flush_id")
        with self._transaction():
            record = self._load_record_locked(fid)
            if record is None:
                raise KeyError(f"unknown flush_id: {fid}")
            if record.status == JOURNAL_COMPLETED:
                return record
            if record.pending_runtimes:
                raise FlushJournalStateError(
                    f"flush {fid!r} still has pending runtimes: "
                    + ", ".join(record.pending_runtimes)
                )
            if record.pending_materializations:
                raise FlushJournalStateError(
                    f"flush {fid!r} still has pending materializations: "
                    + ", ".join(record.pending_materializations)
                )
            now = _now_iso()
            self._connection.execute(
                """
                UPDATE runtime_flush_journal
                   SET status = ?, updated_at = ?, completed_at = ?
                 WHERE flush_id = ? AND status = ?
                """,
                (JOURNAL_COMPLETED, now, now, fid, JOURNAL_PENDING),
            )
            completed = self._load_record_locked(fid)
            assert completed is not None
            return completed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "FlushJournal":
        self._ensure_open()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


__all__ = [
    "FlushJournal",
    "FlushRecord",
    "FlushRuntimeState",
    "FlushMaterializationState",
    "FlushIdempotencyConflict",
    "FlushJournalStateError",
    "JOURNAL_PENDING",
    "JOURNAL_COMPLETED",
    "RUNTIME_PENDING",
    "RUNTIME_COMMITTED",
    "MATERIALIZATION_PENDING",
    "MATERIALIZATION_DELIVERED",
]
