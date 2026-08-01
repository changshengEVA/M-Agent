"""Orchestrate the LangGraph transaction graph against the shared Store."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from langgraph.checkpoint.memory import InMemorySaver

from .checkpointer import Checkpointer, close_checkpointer, create_checkpointer
from .fake_effects import FakeEffectExecutor
from .graph_state import TransactionGraphState, empty_graph_state
from .transaction_graph import compile_transaction_graph


@dataclass
class GraphRunResult:
    state: TransactionGraphState
    checkpoint_saved: bool = True


@dataclass
class TransactionGraphEngine:
    """Run scripted transaction steps through LangGraph with durable checkpoints."""

    registry: Any
    uow: Any
    relay_feedback: Callable[[Any], str]
    fake_effects: FakeEffectExecutor = field(default_factory=FakeEffectExecutor)
    checkpoint_db_path: Optional[Path] = None
    persistent_checkpoint: bool = True
    _checkpoint_fault_armed: bool = field(default=False, init=False)
    _suppress_next_checkpoint: bool = field(default=False, init=False)
    _checkpointer: Checkpointer = field(default_factory=InMemorySaver, init=False)
    _compiled: Any = field(default=None, init=False)
    _last_thread_id: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.fake_effects.install_relay(self.relay_feedback)
        self._recompile()

    def _recompile(self) -> None:
        self._checkpointer = create_checkpointer(
            db_path=self.checkpoint_db_path,
            persistent=self.persistent_checkpoint,
        )
        self._compiled, self._checkpointer = compile_transaction_graph(
            registry=self.registry,
            uow=self.uow,
            fake_effects=self.fake_effects,
            on_uow_committed=self._on_uow_committed,
            checkpointer=self._checkpointer,
        )

    def _on_uow_committed(self) -> None:
        if self._checkpoint_fault_armed:
            self._suppress_next_checkpoint = True

    def arm_checkpoint_fault(self) -> None:
        self._checkpoint_fault_armed = True

    def disarm_checkpoint_fault(self) -> None:
        self._checkpoint_fault_armed = False
        self._suppress_next_checkpoint = False

    def run_once(
        self,
        *,
        conversation_id: str,
        transaction_id: str,
        script: List[Dict[str, Any]],
        script_index: int = 0,
        thread_id: Optional[str] = None,
    ) -> GraphRunResult:
        thread = str(thread_id or transaction_id or "").strip()
        self._last_thread_id = thread
        state = empty_graph_state(
            conversation_id=conversation_id,
            transaction_id=transaction_id,
        )
        state["_script"] = list(script)
        state["script_index"] = int(script_index)
        config = {"configurable": {"thread_id": thread}}
        checkpoint_saved = True
        try:
            final = self._compiled.invoke(state, config=config)
        finally:
            if self._suppress_next_checkpoint:
                checkpoint_saved = False
                self._suppress_next_checkpoint = False
        return GraphRunResult(state=dict(final), checkpoint_saved=checkpoint_saved)

    def run_script(
        self,
        *,
        conversation_id: str,
        transaction_id: str,
        script: List[Dict[str, Any]],
        thread_id: Optional[str] = None,
    ) -> GraphRunResult:
        thread = str(thread_id or transaction_id or "").strip()
        index = 0
        last = GraphRunResult(
            state=empty_graph_state(
                conversation_id=conversation_id,
                transaction_id=transaction_id,
            )
        )
        while index < len(script):
            last = self.run_once(
                conversation_id=conversation_id,
                transaction_id=transaction_id,
                script=script,
                script_index=index,
                thread_id=thread,
            )
            phase = str(last.state.get("graph_phase", "") or "")
            if phase in {"awaiting_feedback", "error", "finished"}:
                break
            index = int(last.state.get("script_index", index + 1) or index + 1)
        return last

    def resume_thread(
        self,
        *,
        thread_id: Optional[str] = None,
        script: Optional[List[Dict[str, Any]]] = None,
        script_index: int = 0,
        conversation_id: str = "",
        transaction_id: str = "",
    ) -> GraphRunResult:
        thread = str(thread_id or self._last_thread_id or "").strip()
        config = {"configurable": {"thread_id": thread}}
        snapshot = self._compiled.get_state(config)
        if snapshot.values:
            state = dict(snapshot.values)
            if script is not None:
                state["_script"] = list(script)
            if script_index:
                state["script_index"] = int(script_index)
            final = self._compiled.invoke(state, config=config)
            return GraphRunResult(state=dict(final), checkpoint_saved=True)
        return self.run_once(
            conversation_id=conversation_id,
            transaction_id=transaction_id or thread,
            script=list(script or []),
            script_index=script_index,
            thread_id=thread,
        )

    def get_checkpoint_state(
        self,
        *,
        thread_id: Optional[str] = None,
    ) -> TransactionGraphState:
        thread = str(thread_id or self._last_thread_id or "").strip()
        config = {"configurable": {"thread_id": thread}}
        snapshot = self._compiled.get_state(config)
        return dict(snapshot.values or {})

    def reset(self) -> None:
        self.fake_effects.reset()
        self.disarm_checkpoint_fault()
        self.close()
        self._recompile()

    def close(self) -> None:
        close_checkpointer(self._checkpointer)
        self._compiled = None
