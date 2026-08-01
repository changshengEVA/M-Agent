"""Engine-neutral transaction lifecycle controls used by product runtimes."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS
from m_agent.runtime.host.product_views import (
    list_transactions,
    serialize_transaction,
)
from m_agent.runtime.think_life.scheduler.cpu_state import THREAD_CPU_STATE
from m_agent.runtime.think_life.contracts import SceneEntry
from m_agent.systems.scene.protocols import SceneWriter


class RuntimeTransactionNotFoundError(LookupError):
    """The target is absent or outside the caller-owned runtime scope."""


class TransactionFencedSceneWriter:
    """Atomically reject transaction-owned Scene writes after deletion."""

    def __init__(self, registry: Any, inner: SceneWriter) -> None:
        self._registry = registry
        self._inner = inner

    def append(
        self,
        conversation_id: str,
        entry: SceneEntry,
        *,
        append_id: Optional[str] = None,
    ) -> SceneEntry:
        transaction_id = str(entry.transaction_id or "").strip()
        if not transaction_id:
            return self._append_inner(
                conversation_id,
                entry,
                append_id=append_id,
            )
        # Registry commands use the same registry -> Store lock order.  The
        # durable lifecycle check and Scene append therefore linearize before
        # or after delete; an append can never cross the tombstone boundary.
        with self._registry._lock:
            current = self._registry.store.load_transaction(transaction_id)
            if current is not None and current.deleted:
                return entry
            return self._append_inner(
                conversation_id,
                entry,
                append_id=append_id,
            )

    def _append_inner(
        self,
        conversation_id: str,
        entry: SceneEntry,
        *,
        append_id: Optional[str],
    ) -> SceneEntry:
        if append_id is None:
            return self._inner.append(conversation_id, entry)
        try:
            return self._inner.append(
                conversation_id,
                entry,
                append_id=append_id,
            )
        except TypeError:
            return self._inner.append(conversation_id, entry)


def _delete_transition_id(thread_id: str, idempotency_key: str) -> str:
    material = (
        f"{str(thread_id or '').strip()}\x1f"
        f"{str(idempotency_key or '').strip()}"
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"transaction-delete:{digest}"


def delete_runtime_transaction(
    runtime: Any,
    *,
    thread_id: str,
    conversation_id: str,
    transaction_id: str,
    expected_revision: int,
    idempotency_key: str,
) -> Dict[str, Any]:
    """Delete one transaction without depending on the active graph engine."""

    tid = str(thread_id or "").strip()
    cid = str(conversation_id or "").strip()
    tx_id = str(transaction_id or "").strip()
    key = str(idempotency_key or "").strip()
    if not tid or not cid or not tx_id:
        raise RuntimeTransactionNotFoundError(tx_id or "transaction")
    if not key:
        raise ValueError("idempotency_key is required")

    # Use the durable record for ownership checks.  Returning 404 for both an
    # unknown id and a cross-scope id avoids exposing another thread's audit
    # records through the destructive endpoint.
    record = runtime.registry.store.load_transaction(tx_id)
    if (
        record is None
        or record.thread_id != tid
        or record.conversation_id != cid
    ):
        raise RuntimeTransactionNotFoundError(tx_id)

    durable = runtime.registry.delete_with_cleanup(
        tx_id,
        expected_revision=int(expected_revision),
        transition_id=_delete_transition_id(tid, key),
        idempotency_key=key,
    )
    cancelled_in_flight = THREAD_CPU_STATE.cancel_if_matches(
        tid,
        tx_id,
        reason="transaction_deleted",
    )
    pending = int(runtime.inbox.pending_count(tid))
    THREAD_RUNTIME_STATUS.set_pending_stimuli(tid, pending)

    snapshot = list_transactions(
        runtime,
        tid,
        conversation_id=cid,
    )
    deleted = runtime.registry.store.load_transaction(tx_id)
    if deleted is None:  # Defensive: delete is a tombstone, never a row drop.
        raise RuntimeTransactionNotFoundError(tx_id)
    cleanup = dict(durable.get("cleanup") or {})
    cleanup["cancelled_in_flight"] = bool(cancelled_in_flight)
    cleanup["pending_stimuli"] = pending
    payload: Dict[str, Any] = {
        "success": True,
        "outcome": str(durable.get("outcome", "deleted") or "deleted"),
        "transaction": serialize_transaction(
            deleted,
            active_transaction_id=snapshot.get("active_transaction_id"),
            cpu_transaction_id=snapshot.get("cpu_transaction_id"),
        ),
        "cleanup": cleanup,
        "thread_id": tid,
        "conversation_id": cid,
        "active_transaction_id": snapshot.get("active_transaction_id"),
        "cpu_transaction_id": snapshot.get("cpu_transaction_id"),
        "replayed": bool(durable.get("replayed", False)),
        "already_deleted": bool(
            durable.get("already_deleted", False)
        ),
    }

    # Idempotency covers externally visible notifications too: a replay or a
    # fresh no-op against a tombstone does not emit a second deletion event.
    if not payload["replayed"] and not payload["already_deleted"]:
        emit = getattr(runtime, "_emit_thread_event", None)
        if callable(emit):
            emit(tid, "transaction_deleted", dict(payload))
        emit_runtime = getattr(runtime, "_emit_runtime_updated", None)
        if callable(emit_runtime):
            emit_runtime(tid)
    return payload


__all__ = [
    "RuntimeTransactionNotFoundError",
    "TransactionFencedSceneWriter",
    "delete_runtime_transaction",
]
