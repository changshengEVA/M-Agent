"""Pure transaction-domain mutations for the Runtime P2 foundation.

The functions in this module mutate the supplied domain records in memory.
They deliberately know nothing about repositories, locks, transactions, or
serialization.  A caller must persist all returned mutations atomically and
advance ``TransactionRecord.revision`` exactly once per command.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from ..domain.contracts import (
    ActivationRecord,
    ActivationStatus,
    DelegateRecord,
    DelegateStatus,
    PauseReason,
    TransactionLifecycle,
    TransactionRecord,
    TransactionState,
)


class DomainTransitionError(ValueError):
    """Raised when a command violates transaction-domain invariants."""


@dataclass(frozen=True)
class ActivationInvalidation:
    """Objects affected when one activation is permanently invalidated."""

    activation: ActivationRecord
    invalidated_delegate_ids: Tuple[str, ...] = ()

    @property
    def activation_id(self) -> str:
        return self.activation.activation_id


@dataclass(frozen=True)
class DomainMutation:
    """Structured result for a transaction-level domain mutation."""

    record: TransactionRecord
    activation: Optional[ActivationRecord] = None
    invalidated_activation_ids: Tuple[str, ...] = ()
    invalidated_delegate_ids: Tuple[str, ...] = ()
    completed_activation_ids: Tuple[str, ...] = ()


def _required_text(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise DomainTransitionError(f"{field_name} is required")
    return normalized


def _require_active_record(record: TransactionRecord) -> None:
    if not isinstance(record, TransactionRecord):
        raise DomainTransitionError("record must be a TransactionRecord")
    _required_text(record.transaction_id, "transaction_id")
    if record.lifecycle_status != TransactionLifecycle.ACTIVE:
        raise DomainTransitionError(
            f"transaction {record.transaction_id} is deleted"
        )
    if record.deleted_at is not None:
        raise DomainTransitionError(
            f"transaction {record.transaction_id} has a deletion tombstone"
        )


def _delegate_tuple(
    delegates: Iterable[DelegateRecord],
) -> Tuple[DelegateRecord, ...]:
    values = tuple(delegates or ())
    seen: set[str] = set()
    for delegate in values:
        if not isinstance(delegate, DelegateRecord):
            raise DomainTransitionError(
                "delegates must contain DelegateRecord values"
            )
        delegate_id = _required_text(delegate.delegate_id, "delegate_id")
        if delegate_id in seen:
            raise DomainTransitionError(
                f"duplicate delegate in mutation: {delegate_id}"
            )
        seen.add(delegate_id)
    return values


def _require_activation_owner(
    record: TransactionRecord,
    activation: ActivationRecord,
    *,
    require_current: bool = True,
    require_active: bool = True,
) -> None:
    if not isinstance(activation, ActivationRecord):
        raise DomainTransitionError(
            "activation must be an ActivationRecord"
        )
    activation_id = _required_text(
        activation.activation_id,
        "activation_id",
    )
    if activation.transaction_id != record.transaction_id:
        raise DomainTransitionError(
            f"activation {activation_id} belongs to another transaction"
        )
    if (
        require_current
        and record.current_activation_id != activation_id
    ):
        raise DomainTransitionError(
            f"activation {activation_id} is not current for "
            f"{record.transaction_id}"
        )
    if require_active and activation.status != ActivationStatus.ACTIVE:
        raise DomainTransitionError(
            f"activation {activation_id} is {activation.status.value}"
        )


def _require_delegate_owner(
    record: TransactionRecord,
    activation: ActivationRecord,
    delegate: DelegateRecord,
) -> None:
    delegate_id = _required_text(delegate.delegate_id, "delegate_id")
    if delegate.transaction_id != record.transaction_id:
        raise DomainTransitionError(
            f"delegate {delegate_id} belongs to another transaction"
        )
    if delegate.activation_id != activation.activation_id:
        raise DomainTransitionError(
            f"delegate {delegate_id} belongs to another activation"
        )


def _validate_activation_delegates(
    record: TransactionRecord,
    activation: ActivationRecord,
    delegates: Tuple[DelegateRecord, ...],
) -> None:
    for delegate in delegates:
        _require_delegate_owner(record, activation, delegate)


def _require_no_pending_delegates(
    record: TransactionRecord,
    delegates: Tuple[DelegateRecord, ...],
) -> None:
    pending = [
        delegate.delegate_id
        for delegate in delegates
        if delegate.status == DelegateStatus.PENDING
    ]
    if pending:
        raise DomainTransitionError(
            "pending delegates prevent this transition: "
            + ", ".join(pending)
        )
    if record.active_delegate_id:
        raise DomainTransitionError(
            f"active delegate {record.active_delegate_id} "
            "prevents this transition"
        )


def _clear_active_delegate(record: TransactionRecord) -> None:
    record.active_delegate_id = None


def _complete_activation(
    record: TransactionRecord,
    activation: ActivationRecord,
    now: str,
) -> ActivationRecord:
    _require_activation_owner(record, activation)
    activation.status = ActivationStatus.COMPLETED
    activation.ended_at = now
    activation.invalidated_reason = None
    activation.to_revision = None
    record.current_activation_id = None
    record.reply_finalized_in_activation = False
    return activation


def open_activation(
    record: TransactionRecord,
    source: str,
    now: str,
    activation_id: Optional[str] = None,
) -> ActivationRecord:
    """Open one execution batch for an active ``continue`` transaction."""

    _require_active_record(record)
    timestamp = _required_text(now, "now")
    source_name = _required_text(source, "source")
    if record.state != TransactionState.CONTINUE:
        raise DomainTransitionError(
            "an activation can only open while transaction state is continue"
        )
    if record.current_activation_id is not None:
        raise DomainTransitionError(
            f"transaction {record.transaction_id} already has activation "
            f"{record.current_activation_id}"
        )
    if record.active_delegate_id is not None:
        raise DomainTransitionError(
            "cannot open an activation while an active delegate remains"
        )

    new_id = (
        _required_text(activation_id, "activation_id")
        if activation_id is not None
        else f"act_{uuid.uuid4().hex}"
    )
    activation = ActivationRecord(
        activation_id=new_id,
        transaction_id=record.transaction_id,
        status=ActivationStatus.ACTIVE,
        source=source_name,
        created_at=timestamp,
        from_revision=int(record.revision),
    )
    record.current_activation_id = new_id
    record.pause_reason = None
    record.reply_finalized_in_activation = False
    record.archived_at = None
    record.terminal_at = None
    record.updated_at = timestamp
    return activation


def invalidate_activation(
    record: TransactionRecord,
    activation: ActivationRecord,
    delegates: Iterable[DelegateRecord],
    reason: str,
    now: str,
) -> ActivationInvalidation:
    """Permanently invalidate an activation and all of its pending delegates."""

    _require_active_record(record)
    timestamp = _required_text(now, "now")
    invalidation_reason = _required_text(reason, "reason")
    values = _delegate_tuple(delegates)
    _require_activation_owner(record, activation)
    _validate_activation_delegates(record, activation, values)

    pending_by_id = {
        delegate.delegate_id: delegate
        for delegate in values
        if delegate.status == DelegateStatus.PENDING
    }
    if (
        record.active_delegate_id is not None
        and record.active_delegate_id not in pending_by_id
    ):
        raise DomainTransitionError(
            f"active delegate {record.active_delegate_id} was not supplied "
            "for invalidation"
        )

    invalidated_ids = []
    for delegate in values:
        if delegate.status != DelegateStatus.PENDING:
            continue
        delegate.status = DelegateStatus.INVALIDATED
        delegate.invalidated_at = timestamp
        delegate.invalidated_reason = invalidation_reason
        invalidated_ids.append(delegate.delegate_id)

    activation.status = ActivationStatus.INVALIDATED
    activation.ended_at = timestamp
    activation.invalidated_reason = invalidation_reason
    activation.to_revision = None
    record.current_activation_id = None
    record.reply_finalized_in_activation = False
    _clear_active_delegate(record)
    record.updated_at = timestamp
    return ActivationInvalidation(
        activation=activation,
        invalidated_delegate_ids=tuple(invalidated_ids),
    )


def begin_delegate(
    record: TransactionRecord,
    activation: ActivationRecord,
    delegate_id: str,
    now: str,
) -> DelegateRecord:
    """Register the one pending delegate for the current activation.

    The transaction stays in ``continue``; in-flight work is expressed by the
    pending delegate / effect, not by domain pause.
    """

    _require_active_record(record)
    timestamp = _required_text(now, "now")
    normalized_id = _required_text(delegate_id, "delegate_id")
    _require_activation_owner(record, activation)
    if record.state != TransactionState.CONTINUE:
        raise DomainTransitionError(
            "a delegate can only begin while transaction state is continue"
        )
    if record.active_delegate_id is not None:
        raise DomainTransitionError(
            f"transaction already has active delegate "
            f"{record.active_delegate_id}"
        )

    delegate = DelegateRecord(
        delegate_id=normalized_id,
        transaction_id=record.transaction_id,
        activation_id=activation.activation_id,
        status=DelegateStatus.PENDING,
        created_at=timestamp,
    )
    record.delegate_count += 1
    record.active_delegate_id = normalized_id
    record.updated_at = timestamp
    record.terminal_at = None
    return delegate


def consume_delegate(
    record: TransactionRecord,
    activation: ActivationRecord,
    delegate: DelegateRecord,
    now: str,
) -> DelegateRecord:
    """Consume valid Feedback and continue the same activation."""

    _require_active_record(record)
    timestamp = _required_text(now, "now")
    _require_activation_owner(record, activation)
    _require_delegate_owner(record, activation, delegate)
    if record.state != TransactionState.CONTINUE:
        raise DomainTransitionError(
            "delegate Feedback requires transaction state continue"
        )
    if delegate.status != DelegateStatus.PENDING:
        raise DomainTransitionError(
            f"delegate {delegate.delegate_id} is {delegate.status.value}"
        )
    if record.active_delegate_id != delegate.delegate_id:
        raise DomainTransitionError(
            f"delegate {delegate.delegate_id} is not the active delegate"
        )

    delegate.status = DelegateStatus.CONSUMED
    delegate.consumed_at = timestamp
    _clear_active_delegate(record)
    record.state = TransactionState.CONTINUE
    record.updated_at = timestamp
    record.terminal_at = None
    return delegate


def pause_transaction(
    record: TransactionRecord,
    activation: Optional[ActivationRecord] = None,
    delegates: Iterable[DelegateRecord] = (),
    *,
    reason: PauseReason = PauseReason.MANUAL_HOLD,
    now: str,
) -> DomainMutation:
    """Pause a transaction while waiting for user collaboration.

    Any current activation and pending delegates are invalidated.
    """

    _require_active_record(record)
    timestamp = _required_text(now, "now")
    try:
        pause_reason = (
            reason if isinstance(reason, PauseReason) else PauseReason(str(reason))
        )
    except ValueError as exc:
        raise DomainTransitionError(
            f"unsupported pause reason: {reason!r}"
        ) from exc
    values = _delegate_tuple(delegates)
    if record.state in {
        TransactionState.COMPLETE,
        TransactionState.ARCHIVE,
    }:
        raise DomainTransitionError(
            f"cannot pause transaction while state is {record.state.value}"
        )
    if record.state == TransactionState.PAUSE:
        if (
            record.pause_reason not in {None, pause_reason}
            and pause_reason
            not in {PauseReason.MANUAL_HOLD, PauseReason.RUNTIME_ERROR}
        ):
            raise DomainTransitionError(
                "transaction is already paused for "
                f"{record.pause_reason.value}"
            )
        record.pause_reason = pause_reason
        return DomainMutation(record=record, activation=activation)

    invalidated_activation_ids: Tuple[str, ...] = ()
    invalidated_delegate_ids: Tuple[str, ...] = ()

    completed_activation_ids: Tuple[str, ...] = ()
    cooperative_pause = pause_reason in {
        PauseReason.AWAITING_USER,
        PauseReason.SCHEDULED_WAIT,
    }

    if record.current_activation_id is not None:
        if activation is None:
            raise DomainTransitionError(
                "pause requires the current activation for invalidation"
            )
        if cooperative_pause:
            _validate_activation_delegates(record, activation, values)
            _require_no_pending_delegates(record, values)
            _complete_activation(record, activation, timestamp)
            completed_activation_ids = (activation.activation_id,)
        else:
            invalidation = invalidate_activation(
                record,
                activation,
                values,
                pause_reason.value,
                timestamp,
            )
            invalidated_activation_ids = (invalidation.activation_id,)
            invalidated_delegate_ids = invalidation.invalidated_delegate_ids
    else:
        if activation is not None:
            raise DomainTransitionError(
                "transaction has no current activation"
            )
        _require_no_pending_delegates(record, values)

    record.state = TransactionState.PAUSE
    record.pause_reason = pause_reason
    record.reply_finalized_in_activation = False
    record.updated_at = timestamp
    record.terminal_at = None
    return DomainMutation(
        record=record,
        activation=activation,
        invalidated_activation_ids=invalidated_activation_ids,
        invalidated_delegate_ids=invalidated_delegate_ids,
        completed_activation_ids=completed_activation_ids,
    )


def fail_transaction(
    record: TransactionRecord,
    activation: Optional[ActivationRecord] = None,
    delegates: Iterable[DelegateRecord] = (),
    *,
    error: str,
    now: str,
) -> DomainMutation:
    """Pause a failed line safely so it remains explicitly restorable."""

    message = _required_text(error, "error")
    mutation = pause_transaction(
        record,
        activation,
        delegates,
        reason=PauseReason.RUNTIME_ERROR,
        now=now,
    )
    record.last_error = message
    record.updated_at = now
    return mutation


def complete_transaction(
    record: TransactionRecord,
    activation: Optional[ActivationRecord] = None,
    delegates: Iterable[DelegateRecord] = (),
    *,
    now: str,
) -> DomainMutation:
    """Finish the current activation and mark the task complete."""

    _require_active_record(record)
    timestamp = _required_text(now, "now")
    values = _delegate_tuple(delegates)
    if record.state != TransactionState.CONTINUE:
        raise DomainTransitionError(
            f"cannot complete transaction from {record.state.value}"
        )
    if activation is None:
        raise DomainTransitionError(
            "complete requires the current activation"
        )
    _require_activation_owner(record, activation)
    _validate_activation_delegates(record, activation, values)
    _require_no_pending_delegates(record, values)
    _complete_activation(record, activation, timestamp)

    record.state = TransactionState.COMPLETE
    record.pause_reason = None
    record.updated_at = timestamp
    record.terminal_at = timestamp
    return DomainMutation(
        record=record,
        activation=activation,
        completed_activation_ids=(activation.activation_id,),
    )


def delete_transaction(
    record: TransactionRecord,
    activation: Optional[ActivationRecord] = None,
    delegates: Iterable[DelegateRecord] = (),
    *,
    now: str,
) -> DomainMutation:
    """Write a permanent deletion tombstone and invalidate live work."""

    _require_active_record(record)
    timestamp = _required_text(now, "now")
    values = _delegate_tuple(delegates)
    invalidated_activation_ids: Tuple[str, ...] = ()
    invalidated_delegate_ids: Tuple[str, ...] = ()

    if record.current_activation_id is not None:
        if activation is None:
            raise DomainTransitionError(
                "delete requires the current activation for invalidation"
            )
        invalidation = invalidate_activation(
            record,
            activation,
            values,
            "transaction_deleted",
            timestamp,
        )
        invalidated_activation_ids = (invalidation.activation_id,)
        invalidated_delegate_ids = (
            invalidation.invalidated_delegate_ids
        )
    else:
        if activation is not None:
            raise DomainTransitionError(
                "transaction has no current activation"
            )
        _require_no_pending_delegates(record, values)

    record.lifecycle_status = TransactionLifecycle.DELETED
    record.deleted_at = timestamp
    record.updated_at = timestamp
    record.terminal_at = timestamp
    record.current_activation_id = None
    _clear_active_delegate(record)
    return DomainMutation(
        record=record,
        activation=activation,
        invalidated_activation_ids=invalidated_activation_ids,
        invalidated_delegate_ids=invalidated_delegate_ids,
    )


def restore_transaction(
    record: TransactionRecord,
    activation: Optional[ActivationRecord] = None,
    delegates: Iterable[DelegateRecord] = (),
    *,
    source: str,
    now: str,
    activation_id: Optional[str] = None,
) -> DomainMutation:
    """Restore pause/complete/archive with a fresh execution activation."""

    _require_active_record(record)
    timestamp = _required_text(now, "now")
    source_name = _required_text(source, "source")
    values = _delegate_tuple(delegates)
    if record.state not in {
        TransactionState.PAUSE,
        TransactionState.COMPLETE,
        TransactionState.ARCHIVE,
    }:
        raise DomainTransitionError(
            f"cannot restore transaction from {record.state.value}"
        )

    invalidated_activation_ids: Tuple[str, ...] = ()
    invalidated_delegate_ids: Tuple[str, ...] = ()
    if record.current_activation_id is not None:
        if activation is None:
            raise DomainTransitionError(
                "restore requires the current activation for invalidation"
            )
        invalidation = invalidate_activation(
            record,
            activation,
            values,
            "superseded_by_restore",
            timestamp,
        )
        invalidated_activation_ids = (invalidation.activation_id,)
        invalidated_delegate_ids = (
            invalidation.invalidated_delegate_ids
        )
    else:
        if activation is not None:
            raise DomainTransitionError(
                "transaction has no current activation"
            )
        _require_no_pending_delegates(record, values)

    record.state = TransactionState.CONTINUE
    record.pause_reason = None
    record.archived_at = None
    record.terminal_at = None
    record.updated_at = timestamp
    _clear_active_delegate(record)
    new_activation = open_activation(
        record,
        source_name,
        timestamp,
        activation_id=activation_id,
    )
    return DomainMutation(
        record=record,
        activation=new_activation,
        invalidated_activation_ids=invalidated_activation_ids,
        invalidated_delegate_ids=invalidated_delegate_ids,
    )


def archive_transaction(
    record: TransactionRecord,
    delegates: Iterable[DelegateRecord] = (),
    *,
    now: str,
    by_flush: bool,
) -> DomainMutation:
    """Archive an eligible complete transaction, exclusively via Flush."""

    _require_active_record(record)
    timestamp = _required_text(now, "now")
    values = _delegate_tuple(delegates)
    if not by_flush:
        raise DomainTransitionError(
            "archive is only legal as part of a flush"
        )
    if record.state != TransactionState.COMPLETE:
        raise DomainTransitionError(
            f"cannot archive transaction from {record.state.value}"
        )
    if record.current_activation_id is not None:
        raise DomainTransitionError(
            "cannot archive while an activation is current"
        )
    _require_no_pending_delegates(record, values)

    record.state = TransactionState.ARCHIVE
    record.pause_reason = None
    record.archived_at = timestamp
    record.updated_at = timestamp
    record.terminal_at = timestamp
    return DomainMutation(record=record)


__all__ = [
    "ActivationInvalidation",
    "DomainMutation",
    "DomainTransitionError",
    "archive_transaction",
    "begin_delegate",
    "complete_transaction",
    "consume_delegate",
    "delete_transaction",
    "fail_transaction",
    "invalidate_activation",
    "open_activation",
    "pause_transaction",
    "restore_transaction",
]
