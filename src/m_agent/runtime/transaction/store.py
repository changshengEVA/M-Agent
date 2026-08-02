"""SQLite authority for the Runtime P2 transaction foundation."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional
import uuid

from m_agent.layers.perception.contracts import Stimulus, StimulusKind

from ..domain.contracts import (
    ActivationRecord,
    ActivationStatus,
    DelegateRecord,
    DelegateStatus,
    PauseReason,
    SceneEntry,
    StimulusEnvelope,
    TransactionLifecycle,
    TransactionRecord,
    TransactionState,
)
from .domain import archive_transaction


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _optional_text(value: object) -> Optional[str]:
    normalized = str(value or "").strip()
    return normalized or None


def _stimulus_kind(value: object) -> StimulusKind:
    if isinstance(value, StimulusKind):
        return value
    try:
        return StimulusKind(str(value or "").strip())
    except ValueError:
        return StimulusKind.OBSERVATION_TRIGGER


def _stimulus_payload(envelope: StimulusEnvelope) -> Dict[str, Any]:
    kind_value = (
        envelope.stimulus.kind.value
        if isinstance(envelope.stimulus.kind, StimulusKind)
        else str(envelope.stimulus.kind)
    )
    return {
        "stimulus_id": envelope.stimulus_id,
        "thread_id": envelope.thread_id,
        "conversation_id": envelope.conversation_id,
        "stimulus": {
            "kind": kind_value,
            "text": envelope.stimulus.text,
            "payload": dict(envelope.stimulus.payload or {}),
        },
        "occurred_at": envelope.occurred_at,
        "transaction_id": envelope.transaction_id,
        "activation_id": envelope.activation_id,
        "delegate_id": envelope.delegate_id,
        "schedule_id": envelope.schedule_id,
        "schedule_run_id": envelope.schedule_run_id,
        "schedule_delivery_id": envelope.schedule_delivery_id,
        "ingress_key": envelope.ingress_key,
        "priority_override": envelope.priority_override,
        "accepted_seq": envelope.accepted_seq,
        "accepted_at": envelope.accepted_at,
        "effective_priority": envelope.effective_priority,
        "disposition": envelope.disposition,
        "disposition_stage": envelope.disposition_stage,
        "disposition_reason": envelope.disposition_reason,
        "claimed_by": envelope.claimed_by,
        "consumer_epoch": envelope.consumer_epoch,
        "claim_epoch": envelope.claim_epoch,
        "claimed_at": envelope.claimed_at,
        "finalized_at": envelope.finalized_at,
        "worker_latch": envelope.worker_latch,
    }


def _stimulus_from_payload(data: Mapping[str, Any]) -> StimulusEnvelope:
    body = data.get("stimulus")
    stimulus_data = dict(body) if isinstance(body, Mapping) else {}
    payload = stimulus_data.get("payload")
    stimulus_payload = dict(payload) if isinstance(payload, Mapping) else {}
    return StimulusEnvelope(
        stimulus_id=str(data.get("stimulus_id", "") or ""),
        thread_id=str(data.get("thread_id", "") or ""),
        conversation_id=str(data.get("conversation_id", "") or ""),
        stimulus=Stimulus(
            kind=_stimulus_kind(stimulus_data.get("kind")),
            text=str(stimulus_data.get("text", "") or ""),
            payload=stimulus_payload,
        ),
        occurred_at=str(data.get("occurred_at", "") or ""),
        transaction_id=_optional_text(data.get("transaction_id")),
        activation_id=_optional_text(data.get("activation_id")),
        delegate_id=_optional_text(data.get("delegate_id")),
        schedule_id=_optional_text(data.get("schedule_id")),
        schedule_run_id=_optional_text(data.get("schedule_run_id")),
        schedule_delivery_id=_optional_text(
            data.get("schedule_delivery_id")
        ),
        ingress_key=_optional_text(data.get("ingress_key")),
        priority_override=(
            int(data["priority_override"])
            if data.get("priority_override") is not None
            else None
        ),
        accepted_seq=(
            int(data["accepted_seq"])
            if data.get("accepted_seq") is not None
            else None
        ),
        accepted_at=_optional_text(data.get("accepted_at")),
        effective_priority=(
            int(data["effective_priority"])
            if data.get("effective_priority") is not None
            else None
        ),
        disposition=str(data.get("disposition", "new") or "new"),
        disposition_stage=_optional_text(data.get("disposition_stage")),
        disposition_reason=_optional_text(
            data.get("disposition_reason")
        ),
        claimed_by=_optional_text(data.get("claimed_by")),
        consumer_epoch=(
            int(data["consumer_epoch"])
            if data.get("consumer_epoch") is not None
            else None
        ),
        claim_epoch=(
            int(data["claim_epoch"])
            if data.get("claim_epoch") is not None
            else None
        ),
        claimed_at=_optional_text(data.get("claimed_at")),
        finalized_at=_optional_text(data.get("finalized_at")),
        worker_latch=_optional_text(data.get("worker_latch")),
    )


def canonical_command_digest(command: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _json_dumps(dict(command)).encode("utf-8")
    ).hexdigest()


class RuntimeStoreError(RuntimeError):
    pass


class StoreInvariantError(RuntimeStoreError, ValueError):
    pass


class RecordNotFoundError(RuntimeStoreError):
    pass


class RevisionConflictError(RuntimeStoreError):
    def __init__(
        self,
        record_id: str,
        *,
        expected_revision: int,
        actual_revision: Optional[int],
    ) -> None:
        self.record_id = str(record_id)
        self.expected_revision = int(expected_revision)
        self.actual_revision = (
            int(actual_revision)
            if actual_revision is not None
            else None
        )
        super().__init__(
            f"revision conflict for {self.record_id}: "
            f"expected={self.expected_revision}, "
            f"actual={self.actual_revision}"
        )


class IdempotencyConflictError(RuntimeStoreError):
    def __init__(
        self,
        record_id: str,
        *,
        stored_digest: str,
        requested_digest: str,
    ) -> None:
        self.record_id = str(record_id)
        self.stored_digest = str(stored_digest)
        self.requested_digest = str(requested_digest)
        super().__init__(
            f"idempotency conflict for {self.record_id}"
        )


class StaleClaimError(RuntimeStoreError):
    def __init__(
        self,
        stimulus_id: str,
        *,
        expected: Mapping[str, Any],
        actual: Mapping[str, Any],
    ) -> None:
        self.stimulus_id = str(stimulus_id)
        self.expected = dict(expected)
        self.actual = dict(actual)
        super().__init__(f"stale claim for {self.stimulus_id}")


@dataclass(frozen=True)
class TransitionExecution:
    result: Dict[str, Any]
    replayed: bool
    from_revision: Optional[int]
    to_revision: Optional[int]


@dataclass(frozen=True)
class FlushCommitResult:
    result: Dict[str, Any]
    replayed: bool = False


_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    revision INTEGER NOT NULL,
    current_activation_id TEXT,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transactions_conversation
    ON transactions(conversation_id, updated_at, transaction_id);
CREATE INDEX IF NOT EXISTS idx_transactions_thread
    ON transactions(thread_id, updated_at, transaction_id);

CREATE TABLE IF NOT EXISTS activations (
    activation_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(transaction_id)
        REFERENCES transactions(transaction_id)
);
CREATE INDEX IF NOT EXISTS idx_activations_transaction
    ON activations(transaction_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_activations_one_active
    ON activations(transaction_id)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS delegates (
    delegate_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    activation_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(transaction_id)
        REFERENCES transactions(transaction_id),
    FOREIGN KEY(activation_id)
        REFERENCES activations(activation_id)
);
CREATE INDEX IF NOT EXISTS idx_delegates_activation
    ON delegates(activation_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_delegates_one_pending
    ON delegates(transaction_id)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS runtime_transitions (
    transition_id TEXT PRIMARY KEY,
    command_digest TEXT NOT NULL,
    command_json TEXT NOT NULL,
    transaction_id TEXT,
    from_revision INTEGER,
    to_revision INTEGER,
    result_json TEXT NOT NULL,
    committed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_migrations (
    migration_id TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_state (
    conversation_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    scene_seq INTEGER NOT NULL DEFAULT 0,
    flush_watermark INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scene_entries (
    append_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(conversation_id, seq),
    FOREIGN KEY(conversation_id)
        REFERENCES conversation_state(conversation_id)
);

CREATE TABLE IF NOT EXISTS flush_operations (
    flush_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    through_seq INTEGER NOT NULL,
    request_digest TEXT,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    committed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flush_outbox (
    flush_id TEXT NOT NULL,
    destination TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(flush_id, destination)
);

CREATE TABLE IF NOT EXISTS schedule_runs (
    schedule_run_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(transaction_id)
        REFERENCES transactions(transaction_id)
);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_transaction
    ON schedule_runs(transaction_id, updated_at, schedule_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_schedule_runs_one_claimed
    ON schedule_runs(transaction_id)
    WHERE status = 'claimed';

CREATE TABLE IF NOT EXISTS stimulus_partitions (
    conversation_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    next_accepted_seq INTEGER NOT NULL DEFAULT 1,
    consumer_owner_id TEXT,
    consumer_epoch INTEGER NOT NULL DEFAULT 0,
    consumer_lease_until TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    worker_latch TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id)
        REFERENCES conversation_state(conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_stimulus_partitions_thread
    ON stimulus_partitions(thread_id, conversation_id);

CREATE TABLE IF NOT EXISTS stimuli (
    stimulus_id TEXT PRIMARY KEY,
    ingress_key TEXT,
    thread_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    disposition TEXT NOT NULL,
    disposition_stage TEXT,
    disposition_reason TEXT,
    accepted_seq INTEGER,
    accepted_at TEXT,
    effective_priority INTEGER,
    payload_json TEXT NOT NULL,
    transaction_id TEXT,
    activation_id TEXT,
    delegate_id TEXT,
    schedule_id TEXT,
    schedule_run_id TEXT,
    schedule_delivery_id TEXT,
    claimed_by TEXT,
    consumer_epoch INTEGER,
    claim_epoch INTEGER,
    claimed_at TEXT,
    finalized_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id)
        REFERENCES conversation_state(conversation_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_stimuli_ingress_key
    ON stimuli(ingress_key)
    WHERE ingress_key IS NOT NULL AND ingress_key != '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_stimuli_conversation_seq
    ON stimuli(conversation_id, accepted_seq)
    WHERE accepted_seq IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_stimuli_ready_conversation
    ON stimuli(conversation_id, status, effective_priority, accepted_seq);
CREATE INDEX IF NOT EXISTS idx_stimuli_ready_thread
    ON stimuli(thread_id, status, effective_priority, accepted_seq);

CREATE TABLE IF NOT EXISTS effect_intents (
    effect_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    activation_id TEXT NOT NULL,
    delegate_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    delivery_guarantee TEXT NOT NULL,
    idempotency_key TEXT,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    visible_effects INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(transaction_id)
        REFERENCES transactions(transaction_id)
);
CREATE INDEX IF NOT EXISTS idx_effect_intents_transaction
    ON effect_intents(transaction_id, updated_at, effect_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_effect_intents_idempotency
    ON effect_intents(idempotency_key)
    WHERE idempotency_key IS NOT NULL AND idempotency_key != '';

CREATE TABLE IF NOT EXISTS feedback_ingress_outbox (
    outbox_id TEXT PRIMARY KEY,
    effect_id TEXT NOT NULL,
    ingress_key TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    activation_id TEXT NOT NULL,
    delegate_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    discard_evidence TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(ingress_key),
    FOREIGN KEY(effect_id)
        REFERENCES effect_intents(effect_id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_outbox_transaction
    ON feedback_ingress_outbox(transaction_id, status, updated_at);
"""


class RuntimeStoreUnitOfWork:
    """Methods scoped to one caller-owned SQLite transaction."""

    def __init__(
        self,
        store: "SQLiteRuntimeStore",
        connection: sqlite3.Connection,
    ) -> None:
        self.store = store
        self.connection = connection

    def create_transaction(
        self,
        record: TransactionRecord,
    ) -> TransactionRecord:
        payload = record.to_dict()
        try:
            self.connection.execute(
                """
                INSERT INTO transactions(
                    transaction_id, thread_id, conversation_id, kind,
                    state, lifecycle_status, revision,
                    current_activation_id, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.transaction_id,
                    record.thread_id,
                    record.conversation_id,
                    record.kind.value,
                    record.state.value,
                    record.lifecycle_status.value,
                    int(record.revision),
                    record.current_activation_id,
                    _json_dumps(payload),
                    record.updated_at or _now_iso(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreInvariantError(
                f"transaction already exists: {record.transaction_id}"
            ) from exc
        return TransactionRecord.from_dict(deepcopy(payload))

    def load_transaction(
        self,
        transaction_id: str,
    ) -> Optional[TransactionRecord]:
        row = self.connection.execute(
            """
            SELECT payload_json FROM transactions
            WHERE transaction_id = ?
            """,
            (str(transaction_id or "").strip(),),
        ).fetchone()
        if row is None:
            return None
        return TransactionRecord.from_dict(json.loads(row["payload_json"]))

    def list_transactions(
        self,
        *,
        thread_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> List[TransactionRecord]:
        clauses: List[str] = []
        values: List[Any] = []
        if thread_id is not None:
            clauses.append("thread_id = ?")
            values.append(str(thread_id or "").strip())
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            values.append(str(conversation_id or "").strip())
        where = (
            " WHERE " + " AND ".join(clauses)
            if clauses
            else ""
        )
        rows = self.connection.execute(
            "SELECT payload_json FROM transactions"
            + where
            + " ORDER BY updated_at, transaction_id",
            tuple(values),
        ).fetchall()
        return [
            TransactionRecord.from_dict(json.loads(row["payload_json"]))
            for row in rows
        ]

    def is_transaction_flush_eligible(
        self,
        record: TransactionRecord,
    ) -> bool:
        """Return the strict archive eligibility used by flush commits."""

        activations = self.list_activations(record.transaction_id)
        delegates = self.list_delegates(
            transaction_id=record.transaction_id
        )
        return (
            record.lifecycle_status == TransactionLifecycle.ACTIVE
            and record.state == TransactionState.COMPLETE
            and record.current_activation_id is None
            and record.active_delegate_id is None
            and not any(
                item.status == ActivationStatus.ACTIVE
                for item in activations
            )
            and not any(
                item.status == DelegateStatus.PENDING
                for item in delegates
            )
        )

    def cas_transaction(
        self,
        record: TransactionRecord,
        *,
        expected_revision: int,
    ) -> TransactionRecord:
        expected = int(expected_revision)
        current = self.connection.execute(
            """
            SELECT thread_id, conversation_id, kind, revision, payload_json
            FROM transactions
            WHERE transaction_id = ?
            """,
            (record.transaction_id,),
        ).fetchone()
        actual = (
            int(current["revision"])
            if current is not None
            else None
        )
        if actual != expected:
            raise RevisionConflictError(
                record.transaction_id,
                expected_revision=expected,
                actual_revision=actual,
            )
        if int(record.revision) != expected + 1:
            raise StoreInvariantError(
                "CAS write must advance revision exactly once"
            )
        assert current is not None
        current_record = TransactionRecord.from_dict(
            json.loads(current["payload_json"])
        )
        immutable_fields = (
            "transaction_id",
            "thread_id",
            "conversation_id",
            "kind",
            "runtime_engine",
            "schema_version",
        )
        changed_immutable = [
            field_name
            for field_name in immutable_fields
            if getattr(record, field_name)
            != getattr(current_record, field_name)
        ]
        if changed_immutable:
            raise StoreInvariantError(
                "transaction immutable field change rejected: "
                + ", ".join(changed_immutable)
            )
        cursor = self.connection.execute(
            """
            UPDATE transactions
            SET state = ?, lifecycle_status = ?, revision = ?,
                current_activation_id = ?, payload_json = ?,
                updated_at = ?
            WHERE transaction_id = ? AND revision = ?
            """,
            (
                record.state.value,
                record.lifecycle_status.value,
                int(record.revision),
                record.current_activation_id,
                _json_dumps(record.to_dict()),
                record.updated_at or _now_iso(),
                record.transaction_id,
                expected,
            ),
        )
        if cursor.rowcount != 1:
            raise RevisionConflictError(
                record.transaction_id,
                expected_revision=expected,
                actual_revision=actual,
            )
        return TransactionRecord.from_dict(record.to_dict())

    def create_activation(
        self,
        activation: ActivationRecord,
    ) -> ActivationRecord:
        try:
            self.connection.execute(
                """
                INSERT INTO activations(
                    activation_id, transaction_id, status, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    activation.activation_id,
                    activation.transaction_id,
                    activation.status.value,
                    _json_dumps(activation.to_dict()),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreInvariantError(
                f"activation already exists: "
                f"{activation.activation_id}"
            ) from exc
        return ActivationRecord.from_dict(activation.to_dict())

    def load_activation(
        self,
        activation_id: str,
    ) -> Optional[ActivationRecord]:
        row = self.connection.execute(
            """
            SELECT payload_json FROM activations
            WHERE activation_id = ?
            """,
            (str(activation_id or "").strip(),),
        ).fetchone()
        return (
            ActivationRecord.from_dict(json.loads(row["payload_json"]))
            if row is not None
            else None
        )

    def list_activations(
        self,
        transaction_id: str,
    ) -> List[ActivationRecord]:
        rows = self.connection.execute(
            """
            SELECT payload_json FROM activations
            WHERE transaction_id = ?
            ORDER BY rowid
            """,
            (str(transaction_id or "").strip(),),
        ).fetchall()
        return [
            ActivationRecord.from_dict(json.loads(row["payload_json"]))
            for row in rows
        ]

    def save_activation(
        self,
        activation: ActivationRecord,
        *,
        expected_status: Optional[ActivationStatus] = None,
    ) -> ActivationRecord:
        current = self.load_activation(activation.activation_id)
        if current is None:
            raise RecordNotFoundError(activation.activation_id)
        if (
            expected_status is not None
            and current.status != expected_status
        ):
            raise StoreInvariantError(
                f"activation status conflict: {current.status.value}"
            )
        if activation.transaction_id != current.transaction_id:
            raise StoreInvariantError(
                "activation transaction_id is immutable"
            )
        self.connection.execute(
            """
            UPDATE activations
            SET status = ?, payload_json = ?
            WHERE activation_id = ?
            """,
            (
                activation.status.value,
                _json_dumps(activation.to_dict()),
                activation.activation_id,
            ),
        )
        return ActivationRecord.from_dict(activation.to_dict())

    def create_delegate(
        self,
        delegate: DelegateRecord,
    ) -> DelegateRecord:
        activation = self.load_activation(delegate.activation_id)
        if (
            activation is None
            or activation.transaction_id != delegate.transaction_id
        ):
            raise StoreInvariantError(
                "delegate activation does not belong to transaction"
            )
        try:
            self.connection.execute(
                """
                INSERT INTO delegates(
                    delegate_id, transaction_id, activation_id,
                    status, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    delegate.delegate_id,
                    delegate.transaction_id,
                    delegate.activation_id,
                    delegate.status.value,
                    _json_dumps(delegate.to_dict()),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreInvariantError(
                f"delegate already exists: {delegate.delegate_id}"
            ) from exc
        return DelegateRecord.from_dict(delegate.to_dict())

    def load_delegate(
        self,
        delegate_id: str,
    ) -> Optional[DelegateRecord]:
        row = self.connection.execute(
            """
            SELECT payload_json FROM delegates
            WHERE delegate_id = ?
            """,
            (str(delegate_id or "").strip(),),
        ).fetchone()
        return (
            DelegateRecord.from_dict(json.loads(row["payload_json"]))
            if row is not None
            else None
        )

    def list_delegates(
        self,
        *,
        transaction_id: Optional[str] = None,
        activation_id: Optional[str] = None,
    ) -> List[DelegateRecord]:
        clauses: List[str] = []
        values: List[Any] = []
        if transaction_id is not None:
            clauses.append("transaction_id = ?")
            values.append(str(transaction_id or "").strip())
        if activation_id is not None:
            clauses.append("activation_id = ?")
            values.append(str(activation_id or "").strip())
        where = (
            " WHERE " + " AND ".join(clauses)
            if clauses
            else ""
        )
        rows = self.connection.execute(
            "SELECT payload_json FROM delegates"
            + where
            + " ORDER BY rowid",
            tuple(values),
        ).fetchall()
        return [
            DelegateRecord.from_dict(json.loads(row["payload_json"]))
            for row in rows
        ]

    def save_delegate(
        self,
        delegate: DelegateRecord,
        *,
        expected_status: Optional[DelegateStatus] = None,
    ) -> DelegateRecord:
        current = self.load_delegate(delegate.delegate_id)
        if current is None:
            raise RecordNotFoundError(delegate.delegate_id)
        if (
            expected_status is not None
            and current.status != expected_status
        ):
            raise StoreInvariantError(
                f"delegate status conflict: {current.status.value}"
            )
        if (
            delegate.transaction_id != current.transaction_id
            or delegate.activation_id != current.activation_id
        ):
            raise StoreInvariantError(
                "delegate ownership fields are immutable"
            )
        self.connection.execute(
            """
            UPDATE delegates
            SET status = ?, payload_json = ?
            WHERE delegate_id = ?
            """,
            (
                delegate.status.value,
                _json_dumps(delegate.to_dict()),
                delegate.delegate_id,
            ),
        )
        return DelegateRecord.from_dict(delegate.to_dict())

    def load_transition(
        self,
        transition_id: str,
    ) -> Optional[Dict[str, Any]]:
        row = self.connection.execute(
            """
            SELECT * FROM runtime_transitions
            WHERE transition_id = ?
            """,
            (str(transition_id or "").strip(),),
        ).fetchone()
        if row is None:
            return None
        return {
            "transition_id": row["transition_id"],
            "command_digest": row["command_digest"],
            "command": json.loads(row["command_json"]),
            "transaction_id": row["transaction_id"],
            "from_revision": row["from_revision"],
            "to_revision": row["to_revision"],
            "result": json.loads(row["result_json"]),
            "committed_at": row["committed_at"],
        }

    def save_transition(
        self,
        *,
        transition_id: str,
        command_digest: str,
        command: Mapping[str, Any],
        transaction_id: Optional[str],
        from_revision: Optional[int],
        to_revision: Optional[int],
        result: Mapping[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO runtime_transitions(
                transition_id, command_digest, command_json,
                transaction_id, from_revision, to_revision,
                result_json, committed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transition_id,
                command_digest,
                _json_dumps(dict(command)),
                transaction_id,
                from_revision,
                to_revision,
                _json_dumps(dict(result)),
                _now_iso(),
            ),
        )

    def create_schedule_run(
        self,
        schedule_run_id: str,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Create one durable schedule run inside the caller's UoW.

        ``schedule_run_id`` is an idempotency identity, not a generated row
        key.  Repeating an identical create returns the existing payload;
        reusing the identity for different content is a hard conflict.
        """

        run_id = str(schedule_run_id or "").strip()
        body = dict(payload)
        body.setdefault("schedule_run_id", run_id)
        body.setdefault("status", "scheduled")
        body.setdefault("revision", 1)
        transaction_id = str(
            body.get("transaction_id", "") or ""
        ).strip()
        if not run_id or not transaction_id:
            raise StoreInvariantError(
                "schedule_run_id and transaction_id are required"
            )
        if str(body.get("schedule_run_id", "") or "").strip() != run_id:
            raise StoreInvariantError(
                "schedule_run_id payload binding is immutable"
            )
        if int(body.get("revision", 0)) != 1:
            raise StoreInvariantError(
                "a new schedule run must start at revision 1"
            )
        if not str(body.get("status", "") or "").strip():
            raise StoreInvariantError("schedule run status is required")
        if self.load_transaction(transaction_id) is None:
            raise RecordNotFoundError(transaction_id)

        try:
            self.connection.execute(
                """
                INSERT INTO schedule_runs(
                    schedule_run_id, transaction_id, status,
                    revision, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    transaction_id,
                    str(body["status"]),
                    int(body["revision"]),
                    _json_dumps(body),
                    _now_iso(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            existing = self.load_schedule_run(run_id)
            if existing == body:
                return existing
            if existing is None:
                raise StoreInvariantError(
                    "schedule run violates a Store uniqueness invariant"
                ) from exc
            raise IdempotencyConflictError(
                run_id,
                stored_digest=canonical_command_digest(existing),
                requested_digest=canonical_command_digest(body),
            ) from exc
        return deepcopy(body)

    def load_schedule_run(
        self,
        schedule_run_id: str,
    ) -> Optional[Dict[str, Any]]:
        row = self.connection.execute(
            """
            SELECT payload_json FROM schedule_runs
            WHERE schedule_run_id = ?
            """,
            (str(schedule_run_id or "").strip(),),
        ).fetchone()
        return (
            json.loads(row["payload_json"])
            if row is not None
            else None
        )

    def list_schedule_runs(
        self,
        *,
        transaction_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        values: List[Any] = []
        if transaction_id is not None:
            clauses.append("transaction_id = ?")
            values.append(str(transaction_id or "").strip())
        if status is not None:
            clauses.append("status = ?")
            values.append(str(status or "").strip())
        where = (
            " WHERE " + " AND ".join(clauses)
            if clauses
            else ""
        )
        rows = self.connection.execute(
            "SELECT payload_json FROM schedule_runs"
            + where
            + " ORDER BY updated_at, schedule_run_id",
            tuple(values),
        ).fetchall()
        return [
            json.loads(row["payload_json"])
            for row in rows
        ]

    def cas_schedule_run(
        self,
        schedule_run_id: str,
        payload: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> Dict[str, Any]:
        """Revision-fenced schedule update inside the caller's UoW."""

        run_id = str(schedule_run_id or "").strip()
        body = dict(payload)
        current = self.load_schedule_run(run_id)
        if current is None:
            raise RecordNotFoundError(run_id)
        actual = int(current.get("revision", 0))
        expected = int(expected_revision)
        if actual != expected:
            raise RevisionConflictError(
                run_id,
                expected_revision=expected,
                actual_revision=actual,
            )
        if str(body.get("schedule_run_id", "") or "").strip() != run_id:
            raise StoreInvariantError(
                "schedule_run_id payload binding is immutable"
            )
        current_transaction_id = str(
            current.get("transaction_id", "") or ""
        ).strip()
        if (
            str(body.get("transaction_id", "") or "").strip()
            != current_transaction_id
        ):
            raise StoreInvariantError(
                "schedule run transaction binding is immutable"
            )
        if int(body.get("revision", 0)) != actual + 1:
            raise StoreInvariantError(
                "schedule run CAS must advance revision once"
            )
        status = str(body.get("status", "") or "").strip()
        if not status:
            raise StoreInvariantError("schedule run status is required")

        try:
            cursor = self.connection.execute(
                """
                UPDATE schedule_runs
                SET transaction_id = ?, status = ?, revision = ?,
                    payload_json = ?, updated_at = ?
                WHERE schedule_run_id = ? AND revision = ?
                """,
                (
                    current_transaction_id,
                    status,
                    int(body["revision"]),
                    _json_dumps(body),
                    _now_iso(),
                    run_id,
                    actual,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreInvariantError(
                "schedule run violates a Store uniqueness invariant"
            ) from exc
        if cursor.rowcount != 1:
            raise RevisionConflictError(
                run_id,
                expected_revision=expected,
                actual_revision=actual,
            )
        return deepcopy(body)

    def create_effect_intent(
        self,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        body = dict(payload)
        effect_id = str(body.get("effect_id", "") or "").strip()
        if not effect_id:
            raise StoreInvariantError("effect_id is required")
        now = _now_iso()
        body.setdefault("created_at", now)
        body["updated_at"] = now
        try:
            self.connection.execute(
                """
                INSERT INTO effect_intents(
                    effect_id, transaction_id, activation_id, delegate_id,
                    capability, delivery_guarantee, idempotency_key,
                    status, attempts, visible_effects, result_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    effect_id,
                    str(body.get("transaction_id", "") or ""),
                    str(body.get("activation_id", "") or ""),
                    str(body.get("delegate_id", "") or ""),
                    str(body.get("capability", "") or ""),
                    str(body.get("delivery_guarantee", "") or ""),
                    body.get("idempotency_key"),
                    str(body.get("status", "pending") or "pending"),
                    int(body.get("attempts", 0) or 0),
                    int(body.get("visible_effects", 0) or 0),
                    body.get("result_json"),
                    str(body.get("created_at", now) or now),
                    str(body.get("updated_at", now) or now),
                ),
            )
        except sqlite3.IntegrityError as exc:
            existing = self.load_effect_intent(effect_id)
            if existing is not None:
                return deepcopy(existing)
            raise StoreInvariantError(
                f"effect intent already exists: {effect_id}"
            ) from exc
        return deepcopy(body)

    def load_effect_intent(
        self,
        effect_id: str,
    ) -> Optional[Dict[str, Any]]:
        row = self.connection.execute(
            """
            SELECT * FROM effect_intents WHERE effect_id = ?
            """,
            (str(effect_id or "").strip(),),
        ).fetchone()
        if row is None:
            return None
        return {
            "effect_id": row["effect_id"],
            "transaction_id": row["transaction_id"],
            "activation_id": row["activation_id"],
            "delegate_id": row["delegate_id"],
            "capability": row["capability"],
            "delivery_guarantee": row["delivery_guarantee"],
            "idempotency_key": row["idempotency_key"],
            "status": row["status"],
            "attempts": int(row["attempts"]),
            "visible_effects": int(row["visible_effects"]),
            "result_json": row["result_json"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_effect_intents(
        self,
        *,
        transaction_id: str,
    ) -> List[Dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM effect_intents
            WHERE transaction_id = ?
            ORDER BY created_at, effect_id
            """,
            (str(transaction_id or "").strip(),),
        ).fetchall()
        return [
            {
                "effect_id": row["effect_id"],
                "transaction_id": row["transaction_id"],
                "activation_id": row["activation_id"],
                "delegate_id": row["delegate_id"],
                "capability": row["capability"],
                "delivery_guarantee": row["delivery_guarantee"],
                "idempotency_key": row["idempotency_key"],
                "status": row["status"],
                "attempts": int(row["attempts"]),
                "visible_effects": int(row["visible_effects"]),
                "result_json": row["result_json"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def update_effect_intent(
        self,
        effect_id: str,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        body = dict(payload)
        eid = str(effect_id or "").strip()
        now = _now_iso()
        body["updated_at"] = now
        self.connection.execute(
            """
            UPDATE effect_intents
            SET status = ?, attempts = ?, visible_effects = ?,
                result_json = ?, updated_at = ?
            WHERE effect_id = ?
            """,
            (
                str(body.get("status", "pending") or "pending"),
                int(body.get("attempts", 0) or 0),
                int(body.get("visible_effects", 0) or 0),
                body.get("result_json"),
                now,
                eid,
            ),
        )
        loaded = self.load_effect_intent(eid)
        if loaded is None:
            raise RecordNotFoundError(eid)
        return loaded

    def create_feedback_outbox(
        self,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        body = dict(payload)
        outbox_id = str(body.get("outbox_id", "") or "").strip()
        if not outbox_id:
            raise StoreInvariantError("outbox_id is required")
        ingress_key = str(body.get("ingress_key", "") or "").strip()
        if not ingress_key:
            raise StoreInvariantError("ingress_key is required")
        now = _now_iso()
        body["updated_at"] = now
        try:
            self.connection.execute(
                """
                INSERT INTO feedback_ingress_outbox(
                    outbox_id, effect_id, ingress_key, transaction_id,
                    activation_id, delegate_id, status, payload_json,
                    attempts, discard_evidence, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outbox_id,
                    str(body.get("effect_id", "") or ""),
                    ingress_key,
                    str(body.get("transaction_id", "") or ""),
                    str(body.get("activation_id", "") or ""),
                    str(body.get("delegate_id", "") or ""),
                    str(body.get("status", "pending") or "pending"),
                    _json_dumps(body.get("payload") or {}),
                    int(body.get("attempts", 0) or 0),
                    body.get("discard_evidence"),
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            existing = self.load_feedback_outbox_by_ingress(ingress_key)
            if existing is not None:
                return deepcopy(existing)
            raise StoreInvariantError(
                f"feedback outbox already exists: {outbox_id}"
            ) from exc
        return deepcopy(body)

    def load_feedback_outbox_by_ingress(
        self,
        ingress_key: str,
    ) -> Optional[Dict[str, Any]]:
        row = self.connection.execute(
            """
            SELECT * FROM feedback_ingress_outbox
            WHERE ingress_key = ?
            """,
            (str(ingress_key or "").strip(),),
        ).fetchone()
        if row is None:
            return None
        return self._feedback_outbox_row(row)

    def list_feedback_outbox(
        self,
        *,
        transaction_id: str,
    ) -> List[Dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM feedback_ingress_outbox
            WHERE transaction_id = ?
            ORDER BY updated_at, outbox_id
            """,
            (str(transaction_id or "").strip(),),
        ).fetchall()
        return [self._feedback_outbox_row(row) for row in rows]

    def update_feedback_outbox(
        self,
        outbox_id: str,
        *,
        status: str,
        attempts: Optional[int] = None,
        discard_evidence: Optional[str] = None,
    ) -> Dict[str, Any]:
        oid = str(outbox_id or "").strip()
        now = _now_iso()
        current = self.connection.execute(
            """
            SELECT * FROM feedback_ingress_outbox WHERE outbox_id = ?
            """,
            (oid,),
        ).fetchone()
        if current is None:
            raise RecordNotFoundError(oid)
        next_attempts = (
            int(attempts)
            if attempts is not None
            else int(current["attempts"]) + 1
        )
        evidence = (
            discard_evidence
            if discard_evidence is not None
            else current["discard_evidence"]
        )
        self.connection.execute(
            """
            UPDATE feedback_ingress_outbox
            SET status = ?, attempts = ?, discard_evidence = ?,
                updated_at = ?
            WHERE outbox_id = ?
            """,
            (
                str(status or "").strip(),
                next_attempts,
                evidence,
                now,
                oid,
            ),
        )
        row = self.connection.execute(
            """
            SELECT * FROM feedback_ingress_outbox WHERE outbox_id = ?
            """,
            (oid,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(oid)
        return self._feedback_outbox_row(row)

    @staticmethod
    def _feedback_outbox_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "outbox_id": row["outbox_id"],
            "effect_id": row["effect_id"],
            "ingress_key": row["ingress_key"],
            "transaction_id": row["transaction_id"],
            "activation_id": row["activation_id"],
            "delegate_id": row["delegate_id"],
            "status": row["status"],
            "payload": json.loads(row["payload_json"]),
            "attempts": int(row["attempts"]),
            "discard_evidence": row["discard_evidence"],
            "updated_at": row["updated_at"],
        }

    def ensure_stimulus_partition(
        self,
        conversation_id: str,
        *,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        cid = str(conversation_id or "").strip()
        state = self.ensure_conversation(cid, thread_id=thread_id)
        tid = str(thread_id or state["thread_id"] or "").strip()
        now = _now_iso()
        self.connection.execute(
            """
            INSERT OR IGNORE INTO stimulus_partitions(
                conversation_id, thread_id, next_accepted_seq,
                consumer_owner_id, consumer_epoch,
                consumer_lease_until, claim_epoch,
                worker_latch, updated_at
            ) VALUES (?, ?, 1, NULL, 0, NULL, 0, NULL, ?)
            """,
            (cid, tid, now),
        )
        row = self.connection.execute(
            """
            SELECT * FROM stimulus_partitions
            WHERE conversation_id = ?
            """,
            (cid,),
        ).fetchone()
        assert row is not None
        return dict(row)

    def load_partition_state(
        self,
        conversation_id: str,
    ) -> Dict[str, Any]:
        state = self.ensure_stimulus_partition(conversation_id)
        ready_count = self.count_ready_stimuli(
            conversation_id=str(state["conversation_id"])
        )
        claimed_count = self.connection.execute(
            """
            SELECT COUNT(*) AS value FROM stimuli
            WHERE conversation_id = ? AND status = 'claimed'
            """,
            (str(state["conversation_id"]),),
        ).fetchone()
        data = dict(state)
        data["ready_count"] = int(ready_count)
        data["claimed_count"] = int(
            claimed_count["value"] if claimed_count is not None else 0
        )
        return data

    def _stimulus_from_row(
        self,
        row: sqlite3.Row,
    ) -> StimulusEnvelope:
        data = json.loads(row["payload_json"])
        data["disposition"] = row["status"]
        data["disposition_stage"] = row["disposition_stage"]
        data["disposition_reason"] = row["disposition_reason"]
        data["accepted_seq"] = row["accepted_seq"]
        data["accepted_at"] = row["accepted_at"]
        data["effective_priority"] = row["effective_priority"]
        data["claimed_by"] = row["claimed_by"]
        data["consumer_epoch"] = row["consumer_epoch"]
        data["claim_epoch"] = row["claim_epoch"]
        data["claimed_at"] = row["claimed_at"]
        data["finalized_at"] = row["finalized_at"]
        return _stimulus_from_payload(data)

    def _write_stimulus_row(
        self,
        data: Mapping[str, Any],
        *,
        insert: bool,
    ) -> None:
        stimulus_data = dict(data.get("stimulus") or {})
        kind_value = str(stimulus_data.get("kind", "") or "")
        status = str(data.get("disposition", "ready") or "ready")
        values = (
            str(data.get("stimulus_id", "") or ""),
            _optional_text(data.get("ingress_key")),
            str(data.get("thread_id", "") or ""),
            str(data.get("conversation_id", "") or ""),
            kind_value,
            status,
            status,
            _optional_text(data.get("disposition_stage")),
            _optional_text(data.get("disposition_reason")),
            data.get("accepted_seq"),
            _optional_text(data.get("accepted_at")),
            data.get("effective_priority"),
            _json_dumps(dict(data)),
            _optional_text(data.get("transaction_id")),
            _optional_text(data.get("activation_id")),
            _optional_text(data.get("delegate_id")),
            _optional_text(data.get("schedule_id")),
            _optional_text(data.get("schedule_run_id")),
            _optional_text(data.get("schedule_delivery_id")),
            _optional_text(data.get("claimed_by")),
            data.get("consumer_epoch"),
            data.get("claim_epoch"),
            _optional_text(data.get("claimed_at")),
            _optional_text(data.get("finalized_at")),
            _now_iso(),
        )
        if insert:
            self.connection.execute(
                """
                INSERT INTO stimuli(
                    stimulus_id, ingress_key, thread_id,
                    conversation_id, kind, status, disposition,
                    disposition_stage, disposition_reason,
                    accepted_seq, accepted_at, effective_priority,
                    payload_json, transaction_id, activation_id,
                    delegate_id, schedule_id, schedule_run_id,
                    schedule_delivery_id, claimed_by, consumer_epoch,
                    claim_epoch, claimed_at, finalized_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                values,
            )
            return
        self.connection.execute(
            """
            UPDATE stimuli
            SET ingress_key = ?, thread_id = ?, conversation_id = ?,
                kind = ?, status = ?, disposition = ?,
                disposition_stage = ?, disposition_reason = ?,
                accepted_seq = ?, accepted_at = ?,
                effective_priority = ?, payload_json = ?,
                transaction_id = ?, activation_id = ?, delegate_id = ?,
                schedule_id = ?, schedule_run_id = ?,
                schedule_delivery_id = ?, claimed_by = ?,
                consumer_epoch = ?, claim_epoch = ?, claimed_at = ?,
                finalized_at = ?, updated_at = ?
            WHERE stimulus_id = ?
            """,
            values[1:] + (values[0],),
        )

    def admit_stimulus(
        self,
        stimulus: StimulusEnvelope,
        *,
        effective_priority: int,
        disposition: str = "ready",
        disposition_stage: Optional[str] = None,
        disposition_reason: Optional[str] = None,
    ) -> StimulusEnvelope:
        sid = str(stimulus.stimulus_id or "").strip()
        cid = str(stimulus.conversation_id or "").strip()
        tid = str(stimulus.thread_id or "").strip()
        if not sid:
            raise StoreInvariantError("stimulus_id is required")
        if not cid:
            raise StoreInvariantError("conversation_id is required")
        if not tid:
            raise StoreInvariantError("thread_id is required")
        ingress_key = _optional_text(stimulus.ingress_key)
        if ingress_key is not None:
            row = self.connection.execute(
                """
                SELECT * FROM stimuli
                WHERE ingress_key = ?
                """,
                (ingress_key,),
            ).fetchone()
            if row is not None:
                return self._stimulus_from_row(row)

        existing = self.connection.execute(
            """
            SELECT * FROM stimuli
            WHERE stimulus_id = ?
            """,
            (sid,),
        ).fetchone()
        if existing is not None:
            return self._stimulus_from_row(existing)

        status = str(disposition or "ready").strip() or "ready"
        data = _stimulus_payload(stimulus)
        data["ingress_key"] = ingress_key
        data["effective_priority"] = int(effective_priority)
        data["disposition"] = status
        data["disposition_stage"] = disposition_stage
        data["disposition_reason"] = disposition_reason
        data["claimed_by"] = None
        data["consumer_epoch"] = None
        data["claim_epoch"] = None
        data["claimed_at"] = None
        data["finalized_at"] = None
        if status == "ready":
            partition = self.ensure_stimulus_partition(
                cid,
                thread_id=tid,
            )
            accepted_seq = (
                int(stimulus.accepted_seq)
                if stimulus.accepted_seq is not None
                else int(partition["next_accepted_seq"])
            )
            data["accepted_seq"] = accepted_seq
            data["accepted_at"] = stimulus.accepted_at or _now_iso()
            next_seq = max(
                int(partition["next_accepted_seq"]),
                accepted_seq + 1,
            )
            self.connection.execute(
                """
                UPDATE stimulus_partitions
                SET next_accepted_seq = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (next_seq, _now_iso(), cid),
            )
        else:
            self.ensure_stimulus_partition(cid, thread_id=tid)
            data["accepted_seq"] = None
            data["accepted_at"] = None
        self._write_stimulus_row(data, insert=True)
        row = self.connection.execute(
            "SELECT * FROM stimuli WHERE stimulus_id = ?",
            (sid,),
        ).fetchone()
        assert row is not None
        return self._stimulus_from_row(row)

    def requeue_claimed_stimulus(
        self,
        stimulus: StimulusEnvelope,
        *,
        effective_priority: int,
    ) -> StimulusEnvelope:
        """CAS a claimed stimulus back to ready with its updated payload."""

        sid = str(stimulus.stimulus_id or "").strip()
        row = self.connection.execute(
            "SELECT * FROM stimuli WHERE stimulus_id = ?",
            (sid,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(sid)
        current = self._stimulus_from_row(row)
        expected = {
            "claimed_by": str(stimulus.claimed_by or ""),
            "consumer_epoch": stimulus.consumer_epoch,
            "claim_epoch": stimulus.claim_epoch,
        }
        actual = {
            "claimed_by": str(current.claimed_by or ""),
            "consumer_epoch": current.consumer_epoch,
            "claim_epoch": current.claim_epoch,
        }
        if current.disposition != "claimed" or expected != actual:
            raise StaleClaimError(sid, expected=expected, actual=actual)
        if (
            stimulus.thread_id != current.thread_id
            or stimulus.conversation_id != current.conversation_id
            or stimulus.transaction_id != current.transaction_id
            or stimulus.accepted_seq != current.accepted_seq
        ):
            raise StoreInvariantError(
                "requeue cannot change stimulus ownership or accepted order"
            )

        data = _stimulus_payload(stimulus)
        data["ingress_key"] = current.ingress_key
        data["accepted_seq"] = current.accepted_seq
        data["accepted_at"] = current.accepted_at
        data["effective_priority"] = int(effective_priority)
        data["disposition"] = "ready"
        data["disposition_stage"] = None
        data["disposition_reason"] = None
        data["claimed_by"] = None
        data["consumer_epoch"] = None
        data["claim_epoch"] = None
        data["claimed_at"] = None
        data["finalized_at"] = None
        self._write_stimulus_row(data, insert=False)
        stored = self.load_stimulus(sid)
        assert stored is not None
        return stored

    def load_stimulus(
        self,
        stimulus_id: str,
    ) -> Optional[StimulusEnvelope]:
        row = self.connection.execute(
            """
            SELECT * FROM stimuli
            WHERE stimulus_id = ?
            """,
            (str(stimulus_id or "").strip(),),
        ).fetchone()
        return self._stimulus_from_row(row) if row is not None else None

    def bind_stimulus_target(
        self,
        stimulus_id: str,
        *,
        transaction_id: str,
    ) -> StimulusEnvelope:
        """Durably bind attributed ready/claimed work to its transaction.

        Sourceless ingress is claimed before semantic attribution.  Persisting
        the resulting transaction id makes delete cleanup crash-safe: a lease
        recovered after process restart cannot be matched to a different
        transaction.  If the tombstone already won, this same Store
        transaction terminalizes the stimulus instead of binding live work.
        """

        sid = str(stimulus_id or "").strip()
        tx_id = str(transaction_id or "").strip()
        if not sid:
            raise StoreInvariantError("stimulus_id is required")
        if not tx_id:
            raise StoreInvariantError("transaction_id is required")
        stimulus = self.load_stimulus(sid)
        if stimulus is None:
            raise RecordNotFoundError(sid)
        if stimulus.disposition not in {"ready", "claimed"}:
            return stimulus
        existing = str(stimulus.transaction_id or "").strip()
        if existing and existing != tx_id:
            raise StoreInvariantError(
                "stimulus is already bound to another transaction"
            )
        record = self.load_transaction(tx_id)
        if record is None:
            raise RecordNotFoundError(tx_id)
        if (
            record.thread_id != stimulus.thread_id
            or record.conversation_id != stimulus.conversation_id
        ):
            raise StoreInvariantError(
                "stimulus target belongs to another runtime scope"
            )
        if record.deleted or record.deleted_at is not None:
            aborted = self.set_stimulus_disposition(
                sid,
                disposition="aborted",
                stage="transaction_delete",
                reason="transaction_deleted",
            )
            assert aborted is not None
            return aborted
        if existing == tx_id:
            return stimulus
        data = _stimulus_payload(stimulus)
        data["transaction_id"] = tx_id
        self._write_stimulus_row(data, insert=False)
        bound = self.load_stimulus(sid)
        assert bound is not None
        return bound

    def list_ready_stimuli(
        self,
        *,
        conversation_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> List[StimulusEnvelope]:
        clauses = ["status = 'ready'"]
        values: List[Any] = []
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            values.append(str(conversation_id or "").strip())
        if thread_id is not None:
            clauses.append("thread_id = ?")
            values.append(str(thread_id or "").strip())
        rows = self.connection.execute(
            """
            SELECT * FROM stimuli
            WHERE """ + " AND ".join(clauses) + """
            ORDER BY effective_priority, accepted_seq, stimulus_id
            """,
            tuple(values),
        ).fetchall()
        return [self._stimulus_from_row(row) for row in rows]

    def list_stimuli(
        self,
        *,
        conversation_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> List[StimulusEnvelope]:
        """Every admitted stimulus in ingress order, whatever its disposition."""

        clauses: List[str] = []
        values: List[Any] = []
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            values.append(str(conversation_id or "").strip())
        if thread_id is not None:
            clauses.append("thread_id = ?")
            values.append(str(thread_id or "").strip())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.connection.execute(
            "SELECT * FROM stimuli"
            + where
            + " ORDER BY accepted_seq, stimulus_id",
            tuple(values),
        ).fetchall()
        return [self._stimulus_from_row(row) for row in rows]

    def count_ready_stimuli(
        self,
        *,
        conversation_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> int:
        clauses = ["status = 'ready'"]
        values: List[Any] = []
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            values.append(str(conversation_id or "").strip())
        if thread_id is not None:
            clauses.append("thread_id = ?")
            values.append(str(thread_id or "").strip())
        row = self.connection.execute(
            "SELECT COUNT(*) AS value FROM stimuli WHERE "
            + " AND ".join(clauses),
            tuple(values),
        ).fetchone()
        return int(row["value"] if row is not None else 0)

    def peek_next_stimulus_priority(
        self,
        thread_id: str,
    ) -> Optional[int]:
        row = self.connection.execute(
            """
            SELECT effective_priority FROM stimuli
            WHERE thread_id = ? AND status = 'ready'
            ORDER BY effective_priority, accepted_seq, stimulus_id
            LIMIT 1
            """,
            (str(thread_id or "").strip(),),
        ).fetchone()
        return int(row["effective_priority"]) if row is not None else None

    def clear_thread_stimuli(
        self,
        thread_id: str,
        *,
        reason: str = "cleared",
    ) -> int:
        tid = str(thread_id or "").strip()
        rows = self.connection.execute(
            """
            SELECT * FROM stimuli
            WHERE thread_id = ? AND status = 'ready'
            """,
            (tid,),
        ).fetchall()
        for row in rows:
            data = _stimulus_payload(self._stimulus_from_row(row))
            data["disposition"] = "aborted"
            data["disposition_stage"] = "control"
            data["disposition_reason"] = reason
            data["finalized_at"] = _now_iso()
            self._write_stimulus_row(data, insert=False)
        return len(rows)

    def _claim_row(
        self,
        row: sqlite3.Row,
        *,
        consumer_id: str,
        consumer_epoch: int,
        claim_epoch: int,
    ) -> StimulusEnvelope:
        stimulus = self._stimulus_from_row(row)
        data = _stimulus_payload(stimulus)
        data["disposition"] = "claimed"
        data["claimed_by"] = consumer_id
        data["consumer_epoch"] = int(consumer_epoch)
        data["claim_epoch"] = int(claim_epoch)
        data["claimed_at"] = _now_iso()
        data["finalized_at"] = None
        self._write_stimulus_row(data, insert=False)
        stored = self.load_stimulus(stimulus.stimulus_id)
        assert stored is not None
        return stored

    def _next_claimable_row(
        self,
        *,
        conversation_id: str,
        consumer_epoch: int,
    ) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT * FROM stimuli
            WHERE conversation_id = ?
              AND (
                status = 'ready'
                OR (
                    status = 'claimed'
                    AND COALESCE(consumer_epoch, 0) < ?
                )
              )
            ORDER BY effective_priority, accepted_seq, stimulus_id
            LIMIT 1
            """,
            (str(conversation_id or "").strip(), int(consumer_epoch)),
        ).fetchone()

    def pop_next_stimulus(
        self,
        thread_id: str,
    ) -> Optional[StimulusEnvelope]:
        tid = str(thread_id or "").strip()
        row = self.connection.execute(
            """
            SELECT * FROM stimuli
            WHERE thread_id = ? AND status = 'ready'
            ORDER BY effective_priority, accepted_seq, stimulus_id
            LIMIT 1
            """,
            (tid,),
        ).fetchone()
        if row is None:
            return None
        cid = str(row["conversation_id"])
        partition = self.ensure_stimulus_partition(
            cid,
            thread_id=tid,
        )
        owner = str(
            partition["consumer_owner_id"] or f"legacy-pop:{tid}"
        )
        consumer_epoch = max(1, int(partition["consumer_epoch"] or 0))
        claim_epoch = int(partition["claim_epoch"] or 0) + 1
        self.connection.execute(
            """
            UPDATE stimulus_partitions
            SET consumer_owner_id = ?, consumer_epoch = ?,
                claim_epoch = ?, updated_at = ?
            WHERE conversation_id = ?
            """,
            (owner, consumer_epoch, claim_epoch, _now_iso(), cid),
        )
        return self._claim_row(
            row,
            consumer_id=owner,
            consumer_epoch=consumer_epoch,
            claim_epoch=claim_epoch,
        )

    def acquire_consumer(
        self,
        *,
        conversation_id: str,
        consumer_id: str,
        lease_seconds: float,
    ) -> Dict[str, Any]:
        cid = str(conversation_id or "").strip()
        consumer = str(consumer_id or "").strip()
        if not consumer:
            raise StoreInvariantError("consumer_id is required")
        partition = self.ensure_stimulus_partition(cid)
        current_owner = _optional_text(partition["consumer_owner_id"])
        current_epoch = int(partition["consumer_epoch"] or 0)
        new_epoch = current_epoch
        if current_owner != consumer:
            new_epoch = current_epoch + 1
        if new_epoch <= 0:
            new_epoch = 1
        lease_until = (
            datetime.now(timezone.utc)
            + timedelta(seconds=max(0.0, float(lease_seconds)))
        ).isoformat().replace("+00:00", "Z")
        self.connection.execute(
            """
            UPDATE stimulus_partitions
            SET consumer_owner_id = ?, consumer_epoch = ?,
                consumer_lease_until = ?, updated_at = ?
            WHERE conversation_id = ?
            """,
            (consumer, new_epoch, lease_until, _now_iso(), cid),
        )
        return self.load_partition_state(cid)

    def takeover_consumer(
        self,
        *,
        conversation_id: str,
        consumer_id: str,
    ) -> Dict[str, Any]:
        cid = str(conversation_id or "").strip()
        consumer = str(consumer_id or "").strip()
        if not consumer:
            raise StoreInvariantError("consumer_id is required")
        partition = self.ensure_stimulus_partition(cid)
        new_epoch = int(partition["consumer_epoch"] or 0) + 1
        self.connection.execute(
            """
            UPDATE stimulus_partitions
            SET consumer_owner_id = ?, consumer_epoch = ?,
                consumer_lease_until = NULL, updated_at = ?
            WHERE conversation_id = ?
            """,
            (consumer, new_epoch, _now_iso(), cid),
        )
        return self.load_partition_state(cid)

    def claim_next_stimulus(
        self,
        *,
        conversation_id: str,
        consumer_id: str,
    ) -> Optional[StimulusEnvelope]:
        cid = str(conversation_id or "").strip()
        consumer = str(consumer_id or "").strip()
        if not consumer:
            raise StoreInvariantError("consumer_id is required")
        partition = self.ensure_stimulus_partition(cid)
        owner = _optional_text(partition["consumer_owner_id"])
        consumer_epoch = int(partition["consumer_epoch"] or 0)
        if owner is None:
            partition = self.acquire_consumer(
                conversation_id=cid,
                consumer_id=consumer,
                lease_seconds=0,
            )
            consumer_epoch = int(partition["consumer_epoch"] or 0)
        elif owner != consumer:
            raise StoreInvariantError(
                f"conversation is owned by {owner}"
            )
        row = self._next_claimable_row(
            conversation_id=cid,
            consumer_epoch=consumer_epoch,
        )
        if row is None:
            return None
        claim_epoch = int(partition["claim_epoch"] or 0) + 1
        self.connection.execute(
            """
            UPDATE stimulus_partitions
            SET claim_epoch = ?, updated_at = ?
            WHERE conversation_id = ?
            """,
            (claim_epoch, _now_iso(), cid),
        )
        return self._claim_row(
            row,
            consumer_id=consumer,
            consumer_epoch=consumer_epoch,
            claim_epoch=claim_epoch,
        )

    def set_stimulus_disposition(
        self,
        stimulus_id: str,
        *,
        disposition: str,
        stage: str,
        reason: str = "",
    ) -> Optional[StimulusEnvelope]:
        stimulus = self.load_stimulus(stimulus_id)
        if stimulus is None:
            return None
        requested = str(disposition or "").strip()
        terminal_dispositions = {
            "aborted",
            "consumed",
            "expected_discard",
            "failed",
        }
        if (
            stimulus.disposition in terminal_dispositions
            and requested != stimulus.disposition
        ):
            return stimulus
        data = _stimulus_payload(stimulus)
        data["disposition"] = requested
        data["disposition_stage"] = str(stage or "").strip() or None
        data["disposition_reason"] = (
            str(reason or "").strip() or None
        )
        data["finalized_at"] = _now_iso()
        self._write_stimulus_row(data, insert=False)
        return self.load_stimulus(stimulus_id)

    def set_worker_latch(
        self,
        *,
        conversation_id: str,
        phase: str,
    ) -> Dict[str, Any]:
        cid = str(conversation_id or "").strip()
        value = str(phase or "").strip() or None
        self.ensure_stimulus_partition(cid)
        self.connection.execute(
            """
            UPDATE stimulus_partitions
            SET worker_latch = ?, updated_at = ?
            WHERE conversation_id = ?
            """,
            (value, _now_iso(), cid),
        )
        return self.load_partition_state(cid)

    def finalize_claimed_stimulus(
        self,
        *,
        stimulus_id: str,
        claim_token: Mapping[str, Any],
        disposition: str,
        transition_id: str,
    ) -> Dict[str, Any]:
        sid = str(stimulus_id or "").strip()
        stimulus = self.load_stimulus(sid)
        if stimulus is None:
            raise RecordNotFoundError(sid)
        expected = {
            "claimed_by": str(
                claim_token.get("claimed_by", "") or ""
            ).strip(),
            "consumer_epoch": int(
                claim_token.get("consumer_epoch", 0) or 0
            ),
            "claim_epoch": int(
                claim_token.get("claim_epoch", 0) or 0
            ),
        }
        actual = {
            "claimed_by": str(stimulus.claimed_by or ""),
            "consumer_epoch": int(stimulus.consumer_epoch or 0),
            "claim_epoch": int(stimulus.claim_epoch or 0),
            "status": stimulus.disposition,
        }
        partition = self.ensure_stimulus_partition(
            stimulus.conversation_id,
            thread_id=stimulus.thread_id,
        )
        current_owner = str(partition["consumer_owner_id"] or "")
        current_epoch = int(partition["consumer_epoch"] or 0)
        actual["current_owner"] = current_owner
        actual["current_consumer_epoch"] = current_epoch
        if (
            stimulus.disposition != "claimed"
            or expected["claimed_by"] != actual["claimed_by"]
            or expected["consumer_epoch"] != actual["consumer_epoch"]
            or expected["claim_epoch"] != actual["claim_epoch"]
            or expected["claimed_by"] != current_owner
            or expected["consumer_epoch"] != current_epoch
        ):
            raise StaleClaimError(sid, expected=expected, actual=actual)
        stored = self.set_stimulus_disposition(
            sid,
            disposition=str(disposition or "").strip() or "consumed",
            stage="final",
            reason="",
        )
        assert stored is not None
        return {
            "stimulus_id": sid,
            "conversation_id": stored.conversation_id,
            "status": stored.disposition,
            "disposition": stored.disposition,
            "disposition_stage": stored.disposition_stage,
            "transition_id": str(transition_id or "").strip(),
            "claim_token": dict(expected),
            "finalized_at": stored.finalized_at,
        }

    def ensure_conversation(
        self,
        conversation_id: str,
        *,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        cid = str(conversation_id or "").strip()
        if not cid:
            raise StoreInvariantError("conversation_id is required")
        tid = (
            str(thread_id or "").strip()
            or cid.rsplit("::", 1)[0]
        )
        now = _now_iso()
        self.connection.execute(
            """
            INSERT OR IGNORE INTO conversation_state(
                conversation_id, thread_id, scene_seq,
                flush_watermark, revision, updated_at
            ) VALUES (?, ?, 0, 0, 0, ?)
            """,
            (cid, tid, now),
        )
        row = self.connection.execute(
            """
            SELECT * FROM conversation_state
            WHERE conversation_id = ?
            """,
            (cid,),
        ).fetchone()
        assert row is not None
        return dict(row)

    def append_scene_entry(
        self,
        conversation_id: str,
        entry: SceneEntry,
        *,
        append_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> SceneEntry:
        cid = str(conversation_id or "").strip()
        stable_id = (
            str(append_id or entry.append_id or "").strip()
            or f"append_{uuid.uuid4().hex}"
        )
        existing = self.connection.execute(
            """
            SELECT payload_json FROM scene_entries
            WHERE append_id = ?
            """,
            (stable_id,),
        ).fetchone()
        if existing is not None:
            return SceneEntry.from_dict(
                json.loads(existing["payload_json"])
            )
        state = self.ensure_conversation(
            cid,
            thread_id=thread_id,
        )
        seq = int(state["scene_seq"]) + 1
        stored = SceneEntry(
            seq=seq,
            occurred_at=entry.occurred_at,
            entry_type=entry.entry_type,
            actor=entry.actor,
            text=entry.text,
            append_id=stable_id,
            transaction_id=entry.transaction_id,
            delegate_id=entry.delegate_id,
            tool_name=entry.tool_name,
            payload_ref=entry.payload_ref,
        )
        self.connection.execute(
            """
            INSERT INTO scene_entries(
                append_id, conversation_id, seq, payload_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                stable_id,
                cid,
                seq,
                _json_dumps(stored.to_dict()),
            ),
        )
        self.connection.execute(
            """
            UPDATE conversation_state
            SET scene_seq = ?, revision = revision + 1,
                updated_at = ?
            WHERE conversation_id = ?
            """,
            (seq, _now_iso(), cid),
        )
        return SceneEntry.from_dict(stored.to_dict())

    def read_scene(
        self,
        conversation_id: str,
        *,
        after_seq: int = 0,
        before_seq: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[SceneEntry]:
        clauses = ["conversation_id = ?", "seq > ?"]
        values: List[Any] = [
            str(conversation_id or "").strip(),
            max(0, int(after_seq)),
        ]
        if before_seq is not None:
            clauses.append("seq < ?")
            values.append(int(before_seq))
        sql = (
            "SELECT payload_json FROM scene_entries WHERE "
            + " AND ".join(clauses)
            + " ORDER BY seq"
        )
        if limit is not None:
            sql += " LIMIT ?"
            values.append(max(1, int(limit)))
        rows = self.connection.execute(sql, tuple(values)).fetchall()
        return [
            SceneEntry.from_dict(json.loads(row["payload_json"]))
            for row in rows
        ]

    def load_conversation_state(
        self,
        conversation_id: str,
    ) -> Dict[str, Any]:
        return self.ensure_conversation(conversation_id)

    def mark_scene_flushed(
        self,
        conversation_id: str,
        *,
        through_seq: int,
    ) -> int:
        state = self.ensure_conversation(conversation_id)
        bounded = min(
            max(0, int(through_seq)),
            int(state["scene_seq"]),
        )
        watermark = max(
            int(state["flush_watermark"]),
            bounded,
        )
        self.connection.execute(
            """
            UPDATE conversation_state
            SET flush_watermark = ?, revision = revision + 1,
                updated_at = ?
            WHERE conversation_id = ?
            """,
            (
                watermark,
                _now_iso(),
                str(conversation_id or "").strip(),
            ),
        )
        return watermark


class SQLiteRuntimeStore:
    """One SQLite database is authoritative for all P2 reference state."""

    _STATUS_AUTHORITY_MIGRATION = "transaction-authority-v3"
    _CONTRACT_V4_MIGRATION = "transaction-contract-v4"

    def __init__(
        self,
        path: Optional[Path | str] = None,
    ) -> None:
        self.path = Path(path).resolve() if path is not None else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            str(self.path) if self.path is not None else ":memory:",
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        if self.path is not None:
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.executescript(_SCHEMA)
        flush_columns = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(flush_operations)"
            ).fetchall()
        }
        if "request_digest" not in flush_columns:
            self._connection.execute(
                "ALTER TABLE flush_operations "
                "ADD COLUMN request_digest TEXT"
            )
        self._closed = False
        self.legacy_status_migration_report = (
            self._migrate_legacy_status_authority()
        )
        self.transaction_contract_migration_report = (
            self._migrate_transaction_contract_v4()
        )

    def _migrate_legacy_status_authority(self) -> Dict[str, Any]:
        """Upgrade v2 status-only facts before any runtime reader starts.

        Legacy FAILED/CANCELLED/COMPLETED writers could leave a live
        ``continue`` transaction and active child records behind.  The v3
        reader never consults that projection, so startup repairs those rows
        once and records a durable migration result.  Ambiguous or internally
        inconsistent work is paused with ``runtime_error`` instead of being
        made runnable.
        """

        migration_id = self._STATUS_AUTHORITY_MIGRATION
        with self.unit_of_work() as uow:
            rows = uow.connection.execute(
                "SELECT payload_json FROM transactions "
                "ORDER BY transaction_id"
            ).fetchall()
            has_legacy_rows = any(
                int(
                    json.loads(row["payload_json"]).get(
                        "schema_version", 2
                    )
                    or 2
                )
                < 3
                for row in rows
            )
            replay = uow.connection.execute(
                "SELECT result_json FROM runtime_migrations "
                "WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            if replay is not None and not has_legacy_rows:
                result = json.loads(replay["result_json"])
                result["replayed"] = True
                return result
            if replay is not None:
                uow.connection.execute(
                    "DELETE FROM runtime_migrations WHERE migration_id = ?",
                    (migration_id,),
                )
            report: Dict[str, Any] = {
                "migration_id": migration_id,
                "scanned": 0,
                "upgraded": 0,
                "repaired": 0,
                "quarantined": 0,
                "actions": {},
                "replayed": replay is not None,
            }
            now = _now_iso()

            for row in rows:
                payload = json.loads(row["payload_json"])
                if int(payload.get("schema_version", 2) or 2) >= 3:
                    continue
                report["scanned"] += 1
                raw_status = str(payload.get("status", "") or "").strip()
                record = TransactionRecord.from_dict(payload)
                activations = uow.list_activations(record.transaction_id)
                delegates = uow.list_delegates(
                    transaction_id=record.transaction_id
                )
                activation_statuses = {
                    item.activation_id: item.status for item in activations
                }
                delegate_statuses = {
                    item.delegate_id: item.status for item in delegates
                }
                facts_changed = False
                quarantined = False
                action = "normalize_projection"

                def close_live_work(
                    reason: str,
                    *,
                    complete_current: bool = False,
                ) -> None:
                    nonlocal facts_changed
                    pending_before = any(
                        item.status == DelegateStatus.PENDING
                        for item in delegates
                    )
                    for delegate in delegates:
                        if delegate.status != DelegateStatus.PENDING:
                            continue
                        delegate.status = DelegateStatus.INVALIDATED
                        delegate.invalidated_at = now
                        delegate.invalidated_reason = reason
                        facts_changed = True
                    for activation in activations:
                        if activation.status != ActivationStatus.ACTIVE:
                            continue
                        is_current = (
                            activation.activation_id
                            == record.current_activation_id
                        )
                        if complete_current and is_current and not pending_before:
                            activation.status = ActivationStatus.COMPLETED
                            activation.invalidated_reason = None
                        else:
                            activation.status = ActivationStatus.INVALIDATED
                            activation.invalidated_reason = reason
                        activation.ended_at = now
                        facts_changed = True
                    if (
                        record.current_activation_id is not None
                        or record.active_delegate_id is not None
                    ):
                        facts_changed = True
                    record.current_activation_id = None
                    record.active_delegate_id = None
                    record.reply_finalized_in_activation = False

                if record.lifecycle_status == TransactionLifecycle.DELETED:
                    action = "repair_deleted_children"
                    close_live_work("legacy_deleted")
                elif record.state == TransactionState.PAUSE:
                    if record.pause_reason is None:
                        record.pause_reason = (
                            PauseReason.RUNTIME_ERROR
                            if raw_status == "failed"
                            else PauseReason.MANUAL_HOLD
                        )
                        facts_changed = True
                    action = "repair_pause_children"
                    close_live_work("legacy_pause")
                elif record.state in {
                    TransactionState.COMPLETE,
                    TransactionState.ARCHIVE,
                }:
                    action = "repair_closed_children"
                    close_live_work(
                        "legacy_closed",
                        complete_current=(
                            record.state == TransactionState.COMPLETE
                        ),
                    )
                    if record.terminal_at is None:
                        record.terminal_at = now
                        facts_changed = True
                elif raw_status == "failed":
                    action = "failed_to_pause"
                    record.state = TransactionState.PAUSE
                    record.pause_reason = PauseReason.RUNTIME_ERROR
                    record.last_error = (
                        str(record.last_error or "").strip()
                        or "legacy status-only failure"
                    )
                    record.terminal_at = None
                    facts_changed = True
                    close_live_work("legacy_failed")
                elif raw_status == "cancelled":
                    action = "cancelled_to_manual_pause"
                    record.state = TransactionState.PAUSE
                    record.pause_reason = PauseReason.MANUAL_HOLD
                    record.terminal_at = None
                    facts_changed = True
                    close_live_work("legacy_cancelled")
                elif raw_status == "completed":
                    has_pending = any(
                        item.status == DelegateStatus.PENDING
                        for item in delegates
                    )
                    if has_pending:
                        action = "completed_conflict_to_error_pause"
                        quarantined = True
                        record.state = TransactionState.PAUSE
                        record.pause_reason = PauseReason.RUNTIME_ERROR
                        record.last_error = (
                            str(record.last_error or "").strip()
                            or "legacy completed transaction had pending work"
                        )
                        record.terminal_at = None
                        close_live_work("legacy_completed_conflict")
                    else:
                        action = "completed_to_complete"
                        record.state = TransactionState.COMPLETE
                        record.pause_reason = None
                        record.terminal_at = record.terminal_at or now
                        close_live_work(
                            "legacy_completed",
                            complete_current=True,
                        )
                    facts_changed = True
                else:
                    active_activations = [
                        item
                        for item in activations
                        if item.status == ActivationStatus.ACTIVE
                    ]
                    pending_delegates = [
                        item
                        for item in delegates
                        if item.status == DelegateStatus.PENDING
                    ]
                    current_ok = (
                        len(active_activations) == 1
                        and active_activations[0].activation_id
                        == record.current_activation_id
                    )
                    pending_ok = (
                        not pending_delegates
                        and record.active_delegate_id is None
                    ) or (
                        len(pending_delegates) == 1
                        and pending_delegates[0].delegate_id
                        == record.active_delegate_id
                        and pending_delegates[0].activation_id
                        == record.current_activation_id
                    )
                    if not current_ok or not pending_ok:
                        action = "open_conflict_to_error_pause"
                        quarantined = True
                        record.state = TransactionState.PAUSE
                        record.pause_reason = PauseReason.RUNTIME_ERROR
                        record.last_error = (
                            str(record.last_error or "").strip()
                            or "legacy transaction child records were inconsistent"
                        )
                        record.terminal_at = None
                        facts_changed = True
                        close_live_work("legacy_open_conflict")

                if record.state == TransactionState.CONTINUE:
                    record.pause_reason = None
                    record.terminal_at = None
                record.schema_version = 3
                if any(
                    activation.status != ActivationStatus.ACTIVE
                    and activation.to_revision is None
                    for activation in activations
                ):
                    facts_changed = True
                previous_revision = int(record.revision)
                if facts_changed:
                    record.revision = previous_revision + 1
                    record.updated_at = now
                    report["repaired"] += 1
                for activation in activations:
                    if (
                        activation.status != ActivationStatus.ACTIVE
                        and activation.to_revision is None
                    ):
                        activation.to_revision = int(record.revision)
                    uow.save_activation(
                        activation,
                        expected_status=activation_statuses[
                            activation.activation_id
                        ],
                    )
                for delegate in delegates:
                    uow.save_delegate(
                        delegate,
                        expected_status=delegate_statuses[
                            delegate.delegate_id
                        ],
                    )
                uow.connection.execute(
                    """
                    UPDATE transactions
                    SET state = ?, lifecycle_status = ?, revision = ?,
                        current_activation_id = ?, payload_json = ?,
                        updated_at = ?
                    WHERE transaction_id = ?
                    """,
                    (
                        record.state.value,
                        record.lifecycle_status.value,
                        int(record.revision),
                        record.current_activation_id,
                        _json_dumps(record.to_dict()),
                        record.updated_at or now,
                        record.transaction_id,
                    ),
                )
                report["upgraded"] += 1
                if quarantined:
                    report["quarantined"] += 1
                actions = report["actions"]
                actions[action] = int(actions.get(action, 0)) + 1

            uow.connection.execute(
                """
                INSERT INTO runtime_migrations(
                    migration_id, result_json, applied_at
                ) VALUES (?, ?, ?)
                """,
                (migration_id, _json_dumps(report), now),
            )
            return report

    def _migrate_transaction_contract_v4(self) -> Dict[str, Any]:
        """Remove persisted transaction-status and obsolete correlation keys."""

        migration_id = self._CONTRACT_V4_MIGRATION
        with self.unit_of_work() as uow:
            rows = uow.connection.execute(
                "SELECT transaction_id, payload_json FROM transactions "
                "ORDER BY transaction_id"
            ).fetchall()
            obsolete_correlation_keys = {
                "delegate_id",
                "parent_transaction_id",
                "supersedes",
            }

            def needs_upgrade(row: sqlite3.Row) -> bool:
                payload = json.loads(row["payload_json"])
                correlation = payload.get("correlation")
                correlation_data = (
                    correlation if isinstance(correlation, dict) else {}
                )
                return (
                    int(payload.get("schema_version", 2) or 2) < 4
                    or "status" in payload
                    or any(
                        key in correlation_data
                        for key in obsolete_correlation_keys
                    )
                )

            has_legacy_rows = any(needs_upgrade(row) for row in rows)
            replay = uow.connection.execute(
                "SELECT result_json FROM runtime_migrations "
                "WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            if replay is not None and not has_legacy_rows:
                result = json.loads(replay["result_json"])
                result["replayed"] = True
                return result
            if replay is not None:
                uow.connection.execute(
                    "DELETE FROM runtime_migrations WHERE migration_id = ?",
                    (migration_id,),
                )
            report: Dict[str, Any] = {
                "migration_id": migration_id,
                "scanned": len(rows),
                "upgraded": 0,
                "removed_status": 0,
                "removed_correlation_keys": 0,
                "replayed": replay is not None,
            }
            for row in rows:
                payload = json.loads(row["payload_json"])
                correlation = payload.get("correlation")
                correlation_data = (
                    correlation if isinstance(correlation, dict) else {}
                )
                removed_correlation = sum(
                    key in correlation_data
                    for key in obsolete_correlation_keys
                )
                had_status = "status" in payload
                schema_version = int(
                    payload.get("schema_version", 2) or 2
                )
                if (
                    schema_version >= 4
                    and not had_status
                    and not removed_correlation
                ):
                    continue

                record = TransactionRecord.from_dict(payload)
                record.schema_version = 4
                uow.connection.execute(
                    "UPDATE transactions SET payload_json = ? "
                    "WHERE transaction_id = ?",
                    (
                        _json_dumps(record.to_dict()),
                        str(row["transaction_id"]),
                    ),
                )
                report["upgraded"] += 1
                report["removed_status"] += int(had_status)
                report["removed_correlation_keys"] += int(
                    removed_correlation
                )

            uow.connection.execute(
                """
                INSERT INTO runtime_migrations(
                    migration_id, result_json, applied_at
                ) VALUES (?, ?, ?)
                """,
                (migration_id, _json_dumps(report), _now_iso()),
            )
            return report

    @contextmanager
    def unit_of_work(self) -> Iterator[RuntimeStoreUnitOfWork]:
        with self._lock:
            if self._closed:
                raise RuntimeStoreError("store is closed")
            self._connection.execute("BEGIN IMMEDIATE")
            uow = RuntimeStoreUnitOfWork(self, self._connection)
            try:
                yield uow
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    atomic = unit_of_work

    def create_transaction(
        self,
        record: TransactionRecord,
    ) -> TransactionRecord:
        with self.unit_of_work() as uow:
            return uow.create_transaction(record)

    def load_transaction(
        self,
        transaction_id: str,
    ) -> Optional[TransactionRecord]:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).load_transaction(transaction_id)

    get_transaction = load_transaction

    def list_transactions(
        self,
        *,
        thread_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> List[TransactionRecord]:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).list_transactions(
                thread_id=thread_id,
                conversation_id=conversation_id,
            )

    load_transactions = list_transactions

    def cas_transaction(
        self,
        record: TransactionRecord,
        *,
        expected_revision: int,
    ) -> TransactionRecord:
        with self.unit_of_work() as uow:
            return uow.cas_transaction(
                record,
                expected_revision=expected_revision,
            )

    def create_activation(
        self,
        activation: ActivationRecord,
    ) -> ActivationRecord:
        with self.unit_of_work() as uow:
            return uow.create_activation(activation)

    def load_activation(
        self,
        activation_id: str,
    ) -> Optional[ActivationRecord]:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).load_activation(activation_id)

    get_activation = load_activation

    def list_activations(
        self,
        transaction_id: str,
    ) -> List[ActivationRecord]:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).list_activations(transaction_id)

    def create_delegate(
        self,
        delegate: DelegateRecord,
    ) -> DelegateRecord:
        with self.unit_of_work() as uow:
            return uow.create_delegate(delegate)

    def load_delegate(
        self,
        delegate_id: str,
    ) -> Optional[DelegateRecord]:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).load_delegate(delegate_id)

    get_delegate = load_delegate

    def list_delegates(
        self,
        *,
        transaction_id: Optional[str] = None,
        activation_id: Optional[str] = None,
    ) -> List[DelegateRecord]:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).list_delegates(
                transaction_id=transaction_id,
                activation_id=activation_id,
            )

    def admit_stimulus(
        self,
        stimulus: StimulusEnvelope,
        *,
        effective_priority: int,
        disposition: str = "ready",
        disposition_stage: Optional[str] = None,
        disposition_reason: Optional[str] = None,
    ) -> StimulusEnvelope:
        with self.unit_of_work() as uow:
            return uow.admit_stimulus(
                stimulus,
                effective_priority=effective_priority,
                disposition=disposition,
                disposition_stage=disposition_stage,
                disposition_reason=disposition_reason,
            )

    def load_stimulus(
        self,
        stimulus_id: str,
    ) -> Optional[StimulusEnvelope]:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).load_stimulus(stimulus_id)

    def bind_stimulus_target(
        self,
        stimulus_id: str,
        *,
        transaction_id: str,
    ) -> StimulusEnvelope:
        with self.unit_of_work() as uow:
            return uow.bind_stimulus_target(
                stimulus_id,
                transaction_id=transaction_id,
            )

    def requeue_claimed_stimulus(
        self,
        stimulus: StimulusEnvelope,
        *,
        effective_priority: int,
    ) -> StimulusEnvelope:
        with self.unit_of_work() as uow:
            return uow.requeue_claimed_stimulus(
                stimulus,
                effective_priority=effective_priority,
            )

    def list_ready_stimuli(
        self,
        *,
        conversation_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> List[StimulusEnvelope]:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).list_ready_stimuli(
                conversation_id=conversation_id,
                thread_id=thread_id,
            )

    def list_stimuli(
        self,
        *,
        conversation_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> List[StimulusEnvelope]:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).list_stimuli(
                conversation_id=conversation_id,
                thread_id=thread_id,
            )

    def count_ready_stimuli(
        self,
        *,
        conversation_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> int:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).count_ready_stimuli(
                conversation_id=conversation_id,
                thread_id=thread_id,
            )

    def peek_next_stimulus_priority(
        self,
        thread_id: str,
    ) -> Optional[int]:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).peek_next_stimulus_priority(thread_id)

    def pop_next_stimulus(
        self,
        thread_id: str,
    ) -> Optional[StimulusEnvelope]:
        with self.unit_of_work() as uow:
            return uow.pop_next_stimulus(thread_id)

    def clear_thread_stimuli(
        self,
        thread_id: str,
        *,
        reason: str = "cleared",
    ) -> int:
        with self.unit_of_work() as uow:
            return uow.clear_thread_stimuli(thread_id, reason=reason)

    def load_partition_state(
        self,
        conversation_id: str,
    ) -> Dict[str, Any]:
        with self.unit_of_work() as uow:
            return uow.load_partition_state(conversation_id)

    def acquire_consumer(
        self,
        *,
        conversation_id: str,
        consumer_id: str,
        lease_seconds: float,
    ) -> Dict[str, Any]:
        with self.unit_of_work() as uow:
            return uow.acquire_consumer(
                conversation_id=conversation_id,
                consumer_id=consumer_id,
                lease_seconds=lease_seconds,
            )

    def takeover_consumer(
        self,
        *,
        conversation_id: str,
        consumer_id: str,
    ) -> Dict[str, Any]:
        with self.unit_of_work() as uow:
            return uow.takeover_consumer(
                conversation_id=conversation_id,
                consumer_id=consumer_id,
            )

    def claim_next_stimulus(
        self,
        *,
        conversation_id: str,
        consumer_id: str,
    ) -> Optional[StimulusEnvelope]:
        with self.unit_of_work() as uow:
            return uow.claim_next_stimulus(
                conversation_id=conversation_id,
                consumer_id=consumer_id,
            )

    def set_stimulus_disposition(
        self,
        stimulus_id: str,
        *,
        disposition: str,
        stage: str,
        reason: str = "",
    ) -> Optional[StimulusEnvelope]:
        with self.unit_of_work() as uow:
            return uow.set_stimulus_disposition(
                stimulus_id,
                disposition=disposition,
                stage=stage,
                reason=reason,
            )

    def set_worker_latch(
        self,
        *,
        conversation_id: str,
        phase: str,
    ) -> Dict[str, Any]:
        with self.unit_of_work() as uow:
            return uow.set_worker_latch(
                conversation_id=conversation_id,
                phase=phase,
            )

    def finalize_stimulus(
        self,
        *,
        stimulus_id: str,
        claim_token: Mapping[str, Any],
        disposition: str,
        transition_id: str,
        command_digest: str,
    ) -> Dict[str, Any]:
        command = {
            "operation": "finalize_stimulus",
            "stimulus_id": str(stimulus_id or "").strip(),
            "claim_token": dict(claim_token or {}),
            "disposition": str(disposition or "").strip(),
            "command_digest": str(command_digest or "").strip(),
        }

        def operation(
            uow: RuntimeStoreUnitOfWork,
        ) -> Mapping[str, Any]:
            return uow.finalize_claimed_stimulus(
                stimulus_id=command["stimulus_id"],
                claim_token=command["claim_token"],
                disposition=command["disposition"],
                transition_id=str(transition_id or "").strip(),
            )

        execution = self.execute_transition(
            transition_id=str(transition_id or "").strip(),
            command=command,
            operation=operation,
        )
        result = dict(execution.result)
        result["replayed"] = bool(execution.replayed)
        return result

    def execute_transition(
        self,
        *,
        transition_id: str,
        command: Mapping[str, Any],
        operation: Callable[
            [RuntimeStoreUnitOfWork],
            Mapping[str, Any],
        ],
        transaction_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        command_digest: Optional[str] = None,
    ) -> TransitionExecution:
        transition = str(transition_id or "").strip()
        if not transition:
            raise StoreInvariantError("transition_id is required")
        canonical_digest = canonical_command_digest(command)
        supplied_digest = str(command_digest or "").strip()
        if supplied_digest and supplied_digest != canonical_digest:
            raise StoreInvariantError(
                "command_digest does not match the canonical command"
            )
        digest = canonical_digest
        with self.unit_of_work() as uow:
            existing = uow.load_transition(transition)
            if existing is not None:
                if existing["command_digest"] != digest:
                    raise IdempotencyConflictError(
                        transition,
                        stored_digest=existing["command_digest"],
                        requested_digest=digest,
                    )
                return TransitionExecution(
                    result=deepcopy(existing["result"]),
                    replayed=True,
                    from_revision=existing["from_revision"],
                    to_revision=existing["to_revision"],
                )

            before: Optional[TransactionRecord] = None
            if transaction_id:
                if expected_revision is None:
                    raise StoreInvariantError(
                        "expected_revision is required for a "
                        "transaction transition"
                    )
                before = uow.load_transaction(transaction_id)
                if before is None:
                    raise RecordNotFoundError(transaction_id)
                if (
                    expected_revision is not None
                    and before.revision != int(expected_revision)
                ):
                    raise RevisionConflictError(
                        transaction_id,
                        expected_revision=int(expected_revision),
                        actual_revision=before.revision,
                    )
            result = json.loads(_json_dumps(dict(operation(uow))))
            after = (
                uow.load_transaction(transaction_id)
                if transaction_id
                else None
            )
            from_revision = before.revision if before else None
            to_revision = after.revision if after else None
            if (
                before is not None
                and (
                    after is None
                    or to_revision != before.revision + 1
                )
            ):
                raise StoreInvariantError(
                    "transaction transition must advance revision "
                    "exactly once"
                )
            uow.save_transition(
                transition_id=transition,
                command_digest=digest,
                command=command,
                transaction_id=transaction_id,
                from_revision=from_revision,
                to_revision=to_revision,
                result=result,
            )
            return TransitionExecution(
                result=deepcopy(result),
                replayed=False,
                from_revision=from_revision,
                to_revision=to_revision,
            )

    def get_transition(
        self,
        transition_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).load_transition(transition_id)

    def append_scene_entry(
        self,
        conversation_id: str,
        entry: SceneEntry,
        *,
        append_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> SceneEntry:
        with self.unit_of_work() as uow:
            return uow.append_scene_entry(
                conversation_id,
                entry,
                append_id=append_id,
                thread_id=thread_id,
            )

    def read_scene(
        self,
        conversation_id: str,
        *,
        after_seq: int = 0,
        before_seq: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[SceneEntry]:
        with self._lock:
            return RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).read_scene(
                conversation_id,
                after_seq=after_seq,
                before_seq=before_seq,
                limit=limit,
            )

    def load_conversation_state(
        self,
        conversation_id: str,
    ) -> Dict[str, Any]:
        with self.unit_of_work() as uow:
            return uow.load_conversation_state(conversation_id)

    def mark_scene_flushed(
        self,
        conversation_id: str,
        *,
        through_seq: int,
    ) -> int:
        with self.unit_of_work() as uow:
            return uow.mark_scene_flushed(
                conversation_id,
                through_seq=through_seq,
            )

    def capture_flush_snapshot(
        self,
        conversation_id: str,
    ) -> Dict[str, Any]:
        """Capture one serializable flush boundary in a single transaction."""

        cid = str(conversation_id or "").strip()
        if not cid:
            raise StoreInvariantError("conversation_id is required")
        with self.unit_of_work() as uow:
            state = uow.connection.execute(
                """
                SELECT scene_seq, flush_watermark
                FROM conversation_state
                WHERE conversation_id = ?
                """,
                (cid,),
            ).fetchone()
            through_seq = int(state["scene_seq"]) if state else 0
            flush_watermark = (
                int(state["flush_watermark"]) if state else 0
            )
            scene_entries = uow.read_scene(
                cid,
                after_seq=flush_watermark,
                before_seq=through_seq + 1,
            )
            eligible_revisions = {
                record.transaction_id: int(record.revision)
                for record in uow.list_transactions(
                    conversation_id=cid
                )
                if uow.is_transaction_flush_eligible(record)
            }
            return {
                "conversation_id": cid,
                "flush_watermark": flush_watermark,
                "through_seq": through_seq,
                "scene_entries": [
                    entry.to_dict() for entry in scene_entries
                ],
                "eligible_revisions": eligible_revisions,
            }

    def commit_flush(
        self,
        *,
        flush_id: str,
        conversation_id: str,
        through_seq: Optional[int] = None,
        payload: Optional[Mapping[str, Any]] = None,
        eligible_revisions: Optional[Mapping[str, int]] = None,
        outbox_payloads: Optional[
            Mapping[str, Mapping[str, Any]]
        ] = None,
        result_extra: Optional[Mapping[str, Any]] = None,
    ) -> FlushCommitResult:
        stable_flush_id = str(flush_id or "").strip()
        cid = str(conversation_id or "").strip()
        if not stable_flush_id:
            raise StoreInvariantError("flush_id is required")
        if not cid:
            raise StoreInvariantError("conversation_id is required")
        request_digest = canonical_command_digest(
            {
                "operation": "flush",
                "conversation_id": cid,
                "through_seq": (
                    int(through_seq)
                    if through_seq is not None
                    else None
                ),
                "payload": dict(payload or {}),
                "eligible_revisions": (
                    {
                        str(key): int(value)
                        for key, value in eligible_revisions.items()
                    }
                    if eligible_revisions is not None
                    else None
                ),
            }
        )
        with self.unit_of_work() as uow:
            replay = uow.connection.execute(
                """
                SELECT request_digest, result_json
                FROM flush_operations
                WHERE flush_id = ?
                """,
                (stable_flush_id,),
            ).fetchone()
            if replay is not None:
                stored_digest = str(
                    replay["request_digest"] or ""
                ).strip()
                if stored_digest and stored_digest != request_digest:
                    raise IdempotencyConflictError(
                        stable_flush_id,
                        stored_digest=stored_digest,
                        requested_digest=request_digest,
                    )
                if not stored_digest:
                    legacy_result = json.loads(replay["result_json"])
                    if (
                        legacy_result.get("conversation_id") != cid
                        or legacy_result.get("payload")
                        != dict(payload or {})
                    ):
                        raise IdempotencyConflictError(
                            stable_flush_id,
                            stored_digest="legacy-flush-request",
                            requested_digest=request_digest,
                        )
                    uow.connection.execute(
                        """
                        UPDATE flush_operations
                        SET request_digest = ?
                        WHERE flush_id = ?
                        """,
                        (request_digest, stable_flush_id),
                    )
                return FlushCommitResult(
                    result=json.loads(replay["result_json"]),
                    replayed=True,
                )

            state = uow.ensure_conversation(cid)
            fixed_through = (
                int(state["scene_seq"])
                if through_seq is None
                else min(
                    max(0, int(through_seq)),
                    int(state["scene_seq"]),
                )
            )
            records = uow.list_transactions(
                conversation_id=cid,
            )
            expected = (
                {
                    str(key): int(value)
                    for key, value in eligible_revisions.items()
                }
                if eligible_revisions is not None
                else {
                    item.transaction_id: item.revision
                    for item in records
                    if uow.is_transaction_flush_eligible(item)
                }
            )
            archived: List[str] = []
            now = _now_iso()
            for transaction_id, expected_revision in expected.items():
                record = uow.load_transaction(transaction_id)
                if record is None:
                    raise RecordNotFoundError(transaction_id)
                if record.conversation_id != cid:
                    raise StoreInvariantError(
                        "flush candidate belongs to another conversation"
                    )
                if record.revision != expected_revision:
                    raise RevisionConflictError(
                        transaction_id,
                        expected_revision=expected_revision,
                        actual_revision=record.revision,
                    )
                if not uow.is_transaction_flush_eligible(record):
                    raise StoreInvariantError(
                        f"transaction is no longer flush eligible: "
                        f"{transaction_id}"
                    )
                delegates = uow.list_delegates(
                    transaction_id=record.transaction_id
                )
                archive_transaction(
                    record,
                    delegates,
                    now=now,
                    by_flush=True,
                )
                record.revision += 1
                uow.cas_transaction(
                    record,
                    expected_revision=expected_revision,
                )
                archived.append(transaction_id)

            watermark = uow.mark_scene_flushed(
                cid,
                through_seq=fixed_through,
            )
            all_ids = [item.transaction_id for item in records]
            unchanged = [
                item
                for item in all_ids
                if item not in set(archived)
            ]
            result: Dict[str, Any] = {
                "flush_id": stable_flush_id,
                "conversation_id": cid,
                "through_seq": fixed_through,
                "watermark": watermark,
                "archived_transaction_ids": archived,
                "unchanged_transaction_ids": unchanged,
                "visible_boundary_count": 1,
                "payload": deepcopy(dict(payload or {})),
            }
            result.update(dict(result_extra or {}))
            uow.connection.execute(
                """
                INSERT INTO flush_operations(
                    flush_id, conversation_id, through_seq,
                    request_digest, status, result_json, committed_at
                ) VALUES (?, ?, ?, ?, 'committed', ?, ?)
                """,
                (
                    stable_flush_id,
                    cid,
                    fixed_through,
                    request_digest,
                    _json_dumps(result),
                    now,
                ),
            )
            for destination, materialization in dict(
                outbox_payloads or {}
            ).items():
                uow.connection.execute(
                    """
                    INSERT INTO flush_outbox(
                        flush_id, destination, status,
                        payload_json, attempts
                    ) VALUES (?, ?, 'pending', ?, 0)
                    """,
                    (
                        stable_flush_id,
                        str(destination),
                        _json_dumps(dict(materialization)),
                    ),
                )
            return FlushCommitResult(
                result=deepcopy(result),
                replayed=False,
            )

    def list_pending_flush_outbox(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM flush_outbox
                WHERE status = 'pending'
                ORDER BY flush_id, destination
                """
            ).fetchall()
            return [
                {
                    "flush_id": row["flush_id"],
                    "destination": row["destination"],
                    "status": row["status"],
                    "payload": json.loads(row["payload_json"]),
                    "attempts": int(row["attempts"]),
                }
                for row in rows
            ]

    def mark_flush_materialized(
        self,
        flush_id: str,
        destination: str,
    ) -> None:
        with self.unit_of_work() as uow:
            uow.connection.execute(
                """
                UPDATE flush_outbox
                SET status = 'materialized', attempts = attempts + 1
                WHERE flush_id = ? AND destination = ?
                """,
                (
                    str(flush_id or "").strip(),
                    str(destination or "").strip(),
                ),
            )

    def materialize_flush_outbox(
        self,
        flush_id: str,
    ) -> List[str]:
        """Atomically mark every pending destination for one Flush."""

        stable_flush_id = str(flush_id or "").strip()
        if not stable_flush_id:
            raise StoreInvariantError("flush_id is required")
        with self.unit_of_work() as uow:
            rows = uow.connection.execute(
                """
                SELECT destination FROM flush_outbox
                WHERE flush_id = ? AND status = 'pending'
                ORDER BY destination
                """,
                (stable_flush_id,),
            ).fetchall()
            destinations = [
                str(row["destination"])
                for row in rows
            ]
            if destinations:
                uow.connection.execute(
                    """
                    UPDATE flush_outbox
                    SET status = 'materialized',
                        attempts = attempts + 1
                    WHERE flush_id = ? AND status = 'pending'
                    """,
                    (stable_flush_id,),
                )
            return destinations

    def flush_materialization_status(self, flush_id: str) -> str:
        stable_flush_id = str(flush_id or "").strip()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT status FROM flush_outbox
                WHERE flush_id = ?
                """,
                (stable_flush_id,),
            ).fetchall()
        if not rows:
            return "not_requested"
        if any(str(row["status"]) == "pending" for row in rows):
            return "pending"
        return "materialized"

    def create_schedule_run(
        self,
        schedule_run_id: str,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        with self.unit_of_work() as uow:
            return uow.create_schedule_run(
                schedule_run_id,
                payload,
            )

    def load_schedule_run(
        self,
        schedule_run_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            value = RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).load_schedule_run(schedule_run_id)
            return deepcopy(value)

    def list_schedule_runs(
        self,
        *,
        transaction_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            values = RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).list_schedule_runs(
                transaction_id=transaction_id,
                status=status,
            )
            return deepcopy(values)

    def cas_schedule_run(
        self,
        schedule_run_id: str,
        payload: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> Dict[str, Any]:
        with self.unit_of_work() as uow:
            return uow.cas_schedule_run(
                schedule_run_id,
                payload,
                expected_revision=expected_revision,
            )

    def list_effect_intents(
        self,
        *,
        transaction_id: str,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            values = RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).list_effect_intents(
                transaction_id=transaction_id,
            )
            return deepcopy(values)

    def load_effect_intent(
        self,
        effect_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            value = RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).load_effect_intent(effect_id)
            return deepcopy(value) if value is not None else None

    def list_feedback_outbox(
        self,
        *,
        transaction_id: str,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            values = RuntimeStoreUnitOfWork(
                self,
                self._connection,
            ).list_feedback_outbox(
                transaction_id=transaction_id,
            )
            return deepcopy(values)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True


__all__ = [
    "FlushCommitResult",
    "IdempotencyConflictError",
    "RecordNotFoundError",
    "RevisionConflictError",
    "RuntimeStoreError",
    "RuntimeStoreUnitOfWork",
    "SQLiteRuntimeStore",
    "StaleClaimError",
    "StoreInvariantError",
    "TransitionExecution",
    "canonical_command_digest",
]
