"""Pure predicates for the four-state transaction domain.

These helpers are the only supported way for runtime readers to interpret a
``TransactionRecord``.  Scheduler/Inbox/Delegate phases deliberately remain
outside the transaction state machine.
"""

from __future__ import annotations

from typing import Optional

from ..contracts import (
    ActivationRecord,
    ActivationStatus,
    PauseReason,
    TransactionLifecycle,
    TransactionRecord,
    TransactionState,
)


def is_live_record(record: TransactionRecord) -> bool:
    return (
        record.lifecycle_status == TransactionLifecycle.ACTIVE
        and record.deleted_at is None
    )


def is_open_continue(record: TransactionRecord) -> bool:
    return is_live_record(record) and record.state == TransactionState.CONTINUE


def is_waiting_delegate(record: TransactionRecord) -> bool:
    return is_open_continue(record) and bool(record.active_delegate_id)


def is_runnable_record(
    record: TransactionRecord,
    activation: Optional[ActivationRecord] = None,
) -> bool:
    if (
        not is_open_continue(record)
        or bool(record.active_delegate_id)
        or not bool(record.current_activation_id)
    ):
        return False
    if activation is None:
        return True
    return (
        activation.transaction_id == record.transaction_id
        and activation.activation_id == record.current_activation_id
        and activation.status == ActivationStatus.ACTIVE
    )


def is_paused(record: TransactionRecord) -> bool:
    return is_live_record(record) and record.state == TransactionState.PAUSE


def is_execution_closed(record: TransactionRecord) -> bool:
    return (
        not is_live_record(record)
        or record.state in {TransactionState.COMPLETE, TransactionState.ARCHIVE}
    )


def is_permanently_closed(record: TransactionRecord) -> bool:
    return not is_live_record(record)


def is_match_candidate(record: TransactionRecord) -> bool:
    return is_live_record(record) and record.state in {
        TransactionState.PAUSE,
        TransactionState.COMPLETE,
    }


def project_compat_status(record: TransactionRecord) -> str:
    """Project domain facts to the deprecated public API status string."""

    if not is_live_record(record):
        return "cancelled"
    if record.state == TransactionState.PAUSE:
        if record.pause_reason == PauseReason.RUNTIME_ERROR:
            return "failed"
        return "suspended"
    if record.state in {TransactionState.COMPLETE, TransactionState.ARCHIVE}:
        return "completed"
    if record.active_delegate_id:
        return "waiting_execution"
    return "running"


__all__ = [
    "is_execution_closed",
    "is_live_record",
    "is_match_candidate",
    "is_open_continue",
    "is_paused",
    "is_permanently_closed",
    "is_runnable_record",
    "is_waiting_delegate",
    "project_compat_status",
]
