"""Inbox drain loop for the LangGraph production runtime.

Two drain modes share the same claim/attribution/CPU bookkeeping:

* the R2 turn loop, where each stimulus advances the transaction graph through
  thinking → delegate → Feedback;
* the R1 MVP step, which only records progress and never delegates. It stays as
  the rollback target for :data:`M_AGENT_LANGGRAPH_TURN_LOOP`.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from m_agent.api.chat_api_shared import _now_iso
from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS
from m_agent.runtime.langgraph.engine import TransactionGraphEngine
from m_agent.runtime.langgraph.fake_effects import FakeEffectIntent
from m_agent.runtime.langgraph.turn_graph import (
    PHASE_AWAITING_FEEDBACK,
    PHASE_COMPLETED,
    PHASE_ERROR,
    TransactionTurnEngine,
)
from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE
from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    StimulusKind,
    TransactionKind,
    TransactionRecord,
    TransactionState,
)
from m_agent.runtime.perception.attributor import TransactionAttributor
from m_agent.runtime.perception.inbox import StimulusInbox
from m_agent.runtime.perception.matcher_scene_view import (
    is_user_visible_scene_interaction,
)
from m_agent.runtime.scene_projection import append_stimulus_scene_entries
from m_agent.runtime.dispatch.cpu_state import THREAD_CPU_STATE
from m_agent.runtime.dispatch.schedule_lifecycle import (
    ScheduleLifecycleHook,
)
from m_agent.runtime.turn_support.think_context import read_scene_segment
from m_agent.runtime.transaction.registry import TransactionRegistry
from m_agent.runtime.transaction.store import StaleClaimError
from m_agent.runtime.transaction.predicates import (
    is_open_continue,
    is_runnable_record,
)
from m_agent.systems.scene.protocols import SceneReader, SceneWriter

logger = logging.getLogger(__name__)

class LangGraphInboxLoop:
    """Drain one conversation inbox and advance LangGraph-owned transactions."""

    def __init__(
        self,
        *,
        registry: TransactionRegistry,
        inbox: StimulusInbox,
        attributor: TransactionAttributor,
        graph_engine: TransactionGraphEngine,
        scene_writer: SceneWriter,
        scene_reader: SceneReader,
        agent_name: str = "Agent",
        scene_context_max_entries: int = 40,
        turn_engine: Optional[TransactionTurnEngine] = None,
        schedule_lifecycle: ScheduleLifecycleHook = None,
        on_runtime_updated: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.registry = registry
        self.inbox = inbox
        self.attributor = attributor
        self.graph_engine = graph_engine
        self.scene_writer = scene_writer
        self.scene_reader = scene_reader
        self.agent_name = str(agent_name or "Agent").strip() or "Agent"
        self.scene_context_max_entries = max(
            1,
            int(scene_context_max_entries or 40),
        )
        self.turn_engine = turn_engine
        self._schedule_lifecycle = schedule_lifecycle
        self._on_runtime_updated = on_runtime_updated

    @property
    def turn_loop_enabled(self) -> bool:
        return self.turn_engine is not None

    def drain_thread(
        self,
        thread_id: str,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        while self.inbox.has_pending(thread_id):
            stimulus = self.inbox.pop_next(thread_id)
            if stimulus is None:
                break
            self._refresh_pending_stimuli(thread_id)
            try:
                result = self._process_one(
                    stimulus,
                    history_messages=history_messages,
                    event_emitter=event_emitter,
                )
                results.append(result)
                if result.get("success"):
                    self._finalize_claim(stimulus, disposition="completed")
                else:
                    self._terminalize_failed_claim(
                        stimulus,
                        reason=str(result.get("error", "") or "turn failed"),
                    )
            except Exception as exc:
                logger.exception(
                    "LangGraph inbox processing failed thread_id=%s",
                    thread_id,
                )
                self._terminalize_failed_claim(stimulus, reason=str(exc))
                results.append(
                    {
                        "success": False,
                        "error": str(exc),
                        "stimulus_id": stimulus.stimulus_id,
                    }
                )
        return results

    def _process_one(
        self,
        stimulus: StimulusEnvelope,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        routing_scene = read_scene_segment(
            self.scene_reader,
            stimulus.conversation_id,
            max_entries=self.scene_context_max_entries,
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
            )
        # Always repair the stimulus projection after durable attribution.
        # Stable append_id makes this a no-op after normal ingress, while the
        # writer's created flag prevents a duplicate scene_entry_appended SSE.
        # This also closes the crash window between inbox admission/binding and
        # the first Scene append.
        self._bind_stimulus_scene(stimulus, transaction)
        if not str(stimulus.transaction_id or "").strip():
            object.__setattr__(
                stimulus,
                "transaction_id",
                transaction.transaction_id,
            )
        if transaction.runtime_engine != LANGGRAPH_RUNTIME_ENGINE:
            raise ValueError(
                "transaction runtime_engine mismatch: "
                f"{transaction.runtime_engine!r}"
            )
        transaction = self.registry.get(transaction.transaction_id) or transaction
        if transaction.deleted:
            return self._handle_transaction_deleted(
                stimulus,
                transaction,
            )
        activation = (
            self.registry.store.load_activation(transaction.current_activation_id)
            if transaction.current_activation_id
            else None
        )
        if not is_runnable_record(transaction, activation):
            if transaction.deleted:
                return self._handle_transaction_deleted(
                    stimulus,
                    transaction,
                )
            raise ValueError(
                "attributed transaction is not runnable: "
                f"{transaction.transaction_id}"
            )
        tid = transaction.thread_id
        txn_id = transaction.transaction_id
        cancel_event = THREAD_CPU_STATE.set_in_flight(
            tid,
            stimulus_id=stimulus.stimulus_id,
            transaction_id=txn_id,
            priority=self.attributor.priority_for(stimulus),
        )
        THREAD_RUNTIME_STATUS.set_cpu_holder(tid, txn_id)
        self._refresh_pending_stimuli(tid)
        schedule_started = False
        try:
            if stimulus.kind == StimulusKind.SCHEDULED_PLAN:
                with self.registry._lock:
                    schedule_started = self._notify_schedule_started(
                        stimulus,
                        transaction,
                    )
            if self.turn_engine is not None:
                result = self._run_turn(
                    stimulus,
                    transaction,
                    history_messages=history_messages,
                    event_emitter=event_emitter,
                )
            else:
                result = self._run_mvp_step(stimulus, transaction)
            current = self.registry.store.load_transaction(txn_id)
            if (
                str(
                    getattr(cancel_event, "cancel_reason", "") or ""
                ).strip()
                == "transaction_deleted"
                or (current is not None and current.deleted)
            ):
                deleted_result = self._handle_transaction_deleted(
                    stimulus,
                    transaction,
                )
                self._notify_schedule_finished(transaction, deleted_result)
                return deleted_result
            refreshed = self.registry.refresh_from_store(txn_id) or transaction
            is_schedule_delivery = bool(
                refreshed.kind == TransactionKind.SCHEDULE
                or refreshed.correlation.schedule_run_id
            )
            schedule_terminal = bool(
                refreshed.state == TransactionState.COMPLETE
                or not result.get("success", False)
                or result.get("cancelled")
            )
            if (
                is_schedule_delivery
                and schedule_terminal
                and not result.get("waiting_feedback")
            ):
                self._notify_schedule_finished(refreshed, result)
            return result
        except Exception as exc:
            current = self.registry.get(txn_id) or transaction
            if current.deleted:
                deleted_result = self._handle_transaction_deleted(
                    stimulus,
                    current,
                )
                self._notify_schedule_finished(current, deleted_result)
                return deleted_result
            if is_open_continue(current):
                current = self.registry.fail(
                    txn_id,
                    error=(
                        str(exc or "langgraph processing failed").strip()
                        or "langgraph processing failed"
                    ),
                )
            if schedule_started or current.correlation.schedule_run_id:
                self._notify_schedule_finished(
                    current,
                    {"success": False, "error": str(exc)},
                )
            raise
        finally:
            THREAD_CPU_STATE.clear_in_flight(
                tid,
                stimulus_id=stimulus.stimulus_id,
            )
            THREAD_RUNTIME_STATUS.set_cpu_holder(tid, None)
            self._refresh_pending_stimuli(tid)

    def _load_stimulus_pool_state(
        self,
        stimulus_id: str,
    ) -> Optional[str]:
        store = getattr(self.inbox, "store", None)
        if store is None:
            return None
        current = store.load_stimulus(stimulus_id)
        return current.pool_state if current is not None else None

    def _load_stimulus_disposition(
        self,
        stimulus_id: str,
    ) -> Optional[str]:
        store = getattr(self.inbox, "store", None)
        if store is None:
            return None
        current = store.load_stimulus(stimulus_id)
        if current is None:
            return None
        return current.disposition or current.pool_state

    def _bind_attributed_stimulus(
        self,
        stimulus: StimulusEnvelope,
        transaction: TransactionRecord,
    ) -> tuple[TransactionRecord, bool]:
        """Persist attribution before Scene/graph work and fence deletion."""

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
    ) -> Dict[str, Any]:
        refreshed = self.registry.refresh_from_store(
            transaction.transaction_id
        )
        if isinstance(refreshed, TransactionRecord):
            transaction = refreshed
        current = self._load_stimulus_pool_state(stimulus.stimulus_id)
        if current in {"ready", "running", "waiting", "claimed"}:
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
            "thread_id": transaction.thread_id,
            "transaction_id": transaction.transaction_id,
            "stimulus_id": stimulus.stimulus_id,
        }

    def _run_turn(
        self,
        stimulus: StimulusEnvelope,
        transaction: TransactionRecord,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        assert self.turn_engine is not None
        txn_id = transaction.transaction_id
        turn = self.turn_engine.run_turn(
            record=transaction,
            stimulus=stimulus,
            history_messages=history_messages,
            event_emitter=event_emitter,
        )
        state = turn.state
        updated = self.registry.get(txn_id) or transaction
        if not turn.success and is_open_continue(updated):
            updated = self.registry.fail(
                txn_id,
                error=(
                    str(state.get("last_error", "") or "").strip()
                    or "langgraph turn failed"
                ),
            )
        decision = dict(state.get("decision") or {})
        pending = dict(state.get("pending_delegate_intent") or {})
        result: Dict[str, Any] = {
            "success": turn.success,
            "thread_id": transaction.thread_id,
            "transaction_id": txn_id,
            "runtime_engine": updated.runtime_engine,
            "graph_phase": turn.graph_phase,
            "revision": int(updated.revision),
            "turn_index": int(state.get("turn_index", 0) or 0),
            "turn_kind": str(state.get("turn_kind", "") or ""),
            "decision_mode": str(decision.get("mode", "") or ""),
            "waiting_feedback": turn.graph_phase == PHASE_AWAITING_FEEDBACK,
            "completed": turn.graph_phase == PHASE_COMPLETED,
            "delegate_id": str(pending.get("delegate_id") or ""),
            "stimulus_id": stimulus.stimulus_id,
        }
        if turn.graph_phase == PHASE_ERROR:
            result["error"] = str(state.get("last_error", "") or "turn failed")
        return result

    def _run_mvp_step(
        self,
        stimulus: StimulusEnvelope,
        transaction: TransactionRecord,
    ) -> Dict[str, Any]:
        """R1 fallback: record progress only, never delegate."""

        txn_id = transaction.transaction_id
        user_text = str(stimulus.text or "").strip() or "user stimulus"
        graph_result = self.graph_engine.run_script(
            conversation_id=transaction.conversation_id,
            transaction_id=txn_id,
            script=[
                {
                    "action": "record_progress",
                    "transition_id": (
                        f"lg-host:{stimulus.stimulus_id}:progress"
                    ),
                    "wm_entries": [{"utterance": user_text}],
                    "goal": user_text[:240],
                }
            ],
            thread_id=txn_id,
        )
        self._append_work_scene(
            conversation_id=transaction.conversation_id,
            transaction_id=txn_id,
            text=f"processed via LangGraph: {user_text[:120]}",
        )
        updated = self.registry.get(txn_id) or transaction
        return {
            "success": graph_result.state.get("graph_phase") != PHASE_ERROR,
            "thread_id": transaction.thread_id,
            "transaction_id": txn_id,
            "runtime_engine": updated.runtime_engine,
            "graph_phase": graph_result.state.get("graph_phase"),
            "revision": int(updated.revision),
        }

    def _append_work_scene(
        self,
        *,
        conversation_id: str,
        transaction_id: str,
        text: str,
    ) -> None:
        current = self.registry.store.load_transaction(transaction_id)
        if current is not None and current.deleted:
            return
        self.scene_writer.append(
            conversation_id,
            SceneEntry(
                seq=0,
                occurred_at=_now_iso(),
                entry_type=SceneEntryType.ACTION,
                actor=SceneActor.WORK,
                actor_name=self.agent_name,
                text=text,
                transaction_id=transaction_id,
            ),
        )

    def _bind_stimulus_scene(
        self,
        stimulus: StimulusEnvelope,
        record: TransactionRecord,
    ) -> None:
        current = self.registry.store.load_transaction(
            record.transaction_id
        )
        if current is not None and current.deleted:
            return
        append_stimulus_scene_entries(
            self.scene_writer,
            stimulus,
            transaction_id=record.transaction_id,
            agent_name=self.agent_name,
        )

    def _notify_schedule_started(
        self,
        stimulus: StimulusEnvelope,
        transaction: TransactionRecord,
    ) -> bool:
        hook = self._schedule_lifecycle
        if hook is None:
            return False
        payload = stimulus.payload if isinstance(stimulus.payload, dict) else {}
        owner_id = str(
            payload.get("owner_id")
            or transaction.correlation.schedule_owner_id
            or ""
        ).strip()
        schedule_id = str(
            stimulus.schedule_id
            or payload.get("schedule_id")
            or transaction.correlation.schedule_id
            or ""
        ).strip()
        run_id = str(
            stimulus.schedule_run_id
            or payload.get("schedule_run_id")
            or payload.get("run_id")
            or transaction.correlation.schedule_run_id
            or ""
        ).strip()
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
            logger.exception(
                "schedule processing_started failed schedule_id=%s",
                schedule_id,
            )
        return True

    def _notify_schedule_finished(
        self,
        transaction: TransactionRecord,
        result: Dict[str, Any],
    ) -> None:
        hook = self._schedule_lifecycle
        if hook is None:
            return
        owner_id = str(
            transaction.correlation.schedule_owner_id or ""
        ).strip()
        schedule_id = str(transaction.correlation.schedule_id or "").strip()
        run_id = str(
            transaction.correlation.schedule_run_id or ""
        ).strip()
        if not owner_id or not schedule_id:
            return
        answer = str(result.get("answer", "") or "").strip()
        if not answer:
            for entry in reversed(
                read_scene_segment(
                    self.scene_reader,
                    transaction.conversation_id,
                    max_entries=self.scene_context_max_entries,
                )
            ):
                if (
                    (
                        entry.entry_type == SceneEntryType.REPLY
                        or (
                            entry.entry_type == SceneEntryType.ACTION
                            and entry.actor == SceneActor.ASSISTANT
                            and str(entry.tool_name or "").strip()
                            == "reply_to_user"
                        )
                    )
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
                success=bool(result.get("success", False)),
                answer=answer,
                error=str(result.get("error", "") or ""),
            )
        except Exception:
            logger.exception(
                "schedule processing_finished failed schedule_id=%s",
                schedule_id,
            )

    def _refresh_pending_stimuli(self, thread_id: str) -> None:
        tid = str(thread_id or "").strip()
        THREAD_RUNTIME_STATUS.set_pending_stimuli(
            tid,
            self.inbox.pending_count(tid),
        )
        if self._on_runtime_updated is not None:
            try:
                self._on_runtime_updated(tid)
            except Exception:
                logger.exception(
                    "LangGraph on_runtime_updated failed thread_id=%s",
                    tid,
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

    def _terminalize_failed_claim(
        self,
        stimulus: StimulusEnvelope,
        *,
        reason: str,
    ) -> None:
        store = getattr(self.inbox, "store", None)
        current = store.load_stimulus(stimulus.stimulus_id) if store else None
        if current is None:
            return
        if current.is_terminated or current.disposition in {
            "aborted",
            "rejected",
            "discarded",
            "completed",
            "failed",
            "merged",
            "expected_discard",
            "consumed",
        }:
            return
        if current.pool_state in {"running", "claimed"} and stimulus.claimed_by:
            self._finalize_claim(stimulus, disposition="failed")
            return
        if current.pool_state == "ready":
            self._mark_stimulus_disposition(
                stimulus,
                disposition="failed",
                stage="final",
                reason=str(reason or "langgraph turn failed"),
            )

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


def relay_fake_feedback(
    runtime: Any,
    intent: FakeEffectIntent,
) -> str:
    from m_agent.runtime.perception.feedback_adapter import (
        FeedbackSignal,
        FeedbackSourceAdapter,
    )

    thread_id = intent.conversation_id.split("::", 1)[0]
    adapter = getattr(runtime, "feedback_adapter", None)
    if adapter is None:
        adapter = FeedbackSourceAdapter(runtime)
    result = adapter.handle_feedback(
        FeedbackSignal(
            tool_history=[],
            summary=str(intent.result_summary or "").strip(),
            delegate_id=str(intent.delegate_id or "").strip(),
            activation_id=str(intent.activation_id or "").strip(),
        ),
        thread_id=thread_id,
        conversation_id=intent.conversation_id,
        transaction_id=intent.transaction_id,
        schedule_drainer=False,
    )
    return result.stimulus_id


__all__ = ["LangGraphInboxLoop", "relay_fake_feedback"]
