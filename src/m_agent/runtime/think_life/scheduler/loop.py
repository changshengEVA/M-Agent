"""Think-life CPU loop: inbox -> attribute -> think -> work -> feedback."""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional

from m_agent.api.chat_api_shared import _now_iso
from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS
from m_agent.layers.execution.core import ExecutionAgent
from m_agent.layers.execution.errors import ExecutionCancelledError
from m_agent.layers.perception.contracts import PerceptionInput
from m_agent.layers.thinking.contracts import (
    TASK_COMPLETION_AWAITING_USER,
    ThinkingDecision,
    is_execute_mode,
    is_reply_mode,
    is_silent_mode,
    normalize_task_completion_status,
    request_is_complete,
)
from m_agent.layers.thinking.core import ThinkingAgent
from m_agent.runtime.think_life.config import ThinkLifeConfig
from m_agent.runtime.think_life.contracts import (
    PauseReason,
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    StimulusKind,
    TransactionKind,
    TransactionRecord,
    TransactionState,
)
from m_agent.runtime.think_life.scheduler.awaiting_user_pause import (
    pause_for_user_collaboration,
)
from m_agent.runtime.think_life.perception.attributor import TransactionAttributor
from m_agent.runtime.think_life.perception.gateway import PerceptionGateway
from m_agent.runtime.think_life.perception.inbox import StimulusInbox
from m_agent.runtime.think_life.perception.matcher_scene_view import (
    is_user_visible_scene_interaction,
)
from m_agent.runtime.think_life.scheduler.cpu_state import THREAD_CPU_STATE
from m_agent.runtime.think_life.scheduler.delegate import (
    DelegateTarget,
    plan_delegate_target,
    resolve_delegate_tool_input,
)
from m_agent.runtime.think_life.scheduler.execution_feedback import (
    augment_perception_with_nudge,
    build_completion_nudge_message,
    build_param_gap_tool_history,
    feedback_summary_from_tool_history,
    param_gap_summary,
    premature_reply_block_reason,
)
from m_agent.runtime.think_life.scheduler.schedule_lifecycle import ScheduleLifecycleHook
from m_agent.runtime.think_life.scheduler.think_context import (
    build_perception_for_stimulus,
    latest_user_utterance_from_scene,
    read_scene_segment,
)
from m_agent.runtime.think_life.transaction_registry import (
    TransactionRegistry,
    TransactionTransitionError,
)
from m_agent.runtime.think_life.transaction.store import (
    RevisionConflictError,
    StaleClaimError,
)
from m_agent.runtime.think_life.transaction.uow import RuntimeUnitOfWork
from m_agent.runtime.think_life.transaction.predicates import (
    is_open_continue,
    is_runnable_record,
)
from m_agent.systems.scene.protocols import SceneReader, SceneWriter
from m_agent.systems.wm import WMSystem

logger = logging.getLogger(__name__)

ReplyCallback = Callable[[str, str, str, bool], None]
ThinkingEventEmitter = Callable[[str, Dict[str, Any]], None]
HistoryProvider = Callable[[str], Optional[List[Dict[str, Any]]]]

_MAX_COMPLETION_GATE_NUDGES = 2


class _TransactionFencedSceneWriter:
    """Suppress tool-owned Scene appends after a transaction tombstone."""

    def __init__(
        self,
        *,
        registry: TransactionRegistry,
        transaction_id: str,
        inner: SceneWriter,
    ) -> None:
        self._registry = registry
        self._transaction_id = str(transaction_id or "").strip()
        self._inner = inner

    def append(
        self,
        conversation_id: str,
        entry: SceneEntry,
        *,
        append_id: Optional[str] = None,
    ) -> SceneEntry:
        current = self._registry.store.load_transaction(
            self._transaction_id
        )
        if current is not None and current.deleted:
            return entry
        if append_id is None:
            return self._inner.append(conversation_id, entry)
        return self._inner.append(
            conversation_id,
            entry,
            append_id=append_id,
        )


class ThinkLifeLoop:
    def __init__(
        self,
        *,
        config: ThinkLifeConfig,
        registry: TransactionRegistry,
        inbox: StimulusInbox,
        attributor: TransactionAttributor,
        gateway: PerceptionGateway,
        thinking_agent: ThinkingAgent,
        execution_agent: ExecutionAgent,
        wm_system: WMSystem,
        scene_writer: SceneWriter,
        scene_reader: SceneReader,
        on_reply: Optional[ReplyCallback] = None,
        event_emitter: Optional[ThinkingEventEmitter] = None,
        schedule_lifecycle: ScheduleLifecycleHook = None,
        on_runtime_updated: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.inbox = inbox
        self.attributor = attributor
        self.gateway = gateway
        self.thinking_agent = thinking_agent
        self.execution_agent = execution_agent
        self.wm_system = wm_system
        self.scene_writer = scene_writer
        self.scene_reader = scene_reader
        self._on_reply = on_reply
        self._event_emitter = event_emitter
        self._schedule_lifecycle = schedule_lifecycle
        self._on_runtime_updated = on_runtime_updated

    def drain_thread(
        self,
        thread_id: str,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        history_provider: Optional[HistoryProvider] = None,
    ) -> List[Dict[str, Any]]:
        """Process all pending stimuli for a thread (serial CPU)."""
        results: List[Dict[str, Any]] = []
        while self.inbox.has_pending(thread_id):
            stimulus = self.inbox.pop_next(thread_id)
            if stimulus is None:
                break
            # Pop removes the item from inbox; refresh pending before in_flight is set
            # so effective_depth is not double-counted (stale pending + in_flight).
            self._refresh_pending_stimuli(thread_id)
            turn_history = self._resolve_history_messages(
                thread_id,
                history_messages=history_messages,
                history_provider=history_provider,
            )
            try:
                result = self._process_one(
                    stimulus,
                    history_messages=turn_history,
                )
                results.append(result)
                if not (
                    result.get("preempted")
                    or result.get("cancelled")
                    or result.get("force_stopped")
                ):
                    self._finalize_claim(
                        stimulus,
                        disposition="consumed",
                    )
            except Exception as exc:
                logger.exception("Think-life process failed thread_id=%s", thread_id)
                current = self._load_stimulus_disposition(
                    stimulus.stimulus_id
                )
                if current in {"expected_discard", "aborted"}:
                    results.append(
                        {
                            "success": True,
                            "expected_discard": current == "expected_discard",
                            "cancelled": current == "aborted",
                            "stimulus_id": stimulus.stimulus_id,
                        }
                    )
                    continue
                self._mark_stimulus_disposition(
                    stimulus,
                    disposition="failed",
                    stage="final",
                    reason=str(exc),
                )
                results.append(
                    {
                        "success": False,
                        "error": str(exc),
                        "stimulus_id": stimulus.stimulus_id,
                    }
                )
        return results

    def _load_stimulus_disposition(
        self,
        stimulus_id: str,
    ) -> Optional[str]:
        store = getattr(self.inbox, "store", None)
        if store is None:
            return None
        stimulus = store.load_stimulus(stimulus_id)
        return stimulus.disposition if stimulus is not None else None

    def _mark_stimulus_disposition(
        self,
        stimulus: StimulusEnvelope,
        *,
        disposition: str,
        stage: str,
        reason: str = "",
    ) -> None:
        store = getattr(self.inbox, "store", None)
        if store is None:
            return
        store.set_stimulus_disposition(
            stimulus.stimulus_id,
            disposition=disposition,
            stage=stage,
            reason=reason,
        )

    def _finalize_claim(
        self,
        stimulus: StimulusEnvelope,
        *,
        disposition: str,
    ) -> None:
        store = getattr(self.inbox, "store", None)
        if store is None or not stimulus.claimed_by:
            return
        try:
            store.finalize_stimulus(
                stimulus_id=stimulus.stimulus_id,
                claim_token={
                    "claimed_by": stimulus.claimed_by,
                    "consumer_epoch": stimulus.consumer_epoch,
                    "claim_epoch": stimulus.claim_epoch,
                },
                disposition=disposition,
                transition_id=(
                    f"stimulus-finalize:{stimulus.stimulus_id}:"
                    f"{stimulus.claim_epoch}"
                ),
                command_digest=disposition,
            )
        except StaleClaimError:
            logger.info(
                "stale stimulus claim ignored stimulus_id=%s",
                stimulus.stimulus_id,
            )

    def _refresh_pending_stimuli(self, thread_id: str) -> None:
        tid = str(thread_id or "").strip()
        THREAD_RUNTIME_STATUS.set_pending_stimuli(tid, self.inbox.pending_count(tid))
        self._emit_runtime_updated(tid)

    @staticmethod
    def _resolve_history_messages(
        thread_id: str,
        *,
        history_messages: Optional[List[Dict[str, Any]]],
        history_provider: Optional[HistoryProvider],
    ) -> Optional[List[Dict[str, Any]]]:
        if history_provider is not None:
            try:
                fresh = history_provider(thread_id)
                if fresh is not None:
                    return fresh
            except Exception:
                logger.exception(
                    "Think-life history_provider failed thread_id=%s",
                    thread_id,
                )
        return history_messages

    def _emit_runtime_updated(self, thread_id: str) -> None:
        if self._on_runtime_updated is not None:
            try:
                self._on_runtime_updated(thread_id)
            except Exception:
                logger.exception("on_runtime_updated failed thread_id=%s", thread_id)

    def _process_one(
        self,
        stimulus: StimulusEnvelope,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        routing_scene = read_scene_segment(
            self.scene_reader,
            stimulus.conversation_id,
            max_entries=self.config.scene_context_max_entries,
            entry_filter=is_user_visible_scene_interaction,
        )
        transaction, _created = self.attributor.resolve(
            stimulus,
            dialogue_history=history_messages,
            scene_entries=routing_scene,
        )
        transaction, deleted_at_binding = self._bind_attributed_stimulus(
            stimulus,
            transaction,
        )
        if deleted_at_binding:
            return self._handle_transaction_deleted(
                stimulus,
                transaction,
                phase="attribution_bind",
            )
        self._bind_deferred_user_scene(stimulus, transaction)
        if not str(stimulus.transaction_id or "").strip():
            object.__setattr__(
                stimulus,
                "transaction_id",
                transaction.transaction_id,
            )
        transaction = self.registry.get(transaction.transaction_id) or transaction
        if self._is_deleted_transaction(transaction):
            return self._handle_transaction_deleted(
                stimulus,
                transaction,
                phase="attribution",
            )
        activation = (
            self.registry.store.load_activation(transaction.current_activation_id)
            if transaction.current_activation_id
            else None
        )
        if not is_runnable_record(transaction, activation):
            if self._is_deleted_transaction(transaction):
                return self._handle_transaction_deleted(
                    stimulus,
                    transaction,
                    phase="attribution",
                )
            raise TransactionTransitionError(
                "attributed transaction is not runnable: "
                f"{transaction.transaction_id}"
            )
        tid = transaction.thread_id
        txn_id = transaction.transaction_id
        priority = self.attributor.priority_for(stimulus)
        cancel_event = THREAD_CPU_STATE.set_in_flight(
            tid,
            stimulus_id=stimulus.stimulus_id,
            transaction_id=txn_id,
            priority=priority,
        )
        THREAD_RUNTIME_STATUS.set_cpu_holder(tid, txn_id)
        self._emit_runtime_updated(tid)
        schedule_started = False
        try:
            if stimulus.kind == StimulusKind.SCHEDULED_PLAN:
                # Linearize the external schedule ``running`` transition with
                # transaction deletion.  Delete uses the same registry lock,
                # so either it wins and no start is emitted, or start wins and
                # every later delete path below emits a matching terminal
                # lifecycle callback.
                with self.registry._lock:
                    if (
                        self._is_transaction_delete_cancel(cancel_event)
                        or self._is_deleted_transaction(transaction)
                    ):
                        return self._handle_transaction_deleted(
                            stimulus,
                            transaction,
                            phase="schedule_start",
                        )
                    schedule_started = self._notify_schedule_started(
                        stimulus,
                        transaction,
                    )
            result = self._run_transaction_turn(
                transaction,
                stimulus,
                history_messages=history_messages,
                cancel_event=cancel_event,
            )
            # The final durable deletion check and the schedule terminal hook
            # are one critical section.  A normal tool/planner result cannot
            # race a tombstone and incorrectly report schedule success.
            with self.registry._lock:
                if (
                    self._is_transaction_delete_cancel(cancel_event)
                    or self._is_deleted_transaction(transaction)
                    or result.get("deleted")
                ):
                    deleted_result = self._handle_transaction_deleted(
                        stimulus,
                        transaction,
                        phase="schedule_finish",
                    )
                    if schedule_started:
                        self._notify_schedule_finished(
                            transaction,
                            {
                                "success": False,
                                "cancelled": True,
                                "deleted": True,
                                "error": "transaction_deleted",
                            },
                        )
                    return deleted_result
                if result.get("preempted"):
                    return result
                record = self.registry.get(transaction.transaction_id) or transaction
                if record.kind == TransactionKind.SCHEDULE:
                    if not (
                        result.get("waiting_feedback")
                        or result.get("cancelled")
                    ):
                        self._notify_schedule_finished(record, result)
                return result
        except ExecutionCancelledError:
            with self.registry._lock:
                if (
                    self._is_transaction_delete_cancel(cancel_event)
                    or self._is_deleted_transaction(transaction)
                ):
                    deleted_result = self._handle_transaction_deleted(
                        stimulus,
                        transaction,
                        phase="execute",
                    )
                    if schedule_started:
                        self._notify_schedule_finished(
                            transaction,
                            {
                                "success": False,
                                "cancelled": True,
                                "deleted": True,
                                "error": "transaction_deleted",
                            },
                        )
                    return deleted_result
                if self._is_force_stop(cancel_event):
                    return self._handle_force_stop(
                        stimulus,
                        transaction,
                        phase="execute",
                    )
                return self._handle_preempt(
                    stimulus,
                    transaction,
                    phase="execute",
                )
        except Exception as exc:
            with self.registry._lock:
                current = self.registry.store.load_transaction(
                    transaction.transaction_id
                )
                deleted = bool(current is not None and current.deleted)
                if deleted or self._is_transaction_delete_cancel(cancel_event):
                    deleted_result = self._handle_transaction_deleted(
                        stimulus,
                        current or transaction,
                        phase="execute_error",
                    )
                    if schedule_started:
                        self._notify_schedule_finished(
                            current or transaction,
                            {
                                "success": False,
                                "cancelled": True,
                                "deleted": True,
                                "error": "transaction_deleted",
                            },
                        )
                    return deleted_result
                if schedule_started:
                    self._notify_schedule_finished(
                        current or transaction,
                        {
                            "success": False,
                            "cancelled": False,
                            "deleted": False,
                            "error": str(exc or "schedule processing failed"),
                        },
                    )
                self._handle_processing_failure(stimulus, transaction, exc)
                raise
        finally:
            THREAD_CPU_STATE.clear_in_flight(tid, stimulus_id=stimulus.stimulus_id)
            THREAD_RUNTIME_STATUS.set_cpu_holder(tid, None)
            self._refresh_pending_stimuli(tid)

    def _handle_processing_failure(
        self,
        stimulus: StimulusEnvelope,
        transaction: TransactionRecord,
        exc: Exception,
    ) -> None:
        current = self.registry.get(transaction.transaction_id) or transaction
        error = str(exc or "think-life processing failed").strip() or "think-life processing failed"
        if is_open_continue(current):
            try:
                self.registry.fail(current.transaction_id, error=error)
            except TransactionTransitionError:
                logger.exception(
                    "Think-life failure transition failed txn=%s state=%s",
                    current.transaction_id,
                    current.state.value,
                )
        if self._event_emitter is not None:
            try:
                self._event_emitter(
                    "turn_failed",
                    {
                        "thread_id": current.thread_id,
                        "conversation_id": current.conversation_id,
                        "transaction_id": current.transaction_id,
                        "stimulus_id": stimulus.stimulus_id,
                        "error": error,
                        "retryable": False,
                    },
                )
            except Exception:
                logger.exception(
                    "Think-life turn_failed emission failed txn=%s",
                    current.transaction_id,
                )

    @staticmethod
    def _is_transaction_delete_cancel(cancel_event: Optional[Any]) -> bool:
        return bool(
            cancel_event is not None
            and str(
                getattr(cancel_event, "cancel_reason", "") or ""
            ).strip()
            == "transaction_deleted"
        )

    def _is_deleted_transaction(
        self,
        transaction: TransactionRecord,
    ) -> bool:
        current = self.registry.store.load_transaction(
            transaction.transaction_id
        )
        return bool(current is not None and current.deleted)

    def _bind_attributed_stimulus(
        self,
        stimulus: StimulusEnvelope,
        transaction: TransactionRecord,
    ) -> tuple[TransactionRecord, bool]:
        """Persist sourceless attribution before Scene or CPU work."""

        with self.registry._lock:
            inbox_store = getattr(self.inbox, "store", None)
            bound = None
            bind = getattr(inbox_store, "bind_stimulus_target", None)
            if callable(bind):
                bound = bind(
                    stimulus.stimulus_id,
                    transaction_id=transaction.transaction_id,
                )
            durable = self.registry.store.load_transaction(
                transaction.transaction_id
            )
            if durable is not None:
                transaction = self.registry.refresh_from_store(
                    transaction.transaction_id
                ) or durable
            deleted = bool(
                (bound is not None and bound.disposition == "aborted")
                or (durable is not None and durable.deleted)
            )
            return transaction, deleted

    def _handle_transaction_deleted(
        self,
        stimulus: StimulusEnvelope,
        transaction: TransactionRecord,
        *,
        phase: str,
    ) -> Dict[str, Any]:
        refreshed = self.registry.refresh_from_store(
            transaction.transaction_id
        )
        if isinstance(refreshed, TransactionRecord):
            transaction = refreshed
        store = getattr(self.inbox, "store", None)
        current_stimulus = (
            store.load_stimulus(stimulus.stimulus_id)
            if store is not None
            else None
        )
        if (
            current_stimulus is not None
            and current_stimulus.disposition in {"ready", "claimed"}
        ):
            self._mark_stimulus_disposition(
                stimulus,
                disposition="aborted",
                stage="transaction_delete",
                reason="transaction_deleted",
            )
        return {
            "success": False,
            "cancelled": True,
            "deleted": True,
            "transaction_id": transaction.transaction_id,
            "stimulus_id": stimulus.stimulus_id,
            "phase": str(phase or "runtime"),
        }

    def _notify_schedule_started(
        self,
        stimulus: StimulusEnvelope,
        transaction: TransactionRecord,
    ) -> bool:
        hook = self._schedule_lifecycle
        if hook is None:
            return False
        payload = stimulus.payload if isinstance(stimulus.payload, dict) else {}
        owner_id = str(payload.get("owner_id", "") or "").strip()
        schedule_id = str(stimulus.schedule_id or payload.get("schedule_id", "") or "").strip()
        run_id = str(payload.get("run_id", "") or "").strip()
        if not owner_id or not schedule_id:
            return False
        try:
            hook.on_schedule_processing_started(
                owner_id=owner_id,
                thread_id=transaction.thread_id,
                schedule_id=schedule_id,
                run_id=run_id,
                stimulus_id=stimulus.stimulus_id,
            )
        except Exception:
            logger.exception("schedule processing_started failed schedule_id=%s", schedule_id)
        # Treat an attempted start as externally visible: a hook may have
        # committed its running state before raising.  The caller must always
        # pair it with a terminal callback on deletion or failure.
        return True

    def _notify_schedule_finished(
        self,
        transaction: TransactionRecord,
        result: Dict[str, Any],
    ) -> None:
        hook = self._schedule_lifecycle
        if hook is None:
            return
        owner_id = str(transaction.correlation.schedule_owner_id or "").strip()
        schedule_id = str(transaction.correlation.schedule_id or "").strip()
        run_id = str(transaction.correlation.schedule_run_id or "").strip()
        if not owner_id or not schedule_id:
            return
        success = bool(result.get("success", False))
        answer = str(
            result.get("replies", [""])[-1]
            if isinstance(result.get("replies"), list)
            and result.get("replies")
            else result.get("answer", "") or ""
        )
        if not answer:
            # Final semantic completion now occurs on the feedback turn, one
            # turn after reply_to_user emitted the schedule response. Recover
            # that response from the transaction's Scene entry for lifecycle
            # reporting instead of losing it on the silent completion turn.
            for entry in reversed(
                read_scene_segment(
                    self.scene_reader,
                    transaction.conversation_id,
                    max_entries=self.config.scene_context_max_entries,
                )
            ):
                if (
                    entry.entry_type == SceneEntryType.REPLY
                    and entry.transaction_id == transaction.transaction_id
                ):
                    answer = str(entry.text or "").strip()
                    break
        try:
            hook.on_schedule_processing_finished(
                owner_id=owner_id,
                thread_id=transaction.thread_id,
                schedule_id=schedule_id,
                run_id=run_id,
                success=success,
                answer=answer,
                error=str(result.get("error", "") or ""),
            )
        except Exception:
            logger.exception("schedule processing_finished failed schedule_id=%s", schedule_id)

    def _should_yield_to_inbox(self, thread_id: str, current_priority: int) -> bool:
        if not self.config.scheduler.preempt_enabled:
            return False
        next_prio = self.inbox.peek_next_priority(thread_id)
        if next_prio is None:
            return False
        return int(next_prio) < int(current_priority)

    def _handle_preempt(
        self,
        stimulus: StimulusEnvelope,
        transaction: TransactionRecord,
        *,
        phase: str,
    ) -> Dict[str, Any]:
        preempt_count = int(stimulus.payload.get("_preempt_count", 0) or 0)
        max_preempt = max(1, int(self.config.scheduler.max_preempt_per_stimulus))
        if preempt_count >= max_preempt:
            self.registry.fail(
                transaction.transaction_id,
                error="max_preempt_per_stimulus exceeded",
            )
            return {
                "success": False,
                "preempted": False,
                "error": "max_preempt_per_stimulus exceeded",
                "transaction_id": transaction.transaction_id,
            }
        new_payload = dict(stimulus.payload)
        new_payload["_preempt_count"] = preempt_count + 1
        new_payload["_checkpoint"] = {
            "phase": phase,
            "transaction_id": transaction.transaction_id,
            "at": _now_iso(),
        }
        requeued = StimulusEnvelope(
            stimulus_id=stimulus.stimulus_id,
            thread_id=stimulus.thread_id,
            conversation_id=stimulus.conversation_id,
            stimulus=replace(stimulus.stimulus, payload=new_payload),
            occurred_at=stimulus.occurred_at,
            transaction_id=transaction.transaction_id,
            activation_id=stimulus.activation_id,
            delegate_id=stimulus.delegate_id,
            schedule_id=stimulus.schedule_id,
            schedule_run_id=stimulus.schedule_run_id,
            schedule_delivery_id=stimulus.schedule_delivery_id,
            ingress_key=stimulus.ingress_key,
            priority_override=stimulus.priority_override,
            accepted_seq=stimulus.accepted_seq,
            accepted_at=stimulus.accepted_at,
            effective_priority=stimulus.effective_priority,
            disposition=stimulus.disposition,
            disposition_stage=stimulus.disposition_stage,
            disposition_reason=stimulus.disposition_reason,
            claimed_by=stimulus.claimed_by,
            consumer_epoch=stimulus.consumer_epoch,
            claim_epoch=stimulus.claim_epoch,
            claimed_at=stimulus.claimed_at,
            finalized_at=stimulus.finalized_at,
            worker_latch=stimulus.worker_latch,
        )
        priority = self.attributor.priority_for(requeued)
        self.inbox.requeue_claimed(requeued, priority=priority)
        self._refresh_pending_stimuli(stimulus.thread_id)
        return {
            "success": True,
            "preempted": True,
            "stimulus_id": stimulus.stimulus_id,
            "transaction_id": transaction.transaction_id,
        }

    @staticmethod
    def _is_force_stop(cancel_event: Optional[Any]) -> bool:
        return bool(cancel_event is not None and getattr(cancel_event, "force_stop", False))

    def _handle_force_stop(
        self,
        stimulus: StimulusEnvelope,
        transaction: TransactionRecord,
        *,
        phase: str,
    ) -> Dict[str, Any]:
        current = self.registry.get(transaction.transaction_id) or transaction
        try:
            if is_open_continue(current):
                self.registry.pause(
                    current.transaction_id,
                    reason=PauseReason.MANUAL_HOLD,
                )
        except TransactionTransitionError:
            pass
        self._refresh_pending_stimuli(stimulus.thread_id)
        return {
            "success": False,
            "cancelled": True,
            "force_stopped": True,
            "phase": phase,
            "stimulus_id": stimulus.stimulus_id,
            "transaction_id": transaction.transaction_id,
        }

    def _think_plan_with_gate(
        self,
        *,
        record: TransactionRecord,
        stimulus: StimulusEnvelope,
        perception: PerceptionInput,
        scene_tail: List[SceneEntry],
    ) -> ThinkingDecision:
        """Run plan; on premature answer_directly after feedback, nudge and replan."""
        perception_plan = perception
        decision: Optional[ThinkingDecision] = None
        for nudge_idx in range(_MAX_COMPLETION_GATE_NUDGES + 1):
            decision = self.thinking_agent.handle(
                perception_plan,
                transaction_state=record,
                event_emitter=self._event_emitter,
            )
            if not is_reply_mode(decision.mode):
                break
            if stimulus.kind != StimulusKind.EXECUTION_FEEDBACK:
                break
            pending = latest_user_utterance_from_scene(scene_tail)
            block = premature_reply_block_reason(
                pending_user_request=pending,
                stimulus=stimulus,
            )
            if not block:
                break
            if nudge_idx >= _MAX_COMPLETION_GATE_NUDGES:
                logger.warning(
                    "completion gate exhausted for txn=%s block=%s; allowing answer_directly",
                    record.transaction_id,
                    block,
                )
                break
            perception_plan = augment_perception_with_nudge(
                perception,
                build_completion_nudge_message(block),
            )
        assert decision is not None
        return decision

    def _run_transaction_turn(
        self,
        transaction: TransactionRecord,
        stimulus: StimulusEnvelope,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        cancel_event: Optional[Any] = None,
    ) -> Dict[str, Any]:
        record = self.registry.get(transaction.transaction_id) or transaction
        if self._is_deleted_transaction(record):
            return self._handle_transaction_deleted(
                stimulus,
                record,
                phase="before_think",
            )
        record.think_rounds += 1
        limit_rounds = self.config.max_think_rounds
        if limit_rounds is not None and record.think_rounds > limit_rounds:
            record = self.registry.fail(
                record.transaction_id,
                error="max_think_rounds exceeded",
            )
            return {
                "success": False,
                "error": record.last_error,
                "transaction_id": record.transaction_id,
            }
        perception = build_perception_for_stimulus(
            transaction=record,
            stimulus=stimulus,
            scene_reader=self.scene_reader,
            scene_context_max_entries=self.config.scene_context_max_entries,
            history_messages=history_messages,
        )
        scene_tail = read_scene_segment(
            self.scene_reader,
            record.conversation_id,
            max_entries=self.config.scene_context_max_entries,
        )
        decision = self._think_plan_with_gate(
            record=record,
            stimulus=stimulus,
            perception=perception,
            scene_tail=scene_tail,
        )
        if (
            self._is_transaction_delete_cancel(cancel_event)
            or self._is_deleted_transaction(record)
        ):
            return self._handle_transaction_deleted(
                stimulus,
                record,
                phase="think",
            )
        if decision.reasoning:
            self._append_scene(
                record,
                entry_type=SceneEntryType.THOUGHT,
                actor=SceneActor.THINK,
                text=str(decision.reasoning),
            )
        if decision.episode_note:
            self._append_scene(
                record,
                entry_type=SceneEntryType.THOUGHT,
                actor=SceneActor.THINK,
                text=str(decision.episode_note),
            )
        current_priority = self.attributor.priority_for(stimulus)
        if self._should_yield_to_inbox(record.thread_id, current_priority):
            return self._handle_preempt(stimulus, record, phase="think")
        if cancel_event is not None and cancel_event.is_set():
            if self._is_transaction_delete_cancel(cancel_event):
                return self._handle_transaction_deleted(
                    stimulus,
                    record,
                    phase="think",
                )
            if self._is_force_stop(cancel_event):
                return self._handle_force_stop(stimulus, record, phase="think")
            return self._handle_preempt(stimulus, record, phase="think")
        enabled_tools = self.execution_agent.enabled_capability_names
        pending_user_request = latest_user_utterance_from_scene(scene_tail)
        if is_execute_mode(decision.mode):
            target = plan_delegate_target(decision, enabled_tools=enabled_tools)
            if target:
                limit = self.config.max_delegates_per_transaction
                if limit is not None and record.delegate_count >= limit:
                    self.registry.fail(
                        record.transaction_id,
                        error="max_delegates_per_transaction exceeded",
                    )
                    return {
                        "success": False,
                        "error": "max_delegates_per_transaction exceeded",
                        "transaction_id": record.transaction_id,
                    }
                return self._delegate_and_wait(
                    record,
                    target=target,
                    pending_user_request=pending_user_request,
                    perception=perception,
                    stimulus=stimulus,
                    cancel_event=cancel_event,
                )
            record = self.registry.fail(
                record.transaction_id,
                error="execute mode requires tool_name",
            )
            return {
                "success": False,
                "error": record.last_error,
                "transaction_id": record.transaction_id,
            }
        if is_silent_mode(decision.mode):
            return self._finish_silent_plan_turn(record, decision)
        if is_reply_mode(decision.mode):
            answer = str(decision.answer or "").strip()
            if answer:
                target = plan_delegate_target(
                    decision,
                    enabled_tools=enabled_tools,
                    for_user_reply=True,
                    user_reply_text=answer,
                )
                if target:
                    return self._delegate_and_wait(
                        record,
                        target=target,
                        pending_user_request=pending_user_request,
                        perception=perception,
                        stimulus=stimulus,
                        cancel_event=cancel_event,
                    )
            logger.warning(
                "answer_directly without answer for txn=%s; treating as silent",
                record.transaction_id,
            )
            return self._finish_silent_plan_turn(record, decision)
        self._complete_transaction_after_turn(record)
        return {
            "success": True,
            "transaction_id": record.transaction_id,
            "completed": True,
            "phases": ["plan"],
        }

    def _finish_silent_plan_turn(
        self,
        record: TransactionRecord,
        decision: ThinkingDecision,
    ) -> Dict[str, Any]:
        """No delegate, no reply; keep open, complete, or pause from macro status.

        Pause is driven by task_state.completion_status==awaiting_user (macro
        decision), not by ThinkingDecision.mode. Once paused, Think hard-rules
        silent on later turns.
        """
        if request_is_complete(decision):
            self._complete_transaction_after_turn(record)
            return {
                "success": True,
                "transaction_id": record.transaction_id,
                "completed": True,
                "silent": True,
                "phases": ["plan"],
            }
        paused_for_user = self._pause_if_macro_awaits_user(record)
        return {
            "success": True,
            "transaction_id": record.transaction_id,
            "completed": False,
            "silent": True,
            "paused_awaiting_user": bool(paused_for_user),
            "phases": ["plan"],
        }

    def _pause_if_macro_awaits_user(self, record: TransactionRecord) -> bool:
        current = self.registry.get(record.transaction_id) or record
        status = normalize_task_completion_status(
            current.task_state.completion_status
        )
        if status != TASK_COMPLETION_AWAITING_USER:
            return False
        return pause_for_user_collaboration(self.registry, current)

    def _complete_transaction_after_turn(self, record: TransactionRecord) -> None:
        """Complete the logical transaction after an explicit completion decision."""
        current = self.registry.get(record.transaction_id) or record
        if current.state == TransactionState.CONTINUE:
            self.registry.complete(record.transaction_id)

    def _delegate_and_wait(
        self,
        record: TransactionRecord,
        *,
        target: DelegateTarget,
        pending_user_request: str = "",
        perception: Any,
        stimulus: StimulusEnvelope,
        cancel_event: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if (
            self._is_transaction_delete_cancel(cancel_event)
            or self._is_deleted_transaction(record)
        ):
            return self._handle_transaction_deleted(
                stimulus,
                record,
                phase="before_execute",
            )
        if self._should_yield_to_inbox(record.thread_id, self.attributor.priority_for(stimulus)):
            return self._handle_preempt(stimulus, record, phase="execute")
        delegate_id = f"dlg_{uuid.uuid4().hex}"
        record = self.registry.begin_delegate(record.transaction_id, delegate_id)
        if (
            self._is_transaction_delete_cancel(cancel_event)
            or self._is_deleted_transaction(record)
        ):
            return self._handle_transaction_deleted(
                stimulus,
                record,
                phase="before_dispatch",
            )
        fill_result = resolve_delegate_tool_input(
            self.execution_agent,
            target,
            thread_id=record.thread_id,
            correlation_id=delegate_id,
            pending_user_request=pending_user_request,
        )
        if (
            self._is_transaction_delete_cancel(cancel_event)
            or self._is_deleted_transaction(record)
        ):
            return self._handle_transaction_deleted(
                stimulus,
                record,
                phase="before_dispatch",
            )
        tool_name = target.tool_name
        if fill_result.needs_clarification:
            tool_history = build_param_gap_tool_history(
                fill_result,
                instruction=target.instruction,
            )
            self._append_tool_scene(record, tool_history, delegate_id=delegate_id)
            if self._is_deleted_transaction(record):
                return self._handle_transaction_deleted(
                    stimulus,
                    record,
                    phase="param_gap",
                )
            self.gateway.submit_execution_feedback(
                thread_id=record.thread_id,
                conversation_id=record.conversation_id,
                transaction_id=record.transaction_id,
                delegate_id=delegate_id,
                activation_id=str(
                    record.current_activation_id or ""
                ),
                tool_history=tool_history,
                summary=param_gap_summary(fill_result),
            )
            return {
                "success": True,
                "transaction_id": record.transaction_id,
                "delegate_id": delegate_id,
                "waiting_feedback": True,
                "param_gap": True,
                "summary": param_gap_summary(fill_result),
            }
        replies: List[str] = []
        finalized = {"value": False}
        def on_reply(message: str, *, finalize: bool) -> None:
            if self._is_deleted_transaction(record):
                return
            replies.append(str(message or "").strip())
            if finalize:
                finalized["value"] = True
            if self._on_reply is not None:
                self._on_reply(record.thread_id, record.transaction_id, message, finalize)
        try:
            if cancel_event is not None and cancel_event.is_set():
                # Once begin_delegate commits, ordinary priority preemption
                # must not replay an effect with unknown side effects.  A
                # user force-stop is the only cancellation that may cross
                # this boundary.
                if self._is_transaction_delete_cancel(cancel_event):
                    return self._handle_transaction_deleted(
                        stimulus,
                        record,
                        phase="before_dispatch",
                    )
                if self._is_force_stop(cancel_event):
                    raise ExecutionCancelledError("execution force-stopped")
            exec_result = self.execution_agent.invoke_tool_direct(
                tool_name=tool_name,
                tool_input=dict(fill_result.args or {}),
                thread_id=record.thread_id,
                correlation_id=delegate_id,
                think_life_hooks={
                    "delegate_id": delegate_id,
                    "transaction_id": record.transaction_id,
                    "conversation_id": record.conversation_id,
                    "on_reply": on_reply,
                    "scene_writer": _TransactionFencedSceneWriter(
                        registry=self.registry,
                        transaction_id=record.transaction_id,
                        inner=self.scene_writer,
                    ),
                },
            )
        except ExecutionCancelledError as exc:
            if self._is_force_stop(cancel_event):
                raise
            raise RuntimeError(
                "delegate execution cancelled after dispatch"
            ) from exc
        if (
            self._is_transaction_delete_cancel(cancel_event)
            or self._is_deleted_transaction(record)
        ):
            return self._handle_transaction_deleted(
                stimulus,
                record,
                phase="execute",
            )
        wm_entries = list(record.wm_entries)
        self.wm_system.write(wm_entries, exec_result.tool_history)

        def commit_wm(transaction: Optional[TransactionRecord]) -> Dict[str, Any]:
            assert transaction is not None
            transaction.wm_entries = list(wm_entries)
            return {
                "action": "commit_delegate_wm",
                "transaction_id": transaction.transaction_id,
                "delegate_id": delegate_id,
                "wm_entry_count": len(wm_entries),
            }

        try:
            RuntimeUnitOfWork(self.registry).apply_transition(
                f"think-life-delegate-result:{delegate_id}",
                {
                    "action": "commit_delegate_wm",
                    "transaction_id": record.transaction_id,
                    "delegate_id": delegate_id,
                },
                record.transaction_id,
                record.revision,
                commit_wm,
            )
        except (RevisionConflictError, TransactionTransitionError):
            if self._is_deleted_transaction(record):
                return self._handle_transaction_deleted(
                    stimulus,
                    record,
                    phase="commit",
                )
            raise
        record = self.registry.get(record.transaction_id) or record
        self._append_tool_scene(record, exec_result.tool_history, delegate_id=delegate_id)
        if self._is_deleted_transaction(record):
            return self._handle_transaction_deleted(
                stimulus,
                record,
                phase="commit",
            )
        # A finalized reply closes the user-visible message stream, not the
        # semantic task. Feed its delivery outcome back through perception so
        # task-state preprocessing can decide completed vs awaiting_user vs
        # further processing just like it does for every other capability.
        self.gateway.submit_execution_feedback(
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            transaction_id=record.transaction_id,
            delegate_id=delegate_id,
            activation_id=str(record.current_activation_id or ""),
            tool_history=exec_result.tool_history,
            summary=feedback_summary_from_tool_history(exec_result.tool_history)
            or str(exec_result.summary or ""),
        )
        return {
            "success": True,
            "transaction_id": record.transaction_id,
            "delegate_id": delegate_id,
            "waiting_feedback": True,
            "reply_finalized": bool(finalized["value"]),
            "replies": replies,
            "summary": exec_result.summary,
        }

    def _append_scene(
        self,
        record: TransactionRecord,
        *,
        entry_type: SceneEntryType,
        actor: SceneActor,
        text: str,
    ) -> None:
        if self._is_deleted_transaction(record):
            return
        body = str(text or "").strip()
        if not body:
            return
        self.scene_writer.append(
            record.conversation_id,
            SceneEntry(
                seq=0,
                occurred_at=_now_iso(),
                entry_type=entry_type,
                actor=actor,
                text=body,
                transaction_id=record.transaction_id,
            ),
        )

    def _bind_deferred_user_scene(
        self,
        stimulus: StimulusEnvelope,
        record: TransactionRecord,
    ) -> None:
        """Bind sourceless user utterances after AT attribution.

        Perception defers Scene writes for sourceless user messages so the
        entry can carry the attributed ``transaction_id``.
        """

        if self._is_deleted_transaction(record):
            return
        if stimulus.kind != StimulusKind.USER_MESSAGE:
            return
        if str(stimulus.transaction_id or "").strip():
            return
        body = str(stimulus.text or "").strip()
        if not body:
            return
        append_id = str(stimulus.stimulus_id or "").strip() or None
        entry = SceneEntry(
            seq=0,
            occurred_at=stimulus.occurred_at,
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text=body,
            append_id=append_id,
            transaction_id=record.transaction_id,
        )
        try:
            self.scene_writer.append(
                stimulus.conversation_id,
                entry,
                append_id=append_id,
            )
        except TypeError:
            self.scene_writer.append(stimulus.conversation_id, entry)

    def _append_tool_scene(
        self,
        record: TransactionRecord,
        tool_history: List[Dict[str, Any]],
        *,
        delegate_id: str,
    ) -> None:
        if self._is_deleted_transaction(record):
            return
        for item in tool_history:
            if not isinstance(item, dict):
                continue
            name = str(item.get("tool_name", "") or "").strip()
            if name == "reply_to_user":
                continue
            result = item.get("result")
            summary = ""
            if isinstance(result, dict):
                summary = str(result.get("summary", result.get("message", "")) or "")[:500]
            elif result is not None:
                summary = str(result)[:500]
            self._append_scene(
                record,
                entry_type=SceneEntryType.ACTION,
                actor=SceneActor.WORK,
                text=f"{name}: {summary}" if summary else name,
            )
