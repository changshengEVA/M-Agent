"""Think-life runtime orchestrator."""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS, ThreadRuntimeSnapshot
from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent
from m_agent.paths import chat_user_persistence_root, chat_user_slug
from m_agent.runtime.think_life.config import ThinkLifeConfig, load_think_life_config
from m_agent.runtime.think_life.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    TransactionKind,
    TransactionRecord,
    TransactionStatus,
)
from m_agent.runtime.think_life.drainer import ThreadDrainerService
from m_agent.runtime.think_life.perception.attributor import TransactionAttributor
from m_agent.runtime.think_life.perception.gateway import PerceptionGateway
from m_agent.runtime.think_life.perception.inbox import StimulusInbox
from m_agent.runtime.think_life.scheduler.cpu_state import THREAD_CPU_STATE
from m_agent.runtime.think_life.scheduler.loop import ThinkLifeLoop
from m_agent.runtime.think_life.scheduler.schedule_lifecycle import ScheduleLifecycleHook
from m_agent.runtime.think_life.transaction_registry import TransactionRegistry
from m_agent.systems.scene import build_default_scene_system
from m_agent.systems.scene.protocols import SceneWriter

logger = logging.getLogger(__name__)

ThreadEventEmitter = Callable[[str, str, Dict[str, Any]], None]
HistoryProvider = Callable[[str], Optional[List[Dict[str, Any]]]]


class _EmittingSceneWriter:
    """Wraps SceneWriter to emit ``scene_entry_appended`` after append."""

    def __init__(
        self,
        inner: SceneWriter,
        on_appended: Callable[[str, SceneEntry], None],
    ) -> None:
        self._inner = inner
        self._on_appended = on_appended

    def append(self, thread_id: str, entry: SceneEntry) -> SceneEntry:
        stored = self._inner.append(thread_id, entry)
        try:
            self._on_appended(thread_id, stored)
        except Exception:
            logger.exception("scene on_appended hook failed thread_id=%s", thread_id)
        return stored


class ThinkLifeRuntime:
    """Product runtime: perception bus + transaction WM + Scene log + Think CPU."""

    def __init__(
        self,
        agent: ThreeLayerChatAgent,
        *,
        config: Optional[ThinkLifeConfig] = None,
        owner_id: str = "anonymous",
    ) -> None:
        self.agent = agent
        self.owner_id = str(owner_id or "anonymous").strip() or "anonymous"
        raw_runtime = agent.config.get("runtime") if isinstance(agent.config.get("runtime"), dict) else {}
        self.config = config or load_think_life_config(
            raw_runtime.get("think_life") if isinstance(raw_runtime.get("think_life"), dict) else {}
        )

        scene_dir = chat_user_persistence_root(chat_user_slug(self.owner_id)) / "scene"
        self.scene_system = build_default_scene_system(
            persist_dir=scene_dir,
            persist_enabled=self.config.scene_persist_jsonl,
        )

        self.registry = TransactionRegistry()
        self.inbox = StimulusInbox()
        self.attributor = TransactionAttributor(
            registry=self.registry,
            config=self.config,
            semantic_resolver=agent.thinking_agent.resolve_transaction,
        )
        self._reply_lock = threading.Lock()
        self._last_replies: Dict[str, List[str]] = {}
        self._thread_event_emitter: Optional[ThreadEventEmitter] = None
        self._history_provider: Optional[HistoryProvider] = None
        self._schedule_lifecycle: ScheduleLifecycleHook = None
        self._drain_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)

        self._emitting_writer = _EmittingSceneWriter(
            self.scene_system.writer,
            self._on_scene_appended,
        )

        self.gateway = PerceptionGateway(
            inbox=self.inbox,
            attributor=self.attributor,
            scene_writer=self._emitting_writer,
            on_enqueued=self._on_stimulus_enqueued,
        )

        self.loop = ThinkLifeLoop(
            config=self.config,
            registry=self.registry,
            inbox=self.inbox,
            attributor=self.attributor,
            gateway=self.gateway,
            thinking_agent=agent.thinking_agent,
            execution_agent=agent.execution_agent,
            wm_system=agent.systems.wm,
            scene_writer=self._emitting_writer,
            scene_reader=self.scene_system.reader,
            on_reply=self._on_reply,
            event_emitter=None,
            schedule_lifecycle=None,
            on_runtime_updated=self._emit_runtime_updated,
        )

        self.drainer = ThreadDrainerService(
            drain_fn=self._drain_for_thread,
            get_pending=lambda tid: self.inbox.pending_count(tid),
            build_emitter=self._build_drainer_emitter,
            get_history=self._history_for_thread,
            on_runtime_updated=self._emit_runtime_updated,
        )

    @classmethod
    def from_config_path(
        cls,
        config_path: str | Path,
        *,
        owner_id: str = "anonymous",
        systems_override: Any = None,
    ) -> "ThinkLifeRuntime":
        agent = ThreeLayerChatAgent(config_path=config_path, systems=systems_override)
        return cls(agent, owner_id=owner_id)

    def set_thread_event_emitter(self, emitter: Optional[ThreadEventEmitter]) -> None:
        self._thread_event_emitter = emitter

    def set_history_provider(self, provider: Optional[HistoryProvider]) -> None:
        self._history_provider = provider

    def set_schedule_lifecycle(self, hook: ScheduleLifecycleHook) -> None:
        self._schedule_lifecycle = hook
        self.loop._schedule_lifecycle = hook

    def _emit_thread_event(self, thread_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        sink = self._thread_event_emitter
        if sink is None:
            return
        try:
            sink(thread_id, event_type, payload)
        except Exception:
            logger.exception(
                "Think-life thread event failed type=%s thread_id=%s",
                event_type,
                thread_id,
            )

    def _emit_runtime_updated(self, thread_id: str) -> None:
        snap = THREAD_RUNTIME_STATUS.snapshot(thread_id)
        self._emit_thread_event(
            thread_id,
            "thread_runtime_updated",
            {"thread_runtime": snap.to_dict()},
        )

    def _on_scene_appended(self, conversation_id: str, entry: SceneEntry) -> None:
        thread_id = str(conversation_id or "").rsplit("::", 1)[0]
        self._emit_thread_event(thread_id, "scene_entry_appended", entry.to_dict())

    def _on_reply(self, thread_id: str, transaction_id: str, message: str, finalize: bool) -> None:
        with self._reply_lock:
            self._last_replies.setdefault(thread_id, []).append(str(message or "").strip())

        record = self.registry.get(transaction_id)
        delegate_id = ""
        if record is not None:
            delegate_id = str(record.active_delegate_id or record.correlation.delegate_id or "")

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

    def _on_stimulus_enqueued(self, stimulus: Any, *, schedule_drainer: bool = True) -> None:
        tid = str(getattr(stimulus, "thread_id", "") or "").strip()
        THREAD_RUNTIME_STATUS.set_preempt_enabled(tid, self.config.scheduler.preempt_enabled)
        pending = self.inbox.pending_count(tid)
        THREAD_RUNTIME_STATUS.set_pending_stimuli(tid, pending)
        if self.config.scheduler.preempt_enabled:
            new_priority = self.attributor.priority_for(stimulus)
            in_flight = THREAD_CPU_STATE.get_in_flight(tid)
            if in_flight is not None and int(new_priority) < int(in_flight.priority):
                THREAD_CPU_STATE.cancel_in_flight(tid)
        snap = THREAD_RUNTIME_STATUS.snapshot(tid)
        self._emit_thread_event(
            tid,
            "stimulus_queued",
            {
                "stimulus_id": str(getattr(stimulus, "stimulus_id", "") or ""),
                "kind": str(getattr(getattr(stimulus, "kind", None), "value", stimulus.kind) or ""),
                "pending_count": pending,
                "effective_depth": snap.effective_depth,
                "runtime_phase": snap.runtime_phase,
            },
        )
        if schedule_drainer:
            self.drainer.ensure_running(tid)
        self._emit_runtime_updated(tid)

    def _history_for_thread(self, thread_id: str) -> Optional[List[Dict[str, Any]]]:
        if self._history_provider is None:
            return None
        try:
            return self._history_provider(thread_id)
        except Exception:
            logger.exception("Think-life history provider failed thread_id=%s", thread_id)
            return None

    def _build_drainer_emitter(self, thread_id: str) -> Optional[Callable[[str, Dict[str, Any]], None]]:
        """Emitter for drainer lifecycle.

        Unlike thinking-layer streaming (which is allow-listed), the drainer needs to
        forward its lifecycle updates such as ``thread_runtime_updated`` so UI busy
        state can settle after the inbox is drained.
        """

        thinking = self._build_thinking_emitter(thread_id)

        def _emit(event_type: str, payload: Dict[str, Any]) -> None:
            if event_type == "thread_runtime_updated":
                safe = dict(payload) if isinstance(payload, dict) else {"data": payload}
                safe.setdefault("thread_id", thread_id)
                self._emit_thread_event(thread_id, event_type, safe)
                return
            if thinking is not None:
                thinking(event_type, payload)

        return _emit

    def _build_thinking_emitter(self, thread_id: str) -> Optional[Callable[[str, Dict[str, Any]], None]]:
        allowed = frozenset(
            {
                "thinking_started",
                "thinking_task_state",
                "thinking_plan",
                "thinking_completed",
                "turn_failed",
            }
        )

        def _emit(event_type: str, payload: Dict[str, Any]) -> None:
            if event_type not in allowed:
                return
            safe = dict(payload) if isinstance(payload, dict) else {"data": payload}
            safe.setdefault("thread_id", thread_id)
            self._emit_thread_event(thread_id, event_type, safe)

        return _emit

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
            conversation_id=str(conversation_id or "").strip() or f"{thread_id}::0",
            text=text,
            payload=payload,
            schedule_drainer=schedule_drainer,
        )

    def submit_heartbeat(
        self,
        *,
        thread_id: str,
        conversation_id: Optional[str] = None,
        schedule_id: str,
        text: str,
        payload: Optional[dict] = None,
    ) -> str:
        return self.gateway.submit_heartbeat(
            thread_id=thread_id,
            conversation_id=str(conversation_id or "").strip() or f"{thread_id}::0",
            schedule_id=schedule_id,
            text=text,
            payload=payload,
        )

    def submit_stimulus_async(
        self,
        *,
        thread_id: str,
        conversation_id: Optional[str] = None,
        text: str,
        payload: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Enqueue a user message and start background drainer if needed."""
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

    def force_stop_thread(self, thread_id: str, *, reason: str = "user_requested") -> Dict[str, Any]:
        """Cancel the in-flight CPU turn and drop queued stimuli for a thread."""
        tid = str(thread_id or "").strip()
        if not tid:
            raise ValueError("thread_id is required")

        in_flight = THREAD_CPU_STATE.get_in_flight(tid)
        in_flight_transaction_id = str(in_flight.transaction_id or "").strip() if in_flight is not None else ""
        cancelled_in_flight = THREAD_CPU_STATE.cancel_in_flight(tid, force=True)
        cleared_pending = self.inbox.clear_thread(tid)
        THREAD_RUNTIME_STATUS.set_pending_stimuli(tid, self.inbox.pending_count(tid))

        cancelled_transactions: List[str] = []
        candidate_ids: List[str] = []
        if in_flight_transaction_id:
            candidate_ids.append(in_flight_transaction_id)
        candidate_ids.extend(
            record.transaction_id
            for record in self.registry.list_for_thread(tid)
            if record.kind == TransactionKind.USER_TASK and not record.status.is_terminal()
        )
        seen: set[str] = set()
        for txn_id in candidate_ids:
            if not txn_id or txn_id in seen:
                continue
            seen.add(txn_id)
            record = self.registry.get(txn_id)
            if record is None or record.status.is_terminal():
                continue
            try:
                self.registry.transition(txn_id, TransactionStatus.CANCELLED)
                cancelled_transactions.append(txn_id)
            except Exception:
                logger.exception("force_stop transition failed txn=%s thread_id=%s", txn_id, tid)

        self._emit_thread_event(
            tid,
            "thinking_force_stopped",
            {
                "thread_id": tid,
                "reason": str(reason or "user_requested").strip() or "user_requested",
                "cancelled_in_flight": bool(cancelled_in_flight),
                "cleared_pending_stimuli": int(cleared_pending),
                "cancelled_transactions": cancelled_transactions,
            },
        )
        self._emit_runtime_updated(tid)
        snap = THREAD_RUNTIME_STATUS.snapshot(tid)
        return {
            "success": True,
            "thread_id": tid,
            "runtime_profile": "think_life",
            "cancelled_in_flight": bool(cancelled_in_flight),
            "cleared_pending_stimuli": int(cleared_pending),
            "cancelled_transactions": cancelled_transactions,
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
        """Enqueue a schedule heartbeat stimulus (same path as user stimuli)."""
        body = dict(payload or {})
        if run_id:
            body["run_id"] = run_id
        if owner_id:
            body["owner_id"] = owner_id
        stimulus_id = self.submit_heartbeat(
            thread_id=thread_id,
            conversation_id=conversation_id,
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

    def list_transactions(self, thread_id: str) -> Dict[str, Any]:
        """Per-transaction WM + status snapshot for UI (Think-life only)."""
        tid = str(thread_id or "").strip()
        records = self.registry.list_for_thread(tid)
        active = next(
            (
                record
                for record in reversed(records)
                if record.kind == TransactionKind.USER_TASK and not record.status.is_terminal()
            ),
            None,
        )
        active_id = str(active.transaction_id) if active is not None else None
        runtime_snap: ThreadRuntimeSnapshot = THREAD_RUNTIME_STATUS.snapshot(tid)
        cpu_txn = str(runtime_snap.active_transaction_id or "").strip() or None
        runtime_phase = str(runtime_snap.runtime_phase or "ready")

        def _serialize(record: TransactionRecord) -> Dict[str, Any]:
            txn_id = str(record.transaction_id)
            return {
                "transaction_id": txn_id,
                "conversation_id": record.conversation_id,
                "thread_id": record.thread_id,
                "status": str(record.status.value),
                "kind": str(record.kind.value),
                "priority": int(record.priority),
                "wm_entries": list(record.wm_entries),
                "wm_entry_count": len(record.wm_entries),
                "task_state": record.task_state.to_dict(),
                "think_rounds": int(record.think_rounds),
                "delegate_count": int(record.delegate_count),
                "active_delegate_id": record.active_delegate_id,
                "schedule_id": record.correlation.schedule_id,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "terminal_at": record.terminal_at,
                "last_error": record.last_error,
                "is_active_user": txn_id == active_id,
                "is_cpu_holder": bool(cpu_txn and txn_id == cpu_txn),
            }

        items = sorted(
            [_serialize(r) for r in records],
            key=lambda item: str(item.get("updated_at", "") or ""),
            reverse=True,
        )
        return {
            "thread_id": tid,
            "transactions": items,
            "active_transaction_id": active_id,
            "cpu_transaction_id": cpu_txn,
            "transaction_count": len(items),
            "runtime_phase": runtime_phase,
            "effective_depth": int(runtime_snap.effective_depth),
        }

    def list_scene(
        self,
        thread_id: str,
        *,
        conversation_id: Optional[str] = None,
        limit: int = 40,
        before_seq: Optional[int] = None,
        since_flush: bool = True,
    ) -> Dict[str, Any]:
        cap = max(1, min(200, int(limit or 40)))
        tid = str(thread_id or "").strip()
        cid = str(conversation_id or "").strip() or f"{tid}::0"
        reader = self.scene_system.reader
        if since_flush:
            self.ensure_scene_thread_loaded(cid)
            since_fn = getattr(reader, "entries_since_flush", None)
            entries = list(since_fn(cid)) if callable(since_fn) else []
            if before_seq is not None:
                entries = [e for e in entries if e.seq < int(before_seq)]
        else:
            entries = list(reader.tail(cid, limit=cap + 1, before_seq=before_seq))
        has_more = len(entries) > cap
        if has_more:
            entries = entries[-cap:]
        return {
            "thread_id": thread_id,
            "conversation_id": cid,
            "entries": [e.to_dict() for e in entries],
            "has_more": has_more,
            "since_flush": bool(since_flush),
        }

    def run_thread(
        self,
        thread_id: str,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Drain perception inbox for one thread (blocking)."""
        tid = str(thread_id or "").strip()
        with self._drain_locks[tid]:
            self.loop._event_emitter = event_emitter
            with self._reply_lock:
                self._last_replies.pop(tid, None)
            history_provider = self._history_for_thread if self._history_provider is not None else None
            results = self.loop.drain_thread(
                tid,
                history_messages=history_messages,
                history_provider=history_provider,
            )
            THREAD_RUNTIME_STATUS.set_pending_stimuli(tid, self.inbox.pending_count(tid))
            replies = []
            with self._reply_lock:
                replies = list(self._last_replies.get(tid, []))
            answer = replies[-1] if replies else ""
            return {
                "success": bool(results) and all(r.get("success", False) for r in results if isinstance(r, dict)),
                "thread_id": tid,
                "results": results,
                "replies": replies,
                "answer": answer,
            }

    def on_flush_segment(
        self,
        thread_id: str,
        *,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Complete the active user transaction when the chat thread is flushed."""
        tid = str(thread_id or "").strip()
        cid = str(conversation_id or "").strip()
        txn_id = self.registry.complete_active_user_transaction(cid) if cid else None
        drained: List[Dict[str, Any]] = []
        flush_key = str(conversation_id or "").strip() or txn_id
        if flush_key:
            try:
                drained = list(
                    self.agent.thinking_agent.on_flush(flush_key, thread_id=tid) or []
                )
            except Exception:
                logger.exception(
                    "Think-life on_flush failed conversation_id=%s thread_id=%s",
                    flush_key,
                    tid,
                )
        self._emit_runtime_updated(tid)
        return {
            "thread_id": tid,
            "completed_transaction_id": txn_id,
            "episode_notes_drained": len(drained),
        }

    def ensure_scene_thread_loaded(self, conversation_id: str) -> None:
        cid = str(conversation_id or "").strip()
        if not cid:
            return
        store = getattr(self.scene_system, "store", None)
        ensure_fn = getattr(store, "ensure_thread_loaded", None)
        if callable(ensure_fn):
            ensure_fn(cid)

    def scene_pending_flush_metrics(
        self, thread_id: str, *, conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return Scene-based flush eligibility for thread_state / idle flush."""
        from m_agent.chat.chat_memory_persistence import scene_entry_to_dialogue_turn

        tid = str(thread_id or "").strip()
        cid = str(conversation_id or "").strip() or f"{tid}::0"
        if not tid:
            return {
                "scene_pending_entries": 0,
                "scene_pending_turns": 0,
                "active_user_segment": False,
                "latest_stimulus_at": None,
                "latest_activity_at": None,
                "can_flush": False,
            }
        self.ensure_scene_thread_loaded(cid)
        reader = self.scene_system.reader
        entries_fn = getattr(reader, "entries_since_flush", None)
        entries: List[Any] = list(entries_fn(cid)) if callable(entries_fn) else []
        user_name = str(getattr(self.agent, "user_name", "User") or "User")
        assistant_name = str(getattr(self.agent, "assistant_name", "Memory Assistant") or "Memory Assistant")
        pending_turns = sum(
            1
            for entry in entries
            if scene_entry_to_dialogue_turn(
                entry,
                user_name=user_name,
                assistant_name=assistant_name,
            )
            is not None
        )
        active_user_segment = any(
            record.kind == TransactionKind.USER_TASK and not record.status.is_terminal()
            for record in self.registry.list_for_thread(tid)
            if record.conversation_id == cid
        )
        stimulus_entries = [
            entry
            for entry in entries
            if entry.actor == SceneActor.USER
            or entry.entry_type == SceneEntryType.UTTERANCE
        ]
        visible_entries = [
            entry
            for entry in entries
            if entry.actor in {SceneActor.USER, SceneActor.ASSISTANT}
            or entry.entry_type in {SceneEntryType.UTTERANCE, SceneEntryType.REPLY}
        ]
        return {
            "scene_pending_entries": len(entries),
            "scene_pending_turns": pending_turns,
            "active_user_segment": active_user_segment,
            "latest_stimulus_at": (
                stimulus_entries[-1].occurred_at if stimulus_entries else None
            ),
            "latest_activity_at": (
                visible_entries[-1].occurred_at if visible_entries else None
            ),
            "can_flush": pending_turns > 0 or active_user_segment,
        }

    def build_dialogue_flush_payload(
        self,
        thread_id: str,
        *,
        conversation_id: Optional[str] = None,
        source: str = "chat_api_thread_flush",
    ) -> Optional[Dict[str, Any]]:
        """Export Scene entries since last flush as canonical dialogue JSON (v2)."""
        tid = str(thread_id or "").strip()
        cid = str(conversation_id or "").strip() or f"{tid}::0"
        if not tid:
            return None
        self.ensure_scene_thread_loaded(cid)
        reader = self.scene_system.reader
        entries_fn = getattr(reader, "entries_since_flush", None)
        if not callable(entries_fn):
            return None
        entries = list(entries_fn(cid))
        if not entries:
            return None

        from m_agent.chat.chat_memory_persistence import (
            build_dialogue_id,
            build_dialogue_payload_from_scene_entries,
        )

        user_name = str(getattr(self.agent, "user_name", "User") or "User")
        assistant_name = str(getattr(self.agent, "assistant_name", "Memory Assistant") or "Memory Assistant")
        start_ts = entries[0].occurred_at
        try:
            from datetime import datetime, timezone

            created_at = datetime.fromisoformat(str(start_ts).replace("Z", "+00:00"))
        except Exception:
            from datetime import datetime, timezone

            created_at = datetime.now(timezone.utc)
        dialogue_id = build_dialogue_id(thread_id=tid, created_at=created_at)
        return build_dialogue_payload_from_scene_entries(
            dialogue_id=dialogue_id,
            thread_id=tid,
            entries=entries,
            source=source,
            user_name=user_name,
            assistant_name=assistant_name,
        )

    def mark_scene_flushed(self, conversation_id: str, *, through_seq: int) -> None:
        cid = str(conversation_id or "").strip()
        self.ensure_scene_thread_loaded(cid)
        mark_fn = getattr(self.scene_system.reader, "mark_flushed", None)
        if callable(mark_fn) and int(through_seq) > 0:
            mark_fn(cid, through_seq=int(through_seq))

    def health(self) -> Dict[str, Any]:
        return {
            "profile": "think_life",
            "pending_stimuli": self.inbox.pending_count(),
            "transactions": self.registry.count_all(),
            "preempt_enabled": self.config.scheduler.preempt_enabled,
            "max_preempt_per_stimulus": self.config.scheduler.max_preempt_per_stimulus,
            "active_drainer_threads": self.drainer.active_drainer_count(),
        }
