"""Transaction registry and lifecycle transitions."""
from __future__ import annotations

import uuid
from threading import RLock
from typing import Dict, List, Optional

from m_agent.api.chat_api_shared import _now_iso

from .contracts import (
    TransactionCorrelation,
    TransactionKind,
    TransactionRecord,
    TransactionStatus,
)


class TransactionTransitionError(ValueError):
    pass


class TransactionRegistry:
    def __init__(self) -> None:
        self._by_id: Dict[str, TransactionRecord] = {}
        self._active_by_conversation: Dict[str, str] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        thread_id: str,
        kind: TransactionKind,
        conversation_id: Optional[str] = None,
        priority: int = 50,
        correlation: Optional[TransactionCorrelation] = None,
    ) -> TransactionRecord:
        tid = str(thread_id or "").strip()
        cid = str(conversation_id or "").strip() or f"{tid}::0"
        if not tid:
            raise ValueError("thread_id is required")
        now = _now_iso()
        record = TransactionRecord(
            transaction_id=f"txn_{uuid.uuid4().hex}",
            thread_id=tid,
            conversation_id=cid,
            status=TransactionStatus.PENDING,
            priority=int(priority),
            kind=kind,
            correlation=correlation or TransactionCorrelation(),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._by_id[record.transaction_id] = record
        return record

    def get(self, transaction_id: str) -> Optional[TransactionRecord]:
        with self._lock:
            return self._by_id.get(str(transaction_id or "").strip())

    def list_for_thread(self, thread_id: str) -> List[TransactionRecord]:
        tid = str(thread_id or "").strip()
        with self._lock:
            return [t for t in self._by_id.values() if t.thread_id == tid]

    def list_for_conversation(self, conversation_id: str) -> List[TransactionRecord]:
        cid = str(conversation_id or "").strip()
        with self._lock:
            return [t for t in self._by_id.values() if t.conversation_id == cid]

    def get_active_user_transaction(self, conversation_id: str) -> Optional[TransactionRecord]:
        cid = str(conversation_id or "").strip()
        with self._lock:
            active_id = self._active_by_conversation.get(cid)
            if not active_id:
                return None
            rec = self._by_id.get(active_id)
            if rec is None or rec.kind != TransactionKind.USER_TASK:
                return None
            if rec.status.is_terminal() or rec.status == TransactionStatus.SUSPENDED:
                return None
            return rec

    def set_active_user_transaction(self, conversation_id: str, transaction_id: str) -> None:
        with self._lock:
            self._active_by_conversation[str(conversation_id).strip()] = str(transaction_id).strip()

    def clear_active_user_transaction(self, conversation_id: str) -> None:
        with self._lock:
            self._active_by_conversation.pop(str(conversation_id).strip(), None)

    def complete_active_user_transaction(self, conversation_id: str) -> Optional[str]:
        """End the flush-bounded user task segment (clears active pointer)."""
        cid = str(conversation_id or "").strip()
        with self._lock:
            active_id = self._active_by_conversation.get(cid)
            active = self._by_id.get(active_id) if active_id else None
            if active is None or active.kind != TransactionKind.USER_TASK:
                return None
            if active.status.is_terminal() or active.status == TransactionStatus.SUSPENDED:
                return None
            tx_id = active.transaction_id
            if active.status == TransactionStatus.WAITING_EXECUTION:
                active = self.transition(tx_id, TransactionStatus.RUNNING)
            if not active.status.is_terminal():
                self.transition(tx_id, TransactionStatus.COMPLETED)
            return tx_id

    def begin_delegate(self, transaction_id: str, delegate_id: str) -> TransactionRecord:
        """Atomically attach a delegate and enter WAITING_EXECUTION."""
        tx_id = str(transaction_id or "").strip()
        did = str(delegate_id or "").strip()
        if not did:
            raise TransactionTransitionError("delegate_id is required")
        with self._lock:
            record = self._by_id.get(tx_id)
            if record is None:
                raise TransactionTransitionError(f"unknown transaction: {tx_id}")
            if record.status != TransactionStatus.RUNNING:
                raise TransactionTransitionError(
                    f"cannot begin delegate while transaction is {record.status.value}"
                )
            record.status = TransactionStatus.WAITING_EXECUTION
            record.delegate_count += 1
            record.active_delegate_id = did
            record.correlation.delegate_id = did
            record.updated_at = _now_iso()
            return record

    def transition(self, transaction_id: str, new_status: TransactionStatus) -> TransactionRecord:
        tx_id = str(transaction_id or "").strip()
        with self._lock:
            record = self._by_id.get(tx_id)
            if record is None:
                raise TransactionTransitionError(f"unknown transaction: {tx_id}")
            allowed = _ALLOWED_TRANSITIONS.get(record.status, set())
            if new_status not in allowed:
                raise TransactionTransitionError(
                    f"cannot transition {record.status.value} -> {new_status.value}"
                )
            record.status = new_status
            record.updated_at = _now_iso()
            if new_status.is_terminal():
                record.terminal_at = record.updated_at
                if record.kind == TransactionKind.USER_TASK:
                    self._active_by_conversation.pop(record.conversation_id, None)
            return record

    def count_all(self) -> int:
        with self._lock:
            return len(self._by_id)

    def find_by_delegate(self, delegate_id: str) -> Optional[TransactionRecord]:
        did = str(delegate_id or "").strip()
        if not did:
            return None
        with self._lock:
            for record in self._by_id.values():
                if record.active_delegate_id == did:
                    return record
                if record.correlation.delegate_id == did:
                    return record
        return None


_ALLOWED_TRANSITIONS: Dict[TransactionStatus, set[TransactionStatus]] = {
    TransactionStatus.PENDING: {TransactionStatus.RUNNING, TransactionStatus.CANCELLED},
    TransactionStatus.RUNNING: {
        TransactionStatus.WAITING_EXECUTION,
        TransactionStatus.SUSPENDED,
        TransactionStatus.COMPLETED,
        TransactionStatus.FAILED,
        TransactionStatus.CANCELLED,
    },
    TransactionStatus.WAITING_EXECUTION: {
        TransactionStatus.RUNNING,
        TransactionStatus.FAILED,
        TransactionStatus.CANCELLED,
    },
    TransactionStatus.SUSPENDED: {
        TransactionStatus.RUNNING,
        TransactionStatus.CANCELLED,
    },
    TransactionStatus.COMPLETED: set(),
    TransactionStatus.FAILED: set(),
    TransactionStatus.CANCELLED: set(),
}
