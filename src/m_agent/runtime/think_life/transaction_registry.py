"""Store-backed transaction registry for the Think-life runtime.

``SQLiteRuntimeStore`` is the durable authority.  The registry keeps a
same-process live cache only because the v1 Think-life runtime still mutates
some transaction-owned payload fields through object references.  Every
registry command persists through a Store unit of work and advances the
transaction revision exactly once.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
import uuid

from m_agent.api.chat_api_shared import _now_iso

from .contracts import (
    ActivationRecord,
    ActivationStatus,
    DelegateRecord,
    DelegateStatus,
    PauseReason,
    TransactionCorrelation,
    TransactionKind,
    TransactionLifecycle,
    TransactionRecord,
    TransactionState,
)
from .transaction.domain import (
    DomainTransitionError,
    archive_transaction,
    begin_delegate as begin_delegate_domain,
    complete_transaction,
    consume_delegate,
    delete_transaction,
    fail_transaction,
    open_activation,
    pause_transaction,
    restore_transaction,
)
from .transaction.predicates import (
    is_open_continue,
    is_runnable_record,
)
from .transaction.schedule import (
    OneShotScheduleRun,
    ScheduleDeliveryStatus,
    ScheduleRunStatus,
)
from .transaction.store import (
    RevisionConflictError,
    RuntimeStoreUnitOfWork,
    SQLiteRuntimeStore,
    StoreInvariantError,
)


class TransactionTransitionError(ValueError):
    """Raised when a registry command is not a legal domain transition."""


Mutation = Callable[[TransactionRecord], Dict[str, Any]]


# These fields were historically mutated directly through the object returned
# by ``create``/``get``.  When a legacy command follows such a mutation, carry
# the transaction-owned payload into the authoritative CAS write without
# allowing the live object to overwrite lifecycle or activation control data.
_LIVE_PAYLOAD_FIELDS: Tuple[str, ...] = (
    "priority",
    "correlation",
    "wm_entries",
    "task_state",
    "think_rounds",
    "last_error",
    "reply_finalized_in_activation",
)


def _required_id(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise TransactionTransitionError(f"{field_name} is required")
    return normalized


class TransactionRegistry:
    """Compatibility index over the durable P2 transaction Store."""

    def __init__(
        self,
        persist_path: Optional[Path | str] = None,
        *,
        store: Optional[SQLiteRuntimeStore] = None,
        default_runtime_engine: Optional[str] = None,
    ) -> None:
        if store is not None and persist_path is not None:
            raise ValueError("pass either persist_path or store, not both")
        self.store = store or SQLiteRuntimeStore(persist_path)
        self.persist_path = self.store.path
        self._default_runtime_engine = str(
            default_runtime_engine or ""
        ).strip() or None
        self._by_id: Dict[str, TransactionRecord] = {}
        self._active_by_conversation: Dict[str, str] = {}
        self._lock = RLock()
        self._rebuild_active_index()

    # ------------------------------------------------------------------
    # Cache and Store helpers
    # ------------------------------------------------------------------
    def _rebuild_active_index(self) -> None:
        """Recover the legacy active pointer from authoritative records."""

        self._active_by_conversation.clear()
        for record in self.store.list_transactions():
            if (
                record.kind == TransactionKind.USER_TASK
                and is_open_continue(record)
            ):
                self._active_by_conversation[
                    record.conversation_id
                ] = record.transaction_id

    def _cache_committed(
        self,
        record: TransactionRecord,
    ) -> TransactionRecord:
        """Publish a committed value while preserving existing references."""

        detached = TransactionRecord.from_dict(record.to_dict())
        current = self._by_id.get(detached.transaction_id)
        if current is None:
            self._by_id[detached.transaction_id] = detached
            return detached
        current.__dict__.clear()
        current.__dict__.update(detached.__dict__)
        return current

    def _visible_record(
        self,
        persisted: TransactionRecord,
    ) -> TransactionRecord:
        live = self._by_id.get(persisted.transaction_id)
        if live is None:
            return self._cache_committed(persisted)
        if persisted.deleted:
            return self._cache_committed(persisted)
        if persisted.revision > live.revision:
            return self._cache_committed(persisted)
        return live

    def _overlay_live_payload(
        self,
        record: TransactionRecord,
    ) -> TransactionRecord:
        live = self._by_id.get(record.transaction_id)
        if live is None or live.revision != record.revision:
            return record
        for field_name in _LIVE_PAYLOAD_FIELDS:
            setattr(
                record,
                field_name,
                deepcopy(getattr(live, field_name)),
            )
        return record

    @staticmethod
    def _check_expected_revision(
        record: TransactionRecord,
        expected_revision: Optional[int],
    ) -> None:
        if (
            expected_revision is not None
            and record.revision != int(expected_revision)
        ):
            raise RevisionConflictError(
                record.transaction_id,
                expected_revision=int(expected_revision),
                actual_revision=record.revision,
            )

    @staticmethod
    def _require_record(
        uow: RuntimeStoreUnitOfWork,
        transaction_id: str,
    ) -> TransactionRecord:
        record = uow.load_transaction(transaction_id)
        if record is None:
            raise TransactionTransitionError(
                f"unknown transaction: {transaction_id}"
            )
        return record

    @classmethod
    def _load_domain_bundle(
        cls,
        uow: RuntimeStoreUnitOfWork,
        transaction_id: str,
    ) -> Tuple[
        TransactionRecord,
        Optional[ActivationRecord],
        List[DelegateRecord],
    ]:
        record = cls._require_record(uow, transaction_id)
        activation: Optional[ActivationRecord] = None
        if record.current_activation_id:
            activation = uow.load_activation(
                record.current_activation_id
            )
            if activation is None:
                raise TransactionTransitionError(
                    "current activation is missing: "
                    f"{record.current_activation_id}"
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
    def _advance_and_cas(
        uow: RuntimeStoreUnitOfWork,
        record: TransactionRecord,
        *,
        now: str,
    ) -> int:
        previous_revision = int(record.revision)
        record.revision = previous_revision + 1
        record.updated_at = now
        uow.cas_transaction(
            record,
            expected_revision=previous_revision,
        )
        return record.revision

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

    def _sync_terminal_active_pointer(
        self,
        record: TransactionRecord,
    ) -> None:
        if record.kind != TransactionKind.USER_TASK:
            return
        if (
            not is_open_continue(record)
        ):
            if (
                self._active_by_conversation.get(
                    record.conversation_id
                )
                == record.transaction_id
            ):
                self._active_by_conversation.pop(
                    record.conversation_id,
                    None,
                )

    # ------------------------------------------------------------------
    # Legacy create/read/index façade
    # ------------------------------------------------------------------
    def create(
        self,
        *,
        thread_id: str,
        kind: TransactionKind,
        conversation_id: Optional[str] = None,
        priority: int = 50,
        correlation: Optional[TransactionCorrelation] = None,
        runtime_engine: Optional[str] = None,
    ) -> TransactionRecord:
        tid = str(thread_id or "").strip()
        cid = str(conversation_id or "").strip() or f"{tid}::0"
        if not tid:
            raise ValueError("thread_id is required")
        try:
            normalized_kind = (
                kind
                if isinstance(kind, TransactionKind)
                else TransactionKind(str(kind))
            )
        except ValueError as exc:
            raise ValueError(f"unsupported transaction kind: {kind!r}") from exc

        from m_agent.runtime.routing import resolve_runtime_engine

        engine = resolve_runtime_engine(
            explicit=runtime_engine or self._default_runtime_engine,
        )

        now = _now_iso()
        record = TransactionRecord(
            transaction_id=f"txn_{uuid.uuid4().hex}",
            thread_id=tid,
            conversation_id=cid,
            state=TransactionState.CONTINUE,
            lifecycle_status=TransactionLifecycle.ACTIVE,
            revision=0,
            priority=int(priority),
            kind=normalized_kind,
            correlation=correlation or TransactionCorrelation(),
            created_at=now,
            updated_at=now,
            runtime_engine=engine,
        )
        activation = open_activation(
            record,
            source="create",
            now=now,
        )
        record.revision = 1

        with self._lock:
            with self.store.unit_of_work() as uow:
                uow.create_transaction(record)
                uow.create_activation(activation)
            self._by_id[record.transaction_id] = record
            return record

    def get(
        self,
        transaction_id: str,
    ) -> Optional[TransactionRecord]:
        tx_id = str(transaction_id or "").strip()
        if not tx_id:
            return None
        with self._lock:
            persisted = self.store.load_transaction(tx_id)
            if persisted is None:
                return None
            return self._visible_record(persisted)

    def list(
        self,
        *,
        thread_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> List[TransactionRecord]:
        with self._lock:
            persisted = self.store.list_transactions(
                thread_id=thread_id,
                conversation_id=conversation_id,
            )
            return [self._visible_record(item) for item in persisted]

    def list_for_thread(
        self,
        thread_id: str,
    ) -> List[TransactionRecord]:
        return self.list(thread_id=str(thread_id or "").strip())

    def list_for_conversation(
        self,
        conversation_id: str,
    ) -> List[TransactionRecord]:
        return self.list(
            conversation_id=str(conversation_id or "").strip()
        )

    def refresh_from_store(
        self,
        transaction_id: Optional[str] = None,
    ) -> Optional[TransactionRecord] | List[TransactionRecord]:
        """Force the live cache to the current durable Store snapshot."""

        with self._lock:
            if transaction_id is not None:
                tx_id = str(transaction_id or "").strip()
                if not tx_id:
                    return None
                persisted = self.store.load_transaction(tx_id)
                if persisted is None:
                    self._by_id.pop(tx_id, None)
                    return None
                return self._cache_committed(persisted)
            return [
                self._cache_committed(item)
                for item in self.store.list_transactions()
            ]

    def get_active_user_transaction(
        self,
        conversation_id: str,
    ) -> Optional[TransactionRecord]:
        cid = str(conversation_id or "").strip()
        with self._lock:
            active_id = self._active_by_conversation.get(cid)
            if not active_id:
                return None
            record = self.get(active_id)
            if record is None or record.kind != TransactionKind.USER_TASK:
                return None
            if (
                not is_open_continue(record)
            ):
                return None
            return record

    def set_active_user_transaction(
        self,
        conversation_id: str,
        transaction_id: str,
    ) -> None:
        with self._lock:
            self._active_by_conversation[
                str(conversation_id or "").strip()
            ] = str(transaction_id or "").strip()

    def set_active(
        self,
        conversation_id: str,
        transaction_id: str,
    ) -> None:
        self.set_active_user_transaction(
            conversation_id,
            transaction_id,
        )

    def clear_active_user_transaction(
        self,
        conversation_id: str,
    ) -> None:
        with self._lock:
            self._active_by_conversation.pop(
                str(conversation_id or "").strip(),
                None,
            )

    clear_active = clear_active_user_transaction

    # ------------------------------------------------------------------
    # P2 domain commands
    # ------------------------------------------------------------------
    def begin_delegate(
        self,
        transaction_id: str,
        delegate_id: str,
        *,
        expected_revision: Optional[int] = None,
    ) -> TransactionRecord:
        tx_id = _required_id(transaction_id, "transaction_id")
        did = _required_id(delegate_id, "delegate_id")
        now = _now_iso()
        try:
            with self._lock:
                with self.store.unit_of_work() as uow:
                    record, activation, _delegates = (
                        self._load_domain_bundle(uow, tx_id)
                    )
                    self._overlay_live_payload(record)
                    self._check_expected_revision(
                        record,
                        expected_revision,
                    )
                    if activation is None:
                        raise TransactionTransitionError(
                            "cannot begin delegate without an activation"
                        )
                    if uow.load_delegate(did) is not None:
                        raise TransactionTransitionError(
                            f"delegate already exists: {did}"
                        )
                    if not is_runnable_record(record, activation):
                        raise TransactionTransitionError(
                            "cannot begin delegate unless transaction is runnable"
                        )
                    delegate = begin_delegate_domain(
                        record,
                        activation,
                        did,
                        now,
                    )
                    self._advance_and_cas(uow, record, now=now)
                    uow.create_delegate(delegate)
                return self._cache_committed(record)
        except DomainTransitionError as exc:
            raise TransactionTransitionError(str(exc)) from exc

    def mark_reply_finalized(
        self,
        transaction_id: str,
        *,
        expected_revision: Optional[int] = None,
    ) -> TransactionRecord:
        """Record that this activation delivered a finalized user-visible reply."""

        tx_id = _required_id(transaction_id, "transaction_id")
        now = _now_iso()
        with self._lock:
            with self.store.unit_of_work() as uow:
                record = uow.load_transaction(tx_id)
                if record is None:
                    raise TransactionTransitionError(
                        f"transaction not found: {tx_id}"
                    )
                self._overlay_live_payload(record)
                self._check_expected_revision(record, expected_revision)
                if (
                    record.lifecycle_status
                    != TransactionLifecycle.ACTIVE
                    or record.deleted_at is not None
                ):
                    raise TransactionTransitionError(
                        "deleted transaction is write-fenced: "
                        f"{record.transaction_id}"
                    )
                if record.state != TransactionState.CONTINUE:
                    return self._cache_committed(record)
                record.reply_finalized_in_activation = True
                record.updated_at = now
                self._advance_and_cas(uow, record, now=now)
                return self._cache_committed(record)

    def pause(
        self,
        transaction_id: str,
        *,
        reason: PauseReason = PauseReason.MANUAL_HOLD,
        expected_revision: Optional[int] = None,
    ) -> TransactionRecord:
        tx_id = _required_id(transaction_id, "transaction_id")
        now = _now_iso()
        try:
            with self._lock:
                with self.store.unit_of_work() as uow:
                    record, activation, delegates = (
                        self._load_domain_bundle(uow, tx_id)
                    )
                    self._overlay_live_payload(record)
                    self._check_expected_revision(
                        record,
                        expected_revision,
                    )
                    activation_status = (
                        activation.status if activation is not None else None
                    )
                    delegate_statuses = {
                        item.delegate_id: item.status
                        for item in delegates
                    }
                    pause_transaction(
                        record,
                        activation,
                        delegates,
                        reason=reason,
                        now=now,
                    )
                    revision = self._advance_and_cas(
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
                        to_revision=revision,
                    )
                visible = self._cache_committed(record)
                self._sync_terminal_active_pointer(visible)
                return visible
        except DomainTransitionError as exc:
            raise TransactionTransitionError(str(exc)) from exc

    def fail(
        self,
        transaction_id: str,
        *,
        error: str,
        expected_revision: Optional[int] = None,
    ) -> TransactionRecord:
        """Pause a failed transaction atomically and invalidate live work."""

        tx_id = _required_id(transaction_id, "transaction_id")
        now = _now_iso()
        try:
            with self._lock:
                with self.store.unit_of_work() as uow:
                    record, activation, delegates = self._load_domain_bundle(
                        uow,
                        tx_id,
                    )
                    self._overlay_live_payload(record)
                    self._check_expected_revision(record, expected_revision)
                    activation_status = (
                        activation.status if activation is not None else None
                    )
                    delegate_statuses = {
                        item.delegate_id: item.status for item in delegates
                    }
                    fail_transaction(
                        record,
                        activation,
                        delegates,
                        error=error,
                        now=now,
                    )
                    revision = self._advance_and_cas(uow, record, now=now)
                    self._save_existing_bundle(
                        uow,
                        activation=activation,
                        activation_status=activation_status,
                        delegates=delegates,
                        delegate_statuses=delegate_statuses,
                        to_revision=revision,
                    )
                visible = self._cache_committed(record)
                self._sync_terminal_active_pointer(visible)
                return visible
        except DomainTransitionError as exc:
            raise TransactionTransitionError(str(exc)) from exc

    def restore(
        self,
        transaction_id: str,
        *,
        source: str,
        expected_revision: Optional[int] = None,
    ) -> TransactionRecord:
        tx_id = _required_id(transaction_id, "transaction_id")
        now = _now_iso()
        try:
            with self._lock:
                with self.store.unit_of_work() as uow:
                    record, old_activation, delegates = (
                        self._load_domain_bundle(uow, tx_id)
                    )
                    self._overlay_live_payload(record)
                    self._check_expected_revision(
                        record,
                        expected_revision,
                    )
                    old_activation_status = (
                        old_activation.status
                        if old_activation is not None
                        else None
                    )
                    delegate_statuses = {
                        item.delegate_id: item.status
                        for item in delegates
                    }
                    mutation = restore_transaction(
                        record,
                        old_activation,
                        delegates,
                        source=source,
                        now=now,
                    )
                    revision = self._advance_and_cas(
                        uow,
                        record,
                        now=now,
                    )
                    self._save_existing_bundle(
                        uow,
                        activation=old_activation,
                        activation_status=old_activation_status,
                        delegates=delegates,
                        delegate_statuses=delegate_statuses,
                        to_revision=revision,
                    )
                    if mutation.activation is None:
                        raise StoreInvariantError(
                            "restore did not create an activation"
                        )
                    uow.create_activation(mutation.activation)
                visible = self._cache_committed(record)
                if visible.kind == TransactionKind.USER_TASK:
                    self._active_by_conversation[
                        visible.conversation_id
                    ] = visible.transaction_id
                return visible
        except DomainTransitionError as exc:
            raise TransactionTransitionError(str(exc)) from exc

    def delete(
        self,
        transaction_id: str,
        *,
        expected_revision: Optional[int] = None,
    ) -> TransactionRecord:
        tx_id = _required_id(transaction_id, "transaction_id")
        now = _now_iso()
        try:
            with self._lock:
                with self.store.unit_of_work() as uow:
                    record, activation, delegates = (
                        self._load_domain_bundle(uow, tx_id)
                    )
                    self._overlay_live_payload(record)
                    self._check_expected_revision(
                        record,
                        expected_revision,
                    )
                    activation_status = (
                        activation.status if activation is not None else None
                    )
                    delegate_statuses = {
                        item.delegate_id: item.status
                        for item in delegates
                    }
                    delete_transaction(
                        record,
                        activation,
                        delegates,
                        now=now,
                    )
                    revision = self._advance_and_cas(
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
                        to_revision=revision,
                    )
                visible = self._cache_committed(record)
                self._sync_terminal_active_pointer(visible)
                return visible
        except DomainTransitionError as exc:
            raise TransactionTransitionError(str(exc)) from exc

    def delete_with_cleanup(
        self,
        transaction_id: str,
        *,
        expected_revision: int,
        transition_id: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """Tombstone one transaction and terminalize its pending runtime work.

        The transaction, activation/delegate invalidation, transaction-side
        schedule cancellation, target stimulus disposition, and feedback
        outbox terminalization commit in one Store transaction.  CPU
        cancellation is deliberately handled by the runtime after this
        durable boundary because it is process-local state.

        A replay of ``transition_id`` returns the original result even though
        the supplied revision is now stale.  A fresh idempotency key against
        an existing tombstone is a no-op only when ``expected_revision`` still
        matches the tombstone revision.
        """

        tx_id = _required_id(transaction_id, "transaction_id")
        transition = _required_id(transition_id, "transition_id")
        request_key = _required_id(idempotency_key, "idempotency_key")
        expected = int(expected_revision)
        command = {
            "action": "delete_transaction",
            "transaction_id": tx_id,
            "expected_revision": expected,
            "idempotency_key": request_key,
        }

        def operation(
            uow: RuntimeStoreUnitOfWork,
        ) -> Mapping[str, Any]:
            record = self._require_record(uow, tx_id)
            self._check_expected_revision(record, expected)
            if (
                record.lifecycle_status == TransactionLifecycle.DELETED
                or record.deleted_at is not None
            ):
                return {
                    "success": True,
                    "outcome": "already_deleted",
                    "already_deleted": True,
                    "transaction": record.to_dict(),
                    "cleanup": {
                        "cancelled_schedule_run_ids": [],
                        "aborted_stimulus_ids": [],
                        "terminal_feedback_outbox_ids": [],
                    },
                }

            record, activation, delegates = self._load_domain_bundle(
                uow,
                tx_id,
            )
            self._overlay_live_payload(record)
            activation_status = (
                activation.status if activation is not None else None
            )
            delegate_statuses = {
                item.delegate_id: item.status for item in delegates
            }
            now = _now_iso()
            delete_transaction(
                record,
                activation,
                delegates,
                now=now,
            )
            revision = self._advance_and_cas(uow, record, now=now)
            self._save_existing_bundle(
                uow,
                activation=activation,
                activation_status=activation_status,
                delegates=delegates,
                delegate_statuses=delegate_statuses,
                to_revision=revision,
            )

            schedule_run_ids: List[str] = []
            schedule_delivery_ids: List[str] = []
            cancelled_schedule_run_ids: List[str] = []
            for payload in uow.list_schedule_runs(transaction_id=tx_id):
                run = OneShotScheduleRun.from_dict(payload)
                schedule_run_ids.append(run.schedule_run_id)
                if run.schedule_delivery_id:
                    schedule_delivery_ids.append(run.schedule_delivery_id)
                if run.status in {
                    ScheduleRunStatus.CONSUMED,
                    ScheduleRunStatus.CANCELLED,
                }:
                    continue
                before_revision = int(run.revision)
                run.status = ScheduleRunStatus.CANCELLED
                run.revision = before_revision + 1
                run.claimed_activation_id = None
                run.blocked_reason = "transaction_deleted"
                run.cancelled_at = now
                run.updated_at = now
                if (
                    run.delivery_status is not None
                    and run.delivery_status
                    != ScheduleDeliveryStatus.CONSUMED
                ):
                    run.delivery_status = ScheduleDeliveryStatus.ABORTED
                uow.cas_schedule_run(
                    run.schedule_run_id,
                    run.to_dict(),
                    expected_revision=before_revision,
                )
                cancelled_schedule_run_ids.append(run.schedule_run_id)

            activation_ids = {
                item.activation_id
                for item in uow.list_activations(tx_id)
            }
            all_delegate_ids = {
                item.delegate_id
                for item in uow.list_delegates(transaction_id=tx_id)
            }
            target_run_ids = set(schedule_run_ids)
            target_delivery_ids = set(schedule_delivery_ids)
            aborted_stimulus_ids: List[str] = []
            for stimulus in uow.list_stimuli(thread_id=record.thread_id):
                is_target = (
                    str(stimulus.transaction_id or "").strip() == tx_id
                    or str(stimulus.activation_id or "").strip()
                    in activation_ids
                    or str(stimulus.delegate_id or "").strip()
                    in all_delegate_ids
                    or str(stimulus.schedule_run_id or "").strip()
                    in target_run_ids
                    or str(stimulus.schedule_delivery_id or "").strip()
                    in target_delivery_ids
                )
                if not is_target or stimulus.disposition not in {
                    "ready",
                    "claimed",
                }:
                    continue
                uow.set_stimulus_disposition(
                    stimulus.stimulus_id,
                    disposition="aborted",
                    stage="transaction_delete",
                    reason="transaction_deleted",
                )
                aborted_stimulus_ids.append(stimulus.stimulus_id)

            terminal_outbox_ids: List[str] = []
            for outbox in uow.list_feedback_outbox(transaction_id=tx_id):
                if outbox["status"] in {"delivered", "terminal"}:
                    continue
                updated = uow.update_feedback_outbox(
                    str(outbox["outbox_id"]),
                    status="terminal",
                    discard_evidence="expected_discard:transaction_deleted",
                )
                terminal_outbox_ids.append(str(updated["outbox_id"]))

            return {
                "success": True,
                "outcome": "deleted",
                "already_deleted": False,
                "transaction": record.to_dict(),
                "cleanup": {
                    "cancelled_schedule_run_ids": cancelled_schedule_run_ids,
                    "aborted_stimulus_ids": aborted_stimulus_ids,
                    "terminal_feedback_outbox_ids": terminal_outbox_ids,
                },
            }

        try:
            with self._lock:
                execution = self.store.execute_transition(
                    transition_id=transition,
                    command=command,
                    operation=operation,
                )
                payload = dict(execution.result)
                persisted = self.store.load_transaction(tx_id)
                if persisted is not None:
                    visible = self._cache_committed(persisted)
                    self._sync_terminal_active_pointer(visible)
                    payload["transaction"] = visible.to_dict()
                payload["replayed"] = bool(execution.replayed)
                return payload
        except DomainTransitionError as exc:
            raise TransactionTransitionError(str(exc)) from exc

    def complete(
        self,
        transaction_id: str,
        *,
        expected_revision: Optional[int] = None,
    ) -> TransactionRecord:
        tx_id = _required_id(transaction_id, "transaction_id")
        now = _now_iso()
        try:
            with self._lock:
                with self.store.unit_of_work() as uow:
                    record, activation, delegates = (
                        self._load_domain_bundle(uow, tx_id)
                    )
                    self._overlay_live_payload(record)
                    self._check_expected_revision(
                        record,
                        expected_revision,
                    )
                    activation_status = (
                        activation.status if activation is not None else None
                    )
                    delegate_statuses = {
                        item.delegate_id: item.status
                        for item in delegates
                    }
                    complete_transaction(
                        record,
                        activation,
                        delegates,
                        now=now,
                    )
                    revision = self._advance_and_cas(
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
                        to_revision=revision,
                    )
                visible = self._cache_committed(record)
                self._sync_terminal_active_pointer(visible)
                return visible
        except DomainTransitionError as exc:
            raise TransactionTransitionError(str(exc)) from exc

    def archive_completed_for_flush(
        self,
        conversation_id: str,
        *,
        flush_id: Optional[str] = None,
    ) -> List[TransactionRecord]:
        cid = _required_id(conversation_id, "conversation_id")
        fid = (
            str(flush_id or "").strip()
            or f"flush_{uuid.uuid4().hex}"
        )
        command = {
            "action": "archive_completed_for_flush",
            "conversation_id": cid,
            "flush_id": fid,
        }
        transition_id = f"tx-flush-archive:{fid}"
        now = _now_iso()

        def operation(
            uow: RuntimeStoreUnitOfWork,
        ) -> Mapping[str, Any]:
            archived: List[Dict[str, Any]] = []
            for record in uow.list_transactions(conversation_id=cid):
                if (
                    record.lifecycle_status
                    != TransactionLifecycle.ACTIVE
                    or record.state != TransactionState.COMPLETE
                    or record.current_activation_id is not None
                    or record.active_delegate_id is not None
                ):
                    continue
                delegates = uow.list_delegates(
                    transaction_id=record.transaction_id
                )
                if any(
                    item.status == DelegateStatus.PENDING
                    for item in delegates
                ):
                    continue
                self._overlay_live_payload(record)
                archive_transaction(
                    record,
                    delegates,
                    now=now,
                    by_flush=True,
                )
                self._advance_and_cas(uow, record, now=now)
                archived.append(
                    {
                        "transaction_id": record.transaction_id,
                        "revision": record.revision,
                    }
                )
            return {
                "conversation_id": cid,
                "flush_id": fid,
                "archived": archived,
            }

        try:
            with self._lock:
                execution = self.store.execute_transition(
                    transition_id=transition_id,
                    command=command,
                    operation=operation,
                )
                records: List[TransactionRecord] = []
                for item in execution.result.get("archived", []):
                    transaction_id = str(
                        dict(item).get("transaction_id", "") or ""
                    )
                    persisted = self.store.load_transaction(
                        transaction_id
                    )
                    if persisted is None:
                        continue
                    visible = self._cache_committed(persisted)
                    self._sync_terminal_active_pointer(visible)
                    records.append(visible)
                return records
        except DomainTransitionError as exc:
            raise TransactionTransitionError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Feedback causal validation and consumption
    # ------------------------------------------------------------------
    def validate_feedback_source(
        self,
        transaction_id: str,
        activation_id: str,
        delegate_id: str,
    ) -> bool:
        tx_id = str(transaction_id or "").strip()
        aid = str(activation_id or "").strip()
        did = str(delegate_id or "").strip()
        if not tx_id or not aid or not did:
            return False
        with self.store.unit_of_work() as uow:
            record = uow.load_transaction(tx_id)
            activation = uow.load_activation(aid)
            delegate = uow.load_delegate(did)
            if record is None or activation is None or delegate is None:
                return False
            return (
                record.lifecycle_status == TransactionLifecycle.ACTIVE
                and record.deleted_at is None
                and record.state == TransactionState.CONTINUE
                and record.current_activation_id == aid
                and record.active_delegate_id == did
                and activation.transaction_id == tx_id
                and activation.status == ActivationStatus.ACTIVE
                and delegate.transaction_id == tx_id
                and delegate.activation_id == aid
                and delegate.status == DelegateStatus.PENDING
            )

    def consume_feedback(
        self,
        transaction_id: str,
        activation_id: str,
        delegate_id: str,
        *,
        expected_revision: Optional[int] = None,
    ) -> TransactionRecord:
        tx_id = _required_id(transaction_id, "transaction_id")
        aid = _required_id(activation_id, "activation_id")
        did = _required_id(delegate_id, "delegate_id")
        now = _now_iso()
        try:
            with self._lock:
                with self.store.unit_of_work() as uow:
                    record = self._require_record(uow, tx_id)
                    self._overlay_live_payload(record)
                    self._check_expected_revision(
                        record,
                        expected_revision,
                    )
                    activation = uow.load_activation(aid)
                    delegate = uow.load_delegate(did)
                    if activation is None:
                        raise TransactionTransitionError(
                            f"unknown activation: {aid}"
                        )
                    if delegate is None:
                        raise TransactionTransitionError(
                            f"unknown delegate: {did}"
                        )
                    activation_status = activation.status
                    delegate_status = delegate.status
                    consume_delegate(
                        record,
                        activation,
                        delegate,
                        now,
                    )
                    self._advance_and_cas(uow, record, now=now)
                    uow.save_activation(
                        activation,
                        expected_status=activation_status,
                    )
                    uow.save_delegate(
                        delegate,
                        expected_status=delegate_status,
                    )
                return self._cache_committed(record)
        except DomainTransitionError as exc:
            raise TransactionTransitionError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Revision-fenced snapshots and transition ledger
    # ------------------------------------------------------------------
    def checkout(
        self,
        transaction_id: str,
        expected_revision: Optional[int] = None,
    ) -> TransactionRecord:
        tx_id = _required_id(transaction_id, "transaction_id")
        record = self.store.load_transaction(tx_id)
        if record is None:
            raise TransactionTransitionError(
                f"unknown transaction: {tx_id}"
            )
        self._check_expected_revision(record, expected_revision)
        return TransactionRecord.from_dict(record.to_dict())

    def commit_snapshot(
        self,
        record: TransactionRecord,
        *,
        expected_revision: int,
    ) -> TransactionRecord:
        return self._save_snapshot(
            record,
            expected_revision=int(expected_revision),
            bump_revision=False,
        )

    def save(
        self,
        record: TransactionRecord,
        *,
        expected_revision: Optional[int] = None,
        bump_revision: bool = False,
    ) -> TransactionRecord:
        return self._save_snapshot(
            record,
            expected_revision=expected_revision,
            bump_revision=bump_revision,
        )

    def _save_snapshot(
        self,
        record: TransactionRecord,
        *,
        expected_revision: Optional[int],
        bump_revision: bool,
    ) -> TransactionRecord:
        if not isinstance(record, TransactionRecord):
            raise TypeError("record must be a TransactionRecord")
        tx_id = _required_id(record.transaction_id, "transaction_id")
        working = TransactionRecord.from_dict(record.to_dict())
        now = _now_iso()
        with self._lock:
            with self.store.unit_of_work() as uow:
                current = self._require_record(uow, tx_id)
                expected = (
                    int(expected_revision)
                    if expected_revision is not None
                    else int(current.revision)
                )
                self._check_expected_revision(current, expected)
                if bump_revision or working.revision == expected:
                    working.revision = expected + 1
                elif working.revision != expected + 1:
                    raise StoreInvariantError(
                        "snapshot revision must equal expected revision "
                        "or expected revision + 1"
                    )
                working.updated_at = now
                uow.cas_transaction(
                    working,
                    expected_revision=expected,
                )
            visible = self._cache_committed(working)
            self._sync_terminal_active_pointer(visible)
            return visible

    def apply_transition(
        self,
        *,
        transition_id: str,
        command: Mapping[str, Any],
        command_digest: str,
        transaction_id: Optional[str],
        expected_revision: Optional[int],
        mutate: Mutation,
    ) -> Dict[str, Any]:
        if not callable(mutate):
            raise TypeError("mutate must be callable")
        tx_id = str(transaction_id or "").strip() or None

        def operation(
            uow: RuntimeStoreUnitOfWork,
        ) -> Mapping[str, Any]:
            if tx_id is None:
                result = mutate(None)  # type: ignore[arg-type]
                if not isinstance(result, Mapping):
                    raise TypeError("mutate must return a mapping")
                return dict(result)
            record = self._require_record(uow, tx_id)
            if (
                record.lifecycle_status != TransactionLifecycle.ACTIVE
                or record.deleted_at is not None
            ):
                raise TransactionTransitionError(
                    "deleted transaction is write-fenced: "
                    f"{record.transaction_id}"
                )
            original_revision = int(record.revision)
            result = mutate(record)
            if not isinstance(result, Mapping):
                raise TypeError("mutate must return a mapping")
            record.revision = original_revision + 1
            record.updated_at = _now_iso()
            uow.cas_transaction(
                record,
                expected_revision=original_revision,
            )
            return dict(result)

        with self._lock:
            execution = self.store.execute_transition(
                transition_id=transition_id,
                command=dict(command),
                command_digest=command_digest,
                transaction_id=tx_id,
                expected_revision=expected_revision,
                operation=operation,
            )
            if tx_id is not None:
                persisted = self.store.load_transaction(tx_id)
                if persisted is not None:
                    visible = self._cache_committed(persisted)
                    self._sync_terminal_active_pointer(visible)
            return dict(execution.result)

    # ------------------------------------------------------------------
    # Counts, lookup, and lifecycle
    # ------------------------------------------------------------------
    def count_all(self) -> int:
        return len(self.store.list_transactions())

    count = count_all

    def find_by_delegate(
        self,
        delegate_id: str,
    ) -> Optional[TransactionRecord]:
        did = str(delegate_id or "").strip()
        if not did:
            return None
        for record in self.list():
            if record.active_delegate_id == did:
                return record
        return None

    find = find_by_delegate

    def close(self) -> None:
        self.store.close()


__all__ = [
    "TransactionRegistry",
    "TransactionTransitionError",
]
