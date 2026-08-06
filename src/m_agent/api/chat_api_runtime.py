from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterator, List, Optional, Sequence, Tuple

from m_agent.chat.chat_agent_factory import create_chat_agent
from m_agent.chat.chat_memory_persistence import (
    _parse_ts_from_turn,
    build_dialogue_id,
    build_dialogue_payload,
)
from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent
from m_agent.chat.working_memory import build_working_memory_api_payload
from m_agent.paths import chat_user_slug
from m_agent.runtime.host import RuntimeHost, create_runtime_host
from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE
from m_agent.systems import SystemsBundle

from .chat_api_shared import (
    _get_thread_lock,
    _normalize_memory_mode,
    _now_iso,
    _now_utc,
    _summarize_memory_write_result,
    _to_iso,
)
from .thread_runtime_status import THREAD_RUNTIME_STATUS

logger = logging.getLogger(__name__)

ThreadEventSink = Optional[Callable[[str, str, Dict[str, Any]], Any]]


class ThinkingForceStoppedError(RuntimeError):
    """Raised when a thread is force-stopped through the chat API."""


class PendingFlushAdmissionError(RuntimeError):
    """Raised when new work would cross an unfinished flush boundary."""


class _RuntimeScheduleLifecycle:
    """Bridges Runtime HEARTBEAT processing to the schedule store."""

    def __init__(self, runtime: "ChatServiceRuntime") -> None:
        self._runtime = runtime

    def _service(self):
        return self._runtime.agent.get_schedule_agent().service

    def _stored_thread_id(self, *, owner_id: str, schedule_id: str, fallback: str) -> str:
        service = self._service()
        store = getattr(service, "store", None)
        finder = getattr(store, "find_by_id", None)
        if callable(finder):
            item = finder(schedule_id, owner_id=owner_id)
            stored_thread_id = str(getattr(item, "thread_id", "") or "").strip()
            if stored_thread_id:
                return stored_thread_id
        return str(fallback or "").strip()

    def on_schedule_processing_started(
        self,
        *,
        owner_id: str,
        thread_id: str,
        schedule_id: str,
        run_id: str,
        stimulus_id: str,
    ) -> None:
        stored_thread_id = self._stored_thread_id(
            owner_id=owner_id,
            schedule_id=schedule_id,
            fallback=thread_id,
        )
        self._service().mark_running(
            owner_id=owner_id,
            thread_id=stored_thread_id,
            schedule_id=schedule_id,
        )
        self._runtime._emit_thread_event(
            thread_id,
            "schedule_started",
            {
                "schedule_id": schedule_id,
                "run_id": run_id,
                "stimulus_id": stimulus_id,
                "status": "running",
            },
        )

    def on_schedule_processing_finished(
        self,
        *,
        owner_id: str,
        thread_id: str,
        schedule_id: str,
        run_id: str,
        success: bool,
        answer: str = "",
        error: str = "",
        memory_capture: Optional[Dict[str, Any]] = None,
    ) -> None:
        stored_thread_id = self._stored_thread_id(
            owner_id=owner_id,
            schedule_id=schedule_id,
            fallback=thread_id,
        )
        if success:
            self._service().mark_done(
                owner_id=owner_id,
                thread_id=stored_thread_id,
                schedule_id=schedule_id,
                run_id=run_id,
                result={"answer": answer, "memory_capture": memory_capture},
            )
            self._runtime._emit_thread_event(
                thread_id,
                "schedule_completed",
                {
                    "schedule_id": schedule_id,
                    "run_id": run_id,
                    "status": "done",
                    "answer": answer,
                },
            )
        else:
            err = str(error or "schedule processing failed").strip() or "schedule processing failed"
            self._service().mark_failed(
                owner_id=owner_id,
                thread_id=stored_thread_id,
                schedule_id=schedule_id,
                error=err,
            )
            self._runtime._emit_thread_event(
                thread_id,
                "schedule_failed",
                {
                    "schedule_id": schedule_id,
                    "run_id": run_id,
                    "status": "failed",
                    "error": err,
                },
            )


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_turn_payload(
    turn: Optional[Dict[str, Any]],
    *,
    fallback_speaker: str,
    fallback_text: str,
) -> Dict[str, Any]:
    payload = dict(turn) if isinstance(turn, dict) else {}
    speaker = _normalize_text(payload.get("speaker")) or fallback_speaker
    text = _normalize_text(payload.get("text")) or _normalize_text(fallback_text)
    normalized: Dict[str, Any] = {
        "speaker": speaker,
        "text": text,
    }
    cap = payload.get("blip_caption")
    if isinstance(cap, str) and cap.strip():
        normalized["blip_caption"] = cap.strip()
    img_url = payload.get("img_url")
    if isinstance(img_url, str) and img_url.strip():
        normalized["img_url"] = img_url.strip()
    img_file = payload.get("img_file")
    if isinstance(img_file, str) and img_file.strip():
        normalized["img_file"] = img_file.strip()
    upload_id = payload.get("upload_id")
    if isinstance(upload_id, str) and upload_id.strip():
        normalized["upload_id"] = upload_id.strip()
    mime_type = payload.get("mime_type")
    if isinstance(mime_type, str) and mime_type.strip():
        normalized["mime_type"] = mime_type.strip()
    width = payload.get("width")
    if isinstance(width, int):
        normalized["width"] = width
    height = payload.get("height")
    if isinstance(height, int):
        normalized["height"] = height
    return normalized


def _render_turn_for_llm(turn: Optional[Dict[str, Any]]) -> str:
    payload = turn if isinstance(turn, dict) else {}
    text = _normalize_text(payload.get("text"))
    cap = _normalize_text(payload.get("blip_caption"))
    if text and cap:
        return f"{text}\n[Image: {cap}]"
    if cap:
        return f"[Image: {cap}]"
    return text


@dataclass
class BufferedRound:
    round_id: str
    user_message: str
    assistant_message: str
    user_turn: Dict[str, Any]
    assistant_turn: Dict[str, Any]
    user_at: datetime
    assistant_at: datetime
    capture_state: str
    flush_id: Optional[str] = None

    def to_history_messages(self) -> List[Dict[str, str]]:
        return [
            {"role": "user", "content": _render_turn_for_llm(self.user_turn)},
            {"role": "assistant", "content": _render_turn_for_llm(self.assistant_turn)},
        ]

    @property
    def is_pending(self) -> bool:
        return self.capture_state == "pending"


@dataclass
class ThreadSessionState:
    thread_id: str
    mode: str = "manual"
    rounds: List[BufferedRound] = field(default_factory=list)
    created_at: datetime = field(default_factory=_now_utc)
    last_activity_at: datetime = field(default_factory=_now_utc)
    # Idle flushing is stimulus-driven. A newly created (or freshly flushed)
    # segment remains unarmed until its first stimulus arrives.
    idle_timer_started_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=_now_utc)
    last_flush_at: Optional[datetime] = None
    last_flush_attempt_at: Optional[datetime] = None
    last_flush_reason: Optional[str] = None
    last_flush_result: Optional[Dict[str, Any]] = None
    flush_count: int = 0
    #: Conversation sequence number; bumped on each successful flush so the
    #: next turn starts a fresh ``ConversationState`` (empty WM + episode
    #: buffer) in the thinking layer. ``conversation_id`` is derived as
    #: ``f"{thread_id}::{conversation_seq}"``.
    conversation_seq: int = 0

    @property
    def conversation_id(self) -> str:
        return f"{self.thread_id}::{int(self.conversation_seq)}"


class ChatServiceRuntime:
    """Long-lived chat runtime with shared agent plus thread-scoped memory buffer state."""

    def __init__(
        self,
        *,
        config_path: Path,
        idle_flush_seconds: int = 1800,
        history_max_rounds: int = 12,
        idle_scan_interval_seconds: int = 5,
        thread_event_sink: ThreadEventSink = None,
        systems_override: Optional[SystemsBundle] = None,
    ) -> None:
        """Initialize the chat runtime.

        Parameters
        ----------
        config_path:
            Path to ``chat_controller.yaml``.
        systems_override:
            Optional :class:`~m_agent.systems.SystemsBundle` injected into
            the chat agent at construction time. Slots that the bundle
            leaves ``None`` fall back to the YAML's ``systems:`` block,
            then to legacy ``plugins:`` / defaults. This is the runtime's
            programmatic entry point for swapping a subsystem without
            editing YAML.
        """
        if systems_override is not None and not isinstance(systems_override, SystemsBundle):
            raise TypeError(
                "ChatServiceRuntime: `systems_override` must be a SystemsBundle "
                f"(got {type(systems_override).__name__})"
            )
        self.config_path = config_path.resolve()
        self.created_at = _now_iso()
        self.idle_flush_seconds = max(0, int(idle_flush_seconds))
        self.history_max_rounds = max(1, int(history_max_rounds))
        self.idle_scan_interval_seconds = max(1, int(idle_scan_interval_seconds))
        self._operation_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._threads_lock = threading.Lock()
        self._agent: Optional[ThreeLayerChatAgent] = None
        self._runtime_host: RuntimeHost
        self._engine: Any
        self._runtime_engine_id: str = LANGGRAPH_RUNTIME_ENGINE
        self._systems_override: Optional[SystemsBundle] = systems_override
        self._threads: Dict[str, ThreadSessionState] = {}
        self._force_stop_lock = threading.Lock()
        self._force_stop_events: Dict[str, threading.Event] = {}
        # Async path: user turns awaiting finalize reply (FIFO per thread).
        self._runtime_pending_users: Dict[str, Deque[Dict[str, Any]]] = {}
        self._runs_started = 0
        self._runs_completed = 0
        self._runs_failed = 0
        self._flushes_started = 0
        self._flushes_completed = 0
        self._flushes_failed = 0
        self._last_run_started_at: Optional[str] = None
        self._last_run_finished_at: Optional[str] = None
        self._last_idle_flush_scan_at: Optional[str] = None
        self._stop_event = threading.Event()
        self._thread_event_sink: ThreadEventSink = thread_event_sink
        self._warm_agent()
        self._idle_worker = threading.Thread(target=self._idle_flush_loop, name="chat-idle-flush", daemon=True)
        self._idle_worker.start()

    def set_thread_event_sink(self, sink: ThreadEventSink) -> None:
        self._thread_event_sink = sink
        self._wire_runtime_host()

    def _wire_runtime_host(self) -> None:
        def _emitter(thread_id: str, event_type: str, payload: Dict[str, Any]) -> None:
            if event_type == "stimulus_queued":
                self._record_thread_activity(thread_id, arm_idle_timer=True)
            if event_type == "reply_emitted" and isinstance(payload, dict) and payload.get("finalize"):
                self._record_thread_activity(thread_id, arm_idle_timer=True)
                self._capture_runtime_round(
                    thread_id,
                    assistant_message=str(payload.get("message", "") or ""),
                )
            elif event_type == "turn_failed":
                self._discard_pending_runtime_user_turn(thread_id)
            self._emit_thread_event(thread_id, event_type, payload)

        self._runtime_host.set_thread_event_emitter(_emitter)

        def _history(thread_id: str) -> List[Dict[str, Any]]:
            session = self._get_or_create_thread(thread_id)
            with self._threads_lock:
                return self._build_history_messages(session)

        set_history = getattr(self._engine, "set_history_provider", None)
        if callable(set_history):
            set_history(_history)
        set_schedule = getattr(self._engine, "set_schedule_lifecycle", None)
        if callable(set_schedule):
            set_schedule(_RuntimeScheduleLifecycle(self))

    def _record_thread_activity(self, thread_id: str, *, arm_idle_timer: bool) -> None:
        tid = str(thread_id or "").strip()
        if not tid:
            return
        session = self._get_or_create_thread(tid)
        occurred_at = _now_utc()
        with self._threads_lock:
            session.last_activity_at = occurred_at
            if arm_idle_timer:
                session.idle_timer_started_at = occurred_at
            session.updated_at = occurred_at

    def _discard_pending_runtime_user_turn(self, thread_id: str) -> None:
        """Resolve the FIFO user turn whose processing ended in failure."""
        tid = str(thread_id or "").strip()
        if not tid:
            return
        with self._threads_lock:
            queue = self._runtime_pending_users.get(tid)
            if not queue:
                return
            queue.popleft()
            if not queue:
                self._runtime_pending_users.pop(tid, None)

    def _enqueue_runtime_user_turn(
        self,
        thread_id: str,
        *,
        user_message: str,
        user_turn: Dict[str, Any],
    ) -> None:
        tid = str(thread_id or "").strip()
        if not tid:
            return
        session = self._get_or_create_thread(tid)
        submitted_at = _now_utc()
        with self._threads_lock:
            queue = self._runtime_pending_users.setdefault(tid, deque())
            queue.append(
                {
                    "user_message": _normalize_text(user_message),
                    "user_turn": deepcopy(user_turn),
                    "submitted_at": submitted_at,
                }
            )
            session.last_activity_at = submitted_at
            session.idle_timer_started_at = submitted_at
            session.updated_at = submitted_at

    def _capture_runtime_round(self, thread_id: str, *, assistant_message: str) -> None:
        """Buffer a completed user/assistant round for flush (runtime async path)."""
        tid = str(thread_id or "").strip()
        if not tid:
            return
        with self._threads_lock:
            queue = self._runtime_pending_users.get(tid)
            if not queue:
                logger.warning(
                    "Runtime reply_emitted finalize with no pending user turn thread_id=%s",
                    tid,
                )
                return
            pending = queue.popleft()
            session = self._threads.get(tid)
            if session is None:
                session = ThreadSessionState(thread_id=tid, mode="manual")
                self._threads[tid] = session
            submitted_at = pending.get("submitted_at")
            if not isinstance(submitted_at, datetime):
                user_turn_obj = pending.get("user_turn") if isinstance(pending.get("user_turn"), dict) else {}
                submitted_at = _parse_ts_from_turn(user_turn_obj) or _now_utc()
            assistant_at = _now_utc()
            user_turn = (
                deepcopy(pending.get("user_turn"))
                if isinstance(pending.get("user_turn"), dict)
                else {}
            )
            user_turn.setdefault(
                "speaker",
                str(getattr(self.agent, "user_name", "user") or "user").strip() or "user",
            )
            user_turn["text"] = str(pending.get("user_message", "") or user_turn.get("text", "") or "")
            user_turn["timestamp"] = _to_iso(submitted_at)
            assistant_turn = {
                "speaker": str(getattr(self.agent, "assistant_name", "assistant") or "assistant").strip()
                or "assistant",
                "text": _normalize_text(assistant_message),
                "timestamp": _to_iso(assistant_at),
                "entry_type": "reply",
                "actor": "assistant",
            }
            self._append_round(
                session,
                user_message=str(pending.get("user_message", "") or ""),
                assistant_message=_normalize_text(assistant_message),
                user_turn=user_turn,
                assistant_turn=assistant_turn,
                user_at=submitted_at,
                assistant_at=assistant_at,
            )
            snapshot = self._thread_state_snapshot(session)
        self._emit_thread_event(tid, "thread_state_updated", {"thread_state": snapshot})

    def _warm_agent(self) -> None:
        logger.info("Initializing chat runtime with config %s", self.config_path)
        if self._systems_override is not None:
            logger.info(
                "Chat runtime: applying systems_override (wm=%s episodic=%s tools=%s)",
                self._systems_override.wm is not None,
                self._systems_override.episodic is not None,
                self._systems_override.tools is not None,
            )
        self._agent = create_chat_agent(
            config_path=self.config_path,
            systems=self._systems_override,
        )
        owner_id = str(
            getattr(self._agent, "owner_id", "")
            or chat_user_slug(str(getattr(self._agent, "user_name", "") or "anonymous"))
        ).strip() or "anonymous"
        self._runtime_host = create_runtime_host(
            agent=self._agent,
            owner_id=owner_id,
        )
        self._engine = self._runtime_host
        self._wire_runtime_host()
        logger.info(
            "Chat runtime initialized: runtime_engine=%s default_thread_id=%s persist_memory=%s",
            self._runtime_engine_id,
            self.default_thread_id,
            bool(getattr(self._agent, "persist_memory", False)),
        )

    @property
    def agent(self) -> ThreeLayerChatAgent:
        if self._agent is None:
            raise RuntimeError("Chat runtime agent is not initialized")
        return self._agent

    @property
    def runtime_host(self) -> RuntimeHost:
        return self._runtime_host

    @property
    def runtime_engine_id(self) -> str:
        return LANGGRAPH_RUNTIME_ENGINE

    @property
    def runtime_profile(self) -> str:
        """Return the stable runtime identifier exposed by the HTTP API."""
        return self.runtime_engine_id

    @property
    def default_thread_id(self) -> str:
        value = str(getattr(self.agent, "default_thread_id", "test-agent-1") or "").strip()
        return value or "test-agent-1"

    def _emit_thread_event(self, thread_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        sink = self._thread_event_sink
        if sink is None:
            return
        try:
            sink(thread_id, event_type, payload)
        except Exception:
            logger.exception("Failed to emit thread event type=%s thread_id=%s", event_type, thread_id)

    @property
    def persist_memory(self) -> bool:
        return bool(getattr(self.agent, "persist_memory", False))

    def shutdown(self) -> None:
        self._stop_event.set()
        if getattr(self, "_idle_worker", None) is not None and self._idle_worker.is_alive():
            self._idle_worker.join(timeout=2.0)
        try:
            self._runtime_host.shutdown()
        except Exception:
            logger.exception("Runtime host shutdown failed")

    def _idle_flush_loop(self) -> None:
        while not self._stop_event.wait(self.idle_scan_interval_seconds):
            try:
                self.flush_idle_threads()
            except Exception:
                logger.exception("Idle flush loop failed")

    def _load_conversation_seq(self, thread_id: str) -> int:
        try:
            return max(
                0,
                int(self._runtime_host.load_conversation_seq(thread_id) or 0),
            )
        except Exception:
            logger.exception(
                "Failed to restore conversation sequence thread_id=%s",
                thread_id,
            )
            return 0

    def _persist_conversation_seq_value(
        self,
        thread_id: str,
        sequence: int,
    ) -> None:
        self._runtime_host.persist_conversation_seq(thread_id, int(sequence))

    def _persist_conversation_seq(self, session: ThreadSessionState) -> None:
        self._persist_conversation_seq_value(
            session.thread_id,
            session.conversation_seq,
        )

    def _get_or_create_thread(self, thread_id: str) -> ThreadSessionState:
        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        with self._threads_lock:
            session = self._threads.get(active_thread_id)
            if session is not None:
                return session
        conversation_seq = self._load_conversation_seq(active_thread_id)
        with self._threads_lock:
            session = self._threads.get(active_thread_id)
            if session is None:
                session = ThreadSessionState(
                    thread_id=active_thread_id,
                    mode="manual",
                    conversation_seq=conversation_seq,
                )
                self._threads[active_thread_id] = session
            return session

    # ------------------------------------------------------------------
    # Working-memory plumbing — Runtime stores WM on transaction records;
    # the service runtime only projects it for API and SSE consumers.
    # ------------------------------------------------------------------

    def _current_wm_entries(self, session: ThreadSessionState) -> List[Dict[str, Any]]:
        """Return WM entries for the active user transaction."""
        transaction = self._runtime_host.active_user_transaction(
            session.conversation_id
        )
        return list(transaction.wm_entries) if transaction is not None else []

    def _current_task_progress(self, session: ThreadSessionState) -> Dict[str, Any]:
        """Return task progress for the active user transaction."""
        transaction = self._runtime_host.active_user_transaction(
            session.conversation_id
        )
        return (
            transaction.task_state.to_dict()
            if transaction is not None
            else {
                "goal": "",
                "completion_status": "processing",
                "completed": [],
                "remaining": [],
            }
        )

    # Allow-list of planning events the runtime forwards from
    # ``ThinkingAgent.handle`` to SSE. Anything outside this set is silently
    # dropped so a misbehaving custom thinking layer cannot inject arbitrary
    # event types into the protocol.
    _RUNTIME_PLANNING_EVENTS = frozenset(
        {
            "thinking_started",
            "thinking_task_state",
            "thinking_plan",
            "thinking_completed",
            "turn_failed",
        }
    )

    def _build_thinking_event_emitter(self, thread_id: str):
        """Return an emitter bound to ``thread_id`` for Runtime planning events."""

        def _emit(event_type: str, payload: Dict[str, Any]) -> None:
            if self._force_stop_requested(thread_id):
                raise ThinkingForceStoppedError("thinking force stopped")
            if event_type not in self._RUNTIME_PLANNING_EVENTS:
                return
            safe_payload = dict(payload) if isinstance(payload, dict) else {"data": payload}
            safe_payload.setdefault("thread_id", thread_id)
            self._emit_thread_event(thread_id, event_type, safe_payload)

        return _emit

    def _force_stop_event(self, thread_id: str) -> threading.Event:
        tid = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        with self._force_stop_lock:
            event = self._force_stop_events.get(tid)
            if event is None:
                event = threading.Event()
                self._force_stop_events[tid] = event
            return event

    def _clear_force_stop(self, thread_id: str) -> threading.Event:
        event = self._force_stop_event(thread_id)
        event.clear()
        return event

    def _force_stop_requested(self, thread_id: str) -> bool:
        tid = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        with self._force_stop_lock:
            event = self._force_stop_events.get(tid)
            return bool(event is not None and event.is_set())

    def force_stop_thread(self, thread_id: str, *, reason: str = "user_requested") -> Dict[str, Any]:
        """Request cancellation for the active thread and clear queued Runtime work."""
        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        self._force_stop_event(active_thread_id).set()
        safe_reason = str(reason or "user_requested").strip() or "user_requested"
        cleared_pending_users = 0
        with self._threads_lock:
            pending = self._runtime_pending_users.pop(active_thread_id, None)
            cleared_pending_users = len(pending or [])

        result = self._engine.force_stop_thread(active_thread_id, reason=safe_reason)

        session = self._get_or_create_thread(active_thread_id)
        with self._threads_lock:
            snapshot = self._thread_state_snapshot(session)
        result = dict(result)
        result["cleared_pending_user_turns"] = int(cleared_pending_users)
        result["thread_state"] = snapshot
        self._emit_thread_event(active_thread_id, "thread_state_updated", {"thread_state": snapshot})
        return result

    def _working_memory_api_payload(self, session: ThreadSessionState) -> Dict[str, Any]:
        """Return the ``thread_state.working_memory`` payload for HTTP/SSE clients."""
        wm_cfg = getattr(self.agent, "working_memory_config", None)
        if wm_cfg is None:
            return {"enabled": False, "stored_entries": 0, "entries": []}
        entries = self._current_wm_entries(session)
        task_progress = self._current_task_progress(session)
        return build_working_memory_api_payload(
            entries,
            wm_cfg,
            task_progress=task_progress,
        )

    def _episodic_persistence_payload(self) -> Dict[str, Any]:
        describe = getattr(self.agent, "describe_episodic_persistence", None)
        if callable(describe):
            payload = describe()
            return payload if isinstance(payload, dict) else {}
        return {}

    def _rounds_for_history(self, session: ThreadSessionState) -> List[BufferedRound]:
        rounds = [item for item in session.rounds if item.capture_state != "flushed"]
        return list(rounds[-self.history_max_rounds :])

    def _build_history_messages(self, session: ThreadSessionState) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []
        for round_item in self._rounds_for_history(session):
            messages.extend(round_item.to_history_messages())
        return messages

    def _trim_history(self, session: ThreadSessionState) -> None:
        while len(session.rounds) > self.history_max_rounds:
            oldest = session.rounds[0]
            if oldest.is_pending:
                break
            session.rounds.pop(0)

    def _append_round(
        self,
        session: ThreadSessionState,
        *,
        user_message: str,
        assistant_message: str,
        user_turn: Optional[Dict[str, Any]] = None,
        assistant_turn: Optional[Dict[str, Any]] = None,
        user_at: Optional[datetime] = None,
        assistant_at: Optional[datetime] = None,
    ) -> BufferedRound:
        resolved_user_at = user_at if isinstance(user_at, datetime) else _now_utc()
        resolved_assistant_at = (
            assistant_at if isinstance(assistant_at, datetime) else resolved_user_at + timedelta(seconds=1)
        )
        capture_state = "pending" if session.mode == "manual" else "skipped"
        round_item = BufferedRound(
            round_id=f"round_{uuid.uuid4().hex}",
            user_message=_normalize_text(user_message),
            assistant_message=_normalize_text(assistant_message),
            user_turn=_normalize_turn_payload(
                user_turn,
                fallback_speaker=str(getattr(self.agent, "user_name", "user") or "user").strip() or "user",
                fallback_text=user_message,
            ),
            assistant_turn=_normalize_turn_payload(
                assistant_turn,
                fallback_speaker=str(getattr(self.agent, "assistant_name", "assistant") or "assistant").strip()
                or "assistant",
                fallback_text=assistant_message,
            ),
            user_at=resolved_user_at,
            assistant_at=resolved_assistant_at,
            capture_state=capture_state,
        )
        session.rounds.append(round_item)
        session.last_activity_at = resolved_assistant_at
        session.idle_timer_started_at = resolved_assistant_at
        session.updated_at = resolved_assistant_at
        self._trim_history(session)
        return round_item

    def _pending_rounds(self, session: ThreadSessionState) -> List[BufferedRound]:
        return [item for item in session.rounds if item.is_pending]

    def _conversation_messages(self, session: ThreadSessionState) -> List[Dict[str, str]]:
        """Project persisted Scene turns into the current conversation transcript.

        ``session.rounds`` is intentionally hot, in-memory state. Scene is the
        durable source for an unflushed Runtime conversation, so this
        projection lets API clients restore the visible transcript after a
        service restart without treating internal thought/tool entries as chat.
        """
        conversation_id = session.conversation_id
        try:
            scene = self._runtime_host.list_scene(
                session.thread_id,
                conversation_id=conversation_id,
                limit=max(40, self.history_max_rounds * 2),
                since_flush=True,
            )
            entries = (
                list(scene.get("entries", []))
                if isinstance(scene, dict)
                else []
            )
        except Exception:
            logger.exception(
                "Failed to restore Scene conversation messages thread_id=%s",
                session.thread_id,
            )
            return []

        messages: List[Dict[str, str]] = []
        for entry in entries:
            if isinstance(entry, dict):
                data = dict(entry)
            elif hasattr(entry, "to_dict"):
                data = entry.to_dict()
            else:
                continue
            actor = str(data.get("actor", "") or "").strip().lower()
            entry_type = str(data.get("entry_type", "") or "").strip().lower()
            content = str(data.get("text", "") or "").strip()
            if not content:
                continue
            if entry_type == "utterance" or actor == "user":
                role = "user"
            elif entry_type == "reply" or actor == "assistant":
                role = "assistant"
            else:
                continue
            seq = int(data.get("seq", len(messages) + 1) or len(messages) + 1)
            messages.append(
                {
                    "message_id": f"scene-{seq}",
                    "role": role,
                    "content": content,
                    "timestamp": str(data.get("occurred_at", "") or ""),
                }
            )
        return messages[-(self.history_max_rounds * 2) :]

    @staticmethod
    def _serialize_round(item: BufferedRound) -> Dict[str, Any]:
        return {
            "round_id": item.round_id,
            "capture_state": item.capture_state,
            "flush_id": item.flush_id,
            "user_message": item.user_message,
            "assistant_message": item.assistant_message,
            "user_turn": deepcopy(item.user_turn),
            "assistant_turn": deepcopy(item.assistant_turn),
            "user_at": _to_iso(item.user_at),
            "assistant_at": _to_iso(item.assistant_at),
        }

    def _thread_state_snapshot(self, session: ThreadSessionState) -> Dict[str, Any]:
        pending_rounds = self._pending_rounds(session)
        pending_turns = len(pending_rounds) * 2
        history_rounds_data = [
            self._serialize_round(item) for item in self._rounds_for_history(session)
        ]
        history_preview = history_rounds_data[-3:]
        conversation_messages = self._conversation_messages(session)

        has_pending_data = bool(pending_rounds)
        scene_pending_entries = 0
        scene_pending_turns = 0
        active_user_segment = False
        try:
            scene_metrics = self._engine.scene_pending_flush_metrics(
                session.thread_id,
                conversation_id=session.conversation_id,
            )
            scene_pending_entries = int(scene_metrics.get("scene_pending_entries", 0) or 0)
            scene_pending_turns = int(scene_metrics.get("scene_pending_turns", 0) or 0)
            active_user_segment = bool(scene_metrics.get("active_user_segment"))
            has_pending_data = has_pending_data or bool(scene_metrics.get("can_flush"))
            if session.idle_timer_started_at is None and scene_metrics.get(
                "latest_stimulus_at"
            ):
                restored_at = _parse_ts_from_turn(
                    {
                        "timestamp": scene_metrics.get("latest_activity_at")
                        or scene_metrics.get("latest_stimulus_at")
                    }
                )
                if restored_at is not None:
                    session.idle_timer_started_at = restored_at
                    session.last_activity_at = restored_at
        except Exception:
            logger.exception(
                "Runtime scene_pending_flush_metrics failed thread_id=%s",
                session.thread_id,
            )

        idle_deadline_at = None
        if (
            self.idle_flush_seconds > 0
            and session.idle_timer_started_at is not None
            and session.mode == "manual"
        ):
            idle_deadline_at = _to_iso(
                session.idle_timer_started_at
                + timedelta(seconds=self.idle_flush_seconds)
            )

        snapshot: Dict[str, Any] = {
            "thread_id": session.thread_id,
            "conversation_id": session.conversation_id,
            "mode": session.mode,
            "history_rounds": len(session.rounds),
            "history_messages": len(self._build_history_messages(session)),
            "pending_rounds": len(pending_rounds),
            "pending_turns": pending_turns,
            "has_pending_data": has_pending_data,
            "scene_pending_entries": scene_pending_entries,
            "scene_pending_turns": scene_pending_turns,
            "active_user_segment": active_user_segment,
            "last_activity_at": _to_iso(session.last_activity_at),
            "idle_timer_armed": session.idle_timer_started_at is not None,
            "idle_timer_started_at": (
                _to_iso(session.idle_timer_started_at)
                if session.idle_timer_started_at is not None
                else None
            ),
            "last_flush_at": _to_iso(session.last_flush_at) if session.last_flush_at else None,
            "last_flush_attempt_at": _to_iso(session.last_flush_attempt_at) if session.last_flush_attempt_at else None,
            "last_flush_reason": session.last_flush_reason,
            "last_flush_success": bool(session.last_flush_result.get("success")) if isinstance(session.last_flush_result, dict) else None,
            "idle_flush_seconds": self.idle_flush_seconds,
            "idle_flush_deadline": idle_deadline_at,
            "history_rounds_data": history_rounds_data,
            "history_preview": history_preview,
            "conversation_messages": conversation_messages,
            "working_memory": self._working_memory_api_payload(session),
            "episodic_persistence": self._episodic_persistence_payload(),
        }
        snap = THREAD_RUNTIME_STATUS.snapshot(session.thread_id)
        context_engine_id = self.runtime_engine_id
        engine_block = {
            "pending_stimuli": snap.pending_stimuli,
            "busy": snap.busy,
            "busy_reason": snap.busy_reason,
            "runtime_profile": context_engine_id,
            "runtime_engine_id": context_engine_id,
            "runtime_phase": snap.runtime_phase,
            "effective_depth": snap.effective_depth,
            "in_flight_stimulus_id": snap.in_flight_stimulus_id,
            "preempt_enabled": snap.preempt_enabled,
        }
        snapshot["runtime"] = dict(engine_block)
        snapshot["runtime_engine_id"] = context_engine_id
        return snapshot

    def get_thread_state(self, thread_id: str) -> Dict[str, Any]:
        session = self._get_or_create_thread(thread_id)
        with self._threads_lock:
            return self._thread_state_snapshot(session)

    def set_thread_mode(self, thread_id: str, *, mode: str, discard_pending: bool = False) -> Dict[str, Any]:
        session = self._get_or_create_thread(thread_id)
        normalized_mode = _normalize_memory_mode(mode, fallback=session.mode)
        with self._threads_lock:
            session.mode = normalized_mode
            session.updated_at = _now_utc()
            if discard_pending:
                for item in session.rounds:
                    if item.is_pending:
                        item.capture_state = "skipped"
                        item.flush_id = None
            snapshot = self._thread_state_snapshot(session)
        self._emit_thread_event(snapshot["thread_id"], "thread_state_updated", {"thread_state": snapshot})
        return {
            "success": True,
            "thread_id": snapshot["thread_id"],
            "mode": snapshot["mode"],
            "discard_pending": bool(discard_pending),
            "thread_state": snapshot,
        }

    def run_chat(
        self,
        *,
        message: str,
        thread_id: str,
        user_turn: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        active_thread_id = (
            str(thread_id or self.default_thread_id).strip()
            or self.default_thread_id
        )
        lock = _get_thread_lock(active_thread_id)
        with lock:
            return self._run_chat_locked(
                message=message,
                thread_id=active_thread_id,
                user_turn=user_turn,
            )

    def _run_chat_locked(
        self,
        *,
        message: str,
        thread_id: str,
        user_turn: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run chat while the shared Chat admission/Flush lock is held."""

        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        self._assert_admission_allowed(active_thread_id)
        cancel_event = self._clear_force_stop(active_thread_id)
        session = self._get_or_create_thread(active_thread_id)
        with self._threads_lock:
            history_messages = self._build_history_messages(session)
            conversation_id = session.conversation_id

        with self._stats_lock:
            self._runs_started += 1
            self._last_run_started_at = _now_iso()

        normalized_user_turn = _normalize_turn_payload(
            user_turn,
            fallback_speaker=str(getattr(self.agent, "user_name", "user") or "user").strip() or "user",
            fallback_text=message,
        )
        rendered_message = _render_turn_for_llm(normalized_user_turn)

        THREAD_RUNTIME_STATUS.mark_busy(active_thread_id, reason="chat_run")
        try:
            self._runtime_host.submit_user_message(
                thread_id=active_thread_id,
                conversation_id=conversation_id,
                text=rendered_message,
                schedule_drainer=False,
            )
            self._enqueue_runtime_user_turn(
                active_thread_id,
                user_message=_normalize_text(normalized_user_turn.get("text"))
                or rendered_message,
                user_turn=normalized_user_turn,
            )
            result = self._runtime_host.run_thread(
                active_thread_id,
                history_messages=history_messages,
                event_emitter=self._build_thinking_event_emitter(active_thread_id),
            )
        finally:
            THREAD_RUNTIME_STATUS.clear_busy(active_thread_id, reason="chat_run")
            THREAD_RUNTIME_STATUS.set_pending_stimuli(
                active_thread_id,
                self._runtime_host.pending_count(active_thread_id),
            )

        if cancel_event.is_set():
            raise ThinkingForceStoppedError("thinking force stopped")

        with self._threads_lock:
            thread_state = self._thread_state_snapshot(session)

        with self._stats_lock:
            self._runs_completed += 1
            self._last_run_finished_at = _now_iso()

        memory_capture = {
            "mode": session.mode,
            "status": "buffered" if session.mode == "manual" else "skipped",
            "reason": None if session.mode == "manual" else "memory mode is off",
            "pending_rounds": thread_state["pending_rounds"],
            "pending_turns": thread_state["pending_turns"],
        }

        output = dict(result)
        output["memory_write"] = None
        output["memory_capture"] = memory_capture
        output["thread_state"] = thread_state
        return output

    @staticmethod
    def _schedule_system_context(schedule_item: Any) -> Dict[str, Any]:
        schedule_id = str(
            getattr(schedule_item, "schedule_id", "") or ""
        ).strip()
        due_at_utc = str(
            getattr(schedule_item, "due_at_utc", "") or ""
        ).strip()
        timezone_name = str(
            getattr(schedule_item, "timezone_name", "") or ""
        ).strip()
        raw_objective = getattr(schedule_item, "deferred_objective", None)
        if hasattr(raw_objective, "to_dict"):
            objective = dict(raw_objective.to_dict())
        elif isinstance(raw_objective, dict):
            objective = dict(raw_objective)
        else:
            objective = {
                "description": str(
                    getattr(schedule_item, "text", "") or ""
                ).strip(),
                "encoding": "legacy_text",
            }
        objective.setdefault("role", "deferred_objective")
        origin = getattr(schedule_item, "origin", None)
        origin = dict(origin) if isinstance(origin, dict) else {}
        activation = {
            "schema_version": 1,
            "event": {
                "role": "activation_event",
                "type": "schedule_due",
                "source": "heartbeat",
                "subject_ref": schedule_id,
                "facts": {
                    "due_at_utc": due_at_utc,
                    "timezone_name": timezone_name,
                },
            },
            "objective": objective,
            "evidence": [],
            "origin": origin,
        }
        return {
            "trigger_source": "schedule",
            "schedule_id": schedule_id,
            "due_at_utc": due_at_utc,
            "timezone_name": timezone_name,
            "semantic_frame_version": 1,
            "activation": activation,
        }

    @staticmethod
    def _schedule_prompt(schedule_item: Any) -> str:
        del schedule_item
        return "schedule_due"

    def import_dialogues(
        self,
        *,
        migrate_legacy: bool = False,
        rebuild_rag: bool = False,
        index_rag: bool = True,
        copy_files: bool = True,
        dialogue_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Import dialogue JSON files into ``chat-api/<user>/`` and optionally index RAG."""
        from m_agent.api.chat_api_shared import ensure_dialogue_archive, resolve_dialogues_dir_for_agent
        from m_agent.chat.dialogue_import import (
            import_dialogue_files,
            legacy_user_dialogues_dir,
            migrate_legacy_user_dialogues,
        )

        user_name = str(getattr(self.agent, "user_name", "") or "default")
        owner_id = str(
            getattr(self.agent, "owner_id", "") or user_name or "default"
        ).strip() or "default"
        assistant_name = str(getattr(self.agent, "assistant_name", "Memory Assistant") or "Memory Assistant")

        if migrate_legacy:
            result = migrate_legacy_user_dialogues(
                user_name,
                assistant_name=assistant_name,
                index_rag=index_rag,
                rebuild_rag=rebuild_rag,
                owner_id=owner_id,
            )
        else:
            dialogues_dir = resolve_dialogues_dir_for_agent(self.agent)
            ensure_dialogue_archive(self.agent)
            sources = sorted(Path(dialogues_dir).rglob("*.json"))
            if dialogue_ids:
                wanted = {str(item).strip() for item in dialogue_ids if str(item).strip()}
                sources = [p for p in sources if p.stem in wanted or p.name in wanted]
            result = import_dialogue_files(
                user_name=user_name,
                source_paths=sources,
                assistant_name=assistant_name,
                copy_to_user_dir=copy_files,
                index_rag=index_rag,
                rebuild_rag=rebuild_rag,
                source_label="chat_api_dialogue_import",
                owner_id=owner_id,
            )

        if not migrate_legacy:
            legacy_dir = legacy_user_dialogues_dir(user_name)
            result["legacy_dir"] = str(legacy_dir)

        result["episodic_persistence"] = self._episodic_persistence_payload()
        return result

    def iter_upload_dialogues(
        self,
        uploads: Sequence[Tuple[str, bytes]],
        *,
        rebuild_rag: bool = False,
        index_rag: bool = True,
    ) -> Iterator[Dict[str, Any]]:
        """Yield SSE-ready events while validating and indexing uploaded dialogue JSON."""
        from m_agent.chat.dialogue_import import import_uploaded_dialogues_stream

        user_name = str(getattr(self.agent, "user_name", "") or "default")
        owner_id = str(
            getattr(self.agent, "owner_id", "") or user_name or "default"
        ).strip() or "default"
        assistant_name = str(getattr(self.agent, "assistant_name", "Memory Assistant") or "Memory Assistant")
        seq = 0
        for event in import_uploaded_dialogues_stream(
            user_name=user_name,
            uploads=uploads,
            assistant_name=assistant_name,
            index_rag=index_rag,
            rebuild_rag=rebuild_rag,
            source_label="chat_api_dialogue_upload",
            owner_id=owner_id,
        ):
            if event.get("type") == "upload_completed" and isinstance(event.get("payload"), dict):
                event["payload"]["episodic_persistence"] = self._episodic_persistence_payload()
            seq += 1
            yield {"seq": seq, **event}

    def _scene_flush_through_seq(self, conversation_id: str) -> int:
        method = getattr(self._runtime_host, "scene_flush_through_seq", None)
        return int(method(conversation_id) or 0) if callable(method) else 0

    def _scene_flush_payload(
        self, thread_id: str, conversation_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            return self._engine.build_dialogue_flush_payload(
                thread_id,
                conversation_id=conversation_id,
                source="chat_api_thread_flush",
            )
        except Exception:
            logger.exception(
                "Runtime build_dialogue_flush_payload failed thread_id=%s",
                thread_id,
            )
            return None

    def _prepare_runtime_flush_plan(
        self,
        thread_id: str,
        conversation_id: str,
    ) -> Dict[str, Any]:
        plan = self._runtime_host.prepare_flush_segment(
            thread_id,
            conversation_id=conversation_id,
            source="chat_api_thread_flush",
        )
        if not isinstance(plan, dict):
            raise TypeError("runtime FlushSnapshot plan must be a mapping")
        if not str(plan.get("flush_id", "") or "").strip():
            raise ValueError("runtime FlushSnapshot plan has no flush_id")
        if not isinstance(plan.get("flush_snapshot"), dict):
            raise ValueError("runtime FlushSnapshot plan has no snapshot")
        return plan

    def _buffered_dialogue_flush_payload(
        self,
        thread_id: str,
        pending_rounds: Sequence[BufferedRound],
    ) -> Dict[str, Any]:
        if not pending_rounds:
            raise ValueError("pending_rounds are required")
        rounds = [
            {
                "user_message": item.user_message,
                "assistant_message": item.assistant_message,
                "user_turn": deepcopy(item.user_turn),
                "assistant_turn": deepcopy(item.assistant_turn),
                "user_at": item.user_at,
                "assistant_at": item.assistant_at,
            }
            for item in pending_rounds
        ]
        return build_dialogue_payload(
            dialogue_id=build_dialogue_id(
                thread_id=thread_id,
                created_at=pending_rounds[0].user_at,
            ),
            thread_id=thread_id,
            rounds=rounds,
            source="chat_api_thread_flush",
            user_name=str(getattr(self.agent, "user_name", "User") or "User"),
            assistant_name=str(
                getattr(self.agent, "assistant_name", "Memory Assistant")
                or "Memory Assistant"
            ),
        )

    def _scene_payload_covers_pending_rounds(
        self,
        scene_payload: Dict[str, Any],
        pending_rounds: Sequence[BufferedRound],
    ) -> bool:
        """Return whether Scene contains every buffered assistant reply.

        Buffered rounds are the loss-prevention copy of finalized replies.  If
        a reply tool accidentally writes to a different Scene conversation,
        exporting the otherwise-valid user-only Scene payload would silently
        discard those replies.  Prefer the complete buffer in that case.
        """
        if not pending_rounds:
            return True
        turns = scene_payload.get("turns")
        if not isinstance(turns, list):
            return False
        assistant_name = str(
            getattr(self.agent, "assistant_name", "Memory Assistant")
            or "Memory Assistant"
        ).strip()
        assistant_turns = sum(
            1
            for turn in turns
            if isinstance(turn, dict)
            and (
                str(turn.get("speaker", "") or "").strip() == assistant_name
                or str(turn.get("actor", "") or "").strip().lower() == "assistant"
                or str(turn.get("entry_type", "") or "").strip().lower() == "reply"
            )
        )
        meta = scene_payload.get("meta")
        try:
            round_count = int(meta.get("round_count", 0) or 0) if isinstance(meta, dict) else 0
        except (TypeError, ValueError):
            round_count = 0
        required = len(pending_rounds)
        return assistant_turns >= required and round_count >= required

    def _flush_block_reason(self, thread_id: str) -> Optional[str]:
        """Return why a conversation segment is not safe to flush yet."""
        tid = str(thread_id or "").strip()
        snap = THREAD_RUNTIME_STATUS.snapshot(tid)
        if snap.drainer_active:
            return "drainer_active"
        if snap.pending_stimuli > 0:
            return "stimuli_queued"
        if snap.in_flight_stimulus_id:
            return "stimulus_in_flight"
        with self._threads_lock:
            if self._runtime_pending_users.get(tid):
                return "reply_pending"
        return None

    def _assert_admission_allowed(self, thread_id: str) -> None:
        """Fence new work while a frozen durable flush is unfinished."""

        tid = str(thread_id or "").strip()
        checker = getattr(self._runtime_host, "has_pending_flush", None)
        if callable(checker) and bool(checker(tid)):
            raise PendingFlushAdmissionError(
                f"thread {tid!r} has an unfinished durable flush; retry the "
                "flush before submitting new work"
            )

    def _busy_flush_result(
        self,
        session: ThreadSessionState,
        *,
        reason: str,
        block_reason: str,
    ) -> Dict[str, Any]:
        with self._threads_lock:
            snapshot = self._thread_state_snapshot(session)
        return {
            "success": False,
            "retryable": True,
            "thread_id": session.thread_id,
            "flush_reason": reason,
            "status": "busy",
            "message": "thread is still processing; flush was deferred",
            "block_reason": block_reason,
            "thread_state": snapshot,
        }

    def flush_thread(self, thread_id: str, *, reason: str = "manual_api") -> Dict[str, Any]:
        """Flush one thread while excluding concurrent stimulus admission."""
        tid = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        lock = _get_thread_lock(tid)
        with lock:
            return self._flush_thread_locked(tid, reason=reason)

    def _flush_thread_locked(self, thread_id: str, *, reason: str) -> Dict[str, Any]:
        session = self._get_or_create_thread(thread_id)
        block_reason = self._flush_block_reason(session.thread_id)
        if block_reason:
            return self._busy_flush_result(
                session,
                reason=reason,
                block_reason=block_reason,
            )
        operation_id = f"flush_{uuid.uuid4().hex}"
        staged_dialogue_materialization: Optional[Dict[str, Any]] = None
        delivered_dialogue_result: Optional[Dict[str, Any]] = None
        with self._threads_lock:
            pending_rounds = list(self._pending_rounds(session))
            session.last_flush_attempt_at = _now_utc()
            session.updated_at = session.last_flush_attempt_at
            try:
                runtime_flush_plan = self._prepare_runtime_flush_plan(
                    session.thread_id,
                    session.conversation_id,
                )
            except Exception as exc:
                logger.exception(
                    "Failed to prepare durable FlushSnapshot thread_id=%s",
                    session.thread_id,
                )
                session.last_flush_result = {
                    "success": False,
                    "status": "failed",
                    "error": str(exc),
                    "conversation_id": session.conversation_id,
                }
                snapshot = self._thread_state_snapshot(session)
                return {
                    "success": False,
                    "retryable": True,
                    "thread_id": session.thread_id,
                    "flush_reason": reason,
                    "status": "failed",
                    "message": "runtime flush snapshot failed",
                    "error": str(exc),
                    "thread_state": snapshot,
                }
            if runtime_flush_plan is not None:
                planned_payload = runtime_flush_plan.get("dialogue_payload")
                scene_payload = (
                    deepcopy(planned_payload)
                    if isinstance(planned_payload, dict)
                    else None
                )
                scene_flush_through_seq = int(
                    runtime_flush_plan.get("through_seq", 0) or 0
                )
                runtime_flush_id = str(
                    runtime_flush_plan.get("flush_id", "") or ""
                ).strip()
                materializations = runtime_flush_plan.get(
                    "materializations"
                )
                materializations = (
                    materializations
                    if isinstance(materializations, dict)
                    else {}
                )
                dialogue_state = materializations.get("dialogue")
                dialogue_state = (
                    dialogue_state if isinstance(dialogue_state, dict) else {}
                )
                delivered_result = dialogue_state.get("result")
                if (
                    dialogue_state.get("status") == "delivered"
                    and isinstance(delivered_result, dict)
                ):
                    delivered_dialogue_result = deepcopy(delivered_result)
                staged_payload = dialogue_state.get("payload")
                if isinstance(staged_payload, dict):
                    staged_dialogue_materialization = deepcopy(
                        staged_payload
                    )
                    frozen_dialogue = staged_payload.get("dialogue_payload")
                    if isinstance(frozen_dialogue, dict):
                        scene_payload = deepcopy(frozen_dialogue)
            else:
                scene_payload = self._scene_flush_payload(
                    session.thread_id, session.conversation_id
                )
                # Advance the Scene watermark even when the pending slice only
                # contains internal/action entries and therefore produces no
                # user-visible Dialogue payload.
                scene_flush_through_seq = self._scene_flush_through_seq(
                    session.conversation_id
                )
                runtime_flush_id = operation_id
            if scene_payload and not self._scene_payload_covers_pending_rounds(
                scene_payload,
                pending_rounds,
            ):
                logger.warning(
                    "Scene payload is missing buffered assistant replies; "
                    "using buffered rounds thread_id=%s conversation_id=%s",
                    session.thread_id,
                    session.conversation_id,
                )
                scene_payload = None

            if not scene_payload and not pending_rounds:
                old_conversation_id = session.conversation_id
                runtime_segment: Optional[Dict[str, Any]] = None
                try:
                    flush_kwargs: Dict[str, Any] = {
                        "conversation_id": old_conversation_id,
                        "flush_id": runtime_flush_id,
                        "through_seq": scene_flush_through_seq,
                        "payload": {
                            "reason": reason,
                            "flush_mode": "noop",
                            "external_dialogue_written": False,
                        },
                    }
                    if runtime_flush_plan is not None:
                        flush_kwargs.update(
                            {
                                "flush_snapshot": runtime_flush_plan[
                                    "flush_snapshot"
                                ],
                                "defer_completion": True,
                            }
                        )
                    runtime_segment = self._engine.on_flush_segment(
                        session.thread_id,
                        **flush_kwargs,
                    )
                except Exception as exc:
                    logger.exception(
                        "Runtime on_flush_segment failed thread_id=%s (noop flush)",
                        session.thread_id,
                    )
                    session.last_flush_result = {
                        "success": False,
                        "status": "failed",
                        "error": str(exc),
                        "conversation_id": old_conversation_id,
                    }
                    snapshot = self._thread_state_snapshot(session)
                    return {
                        "success": False,
                        "retryable": True,
                        "thread_id": session.thread_id,
                        "flush_reason": reason,
                        "status": "failed",
                        "message": "runtime flush commit failed",
                        "error": str(exc),
                        "thread_state": snapshot,
                        "runtime_flush": None,
                    }
                next_conversation_seq = int(session.conversation_seq) + 1
                try:
                    self._persist_conversation_seq_value(
                        session.thread_id,
                        next_conversation_seq,
                    )
                except Exception as exc:
                    logger.exception(
                        "Conversation boundary persistence failed thread_id=%s",
                        session.thread_id,
                    )
                    session.last_flush_result = {
                        "success": False,
                        "status": "failed",
                        "error": str(exc),
                        "conversation_id": old_conversation_id,
                    }
                    snapshot = self._thread_state_snapshot(session)
                    return {
                        "success": False,
                        "retryable": True,
                        "thread_id": session.thread_id,
                        "flush_reason": reason,
                        "status": "failed",
                        "message": "conversation boundary persistence failed",
                        "error": str(exc),
                        "thread_state": snapshot,
                        "runtime_flush": runtime_segment,
                    }
                try:
                    self._agent.on_flush(
                        conversation_id=old_conversation_id,
                        thread_id=session.thread_id,
                    )
                except Exception:
                    logger.exception(
                        "ThreeLayerChatAgent.on_flush failed for conversation_id=%s thread_id=%s",
                        old_conversation_id,
                        session.thread_id,
                    )
                if runtime_flush_plan is not None:
                    try:
                        completion = self._runtime_host.complete_flush_segment(
                            runtime_flush_id
                        )
                        if runtime_segment is not None:
                            runtime_segment["journal_status"] = completion.get(
                                "status",
                                "completed",
                            )
                    except Exception as exc:
                        logger.exception(
                            "Runtime noop flush completion failed after durable "
                            "boundary thread_id=%s",
                            session.thread_id,
                        )
                        session.last_flush_result = {
                            "success": False,
                            "status": "failed",
                            "error": str(exc),
                            "conversation_id": old_conversation_id,
                        }
                        snapshot = self._thread_state_snapshot(session)
                        return {
                            "success": False,
                            "retryable": True,
                            "thread_id": session.thread_id,
                            "flush_reason": reason,
                            "status": "failed",
                            "message": "runtime flush completion failed",
                            "error": str(exc),
                            "thread_state": snapshot,
                            "runtime_flush": runtime_segment,
                        }
                message = "no pending rounds to flush"
                status = "noop"
                if runtime_segment and runtime_segment.get("completed_transaction_id"):
                    message = "runtime user segment closed (no pending dialogue turns)"
                    status = "runtime_segment"
                session.idle_timer_started_at = None
                session.last_flush_at = session.last_flush_attempt_at
                session.last_flush_reason = reason
                session.last_flush_result = {
                    "success": True,
                    "status": status,
                    "conversation_id": old_conversation_id,
                }
                session.flush_count += 1
                session.conversation_seq = next_conversation_seq
                snapshot = self._thread_state_snapshot(session)
                result = {
                    "success": True,
                    "thread_id": snapshot["thread_id"],
                    "flush_reason": reason,
                    "status": status,
                    "message": message,
                    "thread_state": snapshot,
                    "runtime_flush": runtime_segment,
                }
                self._emit_thread_event(
                    snapshot["thread_id"],
                    "flush_completed",
                    {
                        "operation_id": operation_id,
                        "thread_id": snapshot["thread_id"],
                        "flush_reason": reason,
                        "success": True,
                        "status": status,
                        "message": message,
                        "rounds_flushed": 0,
                        "turns_flushed": 0,
                        "thread_state": snapshot,
                    },
                )
                self._emit_thread_event(snapshot["thread_id"], "thread_state_updated", {"thread_state": snapshot})
                return result

        if staged_dialogue_materialization is not None:
            flush_mode = str(
                staged_dialogue_materialization.get("flush_mode", "scene")
                or "scene"
            )
            rounds_flushed = int(
                staged_dialogue_materialization.get("rounds_flushed", 0)
                or 0
            )
            turns_flushed = int(
                staged_dialogue_materialization.get("turns_flushed", 0)
                or 0
            )
        elif scene_payload:
            turns = scene_payload.get("turns") if isinstance(scene_payload.get("turns"), list) else []
            meta = scene_payload.get("meta") if isinstance(scene_payload.get("meta"), dict) else {}
            rounds_flushed = int(meta.get("round_count", 0) or 0)
            turns_flushed = len(turns)
            flush_mode = "scene"
        else:
            turns_flushed = len(pending_rounds) * 2
            rounds_flushed = len(pending_rounds)
            # Recovery path for a missing Scene payload. Buffered rounds are
            # maintained by Runtime for hot history and prevent data loss.
            flush_mode = "buffered_rounds"

        if runtime_flush_plan is not None and staged_dialogue_materialization is None:
            try:
                if scene_payload is None:
                    scene_payload = self._buffered_dialogue_flush_payload(
                        session.thread_id,
                        pending_rounds,
                    )
                staged_dialogue_materialization = {
                    "dialogue_payload": deepcopy(scene_payload),
                    "flush_mode": flush_mode,
                    "rounds_flushed": rounds_flushed,
                    "turns_flushed": turns_flushed,
                }
                runtime_flush_plan = (
                    self._runtime_host.stage_flush_materialization(
                        runtime_flush_id,
                        destination="dialogue",
                        payload=staged_dialogue_materialization,
                    )
                )
            except Exception as exc:
                logger.exception(
                    "Failed to stage durable dialogue materialization thread_id=%s",
                    session.thread_id,
                )
                with self._threads_lock:
                    session.last_flush_result = {
                        "success": False,
                        "status": "failed",
                        "error": str(exc),
                        "conversation_id": session.conversation_id,
                    }
                    snapshot = self._thread_state_snapshot(session)
                return {
                    "success": False,
                    "retryable": True,
                    "thread_id": session.thread_id,
                    "flush_reason": reason,
                    "status": "failed",
                    "message": "dialogue materialization staging failed",
                    "error": str(exc),
                    "thread_state": snapshot,
                }

        self._emit_thread_event(
            session.thread_id,
            "flush_started",
            {
                "operation_id": operation_id,
                "thread_id": session.thread_id,
                "flush_reason": reason,
                "pending_rounds": rounds_flushed,
                "pending_turns": turns_flushed,
                "flush_mode": flush_mode,
            },
        )

        with self._stats_lock:
            self._flushes_started += 1

        def progress_callback(event_type: str, payload: Dict[str, Any]) -> None:
            event_payload = {
                "operation_id": operation_id,
                "thread_id": session.thread_id,
                "flush_reason": reason,
            }
            if isinstance(payload, dict):
                event_payload.update(payload)
            self._emit_thread_event(session.thread_id, event_type, event_payload)

        runtime_segment: Optional[Dict[str, Any]] = None
        old_conversation_id = session.conversation_id
        flush_result: Dict[str, Any]
        if runtime_flush_plan is not None:
            try:
                runtime_segment = self._engine.on_flush_segment(
                    session.thread_id,
                    conversation_id=old_conversation_id,
                    flush_id=runtime_flush_id,
                    through_seq=scene_flush_through_seq,
                    flush_snapshot=runtime_flush_plan["flush_snapshot"],
                    defer_completion=True,
                    payload={
                        "flush_mode": flush_mode,
                        "rounds_flushed": rounds_flushed,
                        "turns_flushed": turns_flushed,
                        "external_dialogue_written": False,
                    },
                )
            except Exception as exc:
                logger.exception(
                    "Runtime flush commit failed before dialogue write thread_id=%s",
                    session.thread_id,
                )
                flush_result = {
                    "success": False,
                    "external_write_success": False,
                    "runtime_flush_id": runtime_flush_id,
                    "error": (
                    f"runtime flush commit failed: {exc}"
                    ),
                }
            else:
                flush_result = {}
        else:
            flush_result = {}

        if delivered_dialogue_result is not None:
            flush_result = deepcopy(delivered_dialogue_result)
        elif not flush_result:
            with self._operation_lock:
                if scene_payload:
                    persist_dialogue_payload = getattr(
                        self.agent,
                        "persist_dialogue_payload",
                        None,
                    )
                    if callable(persist_dialogue_payload):
                        flush_result = persist_dialogue_payload(
                            dialogue_payload=scene_payload,
                            thread_id=session.thread_id,
                            reason=f"chat_thread_{reason}",
                            source="chat_api_thread_flush",
                            progress_callback=progress_callback,
                        )
                    else:
                        flush_result = {
                            "success": False,
                            "error": "agent does not support persist_dialogue_payload",
                        }
                else:
                    round_payloads = [
                        {
                            "user_message": item.user_message,
                            "assistant_message": item.assistant_message,
                            "user_turn": deepcopy(item.user_turn),
                            "assistant_turn": deepcopy(item.assistant_turn),
                            "user_at": item.user_at,
                            "assistant_at": item.assistant_at,
                        }
                        for item in pending_rounds
                    ]
                    persist_dialogue = getattr(
                        self.agent,
                        "persist_dialogue",
                        None,
                    )
                    if callable(persist_dialogue):
                        flush_result = persist_dialogue(
                            thread_id=session.thread_id,
                            rounds=round_payloads,
                            reason=f"chat_thread_{reason}",
                            source="chat_api_thread_flush",
                            progress_callback=progress_callback,
                        )
                    else:
                        flush_result = self.agent.memory_persistence.persist_dialogue(
                            thread_id=session.thread_id,
                            rounds=round_payloads,
                            reason=f"chat_thread_{reason}",
                            source="chat_api_thread_flush",
                            progress_callback=progress_callback,
                        )

        flush_success = bool(flush_result.get("success", False))
        if flush_success and runtime_flush_plan is not None:
            try:
                delivered_plan = self._runtime_host.mark_flush_materialization_delivered(
                    runtime_flush_id,
                    destination="dialogue",
                    result=_summarize_memory_write_result(flush_result),
                )
                if runtime_segment is not None:
                    runtime_segment["journal_status"] = delivered_plan.get(
                        "journal_status",
                        "materialized",
                    )
            except Exception as exc:
                logger.exception(
                    "Runtime flush materialization acknowledgement failed "
                    "after dialogue write thread_id=%s",
                    session.thread_id,
                )
                failed_result = deepcopy(flush_result)
                failed_result["success"] = False
                failed_result["external_write_success"] = True
                failed_result["runtime_flush_id"] = runtime_flush_id
                failed_result["error"] = (
                    f"runtime flush materialization acknowledgement failed: {exc}"
                )
                flush_result = failed_result
                flush_success = False
        elif flush_success:
            runtime_flush_id = (
                str(flush_result.get("dialogue_id", "") or "").strip()
                or operation_id
            )
            try:
                runtime_segment = self._engine.on_flush_segment(
                    session.thread_id,
                    conversation_id=old_conversation_id,
                    flush_id=runtime_flush_id,
                    through_seq=scene_flush_through_seq,
                    payload={
                        "flush_mode": flush_mode,
                        "dialogue_id": str(
                            flush_result.get("dialogue_id", "") or ""
                        ).strip(),
                        "rounds_flushed": rounds_flushed,
                        "turns_flushed": turns_flushed,
                        "external_dialogue_written": True,
                    },
                )
            except Exception as exc:
                logger.exception(
                    "Runtime flush commit failed after dialogue write thread_id=%s",
                    session.thread_id,
                )
                failed_result = deepcopy(flush_result)
                failed_result["success"] = False
                failed_result["external_write_success"] = True
                failed_result["error"] = (
                    f"runtime flush commit failed: {exc}"
                )
                flush_result = failed_result
                flush_success = False
        drained_episode_notes: List[Dict[str, Any]] = []
        next_conversation_seq: Optional[int] = None
        if flush_success:
            next_conversation_seq = int(session.conversation_seq) + 1
            try:
                self._persist_conversation_seq_value(
                    session.thread_id,
                    next_conversation_seq,
                )
            except Exception as exc:
                logger.exception(
                    "Conversation boundary persistence failed thread_id=%s",
                    session.thread_id,
                )
                failed_result = deepcopy(flush_result)
                failed_result["success"] = False
                failed_result["external_write_success"] = True
                failed_result["runtime_flush_id"] = runtime_flush_id
                failed_result["error"] = (
                    f"conversation boundary persistence failed: {exc}"
                )
                flush_result = failed_result
                flush_success = False
        if flush_success:
            try:
                drained_episode_notes = list(
                    self._agent.on_flush(
                        conversation_id=old_conversation_id,
                        thread_id=session.thread_id,
                    )
                    or []
                )
            except Exception:
                # Episode notes are already frozen in the durable FlushSnapshot
                # and materialized with the dialogue. Failure to release the
                # in-process compatibility buffer must not duplicate the write.
                logger.exception(
                    "ThreeLayerChatAgent.on_flush failed for conversation_id=%s thread_id=%s",
                    old_conversation_id,
                    session.thread_id,
                )
        if flush_success and runtime_flush_plan is not None:
            try:
                completion = self._runtime_host.complete_flush_segment(
                    runtime_flush_id
                )
                if runtime_segment is not None:
                    runtime_segment["journal_status"] = completion.get(
                        "status",
                        "completed",
                    )
            except Exception as exc:
                logger.exception(
                    "Runtime flush completion failed after durable boundary thread_id=%s",
                    session.thread_id,
                )
                failed_result = deepcopy(flush_result)
                failed_result["success"] = False
                failed_result["external_write_success"] = True
                failed_result["runtime_flush_id"] = runtime_flush_id
                failed_result["error"] = f"runtime flush completion failed: {exc}"
                flush_result = failed_result
                flush_success = False
        with self._threads_lock:
            session.last_flush_attempt_at = _now_utc()
            session.last_flush_reason = reason
            session.last_flush_result = deepcopy(flush_result)
            if flush_success:
                flush_id = str(flush_result.get("dialogue_id", "") or "") or None
                for item in session.rounds:
                    if item.is_pending:
                        item.capture_state = "flushed"
                        item.flush_id = flush_id
                session.last_flush_at = session.last_flush_attempt_at
                session.flush_count += 1
                session.idle_timer_started_at = None

                assert next_conversation_seq is not None
                session.conversation_seq = next_conversation_seq

                self._trim_history(session)
            snapshot = self._thread_state_snapshot(session)

        if drained_episode_notes:
            logger.info(
                "Flush drained %d episode note(s) for thread_id=%s reason=%s",
                len(drained_episode_notes),
                session.thread_id,
                reason,
            )

        with self._stats_lock:
            if flush_success:
                self._flushes_completed += 1
            else:
                self._flushes_failed += 1

        result = {
            "success": flush_success,
            "retryable": not flush_success,
            "thread_id": session.thread_id,
            "flush_reason": reason,
            "status": "written" if flush_success else "failed",
            "flush_mode": flush_mode,
            "rounds_flushed": rounds_flushed if flush_success else 0,
            "turns_flushed": turns_flushed if flush_success else 0,
            "memory_write": flush_result,
            "runtime_flush": runtime_segment,
            "thread_state": snapshot,
            "error": None if flush_success else str(flush_result.get("error", "memory flush failed")),
        }
        self._emit_thread_event(
            session.thread_id,
            "flush_completed",
            {
                "operation_id": operation_id,
                "thread_id": session.thread_id,
                "flush_reason": reason,
                "success": flush_success,
                "status": result["status"],
                "rounds_flushed": result["rounds_flushed"],
                "turns_flushed": result["turns_flushed"],
                "memory_write": _summarize_memory_write_result(flush_result),
                "thread_state": snapshot,
                "error": result["error"],
            },
        )
        self._emit_thread_event(session.thread_id, "thread_state_updated", {"thread_state": snapshot})
        return result

    def flush_idle_threads(self) -> None:
        if self.idle_flush_seconds <= 0:
            return

        now = _now_utc()
        candidates: List[str] = []
        with self._threads_lock:
            self._last_idle_flush_scan_at = _to_iso(now)
            for thread_id, session in self._threads.items():
                if session.mode != "manual":
                    continue
                has_buffered_pending = bool(self._pending_rounds(session))
                has_scene_pending = False
                scene_metrics: Dict[str, Any] = {}
                try:
                    scene_metrics = self._engine.scene_pending_flush_metrics(
                        thread_id,
                        conversation_id=session.conversation_id,
                    )
                    has_scene_pending = int(
                        scene_metrics.get("scene_pending_turns", 0) or 0
                    ) > 0
                except Exception:
                    logger.exception(
                        "Idle flush scene metrics failed thread_id=%s",
                        thread_id,
                    )
                if not has_buffered_pending and not has_scene_pending:
                    continue
                if session.idle_timer_started_at is None:
                    latest_stimulus_at = scene_metrics.get("latest_stimulus_at")
                    latest_activity_at = scene_metrics.get("latest_activity_at")
                    if latest_stimulus_at:
                        restored_at = _parse_ts_from_turn(
                            {"timestamp": latest_activity_at or latest_stimulus_at}
                        )
                        if restored_at is not None:
                            session.idle_timer_started_at = restored_at
                            session.last_activity_at = restored_at
                    if session.idle_timer_started_at is None:
                        continue
                if (
                    session.idle_timer_started_at
                    + timedelta(seconds=self.idle_flush_seconds)
                    <= now
                ):
                    candidates.append(thread_id)

        for thread_id in candidates:
            lock = _get_thread_lock(thread_id)
            if not lock.acquire(blocking=False):
                continue
            try:
                block_reason = self._flush_block_reason(thread_id)
                if block_reason:
                    continue
                self._flush_thread_locked(thread_id, reason="idle_timeout")
            except Exception:
                logger.exception("Idle flush failed for thread_id=%s", thread_id)
            finally:
                lock.release()

    def submit_stimulus(
        self,
        *,
        thread_id: str,
        message: str,
        user_turn: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        lock = _get_thread_lock(active_thread_id)
        with lock:
            self._assert_admission_allowed(active_thread_id)
            session = self._get_or_create_thread(active_thread_id)
            normalized_user_turn = _normalize_turn_payload(
                user_turn,
                fallback_speaker=str(getattr(self.agent, "user_name", "user") or "user").strip() or "user",
                fallback_text=message,
            )
            rendered_message = _render_turn_for_llm(normalized_user_turn)
            user_text = _normalize_text(normalized_user_turn.get("text")) or rendered_message
            self._enqueue_runtime_user_turn(
                active_thread_id,
                user_message=user_text,
                user_turn=normalized_user_turn,
            )
            return self._engine.submit_stimulus_async(
                thread_id=active_thread_id,
                conversation_id=session.conversation_id,
                text=rendered_message,
                payload={"user_turn": normalized_user_turn},
            )

    def get_scene(
        self,
        thread_id: str,
        *,
        limit: int = 40,
        before_seq: Optional[int] = None,
        since_flush: bool = True,
    ) -> Dict[str, Any]:
        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        return self._engine.list_scene(
            active_thread_id,
            conversation_id=self._get_or_create_thread(active_thread_id).conversation_id,
            limit=limit,
            before_seq=before_seq,
            since_flush=since_flush,
        )

    def get_transactions(
        self,
        thread_id: str,
        *,
        include_history: bool = False,
    ) -> Dict[str, Any]:
        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        session = self._get_or_create_thread(active_thread_id)
        return self._runtime_host.list_transactions(
            active_thread_id,
            conversation_id=session.conversation_id,
            include_history=bool(include_history),
        )

    def delete_transaction(
        self,
        thread_id: str,
        transaction_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        active_thread_id = str(
            thread_id or self.default_thread_id
        ).strip() or self.default_thread_id
        lock = _get_thread_lock(active_thread_id)
        with lock:
            session = self._get_or_create_thread(active_thread_id)
            return self._runtime_host.delete_transaction(
                thread_id=active_thread_id,
                conversation_id=session.conversation_id,
                transaction_id=str(transaction_id or "").strip(),
                expected_revision=int(expected_revision),
                idempotency_key=str(idempotency_key or "").strip(),
            )

    def health_payload(self) -> Dict[str, Any]:
        with self._threads_lock:
            thread_count = len(self._threads)
            pending_thread_count = sum(1 for session in self._threads.values() if self._pending_rounds(session))
        with self._stats_lock:
            payload: Dict[str, Any] = {
                "config_path": str(self.config_path),
                "created_at": self.created_at,
                "default_thread_id": self.default_thread_id,
                "runtime_profile": self.runtime_profile,
                "runtime_engine_id": self.runtime_engine_id,
                "persist_memory": self.persist_memory,
                "idle_flush_seconds": self.idle_flush_seconds,
                "history_max_rounds": self.history_max_rounds,
                "runs_started": self._runs_started,
                "runs_completed": self._runs_completed,
                "runs_failed": self._runs_failed,
                "flushes_started": self._flushes_started,
                "flushes_completed": self._flushes_completed,
                "flushes_failed": self._flushes_failed,
                "thread_count": thread_count,
                "pending_thread_count": pending_thread_count,
                "last_run_started_at": self._last_run_started_at,
                "last_run_finished_at": self._last_run_finished_at,
                "last_idle_flush_scan_at": self._last_idle_flush_scan_at,
            }
        host_health = dict(self._runtime_host.health())
        runtime_metrics = {
            "runtime_engine_id": host_health.get(
                "runtime_engine_id", self.runtime_engine_id
            ),
            "pending_stimuli_total": host_health.get("pending_stimuli", 0),
            "active_drainer_threads": host_health.get("active_drainer_threads", 0),
            "preempt_enabled": host_health.get("preempt_enabled", False),
            "transactions": host_health.get("transactions", 0),
            "turn_loop": host_health.get("turn_loop"),
            "delegate_executor": host_health.get("delegate_executor"),
            "profile": host_health.get("profile"),
            "checkpoint": host_health.get("checkpoint"),
            "turn_checkpoint": host_health.get("turn_checkpoint"),
            "flush_journal": host_health.get("flush_journal"),
        }
        payload["runtime"] = runtime_metrics
        return payload
