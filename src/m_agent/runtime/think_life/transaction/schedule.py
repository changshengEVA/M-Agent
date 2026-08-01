"""Transaction-side one-shot Schedule primitives for the P2 foundation.

This module owns only the atomic relationship between a durable schedule run
and its original transaction.  It intentionally does *not* implement the P3
Inbox admission, stimulus consumer, lease, or preconsume disposition model.
The caller decides when a run is due and invokes :meth:`claim`; this module
then commits the run state and transaction activation in one SQLite UoW.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from ..contracts import (
    ActivationRecord,
    ActivationStatus,
    DelegateRecord,
    DelegateStatus,
    PauseReason,
    TransactionLifecycle,
    TransactionRecord,
    TransactionState,
)
from .domain import (
    DomainTransitionError,
    complete_transaction,
    delete_transaction,
    open_activation,
    pause_transaction,
    restore_transaction,
)
from .store import (
    RecordNotFoundError,
    RevisionConflictError,
    RuntimeStoreUnitOfWork,
    SQLiteRuntimeStore,
    StoreInvariantError,
    TransitionExecution,
)


Clock = Callable[[], str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ScheduleTransitionError(f"{field_name} is required")
    return normalized


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def stable_schedule_run_id(
    schedule_id: str,
    transaction_id: str,
    due_at: str,
) -> str:
    """Derive the stable identity of one one-shot schedule run."""

    return _stable_id(
        "schrun",
        _required(schedule_id, "schedule_id"),
        _required(transaction_id, "transaction_id"),
        _required(due_at, "due_at"),
    )


def stable_schedule_delivery_id(
    schedule_run_id: str,
    generation: int,
) -> str:
    """Derive one stable delivery identity from run + generation."""

    normalized_generation = int(generation)
    if normalized_generation < 1:
        raise ScheduleTransitionError(
            "delivery generation must be positive"
        )
    return _stable_id(
        "schdel",
        _required(schedule_run_id, "schedule_run_id"),
        normalized_generation,
    )


def _stable_activation_id(
    schedule_run_id: str,
    generation: int,
) -> str:
    return _stable_id(
        "act_schedule",
        _required(schedule_run_id, "schedule_run_id"),
        int(generation),
    )


class ScheduleTransitionError(ValueError):
    """Raised when a one-shot Schedule command is not legal."""


class ScheduleRunStatus(str, Enum):
    SCHEDULED = "scheduled"
    DUE = "due"
    BLOCKED_ON_ACTIVATION = "blocked_on_activation"
    CLAIMED = "claimed"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"


class ScheduleDeliveryStatus(str, Enum):
    CLAIMED = "claimed"
    CONSUMED = "consumed"
    ABORTED = "aborted"


@dataclass
class OneShotScheduleRun:
    """Durable transaction-side state of one one-shot run.

    Delivery fields are logical identities only.  P3 will decide how a
    delivery is admitted, claimed, and finalized in the durable Inbox.
    """

    schedule_id: str
    schedule_run_id: str
    transaction_id: str
    conversation_id: str
    due_at: str
    status: ScheduleRunStatus = ScheduleRunStatus.SCHEDULED
    revision: int = 1
    delivery_generation: int = 0
    schedule_delivery_id: Optional[str] = None
    delivery_status: Optional[ScheduleDeliveryStatus] = None
    claimed_activation_id: Optional[str] = None
    blocked_reason: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    consumed_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "schedule_run_id": self.schedule_run_id,
            "transaction_id": self.transaction_id,
            "conversation_id": self.conversation_id,
            "due_at": self.due_at,
            "status": self.status.value,
            "revision": int(self.revision),
            "delivery_generation": int(self.delivery_generation),
            "schedule_delivery_id": self.schedule_delivery_id,
            "delivery_status": (
                self.delivery_status.value
                if self.delivery_status is not None
                else None
            ),
            "claimed_activation_id": self.claimed_activation_id,
            "blocked_reason": self.blocked_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "consumed_at": self.consumed_at,
            "cancelled_at": self.cancelled_at,
            "schema_version": int(self.schema_version),
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "OneShotScheduleRun":
        delivery_status = data.get("delivery_status")
        return cls(
            schedule_id=str(data.get("schedule_id", "") or ""),
            schedule_run_id=str(
                data.get("schedule_run_id", "") or ""
            ),
            transaction_id=str(data.get("transaction_id", "") or ""),
            conversation_id=str(
                data.get("conversation_id", "") or ""
            ),
            due_at=str(data.get("due_at", "") or ""),
            status=ScheduleRunStatus(
                str(
                    data.get(
                        "status",
                        ScheduleRunStatus.SCHEDULED.value,
                    )
                )
            ),
            revision=int(data.get("revision", 1) or 1),
            delivery_generation=int(
                data.get("delivery_generation", 0) or 0
            ),
            schedule_delivery_id=data.get("schedule_delivery_id"),
            delivery_status=(
                ScheduleDeliveryStatus(str(delivery_status))
                if delivery_status
                else None
            ),
            claimed_activation_id=data.get("claimed_activation_id"),
            blocked_reason=data.get("blocked_reason"),
            created_at=str(data.get("created_at", "") or ""),
            updated_at=str(data.get("updated_at", "") or ""),
            consumed_at=data.get("consumed_at"),
            cancelled_at=data.get("cancelled_at"),
            schema_version=int(data.get("schema_version", 1) or 1),
        )


@dataclass(frozen=True)
class ScheduleTransitionResult:
    action: str
    outcome: str
    run: OneShotScheduleRun
    transaction: TransactionRecord
    replayed: bool = False
    cancelled_run_ids: Tuple[str, ...] = ()

    @property
    def activation_id(self) -> Optional[str]:
        return self.run.claimed_activation_id

    @property
    def delivery_id(self) -> Optional[str]:
        return self.run.schedule_delivery_id


class TransactionScheduleCoordinator:
    """Atomic one-shot Schedule commands over ``SQLiteRuntimeStore``."""

    def __init__(
        self,
        store: SQLiteRuntimeStore,
        *,
        clock: Optional[Clock] = None,
    ) -> None:
        if not isinstance(store, SQLiteRuntimeStore):
            raise TypeError("store must be a SQLiteRuntimeStore")
        self.store = store
        self._clock = clock or _now_iso
        self._fault_point: Optional[str] = None

    def inject_fault(self, fault_point: str) -> Dict[str, Any]:
        point = str(fault_point or "").strip()
        self._fault_point = point or None
        return {
            "fault_point": point,
            "armed": bool(point),
            "scope": "p7_schedule_uow",
        }

    def load(
        self,
        schedule_run_id: str,
    ) -> Optional[OneShotScheduleRun]:
        payload = self.store.load_schedule_run(schedule_run_id)
        return (
            OneShotScheduleRun.from_dict(payload)
            if payload is not None
            else None
        )

    def ensure_run(
        self,
        *,
        schedule_id: str,
        schedule_run_id: str,
        transaction_id: str,
        conversation_id: str,
        due_at: Optional[str] = None,
    ) -> OneShotScheduleRun:
        """Create a durable run row without mutating the bound transaction."""

        run_id = _required(schedule_run_id, "schedule_run_id")
        existing = self.load(run_id)
        if existing is not None:
            return existing
        sid = _required(schedule_id, "schedule_id")
        tx_id = _required(transaction_id, "transaction_id")
        cid = _required(conversation_id, "conversation_id")
        due = _required(due_at or self._clock(), "due_at")
        now = self._clock()
        run = OneShotScheduleRun(
            schedule_id=sid,
            schedule_run_id=run_id,
            transaction_id=tx_id,
            conversation_id=cid,
            due_at=due,
            status=ScheduleRunStatus.DUE,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        self.store.create_schedule_run(run_id, run.to_dict())
        return run

    def mark_delivery_observed(
        self,
        schedule_run_id: str,
        *,
        schedule_delivery_id: str,
        expected_run_revision: int,
    ) -> OneShotScheduleRun:
        """Record one delivery generation without opening a new activation."""

        run_id = _required(schedule_run_id, "schedule_run_id")
        delivery_id = _required(
            schedule_delivery_id,
            "schedule_delivery_id",
        )
        expected = int(expected_run_revision)
        run = self._require_run_standalone(run_id)
        if run.schedule_delivery_id == delivery_id:
            return run
        run.schedule_delivery_id = delivery_id
        run.updated_at = self._clock()
        if run.status == ScheduleRunStatus.DUE:
            run.status = ScheduleRunStatus.SCHEDULED
        self.store.cas_schedule_run(
            run_id,
            run.to_dict(),
            expected_revision=expected,
        )
        return self.load(run_id) or run

    def _require_run_standalone(
        self,
        schedule_run_id: str,
    ) -> OneShotScheduleRun:
        payload = self.store.load_schedule_run(schedule_run_id)
        if payload is None:
            raise ScheduleTransitionError(
                f"unknown schedule run: {schedule_run_id}"
            )
        return OneShotScheduleRun.from_dict(payload)

    def list_for_transaction(
        self,
        transaction_id: str,
    ) -> List[OneShotScheduleRun]:
        return [
            OneShotScheduleRun.from_dict(item)
            for item in self.store.list_schedule_runs(
                transaction_id=_required(
                    transaction_id,
                    "transaction_id",
                )
            )
        ]

    def register(
        self,
        *,
        schedule_id: str,
        transaction_id: str,
        due_at: str,
        expected_transaction_revision: int,
        schedule_run_id: Optional[str] = None,
        transition_id: Optional[str] = None,
    ) -> ScheduleTransitionResult:
        """Atomically register a one-shot schedule run on the transaction.

        Completes the current activation and enters
        ``pause(scheduled_wait)``.  The run row owns delivery/claim progress;
        the pause reason explains why the logical transaction is dormant.
        """

        sid = _required(schedule_id, "schedule_id")
        tx_id = _required(transaction_id, "transaction_id")
        due = _required(due_at, "due_at")
        run_id = (
            _required(schedule_run_id, "schedule_run_id")
            if schedule_run_id is not None
            else stable_schedule_run_id(sid, tx_id, due)
        )
        expected_tx = int(expected_transaction_revision)
        transition = (
            _required(transition_id, "transition_id")
            if transition_id is not None
            else f"schedule-register:{run_id}"
        )
        command = {
            "action": "register_one_shot_schedule",
            "schedule_id": sid,
            "schedule_run_id": run_id,
            "transaction_id": tx_id,
            "due_at": due,
            "expected_transaction_revision": expected_tx,
        }

        def operation(
            uow: RuntimeStoreUnitOfWork,
        ) -> Mapping[str, Any]:
            existing = uow.load_schedule_run(run_id)
            if existing is not None:
                existing_run = OneShotScheduleRun.from_dict(existing)
                self._require_run_binding(
                    existing_run,
                    transaction_id=tx_id,
                    schedule_id=sid,
                    due_at=due,
                )
                return self._result_payload(
                    action="register",
                    outcome="idempotent_existing",
                    run=existing_run,
                    transaction=self._require_transaction(uow, tx_id),
                )

            record, activation, delegates = self._load_bundle(
                uow,
                tx_id,
            )
            self._check_revision(record, expected_tx)
            activation_status, delegate_statuses = (
                self._bundle_statuses(activation, delegates)
            )
            now = self._clock()
            pause_transaction(
                record,
                activation,
                delegates,
                reason=PauseReason.SCHEDULED_WAIT,
                now=now,
            )
            record.correlation.schedule_id = sid
            record.correlation.schedule_run_id = run_id
            to_revision = self._advance_transaction(
                uow,
                record,
                now=now,
            )
            self._save_existing_bundle(
                uow,
                activation=activation,
                activation_status=activation_status,
                delegates=delegates,
                delegate_statuses=delegate_statuses,
                to_revision=to_revision,
            )
            run = OneShotScheduleRun(
                schedule_id=sid,
                schedule_run_id=run_id,
                transaction_id=tx_id,
                conversation_id=record.conversation_id,
                due_at=due,
                status=ScheduleRunStatus.SCHEDULED,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            uow.create_schedule_run(run_id, run.to_dict())
            return self._result_payload(
                action="register",
                outcome=ScheduleRunStatus.SCHEDULED.value,
                run=run,
                transaction=record,
            )

        return self._execute(
            transition_id=transition,
            command=command,
            operation=operation,
        )

    def claim(
        self,
        schedule_run_id: str,
        *,
        expected_run_revision: int,
        expected_transaction_revision: int,
        transition_id: Optional[str] = None,
    ) -> ScheduleTransitionResult:
        """Claim a due run and open one new activation on the original task."""

        run_id = _required(schedule_run_id, "schedule_run_id")
        expected_run = int(expected_run_revision)
        expected_tx = int(expected_transaction_revision)
        transition = (
            _required(transition_id, "transition_id")
            if transition_id is not None
            else f"schedule-claim:{run_id}:{expected_run}"
        )
        command = {
            "action": "claim_one_shot_schedule",
            "schedule_run_id": run_id,
            "expected_run_revision": expected_run,
            "expected_transaction_revision": expected_tx,
        }

        def operation(
            uow: RuntimeStoreUnitOfWork,
        ) -> Mapping[str, Any]:
            run = self._require_run(uow, run_id)
            self._check_run_revision(run, expected_run)
            record, old_activation, delegates = self._load_bundle(
                uow,
                run.transaction_id,
            )
            self._check_revision(record, expected_tx)
            self._require_run_binding(
                run,
                transaction_id=record.transaction_id,
                conversation_id=record.conversation_id,
            )
            now = self._clock()

            if record.lifecycle_status == TransactionLifecycle.DELETED:
                self._cancel_run(run, now=now)
                self._save_run(uow, run, expected_revision=expected_run)
                return self._result_payload(
                    action="claim",
                    outcome=ScheduleRunStatus.CANCELLED.value,
                    run=run,
                    transaction=record,
                )

            if run.status in {
                ScheduleRunStatus.CONSUMED,
                ScheduleRunStatus.CANCELLED,
                ScheduleRunStatus.CLAIMED,
            }:
                raise ScheduleTransitionError(
                    f"cannot claim run from {run.status.value}"
                )

            blocked_reason = self._claim_block_reason(
                uow,
                run,
                record,
            )
            if blocked_reason is not None:
                self._block_run(
                    run,
                    reason=blocked_reason,
                    now=now,
                )
                self._save_run(uow, run, expected_revision=expected_run)
                return self._result_payload(
                    action="claim",
                    outcome=ScheduleRunStatus.BLOCKED_ON_ACTIVATION.value,
                    run=run,
                    transaction=record,
                )

            activation_status, delegate_statuses = (
                self._bundle_statuses(old_activation, delegates)
            )
            generation = int(run.delivery_generation) + 1
            delivery_id = stable_schedule_delivery_id(
                run.schedule_run_id,
                generation,
            )
            activation_id = _stable_activation_id(
                run.schedule_run_id,
                generation,
            )
            if record.state == TransactionState.CONTINUE:
                if record.current_activation_id is not None:
                    raise ScheduleTransitionError(
                        "cannot claim while an activation is current"
                    )
                new_activation = open_activation(
                    record,
                    source=f"schedule:{run.schedule_run_id}",
                    now=now,
                    activation_id=activation_id,
                )
                mutation_activation = new_activation
                mutation_old_activation = None
            else:
                mutation = restore_transaction(
                    record,
                    old_activation,
                    delegates,
                    source=f"schedule:{run.schedule_run_id}",
                    now=now,
                    activation_id=activation_id,
                )
                mutation_activation = mutation.activation
                mutation_old_activation = old_activation
            record.correlation.schedule_id = run.schedule_id
            record.correlation.schedule_run_id = run.schedule_run_id
            to_revision = self._advance_transaction(
                uow,
                record,
                now=now,
            )
            self._save_existing_bundle(
                uow,
                activation=mutation_old_activation,
                activation_status=activation_status,
                delegates=delegates,
                delegate_statuses=delegate_statuses,
                to_revision=to_revision,
            )
            if mutation_activation is None:
                raise StoreInvariantError(
                    "schedule claim did not create an activation"
                )
            uow.create_activation(mutation_activation)

            run.status = ScheduleRunStatus.CLAIMED
            run.revision = expected_run + 1
            run.delivery_generation = generation
            run.schedule_delivery_id = delivery_id
            run.delivery_status = ScheduleDeliveryStatus.CLAIMED
            run.claimed_activation_id = activation_id
            run.blocked_reason = None
            run.updated_at = now
            self._save_run_payload(
                uow,
                run,
                expected_revision=expected_run,
            )
            fault = self._fault_point
            if fault == "after_schedule_claim_before_activation":
                self._fault_point = None
                raise ScheduleTransitionError(
                    "simulated crash after schedule claim before activation"
                )
            return self._result_payload(
                action="claim",
                outcome=ScheduleRunStatus.CLAIMED.value,
                run=run,
                transaction=record,
            )

        return self._execute(
            transition_id=transition,
            command=command,
            operation=operation,
        )

    def complete(
        self,
        schedule_run_id: str,
        *,
        expected_run_revision: int,
        expected_transaction_revision: int,
        transition_id: Optional[str] = None,
    ) -> ScheduleTransitionResult:
        """Atomically mark the task complete and the one-shot run consumed."""

        run_id = _required(schedule_run_id, "schedule_run_id")
        expected_run = int(expected_run_revision)
        expected_tx = int(expected_transaction_revision)
        transition = (
            _required(transition_id, "transition_id")
            if transition_id is not None
            else f"schedule-complete:{run_id}:{expected_run}"
        )
        command = {
            "action": "complete_one_shot_schedule",
            "schedule_run_id": run_id,
            "expected_run_revision": expected_run,
            "expected_transaction_revision": expected_tx,
        }

        def operation(
            uow: RuntimeStoreUnitOfWork,
        ) -> Mapping[str, Any]:
            run = self._require_run(uow, run_id)
            self._check_run_revision(run, expected_run)
            if run.status != ScheduleRunStatus.CLAIMED:
                raise ScheduleTransitionError(
                    f"cannot complete run from {run.status.value}"
                )
            record, activation, delegates = self._load_bundle(
                uow,
                run.transaction_id,
            )
            self._check_revision(record, expected_tx)
            if (
                activation is None
                or activation.activation_id
                != run.claimed_activation_id
            ):
                raise ScheduleTransitionError(
                    "claimed schedule activation is not current"
                )
            activation_status, delegate_statuses = (
                self._bundle_statuses(activation, delegates)
            )
            now = self._clock()
            complete_transaction(
                record,
                activation,
                delegates,
                now=now,
            )
            to_revision = self._advance_transaction(
                uow,
                record,
                now=now,
            )
            self._save_existing_bundle(
                uow,
                activation=activation,
                activation_status=activation_status,
                delegates=delegates,
                delegate_statuses=delegate_statuses,
                to_revision=to_revision,
            )
            run.status = ScheduleRunStatus.CONSUMED
            run.revision = expected_run + 1
            run.delivery_status = ScheduleDeliveryStatus.CONSUMED
            run.blocked_reason = None
            run.consumed_at = now
            run.updated_at = now
            self._save_run_payload(
                uow,
                run,
                expected_revision=expected_run,
            )
            return self._result_payload(
                action="complete",
                outcome=ScheduleRunStatus.CONSUMED.value,
                run=run,
                transaction=record,
            )

        return self._execute(
            transition_id=transition,
            command=command,
            operation=operation,
        )

    def manual_pause(
        self,
        schedule_run_id: str,
        *,
        expected_run_revision: int,
        expected_transaction_revision: int,
        delivery_consumed: bool = False,
        transition_id: Optional[str] = None,
    ) -> ScheduleTransitionResult:
        """Apply UI Pause and block, without re-enqueuing, this run."""

        run_id = _required(schedule_run_id, "schedule_run_id")
        expected_run = int(expected_run_revision)
        expected_tx = int(expected_transaction_revision)
        consumed = bool(delivery_consumed)
        transition = (
            _required(transition_id, "transition_id")
            if transition_id is not None
            else f"schedule-manual-pause:{run_id}:{expected_run}"
        )
        command = {
            "action": "manual_pause_one_shot_schedule",
            "schedule_run_id": run_id,
            "expected_run_revision": expected_run,
            "expected_transaction_revision": expected_tx,
            "delivery_consumed": consumed,
        }

        def operation(
            uow: RuntimeStoreUnitOfWork,
        ) -> Mapping[str, Any]:
            run = self._require_run(uow, run_id)
            self._check_run_revision(run, expected_run)
            if run.status in {
                ScheduleRunStatus.CONSUMED,
                ScheduleRunStatus.CANCELLED,
            }:
                raise ScheduleTransitionError(
                    f"cannot pause run from {run.status.value}"
                )
            record, activation, delegates = self._load_bundle(
                uow,
                run.transaction_id,
            )
            self._check_revision(record, expected_tx)
            activation_status, delegate_statuses = (
                self._bundle_statuses(activation, delegates)
            )
            now = self._clock()
            pause_transaction(
                record,
                activation,
                delegates,
                reason=PauseReason.MANUAL_HOLD,
                now=now,
            )
            to_revision = self._advance_transaction(
                uow,
                record,
                now=now,
            )
            self._save_existing_bundle(
                uow,
                activation=activation,
                activation_status=activation_status,
                delegates=delegates,
                delegate_statuses=delegate_statuses,
                to_revision=to_revision,
            )
            run.status = ScheduleRunStatus.BLOCKED_ON_ACTIVATION
            run.revision = expected_run + 1
            run.claimed_activation_id = None
            run.blocked_reason = "pause"
            if consumed or (
                run.delivery_status == ScheduleDeliveryStatus.CONSUMED
            ):
                run.delivery_status = ScheduleDeliveryStatus.CONSUMED
            elif run.schedule_delivery_id is not None:
                run.delivery_status = ScheduleDeliveryStatus.ABORTED
            run.updated_at = now
            self._save_run_payload(
                uow,
                run,
                expected_revision=expected_run,
            )
            return self._result_payload(
                action="manual_pause",
                outcome=ScheduleRunStatus.BLOCKED_ON_ACTIVATION.value,
                run=run,
                transaction=record,
            )

        return self._execute(
            transition_id=transition,
            command=command,
            operation=operation,
        )

    def delete(
        self,
        schedule_run_id: str,
        *,
        expected_run_revision: int,
        expected_transaction_revision: int,
        transition_id: Optional[str] = None,
    ) -> ScheduleTransitionResult:
        """Write the transaction tombstone and cancel all unconsumed runs."""

        run_id = _required(schedule_run_id, "schedule_run_id")
        expected_run = int(expected_run_revision)
        expected_tx = int(expected_transaction_revision)
        transition = (
            _required(transition_id, "transition_id")
            if transition_id is not None
            else f"schedule-delete:{run_id}:{expected_run}"
        )
        command = {
            "action": "delete_scheduled_transaction",
            "schedule_run_id": run_id,
            "expected_run_revision": expected_run,
            "expected_transaction_revision": expected_tx,
        }

        def operation(
            uow: RuntimeStoreUnitOfWork,
        ) -> Mapping[str, Any]:
            target = self._require_run(uow, run_id)
            self._check_run_revision(target, expected_run)
            record, activation, delegates = self._load_bundle(
                uow,
                target.transaction_id,
            )
            self._check_revision(record, expected_tx)
            activation_status, delegate_statuses = (
                self._bundle_statuses(activation, delegates)
            )
            now = self._clock()
            delete_transaction(
                record,
                activation,
                delegates,
                now=now,
            )
            to_revision = self._advance_transaction(
                uow,
                record,
                now=now,
            )
            self._save_existing_bundle(
                uow,
                activation=activation,
                activation_status=activation_status,
                delegates=delegates,
                delegate_statuses=delegate_statuses,
                to_revision=to_revision,
            )

            cancelled_ids: List[str] = []
            target_after = target
            for payload in uow.list_schedule_runs(
                transaction_id=record.transaction_id
            ):
                run = OneShotScheduleRun.from_dict(payload)
                if run.status in {
                    ScheduleRunStatus.CONSUMED,
                    ScheduleRunStatus.CANCELLED,
                }:
                    if run.schedule_run_id == run_id:
                        target_after = run
                    continue
                before_revision = int(run.revision)
                self._cancel_run(run, now=now)
                self._save_run(
                    uow,
                    run,
                    expected_revision=before_revision,
                )
                cancelled_ids.append(run.schedule_run_id)
                if run.schedule_run_id == run_id:
                    target_after = run

            return self._result_payload(
                action="delete",
                outcome=ScheduleRunStatus.CANCELLED.value,
                run=target_after,
                transaction=record,
                cancelled_run_ids=cancelled_ids,
            )

        return self._execute(
            transition_id=transition,
            command=command,
            operation=operation,
        )

    def _execute(
        self,
        *,
        transition_id: str,
        command: Mapping[str, Any],
        operation: Callable[
            [RuntimeStoreUnitOfWork],
            Mapping[str, Any],
        ],
    ) -> ScheduleTransitionResult:
        try:
            execution = self.store.execute_transition(
                transition_id=transition_id,
                command=command,
                operation=operation,
            )
        except DomainTransitionError as exc:
            raise ScheduleTransitionError(str(exc)) from exc
        return self._transition_result(execution)

    @staticmethod
    def _transition_result(
        execution: TransitionExecution,
    ) -> ScheduleTransitionResult:
        payload = execution.result
        return ScheduleTransitionResult(
            action=str(payload.get("action", "") or ""),
            outcome=str(payload.get("outcome", "") or ""),
            run=OneShotScheduleRun.from_dict(
                dict(payload.get("run") or {})
            ),
            transaction=TransactionRecord.from_dict(
                dict(payload.get("transaction") or {})
            ),
            replayed=bool(execution.replayed),
            cancelled_run_ids=tuple(
                str(item)
                for item in payload.get("cancelled_run_ids", [])
            ),
        )

    @staticmethod
    def _result_payload(
        *,
        action: str,
        outcome: str,
        run: OneShotScheduleRun,
        transaction: TransactionRecord,
        cancelled_run_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return {
            "action": action,
            "outcome": outcome,
            "run": run.to_dict(),
            "transaction": transaction.to_dict(),
            "cancelled_run_ids": list(cancelled_run_ids or []),
        }

    @staticmethod
    def _require_transaction(
        uow: RuntimeStoreUnitOfWork,
        transaction_id: str,
    ) -> TransactionRecord:
        record = uow.load_transaction(transaction_id)
        if record is None:
            raise RecordNotFoundError(transaction_id)
        return record

    @classmethod
    def _load_bundle(
        cls,
        uow: RuntimeStoreUnitOfWork,
        transaction_id: str,
    ) -> Tuple[
        TransactionRecord,
        Optional[ActivationRecord],
        List[DelegateRecord],
    ]:
        record = cls._require_transaction(uow, transaction_id)
        activation: Optional[ActivationRecord] = None
        if record.current_activation_id is not None:
            activation = uow.load_activation(
                record.current_activation_id
            )
            if activation is None:
                raise StoreInvariantError(
                    "current transaction activation is missing"
                )
            delegates = uow.list_delegates(
                activation_id=activation.activation_id
            )
        else:
            delegates = uow.list_delegates(
                transaction_id=record.transaction_id
            )
        return record, activation, delegates

    @staticmethod
    def _bundle_statuses(
        activation: Optional[ActivationRecord],
        delegates: List[DelegateRecord],
    ) -> Tuple[
        Optional[ActivationStatus],
        Dict[str, DelegateStatus],
    ]:
        return (
            activation.status if activation is not None else None,
            {
                delegate.delegate_id: delegate.status
                for delegate in delegates
            },
        )

    @staticmethod
    def _save_existing_bundle(
        uow: RuntimeStoreUnitOfWork,
        *,
        activation: Optional[ActivationRecord],
        activation_status: Optional[ActivationStatus],
        delegates: List[DelegateRecord],
        delegate_statuses: Mapping[str, DelegateStatus],
        to_revision: int,
    ) -> None:
        if activation is not None:
            if (
                activation.status != ActivationStatus.ACTIVE
                and activation.to_revision is None
            ):
                activation.to_revision = int(to_revision)
            uow.save_activation(
                activation,
                expected_status=activation_status,
            )
        for delegate in delegates:
            uow.save_delegate(
                delegate,
                expected_status=delegate_statuses[delegate.delegate_id],
            )

    @staticmethod
    def _advance_transaction(
        uow: RuntimeStoreUnitOfWork,
        record: TransactionRecord,
        *,
        now: str,
    ) -> int:
        previous = int(record.revision)
        record.revision = previous + 1
        record.updated_at = now
        uow.cas_transaction(record, expected_revision=previous)
        return int(record.revision)

    @staticmethod
    def _check_revision(
        record: TransactionRecord,
        expected_revision: int,
    ) -> None:
        expected = int(expected_revision)
        if int(record.revision) != expected:
            raise RevisionConflictError(
                record.transaction_id,
                expected_revision=expected,
                actual_revision=int(record.revision),
            )

    @staticmethod
    def _require_run(
        uow: RuntimeStoreUnitOfWork,
        schedule_run_id: str,
    ) -> OneShotScheduleRun:
        payload = uow.load_schedule_run(schedule_run_id)
        if payload is None:
            raise RecordNotFoundError(schedule_run_id)
        return OneShotScheduleRun.from_dict(payload)

    @staticmethod
    def _check_run_revision(
        run: OneShotScheduleRun,
        expected_revision: int,
    ) -> None:
        expected = int(expected_revision)
        if int(run.revision) != expected:
            raise RevisionConflictError(
                run.schedule_run_id,
                expected_revision=expected,
                actual_revision=int(run.revision),
            )

    @staticmethod
    def _require_run_binding(
        run: OneShotScheduleRun,
        *,
        transaction_id: str,
        schedule_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        due_at: Optional[str] = None,
    ) -> None:
        if run.transaction_id != transaction_id:
            raise ScheduleTransitionError(
                "schedule run transaction binding mismatch"
            )
        if (
            schedule_id is not None
            and run.schedule_id != schedule_id
        ):
            raise ScheduleTransitionError(
                "schedule run schedule binding mismatch"
            )
        if (
            conversation_id is not None
            and run.conversation_id != conversation_id
        ):
            raise ScheduleTransitionError(
                "schedule run conversation binding mismatch"
            )
        if due_at is not None and run.due_at != due_at:
            raise ScheduleTransitionError(
                "schedule run due_at binding mismatch"
            )

    @staticmethod
    def _save_run(
        uow: RuntimeStoreUnitOfWork,
        run: OneShotScheduleRun,
        *,
        expected_revision: int,
    ) -> None:
        run.revision = int(expected_revision) + 1
        uow.cas_schedule_run(
            run.schedule_run_id,
            run.to_dict(),
            expected_revision=expected_revision,
        )

    @staticmethod
    def _save_run_payload(
        uow: RuntimeStoreUnitOfWork,
        run: OneShotScheduleRun,
        *,
        expected_revision: int,
    ) -> None:
        if int(run.revision) != int(expected_revision) + 1:
            raise StoreInvariantError(
                "schedule mutation must advance revision exactly once"
            )
        uow.cas_schedule_run(
            run.schedule_run_id,
            run.to_dict(),
            expected_revision=expected_revision,
        )

    @staticmethod
    def _block_run(
        run: OneShotScheduleRun,
        *,
        reason: str,
        now: str,
    ) -> None:
        run.status = ScheduleRunStatus.BLOCKED_ON_ACTIVATION
        run.claimed_activation_id = None
        run.blocked_reason = str(reason)
        run.updated_at = now

    @staticmethod
    def _cancel_run(
        run: OneShotScheduleRun,
        *,
        now: str,
    ) -> None:
        run.status = ScheduleRunStatus.CANCELLED
        run.claimed_activation_id = None
        run.blocked_reason = "transaction_deleted"
        if (
            run.schedule_delivery_id is not None
            and run.delivery_status != ScheduleDeliveryStatus.CONSUMED
        ):
            run.delivery_status = ScheduleDeliveryStatus.ABORTED
        run.cancelled_at = now
        run.updated_at = now

    @staticmethod
    def _claim_block_reason(
        uow: RuntimeStoreUnitOfWork,
        run: OneShotScheduleRun,
        record: TransactionRecord,
    ) -> Optional[str]:
        other_claimed = [
            item
            for item in uow.list_schedule_runs(
                transaction_id=record.transaction_id,
                status=ScheduleRunStatus.CLAIMED.value,
            )
            if str(item.get("schedule_run_id", "") or "")
            != run.schedule_run_id
        ]
        if other_claimed:
            return "another_claimed_run"
        if (
            record.state == TransactionState.PAUSE
            and record.pause_reason != PauseReason.SCHEDULED_WAIT
        ):
            return "pause"
        if record.current_activation_id is not None:
            return "active_activation"
        return None


__all__ = [
    "OneShotScheduleRun",
    "ScheduleDeliveryStatus",
    "ScheduleRunStatus",
    "ScheduleTransitionError",
    "ScheduleTransitionResult",
    "TransactionScheduleCoordinator",
    "stable_schedule_delivery_id",
    "stable_schedule_run_id",
]
