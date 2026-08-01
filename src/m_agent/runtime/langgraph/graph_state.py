"""GraphState schema for the LangGraph transaction graph (P6 PoC → R2 turns)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


SCHEMA_VERSION = "langgraph_tx_v1"


class TransactionGraphState(TypedDict, total=False):
    """Checkpointed execution state; Transaction Store remains authoritative."""

    schema_version: str
    conversation_id: str
    transaction_id: str
    transaction_revision: int
    activation_id: str
    current_stimulus_ref: str
    transition_id: str
    graph_phase: str
    task_state_snapshot: Dict[str, Any]
    wm_snapshot: List[Dict[str, Any]]
    scene_tail_ref: str
    current_decision: str
    pending_delegate_intent: Optional[Dict[str, Any]]
    latest_feedback_ref: str
    script_index: int
    iteration_budget: int
    last_error: str
    last_transition_result: Dict[str, Any]
    paused_awaiting_user: bool
    _script: List[Dict[str, Any]]

    # R2 turn loop: thinking → delegate → feedback inside the graph.
    turn_index: int
    turn_kind: str
    think_rounds: int
    delegate_count: int
    delegate_chain: int
    decision: Optional[Dict[str, Any]]
    delegate_target: Optional[Dict[str, Any]]
    pending_user_request: str
    feedback_matched_pending: bool
    last_delegate_result: Dict[str, Any]
    reply_finalized: bool
    turn_completed: bool


def empty_graph_state(
    *,
    conversation_id: str,
    transaction_id: str,
) -> TransactionGraphState:
    return TransactionGraphState(
        schema_version=SCHEMA_VERSION,
        conversation_id=conversation_id,
        transaction_id=transaction_id,
        transaction_revision=0,
        activation_id="",
        current_stimulus_ref="",
        transition_id="",
        graph_phase="init",
        task_state_snapshot={},
        wm_snapshot=[],
        scene_tail_ref="",
        current_decision="continue",
        pending_delegate_intent=None,
        latest_feedback_ref="",
        script_index=0,
        iteration_budget=32,
        last_error="",
        last_transition_result={},
    )


def empty_turn_state(
    *,
    conversation_id: str,
    transaction_id: str,
) -> TransactionGraphState:
    """Cold-start state for the R2 turn graph (no scripted steps)."""

    state = empty_graph_state(
        conversation_id=conversation_id,
        transaction_id=transaction_id,
    )
    state.update(
        turn_index=0,
        turn_kind="",
        think_rounds=0,
        delegate_count=0,
        delegate_chain=0,
        decision=None,
        delegate_target=None,
        pending_user_request="",
        feedback_matched_pending=False,
        last_delegate_result={},
        reply_finalized=False,
        turn_completed=False,
    )
    return state
