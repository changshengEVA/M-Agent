"""LangGraph transaction graph definition for the P6 PoC."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Union

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .checkpointer import Checkpointer

from m_agent.runtime.domain.contracts import (
    TransactionRecord,
    TransactionState,
)

from .fake_effects import FakeEffectExecutor
from .graph_state import SCHEMA_VERSION, TransactionGraphState


ScriptStep = Dict[str, Any]
RegistryLike = Any


def _record_snapshot(record: TransactionRecord) -> Dict[str, Any]:
    return {
        "transaction_id": record.transaction_id,
        "conversation_id": record.conversation_id,
        "state": record.state.value,
        "revision": int(record.revision),
        "activation_id": record.current_activation_id,
        "active_delegate_id": record.active_delegate_id,
        "wm_entries": list(record.wm_entries),
        "task_state": record.task_state.to_dict(),
    }


def build_transaction_graph(
    *,
    registry: RegistryLike,
    uow: Any,
    fake_effects: FakeEffectExecutor,
    on_uow_committed: Optional[Callable[[], None]] = None,
) -> StateGraph:
    """Build a single-step transaction graph for the P6 PoC."""

    graph: StateGraph = StateGraph(TransactionGraphState)

    def load_transaction(state: TransactionGraphState) -> TransactionGraphState:
        tx_id = str(state.get("transaction_id", "") or "").strip()
        record = registry.get(tx_id)
        if record is None:
            return {
                **state,
                "graph_phase": "error",
                "last_error": f"unknown transaction: {tx_id}",
            }
        snapshot = _record_snapshot(record)
        return {
            **state,
            "schema_version": SCHEMA_VERSION,
            "conversation_id": record.conversation_id,
            "transaction_revision": int(record.revision),
            "activation_id": str(record.current_activation_id or ""),
            "task_state_snapshot": dict(snapshot["task_state"]),
            "wm_snapshot": list(snapshot["wm_entries"]),
            "graph_phase": "loaded",
            "current_decision": record.state.value,
        }

    def validate_activation(state: TransactionGraphState) -> TransactionGraphState:
        if state.get("graph_phase") == "error":
            return state
        tx_id = str(state.get("transaction_id", "") or "").strip()
        record = registry.get(tx_id)
        if record is None:
            return {
                **state,
                "graph_phase": "error",
                "last_error": "transaction missing during validation",
            }
        if record.state == TransactionState.ARCHIVE:
            return {
                **state,
                "graph_phase": "error",
                "last_error": "graph cannot produce archive",
            }
        return {
            **state,
            "graph_phase": "validated",
            "activation_id": str(record.current_activation_id or ""),
            "transaction_revision": int(record.revision),
        }

    def execute_step(state: TransactionGraphState) -> TransactionGraphState:
        if state.get("graph_phase") == "error":
            return state
        script: List[ScriptStep] = list(state.get("_script") or [])
        index = int(state.get("script_index", 0) or 0)
        if index >= len(script):
            return {**state, "graph_phase": "finished"}
        step = dict(script[index])
        action = str(step.get("action", "") or "").strip()
        tx_id = str(state.get("transaction_id", "") or "").strip()
        transition_id = str(
            step.get("transition_id")
            or f"{tx_id}:step:{index}:{action}"
        )
        record = registry.get(tx_id)
        if record is None:
            return {
                **state,
                "graph_phase": "error",
                "last_error": "transaction missing during execution",
            }

        def mutate_progress(
            transaction: Optional[TransactionRecord],
        ) -> Dict[str, Any]:
            assert transaction is not None
            for item in step.get("wm_entries", []):
                transaction.wm_entries.append(dict(item))
            goal = str(step.get("goal", "") or "").strip()
            if goal:
                transaction.task_state.goal = goal
            return {
                "action": "record_progress",
                "transaction_id": transaction.transaction_id,
                "activation_id": transaction.current_activation_id,
                "delegate_id": None,
                "effect_id": None,
                "revision": transaction.revision,
            }

        if action == "record_progress":
            result = uow.apply_transition(
                transition_id,
                {
                    "action": action,
                    "step_index": index,
                    **step,
                },
                tx_id,
                record.revision,
                mutate_progress,
            )
            if on_uow_committed is not None:
                on_uow_committed()
            updated = registry.get(tx_id) or record
            return {
                **state,
                "script_index": index + 1,
                "graph_phase": "step_committed",
                "transition_id": transition_id,
                "last_transition_result": dict(result),
                "transaction_revision": int(updated.revision),
                "wm_snapshot": list(updated.wm_entries),
                "task_state_snapshot": updated.task_state.to_dict(),
                "current_decision": updated.state.value,
            }

        if action == "delegate":
            delegate_id = str(step.get("delegate_id", "") or "").strip()
            capability = str(step.get("capability", "") or "").strip()
            effect_id = str(step.get("effect_id") or f"{tx_id}:{delegate_id}")
            idempotency_key = str(
                step.get("idempotency_key") or f"{capability}:{delegate_id}"
            )
            updated = registry.begin_delegate(
                tx_id,
                delegate_id,
                expected_revision=record.revision,
            )
            intent = fake_effects.dispatch(
                effect_id=effect_id,
                capability=capability,
                delegate_id=delegate_id,
                transaction_id=tx_id,
                activation_id=str(updated.current_activation_id or ""),
                conversation_id=updated.conversation_id,
                thread_id=str(step.get("thread_id", "") or ""),
                idempotency_key=idempotency_key,
            )
            if on_uow_committed is not None:
                on_uow_committed()
            return {
                **state,
                "script_index": index + 1,
                "graph_phase": "awaiting_feedback",
                "transition_id": transition_id,
                "last_transition_result": {
                    "action": "delegate",
                    "transaction_id": tx_id,
                    "activation_id": updated.current_activation_id,
                    "delegate_id": delegate_id,
                    "effect_id": intent.effect_id,
                },
                "pending_delegate_intent": {
                    "delegate_id": delegate_id,
                    "capability": capability,
                    "effect_id": intent.effect_id,
                    "idempotency_key": idempotency_key,
                },
                "transaction_revision": int(updated.revision),
                "activation_id": str(updated.current_activation_id or ""),
                "current_decision": updated.state.value,
            }

        if action == "pause":
            updated = registry.pause(
                tx_id,
                expected_revision=record.revision,
            )
            if on_uow_committed is not None:
                on_uow_committed()
            return {
                **state,
                "script_index": index + 1,
                "graph_phase": "paused",
                "current_decision": updated.state.value,
                "transaction_revision": int(updated.revision),
                "activation_id": str(updated.current_activation_id or ""),
                "pending_delegate_intent": None,
                "last_transition_result": {
                    "action": "pause",
                    "transaction_id": tx_id,
                    "activation_id": updated.current_activation_id,
                },
            }

        if action == "restore":
            updated = registry.restore(
                tx_id,
                source=str(step.get("source", "ui")),
                expected_revision=record.revision,
            )
            if on_uow_committed is not None:
                on_uow_committed()
            return {
                **state,
                "script_index": index + 1,
                "graph_phase": "restored",
                "current_decision": updated.state.value,
                "transaction_revision": int(updated.revision),
                "activation_id": str(updated.current_activation_id or ""),
                "last_transition_result": {
                    "action": "restore",
                    "transaction_id": tx_id,
                    "activation_id": updated.current_activation_id,
                },
            }

        if action == "complete":
            updated = registry.complete(
                tx_id,
                expected_revision=record.revision,
            )
            if on_uow_committed is not None:
                on_uow_committed()
            return {
                **state,
                "script_index": index + 1,
                "graph_phase": "completed",
                "current_decision": updated.state.value,
                "transaction_revision": int(updated.revision),
                "activation_id": str(updated.current_activation_id or ""),
                "pending_delegate_intent": None,
                "last_transition_result": {
                    "action": "complete",
                    "transaction_id": tx_id,
                    "activation_id": updated.current_activation_id,
                },
            }

        return {
            **state,
            "graph_phase": "error",
            "last_error": f"unsupported script action: {action}",
        }

    graph.add_node("load_transaction", load_transaction)
    graph.add_node("validate_activation", validate_activation)
    graph.add_node("execute_step", execute_step)
    graph.add_edge(START, "load_transaction")
    graph.add_edge("load_transaction", "validate_activation")
    graph.add_edge("validate_activation", "execute_step")
    graph.add_edge("execute_step", END)
    return graph


def compile_transaction_graph(
    *,
    registry: RegistryLike,
    uow: Any,
    fake_effects: FakeEffectExecutor,
    on_uow_committed: Optional[Callable[[], None]] = None,
    checkpointer: Optional[Checkpointer] = None,
):
    graph = build_transaction_graph(
        registry=registry,
        uow=uow,
        fake_effects=fake_effects,
        on_uow_committed=on_uow_committed,
    )
    saver = checkpointer or InMemorySaver()
    return graph.compile(checkpointer=saver), saver


__all__ = [
    "build_transaction_graph",
    "compile_transaction_graph",
]
