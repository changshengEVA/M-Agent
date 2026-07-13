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
    ThinkingDecision,
    is_execute_mode,
    is_reply_mode,
    is_silent_mode,
    request_is_complete,
)
from m_agent.layers.thinking.core import ThinkingAgent, ThinkingTurnResult
from m_agent.runtime.think_life.config import ThinkLifeConfig
from m_agent.runtime.think_life.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    StimulusKind,
    TransactionKind,
    TransactionRecord,
    TransactionStatus,
)
from m_agent.runtime.think_life.perception.attributor import TransactionAttributor
from m_agent.runtime.think_life.perception.gateway import PerceptionGateway
from m_agent.runtime.think_life.perception.inbox import StimulusInbox
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
    format_scene_tail,
    latest_user_utterance_from_scene,
    read_scene_segment,
)
from m_agent.runtime.think_life.transaction_registry import (
    TransactionRegistry,
    TransactionTransitionError,
)
from m_agent.systems.scene.protocols import SceneReader, SceneWriter
from m_agent.systems.wm import WMSystem

logger = logging.getLogger(__name__)

ReplyCallback = Callable[[str, str, str, bool], None]
ThinkingEventEmitter = Callable[[str, Dict[str, Any]], None]
HistoryProvider = Callable[[str], Optional[List[Dict[str, Any]]]]

_MAX_COMPLETION_GATE_NUDGES = 2


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
                results.append(
                    self._process_one(stimulus, history_messages=turn_history)
                )
            except Exception as exc:
                logger.exception("Think-life process failed thread_id=%s", thread_id)
                results.append(
                    {
                        "success": False,
                        "error": str(exc),
                        "stimulus_id": stimulus.stimulus_id,
                    }
                )
        return results

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
        )
        transaction, _created = self.attributor.resolve(
            stimulus,
            dialogue_history=history_messages,
            scene_context=format_scene_tail(routing_scene),
        )
        if transaction.status == TransactionStatus.PENDING:
            self.registry.transition(transaction.transaction_id, TransactionStatus.RUNNING)
        elif transaction.status == TransactionStatus.WAITING_EXECUTION:
            self.registry.transition(transaction.transaction_id, TransactionStatus.RUNNING)
        elif transaction.status == TransactionStatus.SUSPENDED:
            self.registry.transition(transaction.transaction_id, TransactionStatus.RUNNING)
        transaction = self.registry.get(transaction.transaction_id) or transaction
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
        if stimulus.kind == StimulusKind.SCHEDULED_PLAN:
            self._notify_schedule_started(stimulus, transaction)
        try:
            result = self._run_transaction_turn(
                transaction,
                stimulus,
                history_messages=history_messages,
                cancel_event=cancel_event,
            )
            if result.get("preempted"):
                return result
            record = self.registry.get(transaction.transaction_id) or transaction
            if record.kind == TransactionKind.SCHEDULE:
                if not result.get("waiting_feedback"):
                    self._notify_schedule_finished(record, result)
            return result
        except ExecutionCancelledError:
            if self._is_force_stop(cancel_event):
                return self._handle_force_stop(stimulus, transaction, phase="execute")
            return self._handle_preempt(stimulus, transaction, phase="execute")
        finally:
            THREAD_CPU_STATE.clear_in_flight(tid, stimulus_id=stimulus.stimulus_id)
            THREAD_RUNTIME_STATUS.set_cpu_holder(tid, None)
            self._refresh_pending_stimuli(tid)

    def _notify_schedule_started(
        self,
        stimulus: StimulusEnvelope,
        transaction: TransactionRecord,
    ) -> None:
        hook = self._schedule_lifecycle
        if hook is None:
            return
        payload = stimulus.payload if isinstance(stimulus.payload, dict) else {}
        owner_id = str(payload.get("owner_id", "") or "").strip()
        schedule_id = str(stimulus.schedule_id or payload.get("schedule_id", "") or "").strip()
        run_id = str(payload.get("run_id", "") or "").strip()
        if not owner_id or not schedule_id:
            return
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
        try:
            hook.on_schedule_processing_finished(
                owner_id=owner_id,
                thread_id=transaction.thread_id,
                schedule_id=schedule_id,
                run_id=run_id,
                success=success,
                answer=str(
                    result.get("replies", [""])[-1]
                    if isinstance(result.get("replies"), list)
                    and result.get("replies")
                    else result.get("answer", "") or ""
                ),
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
            self.registry.transition(transaction.transaction_id, TransactionStatus.FAILED)
            return {
                "success": False,
                "preempted": False,
                "error": "max_preempt_per_stimulus exceeded",
                "transaction_id": transaction.transaction_id,
            }
        try:
            if transaction.status == TransactionStatus.RUNNING:
                self.registry.transition(transaction.transaction_id, TransactionStatus.SUSPENDED)
        except TransactionTransitionError:
            pass
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
            delegate_id=stimulus.delegate_id,
            schedule_id=stimulus.schedule_id,
            priority_override=stimulus.priority_override,
        )
        priority = self.attributor.priority_for(requeued)
        self.inbox.push(requeued, priority=priority)
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
            if not current.status.is_terminal():
                self.registry.transition(current.transaction_id, TransactionStatus.CANCELLED)
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
    ) -> tuple[ThinkingTurnResult, ThinkingDecision]:
        """Run plan; on premature answer_directly after feedback, nudge and replan."""
        perception_plan = perception
        turn: Optional[ThinkingTurnResult] = None
        decision: Optional[ThinkingDecision] = None
        for nudge_idx in range(_MAX_COMPLETION_GATE_NUDGES + 1):
            turn = self.thinking_agent.handle(
                perception_plan,
                transaction_state=record,
                event_emitter=self._event_emitter,
            )
            decision = turn.decision
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
        assert turn is not None and decision is not None
        return turn, decision

    def _run_transaction_turn(
        self,
        transaction: TransactionRecord,
        stimulus: StimulusEnvelope,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        cancel_event: Optional[Any] = None,
    ) -> Dict[str, Any]:
        record = self.registry.get(transaction.transaction_id) or transaction
        record.think_rounds += 1
        limit_rounds = self.config.max_think_rounds
        if limit_rounds is not None and record.think_rounds > limit_rounds:
            self.registry.transition(record.transaction_id, TransactionStatus.FAILED)
            record.last_error = "max_think_rounds exceeded"
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
        turn, decision = self._think_plan_with_gate(
            record=record,
            stimulus=stimulus,
            perception=perception,
            scene_tail=scene_tail,
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
            if self._is_force_stop(cancel_event):
                return self._handle_force_stop(stimulus, record, phase="think")
            return self._handle_preempt(stimulus, record, phase="think")
        enabled_tools = self.execution_agent.enabled_capability_names
        pending_user_request = latest_user_utterance_from_scene(scene_tail)
        if turn.execution_result is not None:
            answer = str(turn.answer or "").strip()
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
        if is_execute_mode(decision.mode):
            target = plan_delegate_target(decision, enabled_tools=enabled_tools)
            if target:
                limit = self.config.max_delegates_per_transaction
                if limit is not None and record.delegate_count >= limit:
                    self.registry.transition(record.transaction_id, TransactionStatus.FAILED)
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
            record.last_error = "execute mode requires tool_name (or a single capability_hint)"
            self.registry.transition(record.transaction_id, TransactionStatus.FAILED)
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
        """No delegate, no reply; keep the transaction open unless request_complete."""
        if request_is_complete(decision):
            self._complete_transaction_after_turn(record)
            return {
                "success": True,
                "transaction_id": record.transaction_id,
                "completed": True,
                "silent": True,
                "phases": ["plan"],
            }
        current = self.registry.get(record.transaction_id) or record
        if current.status in {
            TransactionStatus.PENDING,
            TransactionStatus.WAITING_EXECUTION,
        }:
            self.registry.transition(record.transaction_id, TransactionStatus.RUNNING)
        return {
            "success": True,
            "transaction_id": record.transaction_id,
            "completed": False,
            "silent": True,
            "phases": ["plan"],
        }

    def _complete_transaction_after_turn(self, record: TransactionRecord) -> None:
        """USER_TASK stays open until memory flush; other kinds close after the turn."""
        if record.kind == TransactionKind.USER_TASK:
            current = self.registry.get(record.transaction_id) or record
            if current.status == TransactionStatus.WAITING_EXECUTION:
                self.registry.transition(record.transaction_id, TransactionStatus.RUNNING)
            elif current.status == TransactionStatus.PENDING:
                self.registry.transition(record.transaction_id, TransactionStatus.RUNNING)
            return
        if record.status == TransactionStatus.WAITING_EXECUTION:
            self.registry.transition(record.transaction_id, TransactionStatus.RUNNING)
        current = self.registry.get(record.transaction_id) or record
        if not current.status.is_terminal():
            self.registry.transition(record.transaction_id, TransactionStatus.COMPLETED)

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
        if self._should_yield_to_inbox(record.thread_id, self.attributor.priority_for(stimulus)):
            return self._handle_preempt(stimulus, record, phase="execute")
        delegate_id = f"dlg_{uuid.uuid4().hex}"
        record.delegate_count += 1
        record.active_delegate_id = delegate_id
        record.correlation.delegate_id = delegate_id
        self.registry.transition(record.transaction_id, TransactionStatus.WAITING_EXECUTION)
        fill_result = resolve_delegate_tool_input(
            self.execution_agent,
            target,
            thread_id=record.thread_id,
            correlation_id=delegate_id,
            pending_user_request=pending_user_request,
        )
        tool_name = target.tool_name
        if fill_result.needs_clarification:
            tool_history = build_param_gap_tool_history(
                fill_result,
                instruction=target.instruction,
            )
            self._append_tool_scene(record, tool_history, delegate_id=delegate_id)
            self.gateway.submit_execution_feedback(
                thread_id=record.thread_id,
                conversation_id=record.conversation_id,
                transaction_id=record.transaction_id,
                delegate_id=delegate_id,
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
            replies.append(str(message or "").strip())
            if finalize:
                finalized["value"] = True
            if self._on_reply is not None:
                self._on_reply(record.thread_id, record.transaction_id, message, finalize)
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise ExecutionCancelledError("execution preempted")
            exec_result = self.execution_agent.invoke_tool_direct(
                tool_name=tool_name,
                tool_input=dict(fill_result.args or {}),
                thread_id=record.thread_id,
                correlation_id=delegate_id,
                think_life_hooks={
                    "delegate_id": delegate_id,
                    "transaction_id": record.transaction_id,
                    "on_reply": on_reply,
                    "scene_writer": self.scene_writer,
                },
            )
        except ExecutionCancelledError:
            raise
        self.wm_system.write(record.wm_entries, exec_result.tool_history)
        self._append_tool_scene(record, exec_result.tool_history, delegate_id=delegate_id)
        if finalized["value"]:
            record = self.registry.get(record.transaction_id) or record
            self._complete_transaction_after_turn(record)
            return {
                "success": True,
                "transaction_id": record.transaction_id,
                "delegate_id": delegate_id,
                "replies": replies,
                "completed": True,
                "answer": replies[-1] if replies else "",
            }
        self.gateway.submit_execution_feedback(
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            transaction_id=record.transaction_id,
            delegate_id=delegate_id,
            tool_history=exec_result.tool_history,
            summary=feedback_summary_from_tool_history(exec_result.tool_history)
            or str(exec_result.summary or ""),
        )
        return {
            "success": True,
            "transaction_id": record.transaction_id,
            "delegate_id": delegate_id,
            "waiting_feedback": True,
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

    def _append_tool_scene(
        self,
        record: TransactionRecord,
        tool_history: List[Dict[str, Any]],
        *,
        delegate_id: str,
    ) -> None:
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
