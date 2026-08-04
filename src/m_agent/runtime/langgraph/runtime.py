"""Production LangGraph runtime host (MVP / R2 turn loop / R3 Chat API)."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS
from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent
from m_agent.paths import chat_user_persistence_root, chat_user_slug
from m_agent.runtime.host import product_views
from m_agent.runtime.host.emitting_scene import EmittingSceneWriter
from m_agent.runtime.host.flush_orchestrator import RuntimeFlushOrchestrator
from m_agent.runtime.host.protocol import RuntimeHost, ThreadEventEmitter
from m_agent.runtime.langgraph.config import (
    LangGraphRuntimeConfig,
    load_langgraph_config,
)
from m_agent.runtime.langgraph.engine import TransactionGraphEngine
from m_agent.runtime.langgraph.inbox_loop import LangGraphInboxLoop, relay_fake_feedback
from m_agent.runtime.langgraph.turn_graph import (
    TransactionTurnEngine,
    TurnGraphPorts,
)
from m_agent.runtime.langgraph.turn_ports import (
    DelegateEffectLedger,
    DelegateExecutor,
    ExecutionAgentDelegateExecutor,
    FakeToolDelegateExecutor,
    ThinkingAgentPlanner,
)
from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE
from m_agent.runtime.transaction_control import (
    TransactionFencedSceneWriter,
    delete_runtime_transaction,
)
from m_agent.runtime.config import RuntimeConfig, load_runtime_config
from m_agent.runtime.domain.contracts import (
    PauseReason,
    SceneEntry,
    Stimulus,
    StimulusEnvelope,
    StimulusKind,
    TransactionKind,
)
from m_agent.runtime.dispatch.drainer import ThreadDrainerService
from m_agent.runtime.perception.attributor import TransactionAttributor
from m_agent.runtime.perception.gateway import PerceptionGateway
from m_agent.runtime.perception.inbox import StimulusInbox
from m_agent.runtime.dispatch.cpu_state import THREAD_CPU_STATE
from m_agent.runtime.transaction import RuntimeUnitOfWork
from m_agent.runtime.transaction.effects import EffectCoordinator
from m_agent.runtime.transaction.flush import FlushCoordinator
from m_agent.runtime.transaction.registry import TransactionRegistry
from m_agent.runtime.transaction.store import SQLiteRuntimeStore
from m_agent.runtime.transaction.predicates import (
    is_open_continue,
    is_runnable_record,
)
from m_agent.systems.scene import build_default_scene_system
from m_agent.systems.wm import build_default_wm_system

logger = logging.getLogger(__name__)

ReplyCallback = Callable[[str, str, str, bool], None]
HistoryProvider = Callable[[str], Optional[List[Dict[str, Any]]]]
_RETIRED_RUNTIME_DATABASE_NAME = "".join(
    ("think", "_", "life", ".sqlite3")
)


class LangGraphRuntime:
    """LangGraph-backed product runtime and durable product host."""

    def __init__(
        self,
        agent: ThreeLayerChatAgent,
        *,
        config: Optional[RuntimeConfig] = None,
        engine_config: Optional[LangGraphRuntimeConfig] = None,
        owner_id: str = "anonymous",
        persist_root: Optional[Path | str] = None,
        on_reply: Optional[ReplyCallback] = None,
    ) -> None:
        self.agent = agent
        self.owner_id = str(owner_id or "anonymous").strip() or "anonymous"
        raw_runtime = (
            agent.config.get("runtime")
            if isinstance(agent.config.get("runtime"), dict)
            else {}
        )
        self.config = config or load_runtime_config(
            raw_runtime.get("common")
            if isinstance(raw_runtime.get("common"), dict)
            else {}
        )
        self.engine_config = engine_config or load_langgraph_config(
            raw_runtime.get("langgraph")
            if isinstance(raw_runtime.get("langgraph"), dict)
            else {}
        )
        thinking_agent = getattr(agent, "thinking_agent", None)
        if thinking_agent is not None and hasattr(thinking_agent, "thinking_mode"):
            thinking_agent.thinking_mode = self.engine_config.thinking_mode
        self._external_on_reply = on_reply
        self._reply_lock = threading.Lock()
        self._last_replies: Dict[str, List[str]] = {}
        self._history_provider: Optional[HistoryProvider] = None

        if persist_root is not None:
            self._persistence_root = Path(persist_root).resolve()
        else:
            self._persistence_root = chat_user_persistence_root(
                chat_user_slug(self.owner_id)
            )
        self._persistence_root.mkdir(parents=True, exist_ok=True)
        runtime_dir = self._persistence_root / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        retired_database = runtime_dir / _RETIRED_RUNTIME_DATABASE_NAME
        if retired_database.is_file():
            raise RuntimeError(
                "retired runtime database remains in the online persistence "
                "directory; run the separately authorized retirement backup, "
                "audit, and quarantine batch before starting the final runtime"
            )
        scene_dir = self._persistence_root / "scene"

        self.runtime_store = SQLiteRuntimeStore(runtime_dir / "langgraph.sqlite3")
        self.registry = TransactionRegistry(
            store=self.runtime_store,
            default_runtime_engine=LANGGRAPH_RUNTIME_ENGINE,
        )
        self.uow = RuntimeUnitOfWork(self.registry)
        self.inbox = StimulusInbox(store=self.runtime_store)
        self.scene_system = build_default_scene_system(
            persist_dir=scene_dir,
            persist_enabled=self.config.scene_persist_jsonl,
            runtime_store=self.runtime_store,
        )

        self._thread_event_emitter: Optional[ThreadEventEmitter] = None
        self._drain_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._emitting_writer = EmittingSceneWriter(
            self.scene_system.writer,
            self._on_scene_appended,
        )
        self._transaction_scene_writer = TransactionFencedSceneWriter(
            self.registry,
            self._emitting_writer,
        )

        self.gateway = PerceptionGateway(
            inbox=self.inbox,
            attributor=None,  # type: ignore[arg-type]
            scene_writer=self._transaction_scene_writer,
            on_enqueued=self._on_stimulus_enqueued,
        )
        self.graph_engine = TransactionGraphEngine(
            registry=self.registry,
            uow=self.uow,
            relay_feedback=lambda intent: relay_fake_feedback(
                self.gateway,
                intent,
            ),
            checkpoint_db_path=runtime_dir / "langgraph-checkpoints.sqlite3",
            persistent_checkpoint=True,
        )
        self.attributor = TransactionAttributor(
            registry=self.registry,
            config=self.config,
            semantic_resolver=agent.thinking_agent.resolve_transaction,
            scene_writer=self._transaction_scene_writer,
        )
        self.gateway.attributor = self.attributor
        self.effect_coordinator = EffectCoordinator(
            store=self.runtime_store,
            registry=self.registry,
            feedback_relay=self._relay_effect_feedback,
        )
        self.flush_coordinator = FlushCoordinator(
            store=self.runtime_store,
            registry=self.registry,
        )
        self.flush_orchestrator = RuntimeFlushOrchestrator(
            self,
            journal_path=runtime_dir / "runtime-flush.sqlite3",
        )
        self.turn_engine = self._build_turn_engine(runtime_dir)

        self.loop = LangGraphInboxLoop(
            registry=self.registry,
            inbox=self.inbox,
            attributor=self.attributor,
            graph_engine=self.graph_engine,
            scene_writer=self._transaction_scene_writer,
            scene_reader=self.scene_system.reader,
            scene_context_max_entries=self.config.scene_context_max_entries,
            turn_engine=self.turn_engine,
            on_runtime_updated=self._emit_runtime_updated,
        )
        self.drainer = ThreadDrainerService(
            drain_fn=self._drain_for_thread,
            get_pending=lambda tid: self.inbox.pending_count(tid),
            build_emitter=lambda _tid: None,
            get_history=lambda _tid: None,
            on_runtime_updated=self._emit_runtime_updated,
        )
        self.recover_pending_effect_feedback(schedule_drainers=True)

    def _build_turn_engine(
        self,
        runtime_dir: Path,
    ) -> Optional[TransactionTurnEngine]:
        """Build the R2 turn engine, or ``None`` when rolled back to the MVP."""

        if not self.engine_config.turn_loop_enabled:
            logger.info(
                "LangGraph turn loop disabled; falling back to MVP record_progress"
            )
            return None
        systems = getattr(self.agent, "systems", None)
        wm_system = getattr(systems, "wm", None) or build_default_wm_system()
        ports = TurnGraphPorts(
            registry=self.registry,
            uow=self.uow,
            gateway=self.gateway,
            planner=ThinkingAgentPlanner(
                thinking_agent=self.agent.thinking_agent,
            ),
            delegate_executor=self._build_delegate_executor(),
            scene_writer=self._transaction_scene_writer,
            scene_reader=self.scene_system.reader,
            wm_system=wm_system,
            runtime_config=self.config,
            langgraph_config=self.engine_config,
            effect_ledger=DelegateEffectLedger(
                coordinator=self.effect_coordinator,
                delivery_guarantee=self.engine_config.delivery_guarantee,
                capability_registry=getattr(
                    getattr(self.agent, "execution_agent", None),
                    "registry",
                    None,
                ),
            ),
        )
        return TransactionTurnEngine(
            ports=ports,
            checkpoint_db_path=runtime_dir / "langgraph-turn-checkpoints.sqlite3",
            persistent_checkpoint=True,
        )

    def _build_delegate_executor(self) -> DelegateExecutor:
        def transaction_is_deleted(transaction_id: str) -> bool:
            record = self.registry.store.load_transaction(transaction_id)
            return bool(record is not None and record.deleted)

        if self.engine_config.uses_execution_agent:
            execution_agent = getattr(self.agent, "execution_agent", None)
            if execution_agent is None:
                raise ValueError(
                    "delegate_executor=execution_agent requires an agent with "
                    "an execution layer"
                )
            return ExecutionAgentDelegateExecutor(
                execution_agent=execution_agent,
                scene_writer=self._transaction_scene_writer,
                on_reply=self._handle_reply,
                transaction_is_deleted=transaction_is_deleted,
                on_schedule_created=self._record_schedule_created,
            )
        return FakeToolDelegateExecutor(
            scene_writer=self._transaction_scene_writer,
            capabilities=self.engine_config.fake_capabilities,
            on_reply=self._handle_reply,
            transaction_is_deleted=transaction_is_deleted,
        )

    @property
    def runtime_engine_id(self) -> str:
        return LANGGRAPH_RUNTIME_ENGINE

    @property
    def turn_loop_enabled(self) -> bool:
        return self.turn_engine is not None

    def set_thread_event_emitter(
        self,
        emitter: Optional[ThreadEventEmitter],
    ) -> None:
        self._thread_event_emitter = emitter

    def set_history_provider(self, provider: Optional[HistoryProvider]) -> None:
        self._history_provider = provider

    def set_schedule_lifecycle(self, hook: Any) -> None:
        """Bridge scheduled-plan processing to the product schedule store."""

        self.loop._schedule_lifecycle = hook

    def _record_schedule_created(
        self,
        *,
        transaction_id: str,
        schedule_id: str,
        due_at: str,
        owner_id: str = "",
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        del result
        self.registry.record_schedule_intent(
            transaction_id,
            schedule_id=schedule_id,
            due_at=due_at,
            owner_id=owner_id,
        )

    def _emit_thread_event(
        self,
        thread_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        sink = self._thread_event_emitter
        if sink is None:
            return
        try:
            sink(thread_id, event_type, payload)
        except Exception:
            logger.exception(
                "LangGraph thread event failed type=%s thread_id=%s",
                event_type,
                thread_id,
            )

    def _on_scene_appended(self, conversation_id: str, entry: SceneEntry) -> None:
        thread_id = str(conversation_id or "").rsplit("::", 1)[0]
        self._emit_thread_event(thread_id, "scene_entry_appended", entry.to_dict())

    def _handle_reply(
        self,
        thread_id: str,
        transaction_id: str,
        message: str,
        finalize: bool,
    ) -> None:
        with self.registry._lock:
            persisted = self.registry.store.load_transaction(transaction_id)
            if persisted is None or persisted.deleted:
                return
            with self._reply_lock:
                self._last_replies.setdefault(thread_id, []).append(
                    str(message or "").strip()
                )
            record = self.registry.get(transaction_id)
            delegate_id = ""
            if record is not None:
                delegate_id = str(record.active_delegate_id or "")
            if finalize and str(transaction_id or "").strip():
                try:
                    self.registry.mark_reply_finalized(transaction_id)
                except Exception:
                    logger.exception(
                        "failed to mark reply finalized txn=%s",
                        transaction_id,
                    )
                    current = self.registry.store.load_transaction(
                        transaction_id
                    )
                    if current is None or current.deleted:
                        return
            self._emit_thread_event(
                thread_id,
                "reply_emitted",
                {
                    "message": str(message or "").strip(),
                    "finalize": bool(finalize),
                    "transaction_id": transaction_id,
                    "delegate_id": delegate_id,
                },
            )
            if self._external_on_reply is not None:
                self._external_on_reply(thread_id, transaction_id, message, finalize)

    def _emit_runtime_updated(self, thread_id: str) -> None:
        snap = THREAD_RUNTIME_STATUS.snapshot(thread_id)
        self._emit_thread_event(
            thread_id,
            "thread_runtime_updated",
            {"thread_runtime": snap.to_dict()},
        )

    def _on_stimulus_enqueued(
        self,
        stimulus: Any,
        *,
        schedule_drainer: bool = True,
    ) -> None:
        tid = str(getattr(stimulus, "thread_id", "") or "").strip()
        THREAD_RUNTIME_STATUS.set_preempt_enabled(
            tid,
            self.config.scheduler.preempt_enabled,
        )
        pending = self.inbox.pending_count(tid)
        THREAD_RUNTIME_STATUS.set_pending_stimuli(tid, pending)
        self._emit_thread_event(
            tid,
            "stimulus_queued",
            {
                "stimulus_id": str(getattr(stimulus, "stimulus_id", "") or ""),
                "pending_count": pending,
            },
        )
        if schedule_drainer:
            self.drainer.ensure_running(tid)
        self._emit_runtime_updated(tid)

    def _drain_for_thread(
        self,
        thread_id: str,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        return self.run_thread(
            thread_id,
            history_messages=history_messages,
            event_emitter=event_emitter,
        )

    def _relay_effect_feedback(self, delivery: Dict[str, Any]) -> Dict[str, Any]:
        """Relay one durable outbox item through canonical Feedback ingress."""

        effect = dict(delivery.get("effect") or {})
        outcome = dict(delivery.get("outcome") or {})
        ingress_key = str(delivery.get("ingress_key", "") or "").strip()
        effect_id = str(effect.get("effect_id", "") or "").strip()
        transaction_id = str(
            effect.get("transaction_id", "") or ""
        ).strip()
        record = self.runtime_store.load_transaction(transaction_id)
        if record is None:
            raise ValueError(
                f"effect Feedback transaction not found: {transaction_id}"
            )
        if record.deleted:
            raise ValueError(
                f"effect Feedback transaction deleted: {transaction_id}"
            )
        summary = str(outcome.get("summary", "") or "").strip()
        tool_history = list(outcome.get("tool_history") or [])
        readable = summary or "Execution finished and returned tool evidence."
        stimulus_id = f"stim_{effect_id}"
        stimulus = StimulusEnvelope(
            stimulus_id=stimulus_id,
            ingress_key=ingress_key or None,
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            stimulus=Stimulus(
                kind=StimulusKind.EXECUTION_FEEDBACK,
                text=readable,
                payload={
                    "activation_id": str(effect.get("activation_id", "") or ""),
                    "delegate_id": str(effect.get("delegate_id", "") or ""),
                    "effect_id": effect_id,
                    "tool_history": tool_history,
                    "summary": summary,
                    "effect_status": str(effect.get("status", "") or ""),
                },
            ),
            occurred_at=datetime.now(timezone.utc).isoformat().replace(
                "+00:00",
                "Z",
            ),
            transaction_id=transaction_id,
            activation_id=str(effect.get("activation_id", "") or "") or None,
            delegate_id=str(effect.get("delegate_id", "") or "") or None,
        )
        stored_id = self.gateway.submit(stimulus, schedule_drainer=False)
        stored = self.runtime_store.load_stimulus(stored_id)
        return {
            "stimulus_id": stored_id,
            "thread_id": record.thread_id,
            "transaction_id": transaction_id,
            "disposition": str(
                getattr(stored, "disposition", "") or ""
            ),
            "disposition_reason": str(
                getattr(stored, "disposition_reason", "") or ""
            ),
        }

    def recover_pending_effect_feedback(
        self,
        *,
        schedule_drainers: bool = False,
    ) -> Dict[str, Any]:
        recovery = self.effect_coordinator.recover_pending_relays()
        if schedule_drainers:
            for item in list(recovery.get("recovered") or []):
                relay_result = item.get("relay_result")
                if not isinstance(relay_result, dict):
                    continue
                thread_id = str(relay_result.get("thread_id", "") or "").strip()
                if thread_id:
                    self.drainer.ensure_running(thread_id)
        return recovery

    def submit_user_message(
        self,
        *,
        thread_id: str,
        conversation_id: Optional[str] = None,
        text: str,
        payload: Optional[dict] = None,
        schedule_drainer: bool = True,
    ) -> str:
        return self.gateway.submit_user_message(
            thread_id=thread_id,
            conversation_id=str(conversation_id or "").strip()
            or f"{thread_id}::0",
            text=text,
            payload=payload,
            schedule_drainer=schedule_drainer,
        )

    def run_thread(
        self,
        thread_id: str,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del kwargs
        tid = str(thread_id or "").strip()
        resolved_history = history_messages
        if resolved_history is None and self._history_provider is not None:
            try:
                resolved_history = self._history_provider(tid)
            except Exception:
                logger.exception(
                    "LangGraph history provider failed thread_id=%s", tid
                )
                resolved_history = None
        with self._drain_locks[tid]:
            with self._reply_lock:
                self._last_replies.pop(tid, None)
            results = self.loop.drain_thread(
                tid,
                history_messages=resolved_history,
                event_emitter=event_emitter,
            )
            THREAD_RUNTIME_STATUS.set_pending_stimuli(
                tid,
                self.inbox.pending_count(tid),
            )
            with self._reply_lock:
                replies = list(self._last_replies.get(tid, []))
            answer = replies[-1] if replies else ""
            return {
                "success": bool(results)
                and all(r.get("success") for r in results),
                "thread_id": tid,
                "results": results,
                "replies": replies,
                "answer": answer,
                "runtime_engine_id": self.runtime_engine_id,
                "turn_loop": self.turn_loop_enabled,
                "thinking_mode": self.engine_config.thinking_mode,
            }

    def advance_transaction(
        self,
        transaction_id: str,
        *,
        user_text: str,
        transition_id: str = "",
    ) -> Dict[str, Any]:
        """Run one MVP progress step for an existing LangGraph transaction.

        Bypasses the R2 turn loop on purpose: this is the scripted single-step
        entry used by dev tooling, not the thinking/delegate path.
        """

        record = self.registry.get(transaction_id)
        if record is None:
            raise ValueError(f"unknown transaction: {transaction_id}")
        if record.runtime_engine != LANGGRAPH_RUNTIME_ENGINE:
            raise ValueError(
                "transaction is not owned by LangGraph runtime: "
                f"{record.runtime_engine!r}"
            )
        activation = (
            self.registry.store.load_activation(record.current_activation_id)
            if record.current_activation_id
            else None
        )
        if not is_runnable_record(record, activation):
            raise ValueError(f"transaction is not runnable: {transaction_id}")
        tid = str(transition_id or f"lg-host:{transaction_id}:manual").strip()
        graph_result = self.graph_engine.run_script(
            conversation_id=record.conversation_id,
            transaction_id=transaction_id,
            script=[
                {
                    "action": "record_progress",
                    "transition_id": tid,
                    "wm_entries": [{"utterance": user_text}],
                    "goal": user_text[:240],
                }
            ],
            thread_id=transaction_id,
        )
        updated = self.registry.get(transaction_id) or record
        return {
            "success": graph_result.state.get("graph_phase") != "error",
            "transaction_id": transaction_id,
            "runtime_engine": updated.runtime_engine,
            "graph_phase": graph_result.state.get("graph_phase"),
            "revision": int(updated.revision),
        }

    def submit_stimulus_async(
        self,
        *,
        thread_id: str,
        conversation_id: Optional[str] = None,
        text: str,
        payload: Optional[dict] = None,
    ) -> Dict[str, Any]:
        stimulus_id = self.submit_user_message(
            thread_id=thread_id,
            conversation_id=conversation_id,
            text=text,
            payload=payload,
        )
        pending = self.inbox.pending_count(thread_id)
        snap = THREAD_RUNTIME_STATUS.snapshot(thread_id)
        return {
            "stimulus_id": stimulus_id,
            "thread_id": thread_id,
            "pending_count": pending,
            "effective_depth": snap.effective_depth,
            "runtime_phase": snap.runtime_phase,
            "accepted": True,
        }

    def force_stop_thread(
        self,
        thread_id: str,
        *,
        reason: str = "user_requested",
    ) -> Dict[str, Any]:
        tid = str(thread_id or "").strip()
        if not tid:
            raise ValueError("thread_id is required")
        in_flight = THREAD_CPU_STATE.get_in_flight(tid)
        in_flight_transaction_id = (
            str(in_flight.transaction_id or "").strip() if in_flight is not None else ""
        )
        cancelled_in_flight = THREAD_CPU_STATE.cancel_in_flight(tid, force=True)
        cleared_pending = self.inbox.clear_thread(tid)
        THREAD_RUNTIME_STATUS.set_pending_stimuli(tid, self.inbox.pending_count(tid))
        paused_transactions: List[str] = []
        candidate_ids: List[str] = []
        if in_flight_transaction_id:
            candidate_ids.append(in_flight_transaction_id)
        candidate_ids.extend(
            record.transaction_id
            for record in self.registry.list_for_thread(tid)
            if record.kind == TransactionKind.USER_TASK and is_open_continue(record)
        )
        seen: set[str] = set()
        for txn_id in candidate_ids:
            if not txn_id or txn_id in seen:
                continue
            seen.add(txn_id)
            record = self.registry.get(txn_id)
            if record is None or not is_open_continue(record):
                continue
            try:
                self.registry.pause(txn_id, reason=PauseReason.MANUAL_HOLD)
                paused_transactions.append(txn_id)
            except Exception:
                logger.exception(
                    "LangGraph force_stop pause failed txn=%s thread=%s",
                    txn_id,
                    tid,
                )
        self._emit_thread_event(
            tid,
            "thinking_force_stopped",
            {
                "thread_id": tid,
                "reason": str(reason or "user_requested").strip() or "user_requested",
                "cancelled_in_flight": bool(cancelled_in_flight),
                "cleared_pending_stimuli": int(cleared_pending),
                "paused_transactions": paused_transactions,
            },
        )
        self._emit_runtime_updated(tid)
        snap = THREAD_RUNTIME_STATUS.snapshot(tid)
        return {
            "success": True,
            "thread_id": tid,
            "runtime_profile": self.runtime_engine_id,
            "cancelled_in_flight": bool(cancelled_in_flight),
            "cleared_pending_stimuli": int(cleared_pending),
            "paused_transactions": paused_transactions,
            "thread_runtime": snap.to_dict(),
        }

    def enqueue_schedule(
        self,
        *,
        thread_id: str,
        conversation_id: Optional[str] = None,
        schedule_id: str,
        text: str,
        payload: Optional[dict] = None,
        run_id: str = "",
        owner_id: str = "",
    ) -> Dict[str, Any]:
        body = dict(payload or {})
        if run_id:
            body["run_id"] = run_id
        if owner_id:
            body["owner_id"] = owner_id
        stimulus_id = self.gateway.submit_heartbeat(
            thread_id=thread_id,
            conversation_id=str(conversation_id or "").strip() or f"{thread_id}::0",
            schedule_id=schedule_id,
            text=text,
            payload=body,
        )
        pending = self.inbox.pending_count(thread_id)
        snap = THREAD_RUNTIME_STATUS.snapshot(thread_id)
        return {
            "stimulus_id": stimulus_id,
            "thread_id": thread_id,
            "schedule_id": schedule_id,
            "run_id": run_id,
            "pending_count": pending,
            "effective_depth": snap.effective_depth,
            "runtime_phase": snap.runtime_phase,
            "accepted": True,
        }

    def list_transactions(
        self,
        thread_id: str,
        *,
        conversation_id: Optional[str] = None,
        include_history: bool = False,
    ) -> Dict[str, Any]:
        return product_views.list_transactions(
            self,
            thread_id,
            conversation_id=conversation_id,
            include_history=include_history,
        )

    def delete_transaction(
        self,
        *,
        thread_id: str,
        conversation_id: str,
        transaction_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        return delete_runtime_transaction(
            self,
            thread_id=thread_id,
            conversation_id=conversation_id,
            transaction_id=transaction_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def list_scene(
        self,
        thread_id: str,
        *,
        conversation_id: Optional[str] = None,
        limit: int = 40,
        before_seq: Optional[int] = None,
        since_flush: bool = True,
    ) -> Dict[str, Any]:
        return product_views.list_scene(
            self,
            thread_id,
            conversation_id=conversation_id,
            limit=limit,
            before_seq=before_seq,
            since_flush=since_flush,
        )

    def _commit_flush_segment(
        self,
        thread_id: str,
        *,
        conversation_id: Optional[str] = None,
        flush_id: Optional[str] = None,
        through_seq: Optional[int] = None,
        eligible_revisions: Optional[Mapping[str, int]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return product_views.on_flush_segment(
            self,
            thread_id,
            conversation_id=conversation_id,
            flush_id=flush_id,
            through_seq=through_seq,
            eligible_revisions=eligible_revisions,
            payload=payload,
            emit_runtime_updated=self._emit_runtime_updated,
        )

    def prepare_flush_segment(
        self,
        thread_id: str,
        *,
        conversation_id: str,
        source: str = "chat_api_thread_flush",
    ) -> Dict[str, Any]:
        return self.flush_orchestrator.prepare(
            thread_id,
            conversation_id=conversation_id,
            source=source,
        )

    def stage_flush_materialization(
        self,
        flush_id: str,
        *,
        destination: str,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        return self.flush_orchestrator.stage_materialization(
            flush_id,
            destination=destination,
            payload=payload,
        )

    def mark_flush_materialization_delivered(
        self,
        flush_id: str,
        *,
        destination: str,
        result: Mapping[str, Any],
    ) -> Dict[str, Any]:
        return self.flush_orchestrator.mark_materialization_delivered(
            flush_id,
            destination=destination,
            result=result,
        )

    def on_flush_segment(
        self,
        thread_id: str,
        *,
        conversation_id: Optional[str] = None,
        flush_id: Optional[str] = None,
        through_seq: Optional[int] = None,
        eligible_revisions: Optional[Mapping[str, int]] = None,
        payload: Optional[Dict[str, Any]] = None,
        flush_snapshot: Optional[Mapping[str, Any]] = None,
        defer_completion: bool = False,
    ) -> Dict[str, Any]:
        """Commit a staged product flush, or support a direct internal commit."""

        if flush_snapshot is not None:
            return self.flush_orchestrator.commit_runtime(
                thread_id,
                conversation_id=str(conversation_id or "").strip(),
                flush_snapshot=flush_snapshot,
                defer_completion=defer_completion,
                payload=payload,
            )
        return self._commit_flush_segment(
            thread_id,
            conversation_id=conversation_id,
            flush_id=flush_id,
            through_seq=through_seq,
            eligible_revisions=eligible_revisions,
            payload=payload,
        )

    def complete_flush_segment(self, flush_id: str) -> Dict[str, Any]:
        return self.flush_orchestrator.complete(flush_id)

    def ensure_scene_thread_loaded(self, conversation_id: str) -> None:
        product_views.ensure_scene_thread_loaded(self, conversation_id)

    def scene_pending_flush_metrics(
        self,
        thread_id: str,
        *,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return product_views.scene_pending_flush_metrics(
            self,
            thread_id,
            conversation_id=conversation_id,
        )

    def build_dialogue_flush_payload(
        self,
        thread_id: str,
        *,
        conversation_id: Optional[str] = None,
        source: str = "chat_api_thread_flush",
    ) -> Optional[Dict[str, Any]]:
        return product_views.build_dialogue_flush_payload(
            self,
            thread_id,
            conversation_id=conversation_id,
            source=source,
        )

    def mark_scene_flushed(self, conversation_id: str, *, through_seq: int) -> None:
        product_views.mark_scene_flushed(
            self,
            conversation_id,
            through_seq=through_seq,
        )

    def scene_flush_through_seq(self, conversation_id: str) -> int:
        entries = list(
            self.scene_system.reader.entries_since_flush(
                str(conversation_id or "").strip()
            )
        )
        return max(
            (int(getattr(entry, "seq", 0) or 0) for entry in entries),
            default=0,
        )

    def load_conversation_seq(self, thread_id: str) -> int:
        store = getattr(self.scene_system, "store", None)
        load = getattr(store, "load_conversation_seq", None)
        return max(0, int(load(thread_id) or 0)) if callable(load) else 0

    def persist_conversation_seq(self, thread_id: str, sequence: int) -> None:
        store = getattr(self.scene_system, "store", None)
        persist = getattr(store, "persist_conversation_seq", None)
        if callable(persist):
            persist(thread_id, int(sequence))

    def active_user_transaction(self, conversation_id: str) -> Any:
        return self.registry.get_active_user_transaction(conversation_id)

    def pending_count(self, thread_id: Optional[str] = None) -> int:
        return self.inbox.pending_count(thread_id)

    def health(self) -> Dict[str, Any]:
        return {
            "profile": "langgraph",
            "runtime_engine_id": self.runtime_engine_id,
            "pending_stimuli": self.inbox.pending_count(),
            "transactions": self.registry.count_all(),
            "active_drainer_threads": self.drainer.active_drainer_count(),
            "preempt_enabled": self.config.scheduler.preempt_enabled,
            "turn_loop": self.turn_loop_enabled,
            "thinking_mode": self.engine_config.thinking_mode,
            "delegate_executor": self.engine_config.delegate_executor,
            "checkpoint": self.graph_engine.checkpoint_metadata,
            "turn_checkpoint": (
                self.turn_engine.checkpoint_metadata
                if self.turn_engine is not None
                else None
            ),
            "transaction_authority_migration": dict(
                self.runtime_store.legacy_status_migration_report
            ),
            "transaction_contract_migration": dict(
                self.runtime_store.transaction_contract_migration_report
            ),
            "flush_journal": self.flush_orchestrator.health(),
        }

    def effect_ledger_snapshot(self, transaction_id: str) -> Dict[str, Any]:
        """Effect intents and Feedback outbox rows for one transaction."""

        return self.effect_coordinator.load_for_transaction(transaction_id)

    def shutdown(self) -> None:
        if self.turn_engine is not None:
            self.turn_engine.close()
        self.graph_engine.close()
        self.flush_orchestrator.close()
        self.runtime_store.close()


# RuntimeHost structural typing: LangGraphRuntime implements the protocol.
def as_langgraph_host(runtime: LangGraphRuntime) -> RuntimeHost:
    return runtime


__all__ = ["LangGraphRuntime", "as_langgraph_host"]
