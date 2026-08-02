"""Runtime-neutral Harness facade over the shared runtime components."""

from __future__ import annotations

import itertools
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Dict, Optional, Sequence

from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.runtime.config import RuntimeConfig
from m_agent.runtime.domain.contracts import (
    SceneEntry,
    StimulusEnvelope,
    TransactionKind,
    TransactionLifecycle,
    TransactionRecord,
    TransactionState,
)
from m_agent.runtime.perception.attributor import (
    SemanticResolver,
    TransactionAttributor,
)
from m_agent.runtime.perception.gateway import PerceptionGateway
from m_agent.runtime.perception.inbox import StimulusInbox
from m_agent.runtime.transaction import (
    EffectCoordinator,
    FlushCoordinator,
    IdempotencyConflictError,
    RuntimeUnitOfWork,
    SQLiteRuntimeStore,
    StaleClaimError,
    TransactionScheduleCoordinator,
)
from m_agent.runtime.transaction.registry import TransactionRegistry
from m_agent.systems.scene.default import SceneWriterAdapter
from m_agent.systems.scene.default.jsonl_store import SceneLogStore

from .base import HarnessResult
from .transaction_fixtures import (
    FixtureTransactionStatus as TransactionStatus,
    apply_fixture_status,
)


_KIND_BY_NAME = {
    item.value: item
    for item in StimulusKind
}


class SharedRuntimeHarness:
    """Expose current behavior through the P1 command/query vocabulary.

    Unsupported results are intentional evidence: P1 needs a complete executable
    gap matrix before P2 changes the Runtime.
    """

    runtime_id = ""
    _next_restart_persist_dir: Optional[Path] = None

    def __init__(
        self,
        *,
        semantic_resolver: Optional[SemanticResolver] = None,
        persist_dir: Optional[Path] = None,
    ) -> None:
        self.config = RuntimeConfig()
        self._semantic_resolver = semantic_resolver
        self._temporary_directory: Optional[TemporaryDirectory[str]] = None
        if persist_dir is None and self.__class__._next_restart_persist_dir is not None:
            self._temporary_directory = None
            self._persist_root = self.__class__._next_restart_persist_dir
            self.__class__._next_restart_persist_dir = None
        elif persist_dir is None:
            self._temporary_directory = TemporaryDirectory(
                prefix="m-agent-p2-harness-",
            )
            self._persist_root = Path(self._temporary_directory.name)
        else:
            self._persist_root = Path(persist_dir).resolve()
            self._persist_root.mkdir(parents=True, exist_ok=True)
        self._database_path = self._persist_root / "runtime.sqlite3"
        self._scene_dir = self._persist_root / "scene"
        self._closed = False
        self._uow_fault_point: Optional[str] = None
        self._open_runtime_components()
        self._ids = itertools.count(1)
        self._thread_by_conversation: Dict[str, str] = {}
        self._next_conversation_seq: Dict[str, int] = {}

    def _open_runtime_components(self) -> None:
        self.runtime_store = SQLiteRuntimeStore(self._database_path)
        self.registry = TransactionRegistry(store=self.runtime_store)
        self.uow = RuntimeUnitOfWork(self.registry)
        self.inbox = StimulusInbox(store=self.runtime_store)
        self.scene_store = SceneLogStore(
            runtime_store=self.runtime_store,
            persist_dir=self._scene_dir,
            persist_enabled=True,
        )
        self.scene_writer = SceneWriterAdapter(self.scene_store)
        self.flush_coordinator = FlushCoordinator(
            store=self.runtime_store,
            registry=self.registry,
        )
        self.effect_coordinator = EffectCoordinator(
            store=self.runtime_store,
            registry=self.registry,
        )
        self.schedule_coordinator = TransactionScheduleCoordinator(
            store=self.runtime_store,
        )
        self._wrap_registry_begin_delegate()
        self.attributor = TransactionAttributor(
            registry=self.registry,
            config=self.config,
            semantic_resolver=self._semantic_resolver,
            scene_writer=self.scene_writer,
        )
        self.gateway = PerceptionGateway(
            inbox=self.inbox,
            attributor=self.attributor,
            scene_writer=self.scene_writer,
        )

    def _wrap_registry_begin_delegate(self) -> None:
        registry = self.registry
        effect_coordinator = self.effect_coordinator
        original_begin_delegate = registry.begin_delegate

        def begin_delegate_with_effect(
            transaction_id: str,
            delegate_id: str,
            **kwargs: Any,
        ) -> TransactionRecord:
            record = original_begin_delegate(
                transaction_id,
                delegate_id,
                **kwargs,
            )
            activation_id = str(record.current_activation_id or "")
            if activation_id:
                effect_coordinator.record_intent_for_delegate(
                    transaction_id=record.transaction_id,
                    activation_id=activation_id,
                    delegate_id=delegate_id,
                )
            return record

        registry.begin_delegate = begin_delegate_with_effect  # type: ignore[method-assign]

    def _close_runtime_components(self) -> None:
        close_registry = getattr(self.registry, "close", None)
        if callable(close_registry):
            close_registry()
        self.runtime_store.close()

    def close(self) -> None:
        if self._closed:
            return
        self._close_runtime_components()
        self._closed = True
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None

    def __enter__(self) -> "SharedRuntimeHarness":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            # Destructors must stay best-effort during interpreter shutdown.
            pass

    @staticmethod
    def _ok(operation: str, **data: Any) -> HarnessResult:
        return HarnessResult(
            operation=operation,
            outcome="ok",
            supported=True,
            data=data,
        )

    @staticmethod
    def _unsupported(
        operation: str,
        reason: str,
        **data: Any,
    ) -> HarnessResult:
        return HarnessResult(
            operation=operation,
            outcome="unsupported",
            supported=False,
            reason=reason,
            data=data,
        )

    def create_conversation(self, *, thread_id: str) -> HarnessResult:
        thread = str(thread_id or "").strip()
        if not thread:
            return HarnessResult(
                operation="create_conversation",
                outcome="rejected",
                supported=True,
                reason="thread_id_required",
            )
        conversation_seq = self._next_conversation_seq.get(thread, 0)
        self._next_conversation_seq[thread] = conversation_seq + 1
        conversation_id = f"{thread}::{conversation_seq}"
        self._thread_by_conversation[conversation_id] = thread
        return self._ok(
            "create_conversation",
            thread_id=thread,
            conversation_id=conversation_id,
        )

    def make_stimulus(
        self,
        *,
        conversation_id: str,
        kind: str,
        text: str,
        payload: Optional[Dict[str, Any]] = None,
        source: Optional[Dict[str, str]] = None,
        priority_override: Optional[int] = None,
        stimulus_id: str = "",
        ingress_key: str = "",
        occurred_at: str = "2026-01-01T00:00:00Z",
    ) -> StimulusEnvelope:
        normalized_kind = str(kind or "").strip().lower()
        extension_kind = ""
        if normalized_kind == "registered_extension_fixture":
            extension_kind = normalized_kind
            normalized_kind = StimulusKind.OBSERVATION_TRIGGER.value
        if normalized_kind not in _KIND_BY_NAME:
            raise ValueError(f"unknown stimulus kind: {kind}")
        cid = str(conversation_id or "").strip()
        thread_id = self._thread_by_conversation.get(cid)
        if not thread_id:
            thread_id = cid.split("::", 1)[0]
            self._thread_by_conversation[cid] = thread_id
        source_data = dict(source or {})
        return StimulusEnvelope(
            stimulus_id=(
                str(stimulus_id or "").strip()
                or f"acceptance-stim-{next(self._ids)}"
            ),
            thread_id=thread_id,
            conversation_id=cid,
            stimulus=Stimulus(
                kind=_KIND_BY_NAME[normalized_kind],
                text=str(text or ""),
                payload={
                    **dict(payload or {}),
                    **(
                        {"extension_kind": extension_kind}
                        if extension_kind
                        else {}
                    ),
                },
            ),
            occurred_at=occurred_at,
            transaction_id=source_data.get("transaction_id"),
            activation_id=source_data.get("activation_id"),
            delegate_id=source_data.get("delegate_id"),
            schedule_id=source_data.get("schedule_id"),
            schedule_run_id=source_data.get("schedule_run_id"),
            schedule_delivery_id=source_data.get(
                "schedule_delivery_id"
            ),
            ingress_key=str(ingress_key or "").strip() or None,
            priority_override=priority_override,
        )

    def seed_transaction(
        self,
        *,
        conversation_id: str,
        fixture: Dict[str, Any],
    ) -> HarnessResult:
        target_state = str(fixture.get("state", "continue") or "continue")
        record = self.create_runtime_transaction(
            conversation_id=conversation_id,
            kind=TransactionKind(
                str(fixture.get("kind", TransactionKind.USER_TASK.value))
            ),
            status=TransactionStatus.RUNNING,
        )
        record.wm_entries.extend(
            list(fixture.get("wm_entries", []))
        )
        task_state = fixture.get("task_state", {})
        if isinstance(task_state, dict):
            record.task_state.goal = str(task_state.get("goal", "") or "")
            record.task_state.completed = [
                str(item)
                for item in task_state.get("completed", [])
            ]
            record.task_state.remaining = [
                str(item)
                for item in task_state.get("remaining", [])
            ]
        supported = True
        reason = ""
        try:
            if target_state == TransactionState.PAUSE.value:
                record = self.registry.pause(record.transaction_id)
            elif target_state == TransactionState.COMPLETE.value:
                record = self.registry.complete(record.transaction_id)
            elif target_state == TransactionState.ARCHIVE.value:
                self.registry.complete(record.transaction_id)
                self.flush_coordinator.trigger(
                    conversation_id=conversation_id,
                    flush_id=(
                        f"acceptance-seed-archive-"
                        f"{record.transaction_id}"
                    ),
                )
                record = (
                    self.registry.get(record.transaction_id)
                    or record
                )
            elif target_state == "deleted":
                record = self.registry.delete(record.transaction_id)
            elif target_state != TransactionState.CONTINUE.value:
                supported = False
                reason = f"unsupported target state: {target_state}"
        except (TypeError, ValueError) as exc:
            supported = False
            reason = str(exc)
        return HarnessResult(
            operation="seed_transaction",
            outcome="ok" if supported else "rejected",
            supported=supported,
            reason=reason,
            data={
                "transaction": self.transaction_snapshot(record),
                "requested_state": target_state,
                "activation_id": record.current_activation_id,
                "revision": record.revision,
            },
        )

    def seed_delegate(
        self,
        *,
        transaction_id: str,
        fixture: Dict[str, Any],
    ) -> HarnessResult:
        record = self.registry.get(transaction_id)
        if record is None:
            return HarnessResult(
                operation="seed_delegate",
                outcome="not_found",
                supported=True,
                reason="unknown_transaction",
            )
        delegate_id = str(
            fixture.get("delegate_id", f"acceptance-dlg-{next(self._ids)}")
        )
        try:
            delegated = self.registry.begin_delegate(
                record.transaction_id,
                delegate_id,
            )
        except (TypeError, ValueError) as exc:
            return HarnessResult(
                operation="seed_delegate",
                outcome="rejected",
                supported=True,
                reason=str(exc),
            )
        return self._ok(
            "seed_delegate",
            transaction_id=delegated.transaction_id,
            activation_id=delegated.current_activation_id,
            delegate_id=delegate_id,
            status="pending",
        )

    def submit_stimulus(
        self,
        *,
        conversation_id: str,
        kind: str,
        text: str,
        payload: Optional[Dict[str, Any]] = None,
        source: Optional[Dict[str, str]] = None,
        priority_override: Optional[int] = None,
        ingress_key: str = "",
    ) -> HarnessResult:
        try:
            stimulus = self.make_stimulus(
                conversation_id=conversation_id,
                kind=kind,
                text=text,
                payload=payload,
                source=source,
                priority_override=priority_override,
                ingress_key=ingress_key,
            )
            priority = self.attributor.priority_for(stimulus)
            self.gateway.submit(stimulus, schedule_drainer=False)
        except Exception as exc:
            return HarnessResult(
                operation="submit_stimulus",
                outcome="rejected",
                supported=True,
                reason=str(exc),
            )
        outcome = (
            "expected_discard"
            if stimulus.disposition == "expected_discard"
            else "ok"
        )
        return HarnessResult(
            operation="submit_stimulus",
            outcome=outcome,
            supported=True,
            reason=str(stimulus.disposition_reason or ""),
            data={
                "stimulus_id": stimulus.stimulus_id,
                "ingress_key": (
                    str(ingress_key) if ingress_key else None
                ),
                "ingress_key_persisted": bool(stimulus.ingress_key),
                "conversation_id": stimulus.conversation_id,
                "kind": stimulus.kind.value,
                "effective_priority": priority,
                "accepted_seq": stimulus.accepted_seq,
                "status": stimulus.disposition,
                "stage": stimulus.disposition_stage,
                "disposition_stage": stimulus.disposition_stage,
                "disposition_reason": stimulus.disposition_reason,
                "source": {
                    "transaction_id": stimulus.transaction_id,
                    "activation_id": stimulus.activation_id,
                    "delegate_id": stimulus.delegate_id,
                    "schedule_id": stimulus.schedule_id,
                    "schedule_run_id": stimulus.schedule_run_id,
                    "schedule_delivery_id": (
                        stimulus.schedule_delivery_id
                    ),
                },
            },
        )

    def list_ready_stimuli(self, *, conversation_id: str) -> HarnessResult:
        cid = str(conversation_id or "").strip()
        thread_id = self._thread_by_conversation.get(
            cid,
            cid.split("::", 1)[0],
        )
        items = self.runtime_store.list_ready_stimuli(
            conversation_id=cid,
        )
        return self._ok(
            "list_ready_stimuli",
            conversation_id=cid,
            thread_id=thread_id,
            ready_count=len(items),
            partition_key="conversation_id",
            stimuli=[self.stimulus_snapshot(item) for item in items],
        )

    def load_stimulus(self, *, stimulus_id: str) -> HarnessResult:
        sid = str(stimulus_id or "").strip()
        stimulus = self.runtime_store.load_stimulus(sid)
        if stimulus is None:
            return HarnessResult(
                operation="load_stimulus",
                outcome="not_found",
                supported=True,
                reason="unknown_stimulus",
                data={"stimulus_id": sid},
            )
        return self._ok(
            "load_stimulus",
            **self.stimulus_snapshot(stimulus),
        )

    def load_partition_state(
        self,
        *,
        conversation_id: str,
    ) -> HarnessResult:
        data = self.runtime_store.load_partition_state(conversation_id)
        return self._ok(
            "load_partition_state",
            conversation_id=str(data.get("conversation_id", "")),
            thread_id=str(data.get("thread_id", "")),
            consumer_owner=data.get("consumer_owner_id"),
            consumer_lease_until=data.get("consumer_lease_until"),
            consumer_epoch=int(data.get("consumer_epoch", 0) or 0),
            claim_epoch=int(data.get("claim_epoch", 0) or 0),
            next_accepted_seq=int(
                data.get("next_accepted_seq", 1) or 1
            ),
            worker_latch=data.get("worker_latch"),
            ready_count=int(data.get("ready_count", 0) or 0),
            claimed_count=int(data.get("claimed_count", 0) or 0),
        )

    def acquire_consumer(
        self,
        *,
        conversation_id: str,
        consumer_id: str,
        lease_seconds: float,
    ) -> HarnessResult:
        try:
            data = self.runtime_store.acquire_consumer(
                conversation_id=conversation_id,
                consumer_id=consumer_id,
                lease_seconds=lease_seconds,
            )
        except Exception as exc:
            return HarnessResult(
                operation="acquire_consumer",
                outcome="rejected",
                supported=True,
                reason=str(exc),
                data={
                    "conversation_id": conversation_id,
                    "consumer_id": consumer_id,
                },
            )
        return self._ok(
            "acquire_consumer",
            conversation_id=str(data.get("conversation_id", "")),
            consumer_id=consumer_id,
            consumer_owner=data.get("consumer_owner_id"),
            consumer_epoch=int(data.get("consumer_epoch", 0) or 0),
            consumer_lease_until=data.get("consumer_lease_until"),
            lease_seconds=lease_seconds,
        )

    def takeover_consumer(
        self,
        *,
        conversation_id: str,
        consumer_id: str,
    ) -> HarnessResult:
        try:
            data = self.runtime_store.takeover_consumer(
                conversation_id=conversation_id,
                consumer_id=consumer_id,
            )
        except Exception as exc:
            return HarnessResult(
                operation="takeover_consumer",
                outcome="rejected",
                supported=True,
                reason=str(exc),
                data={
                    "conversation_id": conversation_id,
                    "consumer_id": consumer_id,
                },
            )
        return self._ok(
            "takeover_consumer",
            conversation_id=str(data.get("conversation_id", "")),
            consumer_id=consumer_id,
            consumer_owner=data.get("consumer_owner_id"),
            consumer_epoch=int(data.get("consumer_epoch", 0) or 0),
            claim_epoch=int(data.get("claim_epoch", 0) or 0),
        )

    def claim_next_stimulus(
        self,
        *,
        conversation_id: str,
        consumer_id: str,
    ) -> HarnessResult:
        cid = str(conversation_id or "").strip()
        try:
            stimulus = self.runtime_store.claim_next_stimulus(
                conversation_id=cid,
                consumer_id=consumer_id,
            )
        except Exception as exc:
            return HarnessResult(
                operation="claim_next_stimulus",
                outcome="rejected",
                supported=True,
                reason=str(exc),
                data={
                    "conversation_id": cid,
                    "consumer_id": consumer_id,
                },
            )
        if stimulus is None:
            return HarnessResult(
                operation="claim_next_stimulus",
                outcome="empty",
                supported=True,
                data={"conversation_id": cid, "consumer_id": consumer_id},
            )
        partition = self.runtime_store.load_partition_state(cid)
        return self._ok(
            "claim_next_stimulus",
            stimulus_id=stimulus.stimulus_id,
            requested_conversation_id=cid,
            actual_conversation_id=stimulus.conversation_id,
            transaction_id=stimulus.transaction_id,
            consumer_id=consumer_id,
            consumer_owner=partition.get("consumer_owner_id"),
            consumer_epoch=stimulus.consumer_epoch,
            claim_epoch=stimulus.claim_epoch,
            claim_token={
                "claimed_by": stimulus.claimed_by,
                "consumer_epoch": stimulus.consumer_epoch,
                "claim_epoch": stimulus.claim_epoch,
            },
            accepted_seq=stimulus.accepted_seq,
            durable_claim=True,
        )

    def finalize_stimulus(
        self,
        *,
        stimulus_id: str,
        claim_token: Dict[str, Any],
        disposition: str,
        transition_id: str,
        command_digest: str,
    ) -> HarnessResult:
        if self.runtime_store.load_stimulus(stimulus_id) is not None:
            try:
                data = self.runtime_store.finalize_stimulus(
                    stimulus_id=stimulus_id,
                    claim_token=claim_token,
                    disposition=disposition,
                    transition_id=transition_id,
                    command_digest=command_digest,
                )
            except StaleClaimError as exc:
                return HarnessResult(
                    operation="finalize_stimulus",
                    outcome="stale_claim",
                    supported=True,
                    reason=str(exc),
                    data={
                        "stimulus_id": stimulus_id,
                        "claim_token": dict(claim_token or {}),
                        "disposition": disposition,
                        "transition_id": transition_id,
                    },
                )
            except IdempotencyConflictError as exc:
                return HarnessResult(
                    operation="finalize_stimulus",
                    outcome="idempotency_conflict",
                    supported=True,
                    reason=str(exc),
                    data={
                        "stimulus_id": stimulus_id,
                        "claim_token": dict(claim_token or {}),
                        "disposition": disposition,
                        "transition_id": transition_id,
                    },
                )
            except Exception as exc:
                return HarnessResult(
                    operation="finalize_stimulus",
                    outcome="rejected",
                    supported=True,
                    reason=str(exc),
                    data={
                        "stimulus_id": stimulus_id,
                        "claim_token": dict(claim_token or {}),
                        "disposition": disposition,
                        "transition_id": transition_id,
                    },
                )
            self._uow_fault_point = None
            return HarnessResult(
                operation="finalize_stimulus",
                outcome="ok",
                supported=True,
                data=dict(data),
            )
        stable_result = {
            "stimulus_id": str(stimulus_id or "").strip(),
            "claim_token": dict(claim_token or {}),
            "disposition": str(disposition or "").strip(),
            "transition_id": str(transition_id or "").strip(),
            "command_digest": str(command_digest or "").strip(),
            "status": "finalized",
        }
        command = {
            "operation": "finalize_stimulus",
            **stable_result,
        }

        def commit_without_transaction(
            _record: Optional[TransactionRecord],
        ) -> Dict[str, Any]:
            return dict(stable_result)

        try:
            data = self.uow.apply_transition(
                stable_result["transition_id"],
                command,
                None,
                None,
                commit_without_transaction,
            )
        except IdempotencyConflictError as exc:
            return HarnessResult(
                operation="finalize_stimulus",
                outcome="idempotency_conflict",
                supported=True,
                reason=str(exc),
                data=dict(stable_result),
            )
        self._uow_fault_point = None
        return HarnessResult(
            operation="finalize_stimulus",
            outcome="ok",
            supported=True,
            data=dict(data),
        )

    def process_next_stimulus(
        self,
        *,
        conversation_id: str,
    ) -> HarnessResult:
        claimed = self.claim_next_stimulus(
            conversation_id=conversation_id,
            consumer_id="acceptance-consumer",
        )
        if claimed.outcome == "empty":
            return claimed
        stimulus_id = str(claimed.data.get("stimulus_id", ""))
        return self._unsupported(
            "process_next_stimulus",
            "The current public loop couples attribution, Thinking and synchronous tool execution; the Harness cannot stop at a durable commit boundary.",
            stimulus_id=stimulus_id,
            claim=claimed.to_dict(),
        )

    def create_runtime_transaction(
        self,
        *,
        conversation_id: str,
        kind: TransactionKind = TransactionKind.USER_TASK,
        status: TransactionStatus = TransactionStatus.PENDING,
    ) -> TransactionRecord:
        cid = str(conversation_id or "").strip()
        thread_id = self._thread_by_conversation.get(
            cid,
            cid.split("::", 1)[0],
        )
        record = self.registry.create(
            thread_id=thread_id,
            conversation_id=cid,
            kind=kind,
        )
        return apply_fixture_status(self.registry, record, status)

    @staticmethod
    def transaction_snapshot(record: TransactionRecord) -> Dict[str, Any]:
        return {
            "transaction_id": record.transaction_id,
            "conversation_id": record.conversation_id,
            "kind": record.kind.value,
            "state": record.state.value,
            "lifecycle_status": record.lifecycle_status.value,
            "revision": int(record.revision),
            "current_activation_id": record.current_activation_id,
            "active_delegate_id": record.active_delegate_id,
            "wm_entries": list(record.wm_entries),
            "task_state": record.task_state.to_dict(),
            "deleted": (
                record.lifecycle_status
                == TransactionLifecycle.DELETED
            ),
        }

    @staticmethod
    def stimulus_snapshot(
        stimulus: StimulusEnvelope,
    ) -> Dict[str, Any]:
        return {
            "stimulus_id": stimulus.stimulus_id,
            "thread_id": stimulus.thread_id,
            "conversation_id": stimulus.conversation_id,
            "kind": stimulus.kind.value,
            "text": stimulus.text,
            "payload": dict(stimulus.payload),
            "occurred_at": stimulus.occurred_at,
            "transaction_id": stimulus.transaction_id,
            "activation_id": stimulus.activation_id,
            "delegate_id": stimulus.delegate_id,
            "schedule_id": stimulus.schedule_id,
            "schedule_run_id": stimulus.schedule_run_id,
            "schedule_delivery_id": stimulus.schedule_delivery_id,
            "ingress_key": stimulus.ingress_key,
            "priority_override": stimulus.priority_override,
            "effective_priority": stimulus.effective_priority,
            "accepted_seq": stimulus.accepted_seq,
            "accepted_at": stimulus.accepted_at,
            "status": stimulus.disposition,
            "disposition": stimulus.disposition,
            "disposition_stage": stimulus.disposition_stage,
            "disposition_reason": stimulus.disposition_reason,
            "claimed_by": stimulus.claimed_by,
            "consumer_epoch": stimulus.consumer_epoch,
            "claim_epoch": stimulus.claim_epoch,
            "claimed_at": stimulus.claimed_at,
            "finalized_at": stimulus.finalized_at,
            "worker_latch": stimulus.worker_latch,
        }

    def load_transaction(self, *, transaction_id: str) -> HarnessResult:
        record = self.registry.get(transaction_id)
        if record is None:
            return HarnessResult(
                operation="load_transaction",
                outcome="not_found",
                supported=True,
                reason="unknown_transaction",
            )
        return self._ok(
            "load_transaction",
            transaction=self.transaction_snapshot(record),
        )

    def list_match_candidates(self, *, conversation_id: str) -> HarnessResult:
        candidates = self.attributor.list_match_candidates(conversation_id)
        return self._ok(
            "list_match_candidates",
            conversation_id=conversation_id,
            candidate_ids=[item.transaction_id for item in candidates],
            candidate_states=[item.state.value for item in candidates],
            target_state_filter_available=True,
        )

    def control_transaction(
        self,
        *,
        transaction_id: str,
        action: str,
    ) -> HarnessResult:
        tx_id = str(transaction_id or "").strip()
        normalized_action = str(action or "").strip().lower()
        record = self.registry.get(tx_id)
        if record is None:
            return HarnessResult(
                operation="control_transaction",
                outcome="not_found",
                supported=True,
                reason="unknown_transaction",
                data={
                    "transaction_id": tx_id,
                    "action": normalized_action,
                },
            )
        schedule_data: Dict[str, Any] = {}
        outbox_data: Dict[str, Any] = {}
        if normalized_action == TransactionState.ARCHIVE.value:
            return HarnessResult(
                operation="control_transaction",
                outcome="rejected",
                supported=True,
                reason="archive_requires_flush",
                data={
                    **self.transaction_snapshot(record),
                    "action": normalized_action,
                    "flush_boundary_created": False,
                },
            )
        try:
            if normalized_action == TransactionState.PAUSE.value:
                record = self.registry.pause(tx_id)
                schedule_data = self._pause_schedule_runs_for_transaction(
                    tx_id,
                    record,
                )
                outbox_data = (
                    self.effect_coordinator.acknowledge_stale_on_pause(
                        transaction_id=tx_id,
                    )
                )
            elif normalized_action in {
                "delete",
                "deleted",
            }:
                record = self.registry.delete(tx_id)
            elif normalized_action == TransactionState.COMPLETE.value:
                record = self.registry.complete(tx_id)
            else:
                return HarnessResult(
                    operation="control_transaction",
                    outcome="rejected",
                    supported=True,
                    reason="unknown_action",
                    data={
                        **self.transaction_snapshot(record),
                        "action": normalized_action,
                    },
                )
        except (TypeError, ValueError) as exc:
            return HarnessResult(
                operation="control_transaction",
                outcome="rejected",
                supported=True,
                reason=str(exc),
                data={
                    **self.transaction_snapshot(
                        self.registry.get(tx_id) or record
                    ),
                    "action": normalized_action,
                },
            )
        data = {
            **self.transaction_snapshot(record),
            "action": normalized_action,
            "flush_boundary_created": False,
        }
        if normalized_action == TransactionState.PAUSE.value:
            data.update(schedule_data)
            data.update(outbox_data)
        if normalized_action in {
            TransactionState.PAUSE.value,
            "delete",
            "deleted",
        }:
            data["stimulus_disposition"] = "aborted"
        return HarnessResult(
            operation="control_transaction",
            outcome="ok",
            supported=True,
            data=data,
        )

    def restore_transaction(
        self,
        *,
        transaction_id: str,
        source: str,
    ) -> HarnessResult:
        tx_id = str(transaction_id or "").strip()
        restore_source = str(source or "").strip() or "manual"
        if self.registry.get(tx_id) is None:
            return HarnessResult(
                operation="restore_transaction",
                outcome="not_found",
                supported=True,
                reason="unknown_transaction",
                data={
                    "transaction_id": tx_id,
                    "source": restore_source,
                    "created_new": False,
                    "matcher_called": False,
                },
            )
        try:
            record = self.registry.restore(
                tx_id,
                source=restore_source,
            )
        except (TypeError, ValueError) as exc:
            current = self.registry.get(tx_id)
            return HarnessResult(
                operation="restore_transaction",
                outcome="rejected",
                supported=True,
                reason=str(exc),
                data={
                    **(
                        self.transaction_snapshot(current)
                        if current is not None
                        else {"transaction_id": tx_id}
                    ),
                    "source": restore_source,
                    "created_new": False,
                    "matcher_called": False,
                },
            )
        return HarnessResult(
            operation="restore_transaction",
            outcome="ok",
            supported=True,
            data={
                **self.transaction_snapshot(record),
                "activation_id": record.current_activation_id,
                "source": restore_source,
                "created_new": False,
                "matcher_called": False,
            },
        )

    def append_scene(
        self,
        conversation_id: str,
        entry: SceneEntry,
    ) -> SceneEntry:
        return self.scene_store.append(conversation_id, entry)

    def read_scene(
        self,
        *,
        conversation_id: str,
        after_seq: int = 0,
    ) -> HarnessResult:
        entries = [
            item.to_dict()
            for item in self.scene_store.tail(conversation_id)
            if item.seq > int(after_seq)
        ]
        return self._ok(
            "read_scene",
            conversation_id=conversation_id,
            entries=entries,
            current_seq=max(
                [int(item["seq"]) for item in entries],
                default=int(after_seq),
            ),
            flush_watermark=self.scene_store.flush_watermark(
                conversation_id
            ),
        )

    def load_schedule(self, *, schedule_run_id: str) -> HarnessResult:
        run_id = str(schedule_run_id or "").strip()
        run = self.schedule_coordinator.load(run_id)
        if run is None:
            return HarnessResult(
                operation="load_schedule",
                outcome="not_found",
                supported=True,
                reason="unknown_schedule_run",
                data={"schedule_run_id": run_id},
            )
        return self._ok("load_schedule", **run.to_dict())

    def load_effects(self, *, transaction_id: str) -> HarnessResult:
        tx_id = str(transaction_id or "").strip()
        data = self.effect_coordinator.load_for_transaction(tx_id)
        return self._ok(
            "load_effects",
            transaction_id=tx_id,
            **data,
        )

    def _pause_schedule_runs_for_transaction(
        self,
        transaction_id: str,
        record: TransactionRecord,
    ) -> Dict[str, Any]:
        runs = self.schedule_coordinator.list_for_transaction(
            transaction_id,
        )
        primary: Dict[str, Any] = {}
        for run in runs:
            if run.status.value in {
                "consumed",
                "cancelled",
            }:
                continue
            try:
                result = self.schedule_coordinator.manual_pause(
                    run.schedule_run_id,
                    expected_run_revision=int(run.revision),
                    expected_transaction_revision=int(record.revision),
                )
            except Exception:
                continue
            primary = {
                "run_status": result.run.status.value,
                "blocked_reason": result.run.blocked_reason,
                "schedule_run_id": result.run.schedule_run_id,
            }
            record = result.transaction
        return primary

    def trigger_flush(
        self,
        *,
        conversation_id: str,
        flush_id: str,
    ) -> HarnessResult:
        try:
            data = self.flush_coordinator.trigger(
                conversation_id=conversation_id,
                flush_id=flush_id,
            )
        except (TypeError, ValueError) as exc:
            return HarnessResult(
                operation="trigger_flush",
                outcome="rejected",
                supported=True,
                reason=str(exc),
                data={
                    "conversation_id": str(
                        conversation_id or ""
                    ).strip(),
                    "flush_id": str(flush_id or "").strip(),
                    "current_watermark": (
                        self.scene_store.flush_watermark(
                            conversation_id
                        )
                    ),
                },
            )
        return HarnessResult(
            operation="trigger_flush",
            outcome="ok",
            supported=True,
            data=dict(data),
        )

    def inject_fault(self, *, fault_point: str) -> HarnessResult:
        point = str(fault_point or "").strip()
        if point in {
            "after_transaction_commit_before_response",
            "after_uow_commit_before_response",
        }:
            self._uow_fault_point = point
            return self._ok(
                "inject_fault",
                fault_point=point,
                armed=True,
                scope="runtime_uow",
            )
        if point == "after_flush_commit_before_materialize":
            return self._ok(
                "inject_fault",
                **self.flush_coordinator.inject_fault(point),
            )
        if point == "after_schedule_claim_before_activation":
            return self._ok(
                "inject_fault",
                **self.schedule_coordinator.inject_fault(point),
            )
        if point == "after_effect_result_before_feedback_relay":
            return self._ok(
                "inject_fault",
                **self.effect_coordinator.inject_fault(point),
            )
        return self._unsupported(
            "inject_fault",
            "The requested deterministic fault point is not part of the P7 transaction/Flush/Schedule/Effect boundary.",
            fault_point=point,
        )

    def set_worker_latch(
        self,
        *,
        conversation_id: str,
        phase: str,
    ) -> HarnessResult:
        data = self.runtime_store.set_worker_latch(
            conversation_id=conversation_id,
            phase=phase,
        )
        return self._ok(
            "set_worker_latch",
            conversation_id=str(data.get("conversation_id", "")),
            phase=phase,
            worker_latch=data.get("worker_latch"),
            consumer_owner=data.get("consumer_owner_id"),
            consumer_epoch=int(data.get("consumer_epoch", 0) or 0),
        )

    def wait_for_disposition(
        self,
        *,
        stimulus_id: str,
        timeout_seconds: float,
    ) -> HarnessResult:
        sid = str(stimulus_id or "").strip()
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        stimulus = self.runtime_store.load_stimulus(sid)
        while (
            stimulus is not None
            and stimulus.disposition in {"ready", "claimed", "new"}
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
            stimulus = self.runtime_store.load_stimulus(sid)
        if stimulus is None:
            return HarnessResult(
                operation="wait_for_disposition",
                outcome="not_found",
                supported=True,
                reason="unknown_stimulus",
                data={
                    "stimulus_id": sid,
                    "timeout_seconds": timeout_seconds,
                },
            )
        outcome = (
            "ok"
            if stimulus.disposition not in {"ready", "claimed", "new"}
            else "timeout"
        )
        return HarnessResult(
            operation="wait_for_disposition",
            outcome=outcome,
            supported=True,
            data={
                **self.stimulus_snapshot(stimulus),
                "timeout_seconds": timeout_seconds,
            },
        )

    def control_effect_sink(
        self,
        *,
        action: str,
        effect_id: str = "",
    ) -> HarnessResult:
        normalized = str(action or "").strip().lower()
        eid = str(effect_id or "").strip()
        if normalized == "relay_feedback":
            if not eid:
                return HarnessResult(
                    operation="control_effect_sink",
                    outcome="rejected",
                    supported=True,
                    reason="effect_id_required",
                    data={"action": normalized},
                )
            try:
                self.effect_coordinator.commit_result_with_outbox(
                    effect_id=eid,
                )
            except Exception:
                pass
            data = self.effect_coordinator.relay_feedback(
                effect_id=eid,
            )
            return self._ok(
                "control_effect_sink",
                action=normalized,
                effect_id=eid,
                **data,
            )
        if normalized.startswith("exercise_"):
            guarantee = normalized[len("exercise_") :]
            data = self.effect_coordinator.exercise_guarantee(
                guarantee=guarantee,
                effect_id=eid or f"tx-07-{guarantee}",
            )
            payload = dict(data)
            payload.pop("effect_id", None)
            resolved_effect_id = eid or data.get("effect_id")
            return self._ok(
                "control_effect_sink",
                action=normalized,
                effect_id=resolved_effect_id,
                **payload,
            )
        if normalized not in {
            "install",
            "release",
            "fail",
            "reset",
            "status",
        }:
            return HarnessResult(
                operation="control_effect_sink",
                outcome="rejected",
                supported=True,
                reason="unknown_effect_sink_action",
                data={"action": normalized, "effect_id": effect_id},
            )
        return self._ok(
            "control_effect_sink",
            action=normalized,
            effect_id=eid or None,
            installed=normalized == "install",
            controllable=True,
        )

    def restart_runtime(self) -> HarnessResult:
        try:
            self._close_runtime_components()
            self._open_runtime_components()
            self.__class__._next_restart_persist_dir = self._persist_root
            recovery = (
                self.flush_coordinator.recover_materializations()
            )
            effect_recovery = (
                self.effect_coordinator.recover_pending_relays()
            )
            recovery = {**recovery, **effect_recovery}
        except Exception as exc:
            return HarnessResult(
                operation="restart_runtime",
                outcome="error",
                supported=True,
                reason=str(exc),
                data={"database_path": str(self._database_path)},
            )
        return self._ok(
            "restart_runtime",
            database_path=str(self._database_path),
            transaction_count=self.registry.count_all(),
            **recovery,
        )

    def evaluate_matcher(
        self,
        *,
        samples: Sequence[Dict[str, Any]],
    ) -> HarnessResult:
        from m_agent.acceptance.matcher_evaluation import (
            evaluate_matcher_predictions,
            run_matcher_v1_offline,
        )

        runs = run_matcher_v1_offline(samples, runs=3)
        metrics = evaluate_matcher_predictions(samples, runs)
        return self._ok(
            "evaluate_matcher",
            sample_count=metrics["sample_count"],
            run_count=metrics["run_count"],
            runs=runs,
            structured_output_valid_rate=metrics[
                "structured_output_valid_rate"
            ],
            total_accuracy=metrics["total_accuracy"],
            reuse_recall=metrics["reuse_recall"],
            wrong_candidate_count=metrics["wrong_candidate_count"],
            false_reuse_count=metrics["false_reuse_count"],
            external_reference_count=metrics[
                "external_reference_count"
            ],
            decision_stability_rate=metrics[
                "decision_stability_rate"
            ],
            matcher_mode="offline_policy_v1",
        )

    def resolve(
        self,
        stimulus: StimulusEnvelope,
    ) -> tuple[TransactionRecord, bool]:
        return self.attributor.resolve(stimulus)
