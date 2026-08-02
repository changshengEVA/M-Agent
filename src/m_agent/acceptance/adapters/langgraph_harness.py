"""LangGraph-backed harness for P6 acceptance scenarios."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from m_agent.runtime.langgraph.engine import TransactionGraphEngine
from m_agent.runtime.langgraph.fake_effects import FakeEffectIntent
from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE
from m_agent.runtime.domain.contracts import (
    TransactionKind,
    TransactionRecord,
)

from .base import HarnessResult
from .shared_harness import SharedRuntimeHarness
from .transaction_fixtures import (
    FixtureTransactionStatus as TransactionStatus,
    apply_fixture_status,
)


class LangGraphV1Harness(SharedRuntimeHarness):
    """Reuse the shared Store while executing transaction steps via LangGraph."""

    runtime_id = "langgraph_v1"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        checkpoint_path = self._persist_root / "langgraph-checkpoints.sqlite3"
        self._graph_engine = TransactionGraphEngine(
            registry=self.registry,
            uow=self.uow,
            relay_feedback=self._relay_fake_feedback,
            checkpoint_db_path=checkpoint_path,
            persistent_checkpoint=True,
        )

    @property
    def graph_engine(self) -> TransactionGraphEngine:
        return self._graph_engine

    def _relay_fake_feedback(self, intent: FakeEffectIntent) -> str:
        thread_id = self._thread_by_conversation.get(
            intent.conversation_id,
            intent.conversation_id.split("::", 1)[0],
        )
        feedback_id = self.gateway.submit_execution_feedback(
            thread_id=thread_id,
            conversation_id=intent.conversation_id,
            transaction_id=intent.transaction_id,
            delegate_id=intent.delegate_id,
            activation_id=intent.activation_id,
            tool_history=[],
            summary=intent.result_summary,
            schedule_drainer=False,
        )
        return feedback_id

    def run_graph_script(
        self,
        *,
        conversation_id: str,
        transaction_id: str,
        script: List[Dict[str, Any]],
        thread_id: str = "",
    ) -> HarnessResult:
        try:
            result = self._graph_engine.run_script(
                conversation_id=conversation_id,
                transaction_id=transaction_id,
                script=script,
                thread_id=thread_id or transaction_id,
            )
        except Exception as exc:
            return HarnessResult(
                operation="run_graph_script",
                outcome="rejected",
                supported=True,
                reason=str(exc),
                data={
                    "conversation_id": conversation_id,
                    "transaction_id": transaction_id,
                },
            )
        record = self.registry.get(transaction_id)
        return HarnessResult(
            operation="run_graph_script",
            outcome="ok",
            supported=True,
            data={
                "graph_state": dict(result.state),
                "checkpoint_saved": result.checkpoint_saved,
                "transaction": (
                    self.transaction_snapshot(record)
                    if record is not None
                    else {}
                ),
            },
        )

    def resume_graph(
        self,
        *,
        transaction_id: str,
        script: Optional[List[Dict[str, Any]]] = None,
        script_index: int = 0,
        conversation_id: str = "",
    ) -> HarnessResult:
        try:
            result = self._graph_engine.resume_thread(
                thread_id=transaction_id,
                script=script,
                script_index=script_index,
                conversation_id=conversation_id,
                transaction_id=transaction_id,
            )
        except Exception as exc:
            return HarnessResult(
                operation="resume_graph",
                outcome="rejected",
                supported=True,
                reason=str(exc),
                data={"transaction_id": transaction_id},
            )
        record = self.registry.get(transaction_id)
        return HarnessResult(
            operation="resume_graph",
            outcome="ok",
            supported=True,
            data={
                "graph_state": dict(result.state),
                "transaction": (
                    self.transaction_snapshot(record)
                    if record is not None
                    else {}
                ),
            },
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
            runtime_engine=LANGGRAPH_RUNTIME_ENGINE,
        )
        return apply_fixture_status(self.registry, record, status)

    def restart_runtime(self) -> HarnessResult:
        self._graph_engine.close()
        return super().restart_runtime()

    def seed_attributed_transaction(
        self,
        *,
        conversation_id: str,
        wm_entries: Optional[List[Dict[str, Any]]] = None,
        goal: str = "",
    ) -> HarnessResult:
        record = self.create_runtime_transaction(
            conversation_id=conversation_id,
            status=TransactionStatus.RUNNING,
        )
        if wm_entries:
            record.wm_entries.extend(list(wm_entries))
        if goal:
            record.task_state.goal = goal
        return HarnessResult(
            operation="seed_attributed_transaction",
            outcome="ok",
            supported=True,
            data={
                "transaction": self.transaction_snapshot(record),
                "transaction_id": record.transaction_id,
                "activation_id": record.current_activation_id,
            },
        )

    def inject_fault(self, *, fault_point: str) -> HarnessResult:
        point = str(fault_point or "").strip()
        if point in {
            "after_uow_commit_before_checkpoint",
            "after_transaction_commit_before_checkpoint",
        }:
            self._graph_engine.arm_checkpoint_fault()
            return self._ok(
                "inject_fault",
                fault_point=point,
                armed=True,
                scope="langgraph_checkpoint",
            )
        return super().inject_fault(fault_point=point)

    def load_effects(self, *, transaction_id: str) -> HarnessResult:
        effects = self._graph_engine.fake_effects.list_for_transaction(
            transaction_id
        )
        if not effects:
            return super().load_effects(transaction_id=transaction_id)
        return self._ok(
            "load_effects",
            transaction_id=transaction_id,
            effects=[
                {
                    "effect_id": item.effect_id,
                    "capability": item.capability,
                    "delegate_id": item.delegate_id,
                    "activation_id": item.activation_id,
                    "idempotency_key": item.idempotency_key,
                    "status": item.status,
                    "attempts": item.attempts,
                    "visible_effects": item.visible_effects,
                }
                for item in effects
            ],
        )

    def close(self) -> None:
        self._graph_engine.close()
        super().close()


__all__ = ["LangGraphV1Harness"]
