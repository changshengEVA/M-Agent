"""R2 turn graph: thinking → delegate → Feedback inside the LangGraph host.

One graph run advances one transaction by exactly one stimulus. A delegate run
ends at ``awaiting_feedback``; the Feedback the delegate produced re-enters the
Inbox through the Perception Gateway and resumes this same graph thread on the
next drain iteration.

The Transaction Store stays authoritative: every mutation goes through the
registry or a revision-fenced :meth:`RuntimeUnitOfWork.apply_transition`, and
the checkpoint only carries execution state needed to resume.
"""

from __future__ import annotations

import logging
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from langgraph.graph import END, START, StateGraph

from m_agent.layers.thinking.contracts import (
    TASK_COMPLETION_AWAITING_USER,
    ThinkingDecision,
    is_execute_mode,
    is_reply_mode,
    normalize_task_completion_status,
    request_is_complete,
)
from m_agent.runtime.langgraph.checkpointer import (
    Checkpointer,
    close_checkpointer,
    create_checkpointer,
)
from m_agent.runtime.langgraph.config import LangGraphRuntimeConfig
from m_agent.runtime.langgraph.graph_state import (
    SCHEMA_VERSION,
    TransactionGraphState,
    empty_turn_state,
)
from m_agent.runtime.langgraph.turn_ports import (
    DelegateEffectLedger,
    DelegateExecutor,
    TurnPlanner,
)
from m_agent.runtime.think_life.config import ThinkLifeConfig
from m_agent.runtime.think_life.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    StimulusKind,
    TransactionRecord,
    TransactionState,
)
from m_agent.runtime.think_life.scheduler.awaiting_user_pause import (
    pause_for_user_collaboration,
)
from m_agent.runtime.think_life.scheduler.delegate import (
    DelegateTarget,
    plan_delegate_target,
)
from m_agent.runtime.think_life.scheduler.think_context import (
    build_perception_for_stimulus,
    latest_user_utterance_from_scene,
    read_scene_segment,
)
from m_agent.runtime.think_life.scheduler.tool_runner import REPLY_TOOL_NAME
from m_agent.runtime.think_life.transaction.predicates import (
    is_open_continue,
    is_runnable_record,
)
from m_agent.systems.scene.protocols import SceneReader, SceneWriter

logger = logging.getLogger(__name__)

TURN_THREAD_PREFIX = "turn"

PHASE_ERROR = "error"
PHASE_TURN_LOADED = "turn_loaded"
PHASE_PLANNED = "planned"
PHASE_THOUGHT_COMMITTED = "thought_committed"
PHASE_AWAITING_FEEDBACK = "awaiting_feedback"
PHASE_TURN_IDLE = "turn_idle"
PHASE_COMPLETED = "completed"

TERMINAL_TURN_PHASES = frozenset(
    {PHASE_ERROR, PHASE_AWAITING_FEEDBACK, PHASE_TURN_IDLE, PHASE_COMPLETED}
)


def turn_thread_id(transaction_id: str) -> str:
    """Checkpoint thread for the turn graph.

    Namespaced away from the scripted PoC graph so both can share one
    checkpoint database without overwriting each other's state.
    """

    return f"{TURN_THREAD_PREFIX}:{str(transaction_id or '').strip()}"


def decision_to_dict(decision: ThinkingDecision) -> Dict[str, Any]:
    return {
        "mode": str(decision.mode or ""),
        "tool_name": decision.tool_name,
        "instruction": decision.instruction,
        "answer": decision.answer,
        "episode_note": decision.episode_note,
        "request_complete": decision.request_complete,
        "reasoning": decision.reasoning,
    }


def decision_from_dict(data: Optional[Dict[str, Any]]) -> ThinkingDecision:
    payload = dict(data or {})
    return ThinkingDecision(
        mode=str(payload.get("mode", "") or "silent"),
        tool_name=payload.get("tool_name"),
        instruction=payload.get("instruction"),
        answer=payload.get("answer"),
        episode_note=payload.get("episode_note"),
        request_complete=payload.get("request_complete"),
        reasoning=payload.get("reasoning"),
    )


def _target_to_dict(target: DelegateTarget) -> Dict[str, Any]:
    return {
        "tool_name": target.tool_name,
        "instruction": target.instruction,
        "for_user_reply": bool(target.for_user_reply),
        "user_reply_text": target.user_reply_text,
    }


def _target_from_dict(data: Dict[str, Any]) -> DelegateTarget:
    return DelegateTarget(
        tool_name=str(data.get("tool_name", "") or ""),
        instruction=str(data.get("instruction", "") or ""),
        for_user_reply=bool(data.get("for_user_reply")),
        user_reply_text=data.get("user_reply_text"),
    )


@dataclass
class TurnContext:
    """Per-run inputs that must not be checkpointed."""

    stimulus: Optional[StimulusEnvelope] = None
    history_messages: Optional[List[Dict[str, Any]]] = None

    def require_stimulus(self) -> StimulusEnvelope:
        if self.stimulus is None:
            raise RuntimeError("turn graph invoked without a stimulus")
        return self.stimulus

    def clear(self) -> None:
        self.stimulus = None
        self.history_messages = None


@dataclass
class TurnGraphPorts:
    """Everything the turn graph needs outside its own state."""

    registry: Any
    uow: Any
    gateway: Any
    planner: TurnPlanner
    delegate_executor: DelegateExecutor
    scene_writer: SceneWriter
    scene_reader: SceneReader
    wm_system: Any
    think_life_config: ThinkLifeConfig
    langgraph_config: LangGraphRuntimeConfig
    effect_ledger: DelegateEffectLedger = field(
        default_factory=DelegateEffectLedger,
    )
    context: TurnContext = field(default_factory=TurnContext)


def _fail(
    state: TransactionGraphState,
    message: str,
) -> TransactionGraphState:
    return {**state, "graph_phase": PHASE_ERROR, "last_error": message}


def _in_error(state: TransactionGraphState) -> bool:
    return str(state.get("graph_phase", "") or "") == PHASE_ERROR


def build_turn_graph(ports: TurnGraphPorts) -> StateGraph:
    """Build the per-stimulus transaction turn graph."""

    registry = ports.registry
    uow = ports.uow
    config = ports.think_life_config
    engine_config = ports.langgraph_config

    def _is_deleted(transaction_id: str) -> bool:
        current = registry.store.load_transaction(transaction_id)
        return bool(current is not None and current.deleted)

    def _deleted_state(
        state: TransactionGraphState,
        *,
        phase: str,
    ) -> TransactionGraphState:
        return {
            **state,
            "graph_phase": PHASE_ERROR,
            "last_error": f"transaction deleted during {phase}",
        }

    def _append_scene(
        record: TransactionRecord,
        *,
        entry_type: SceneEntryType,
        actor: SceneActor,
        text: str,
        delegate_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> None:
        if _is_deleted(record.transaction_id):
            return
        body = str(text or "").strip()
        if not body:
            return
        ports.scene_writer.append(
            record.conversation_id,
            SceneEntry(
                seq=0,
                occurred_at="",
                entry_type=entry_type,
                actor=actor,
                text=body,
                transaction_id=record.transaction_id,
                delegate_id=delegate_id,
                tool_name=tool_name,
            ),
        )

    def _append_tool_scene(
        record: TransactionRecord,
        tool_history: List[Dict[str, Any]],
        *,
        delegate_id: str,
    ) -> None:
        for item in tool_history:
            if not isinstance(item, dict):
                continue
            name = str(item.get("tool_name", "") or "").strip()
            if name == REPLY_TOOL_NAME:
                continue
            result = item.get("result")
            summary = ""
            if isinstance(result, dict):
                summary = str(
                    result.get("summary", result.get("message", "")) or ""
                )[:500]
            elif result is not None:
                summary = str(result)[:500]
            _append_scene(
                record,
                entry_type=SceneEntryType.ACTION,
                actor=SceneActor.WORK,
                text=f"{name}: {summary}" if summary else name,
                delegate_id=delegate_id,
                tool_name=name or None,
            )

    def _record_snapshot(
        state: TransactionGraphState,
        record: TransactionRecord,
    ) -> TransactionGraphState:
        return {
            **state,
            "schema_version": SCHEMA_VERSION,
            "conversation_id": record.conversation_id,
            "transaction_revision": int(record.revision),
            "activation_id": str(record.current_activation_id or ""),
            "task_state_snapshot": record.task_state.to_dict(),
            "wm_snapshot": list(record.wm_entries),
            "current_decision": record.state.value,
            "delegate_count": int(record.delegate_count),
        }

    def load_turn(state: TransactionGraphState) -> TransactionGraphState:
        tx_id = str(state.get("transaction_id", "") or "").strip()
        record = registry.get(tx_id)
        if record is None:
            return _fail(state, f"unknown transaction: {tx_id}")
        if record.state == TransactionState.ARCHIVE:
            return _fail(state, "graph cannot resume an archived transaction")
        activation = (
            registry.store.load_activation(record.current_activation_id)
            if record.current_activation_id
            else None
        )
        if not is_runnable_record(record, activation):
            return _fail(state, "graph transaction is not runnable")
        stimulus = ports.context.require_stimulus()
        is_feedback = stimulus.kind == StimulusKind.EXECUTION_FEEDBACK

        pending = state.get("pending_delegate_intent") or None
        matched = bool(
            is_feedback
            and pending
            and str(pending.get("delegate_id") or "")
            == str(stimulus.delegate_id or "")
        )
        chain = int(state.get("delegate_chain", 0) or 0) if is_feedback else 0
        if chain >= max(1, int(engine_config.max_delegate_chain)):
            return _fail(
                state,
                "max_delegate_chain exceeded: "
                f"{chain}/{engine_config.max_delegate_chain}",
            )

        rounds = int(record.think_rounds) + 1
        limit_rounds = config.max_think_rounds
        if limit_rounds is not None and rounds > limit_rounds:
            _fail_transaction(
                record.transaction_id,
                "max_think_rounds exceeded",
            )
            return _fail(state, "max_think_rounds exceeded")

        loaded = _record_snapshot(state, record)
        loaded.update(
            graph_phase=PHASE_TURN_LOADED,
            turn_index=int(state.get("turn_index", 0) or 0) + 1,
            turn_kind=stimulus.kind.value,
            current_stimulus_ref=str(stimulus.stimulus_id or ""),
            think_rounds=rounds,
            delegate_chain=chain,
            decision=None,
            delegate_target=None,
            last_delegate_result={},
            reply_finalized=False,
            turn_completed=False,
            last_error="",
        )
        if is_feedback:
            loaded.update(
                latest_feedback_ref=str(stimulus.stimulus_id or ""),
                feedback_matched_pending=matched,
                pending_delegate_intent=None,
            )
            if pending and not matched:
                logger.info(
                    "feedback does not match pending delegate txn=%s "
                    "pending=%s feedback=%s",
                    tx_id,
                    pending.get("delegate_id"),
                    stimulus.delegate_id,
                )
        return loaded

    def think(state: TransactionGraphState) -> TransactionGraphState:
        if _in_error(state):
            return state
        tx_id = str(state.get("transaction_id", "") or "").strip()
        record = registry.get(tx_id)
        if record is None:
            return _fail(state, "transaction missing before thinking")
        stimulus = ports.context.require_stimulus()
        perception = build_perception_for_stimulus(
            transaction=record,
            stimulus=stimulus,
            scene_reader=ports.scene_reader,
            scene_context_max_entries=config.scene_context_max_entries,
            history_messages=ports.context.history_messages,
        )
        scene_tail = read_scene_segment(
            ports.scene_reader,
            record.conversation_id,
            max_entries=config.scene_context_max_entries,
        )
        decision = ports.planner.plan(
            record=record,
            stimulus=stimulus,
            perception=perception,
            scene_tail=scene_tail,
        )
        if _is_deleted(tx_id):
            return _deleted_state(state, phase="planning")
        return {
            **state,
            "graph_phase": PHASE_PLANNED,
            "decision": decision_to_dict(decision),
            "pending_user_request": latest_user_utterance_from_scene(scene_tail),
            "task_state_snapshot": record.task_state.to_dict(),
        }

    def commit_thought(state: TransactionGraphState) -> TransactionGraphState:
        """Persist the thinking round with an expected revision, then Scene."""

        if _in_error(state):
            return state
        tx_id = str(state.get("transaction_id", "") or "").strip()
        record = registry.get(tx_id)
        if record is None:
            return _fail(state, "transaction missing before thought commit")
        if _is_deleted(tx_id):
            return _deleted_state(state, phase="thought commit")
        decision = decision_from_dict(state.get("decision"))
        rounds = int(state.get("think_rounds", 0) or 0) or record.think_rounds + 1
        # The thinking layer's task-state pass mutates the live record; copy it
        # into the store-loaded record so the CAS write carries it.
        task_state = deepcopy(record.task_state)
        transition_id = (
            f"lg-turn:{tx_id}:{state.get('current_stimulus_ref', '')}:thought"
        )

        def mutate(transaction: Optional[TransactionRecord]) -> Dict[str, Any]:
            assert transaction is not None
            transaction.think_rounds = rounds
            transaction.task_state = deepcopy(task_state)
            transaction.last_error = None
            return {
                "action": "commit_thought",
                "transaction_id": transaction.transaction_id,
                "activation_id": transaction.current_activation_id,
                "think_rounds": rounds,
                "mode": str(decision.mode or ""),
                "revision": transaction.revision,
            }

        result = uow.apply_transition(
            transition_id,
            {
                "action": "commit_thought",
                "transaction_id": tx_id,
                "stimulus_id": str(state.get("current_stimulus_ref", "") or ""),
                "turn_index": int(state.get("turn_index", 0) or 0),
                "mode": str(decision.mode or ""),
            },
            tx_id,
            record.revision,
            mutate,
        )
        if _is_deleted(tx_id):
            return _deleted_state(state, phase="thought scene commit")
        updated = registry.get(tx_id) or record
        if decision.reasoning:
            _append_scene(
                updated,
                entry_type=SceneEntryType.THOUGHT,
                actor=SceneActor.THINK,
                text=str(decision.reasoning),
            )
        if decision.episode_note:
            _append_scene(
                updated,
                entry_type=SceneEntryType.THOUGHT,
                actor=SceneActor.THINK,
                text=str(decision.episode_note),
            )
        committed = _record_snapshot(state, updated)
        committed.update(
            graph_phase=PHASE_THOUGHT_COMMITTED,
            transition_id=transition_id,
            last_transition_result=dict(result),
            think_rounds=int(updated.think_rounds),
        )
        return committed

    def plan_delegate(state: TransactionGraphState) -> TransactionGraphState:
        """Resolve at most one capability for this turn, or settle instead."""

        if _in_error(state):
            return state
        tx_id = str(state.get("transaction_id", "") or "").strip()
        record = registry.get(tx_id)
        if record is None:
            return _fail(state, "transaction missing before delegate planning")
        decision = decision_from_dict(state.get("decision"))
        enabled_tools = list(ports.delegate_executor.enabled_tools)
        answer = str(decision.answer or "").strip()

        if is_execute_mode(decision.mode):
            target = plan_delegate_target(decision, enabled_tools=enabled_tools)
            if target is None:
                _fail_transaction(
                    tx_id,
                    "execute mode requires tool_name (tool_name required)",
                )
                return _fail(
                    state,
                    "execute mode requires tool_name "
                    "(tool_name required)",
                )
        elif is_reply_mode(decision.mode) and answer:
            target = plan_delegate_target(
                decision,
                enabled_tools=enabled_tools,
                for_user_reply=True,
                user_reply_text=answer,
            )
            if target is None:
                logger.warning(
                    "reply capability unavailable txn=%s; settling silently",
                    tx_id,
                )
                return {**state, "delegate_target": None}
        else:
            return {**state, "delegate_target": None}

        limit = config.max_delegates_per_transaction
        if limit is not None and int(record.delegate_count) >= int(limit):
            _fail_transaction(
                tx_id,
                "max_delegates_per_transaction exceeded",
            )
            return _fail(state, "max_delegates_per_transaction exceeded")
        return {**state, "delegate_target": _target_to_dict(target)}

    def delegate(state: TransactionGraphState) -> TransactionGraphState:
        """Emit one delegate intent, run it, then relay Feedback to the Inbox."""

        tx_id = str(state.get("transaction_id", "") or "").strip()
        record = registry.get(tx_id)
        if record is None:
            return _fail(state, "transaction missing before delegate")
        if _is_deleted(tx_id):
            return _deleted_state(state, phase="delegate dispatch")
        target = _target_from_dict(dict(state.get("delegate_target") or {}))
        delegate_id = f"lgdlg_{uuid.uuid4().hex}"
        record = registry.begin_delegate(
            tx_id,
            delegate_id,
            expected_revision=record.revision,
        )
        if _is_deleted(tx_id):
            return _deleted_state(state, phase="delegate intent")
        activation_id = str(record.current_activation_id or "")
        effect = ports.effect_ledger.open_intent(
            record=record,
            delegate_id=delegate_id,
            capability=target.tool_name,
        )
        if _is_deleted(tx_id):
            return _deleted_state(state, phase="delegate dispatch")
        effect_id = str(effect.get("effect_id") or "")
        outcome = ports.delegate_executor.run(
            record=record,
            target=target,
            delegate_id=delegate_id,
            effect_id=effect_id,
            pending_user_request=str(state.get("pending_user_request", "") or ""),
        )
        if _is_deleted(tx_id):
            return _deleted_state(state, phase="delegate execution")

        wm_entries = list(record.wm_entries)
        if ports.wm_system is not None:
            ports.wm_system.write(wm_entries, outcome.tool_history)
        result_transition_id = f"lg-turn:{delegate_id}:result"

        def mutate(transaction: Optional[TransactionRecord]) -> Dict[str, Any]:
            assert transaction is not None
            transaction.wm_entries = list(wm_entries)
            return {
                "action": "delegate_result",
                "transaction_id": transaction.transaction_id,
                "activation_id": transaction.current_activation_id,
                "delegate_id": delegate_id,
                "effect_id": effect_id,
                "revision": transaction.revision,
            }

        transition_result = uow.apply_transition(
            result_transition_id,
            {
                "action": "delegate_result",
                "transaction_id": tx_id,
                "delegate_id": delegate_id,
                "capability": target.tool_name,
                "needs_clarification": bool(outcome.needs_clarification),
            },
            tx_id,
            record.revision,
            mutate,
        )
        if _is_deleted(tx_id):
            return _deleted_state(state, phase="delegate result commit")
        updated = registry.get(tx_id) or record
        _append_tool_scene(updated, outcome.tool_history, delegate_id=delegate_id)
        committed_effect = ports.effect_ledger.commit_result(effect_id=effect_id)
        if _is_deleted(tx_id):
            return _deleted_state(state, phase="feedback relay")

        feedback_id = ports.gateway.submit_execution_feedback(
            thread_id=updated.thread_id,
            conversation_id=updated.conversation_id,
            transaction_id=tx_id,
            delegate_id=delegate_id,
            activation_id=activation_id,
            tool_history=outcome.tool_history,
            summary=outcome.summary,
            schedule_drainer=False,
        )
        relayed = ports.effect_ledger.mark_relayed(effect_id=effect_id)

        dispatched = _record_snapshot(state, updated)
        dispatched.update(
            graph_phase=PHASE_AWAITING_FEEDBACK,
            transition_id=result_transition_id,
            last_transition_result=dict(transition_result),
            delegate_chain=int(state.get("delegate_chain", 0) or 0) + 1,
            reply_finalized=bool(outcome.reply_finalized),
            pending_delegate_intent={
                "delegate_id": delegate_id,
                "capability": target.tool_name,
                "effect_id": effect_id,
                "activation_id": activation_id,
                "feedback_stimulus_id": str(feedback_id or ""),
                "for_user_reply": bool(target.for_user_reply),
            },
            last_delegate_result={
                "delegate_id": delegate_id,
                "capability": target.tool_name,
                "summary": outcome.summary,
                "needs_clarification": bool(outcome.needs_clarification),
                "visible_effects": int(outcome.visible_effects),
                "attempts": int(outcome.attempts),
                "effect": committed_effect,
                "relay": relayed,
            },
        )
        return dispatched

    def settle_turn(state: TransactionGraphState) -> TransactionGraphState:
        """No delegate this turn: keep the line open or close it."""

        if _in_error(state):
            return state
        tx_id = str(state.get("transaction_id", "") or "").strip()
        record = registry.get(tx_id)
        if record is None:
            return _fail(state, "transaction missing before settling")
        decision = decision_from_dict(state.get("decision"))
        closed = False
        paused_awaiting_user = False
        if request_is_complete(decision):
            closed = _complete_after_turn(record)
        else:
            # Macro task_state.awaiting_user → domain pause; mode stays silent.
            status = normalize_task_completion_status(
                record.task_state.completion_status
            )
            if status == TASK_COMPLETION_AWAITING_USER:
                paused_awaiting_user = pause_for_user_collaboration(registry, record)
        updated = registry.get(tx_id) or record
        settled = _record_snapshot(state, updated)
        settled.update(
            graph_phase=PHASE_COMPLETED if closed else PHASE_TURN_IDLE,
            turn_completed=bool(closed),
            pending_delegate_intent=None,
            paused_awaiting_user=bool(paused_awaiting_user),
        )
        return settled

    def _fail_transaction(transaction_id: str, message: str) -> None:
        current = registry.get(transaction_id)
        if current is None or not is_open_continue(current):
            return
        try:
            registry.fail(transaction_id, error=message)
        except Exception:
            logger.exception(
                "langgraph turn failure command failed txn=%s",
                transaction_id,
            )

    def _complete_after_turn(record: TransactionRecord) -> bool:
        """Complete the logical transaction after an explicit decision."""

        tx_id = record.transaction_id
        current = registry.get(tx_id)
        if current is None:
            return False
        if current.state == TransactionState.CONTINUE:
            registry.complete(tx_id)
            current = registry.get(tx_id)
        return bool(
            current is not None
            and current.state
            in {TransactionState.COMPLETE, TransactionState.ARCHIVE}
        )

    def route_after_plan(state: TransactionGraphState) -> str:
        if _in_error(state):
            return "settle_turn"
        return "delegate" if state.get("delegate_target") else "settle_turn"

    graph: StateGraph = StateGraph(TransactionGraphState)
    graph.add_node("load_turn", load_turn)
    graph.add_node("think", think)
    graph.add_node("commit_thought", commit_thought)
    graph.add_node("plan_delegate", plan_delegate)
    graph.add_node("delegate", delegate)
    graph.add_node("settle_turn", settle_turn)
    graph.add_edge(START, "load_turn")
    graph.add_edge("load_turn", "think")
    graph.add_edge("think", "commit_thought")
    graph.add_edge("commit_thought", "plan_delegate")
    graph.add_conditional_edges(
        "plan_delegate",
        route_after_plan,
        {"delegate": "delegate", "settle_turn": "settle_turn"},
    )
    graph.add_edge("delegate", END)
    graph.add_edge("settle_turn", END)
    return graph


@dataclass
class TurnRunResult:
    state: TransactionGraphState

    @property
    def graph_phase(self) -> str:
        return str(self.state.get("graph_phase", "") or "")

    @property
    def success(self) -> bool:
        return self.graph_phase != PHASE_ERROR


class TransactionTurnEngine:
    """Compile and drive the R2 turn graph with durable checkpoints."""

    def __init__(
        self,
        *,
        ports: TurnGraphPorts,
        checkpoint_db_path: Optional[Path | str] = None,
        persistent_checkpoint: bool = True,
        checkpointer: Optional[Checkpointer] = None,
    ) -> None:
        self.ports = ports
        self._owns_checkpointer = checkpointer is None
        self._checkpointer: Checkpointer = checkpointer or create_checkpointer(
            db_path=checkpoint_db_path,
            persistent=persistent_checkpoint,
        )
        self._compiled = build_turn_graph(ports).compile(
            checkpointer=self._checkpointer,
        )

    def run_turn(
        self,
        *,
        record: TransactionRecord,
        stimulus: StimulusEnvelope,
        history_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> TurnRunResult:
        """Advance one transaction by one stimulus, resuming prior turn state."""

        thread = turn_thread_id(record.transaction_id)
        graph_config = {"configurable": {"thread_id": thread}}
        state = self._resume_state(
            graph_config,
            conversation_id=record.conversation_id,
            transaction_id=record.transaction_id,
        )
        self.ports.context.stimulus = stimulus
        self.ports.context.history_messages = history_messages
        try:
            final = self._compiled.invoke(state, config=graph_config)
        finally:
            self.ports.context.clear()
        return TurnRunResult(state=dict(final))

    def _resume_state(
        self,
        graph_config: Dict[str, Any],
        *,
        conversation_id: str,
        transaction_id: str,
    ) -> TransactionGraphState:
        snapshot = self._compiled.get_state(graph_config)
        if not snapshot.values:
            return empty_turn_state(
                conversation_id=conversation_id,
                transaction_id=transaction_id,
            )
        state = dict(snapshot.values)
        state["conversation_id"] = conversation_id
        state["transaction_id"] = transaction_id
        return state

    def get_checkpoint_state(
        self,
        *,
        transaction_id: str,
    ) -> TransactionGraphState:
        graph_config = {
            "configurable": {"thread_id": turn_thread_id(transaction_id)}
        }
        snapshot = self._compiled.get_state(graph_config)
        return dict(snapshot.values or {})

    def close(self) -> None:
        if self._owns_checkpointer:
            close_checkpointer(self._checkpointer)
        self._compiled = None


__all__ = [
    "PHASE_AWAITING_FEEDBACK",
    "PHASE_COMPLETED",
    "PHASE_ERROR",
    "PHASE_THOUGHT_COMMITTED",
    "PHASE_TURN_IDLE",
    "TERMINAL_TURN_PHASES",
    "TransactionTurnEngine",
    "TurnContext",
    "TurnGraphPorts",
    "TurnRunResult",
    "build_turn_graph",
    "decision_from_dict",
    "decision_to_dict",
    "turn_thread_id",
]
