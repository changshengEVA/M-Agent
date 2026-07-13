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
from m_agent.chat.chat_memory_persistence import _parse_ts_from_turn
from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent
from m_agent.chat.working_memory import build_working_memory_api_payload
from m_agent.paths import chat_user_slug
from m_agent.runtime.think_life import ThinkLifeRuntime
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


class _ThinkLifeScheduleLifecycle:
    """Bridges Think-life HEARTBEAT processing to the schedule store."""

    def __init__(self, runtime: "ChatServiceRuntime") -> None:
        self._runtime = runtime

    def _service(self):
        return self._runtime.agent.get_schedule_agent().service

    def on_schedule_processing_started(
        self,
        *,
        owner_id: str,
        thread_id: str,
        schedule_id: str,
        run_id: str,
        stimulus_id: str,
    ) -> None:
        self._service().mark_running(
            owner_id=owner_id,
            thread_id=thread_id,
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
        if success:
            self._service().mark_done(
                owner_id=owner_id,
                thread_id=thread_id,
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
                thread_id=thread_id,
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
    agent_result: Optional[Dict[str, Any]]
    capture_state: str
    source: str = "user"
    flush_id: Optional[str] = None

    def to_history_messages(self) -> List[Dict[str, str]]:
        if self.source == "schedule":
            return [{"role": "assistant", "content": _render_turn_for_llm(self.assistant_turn)}]
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
        runtime_profile: Optional[str] = None,
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
        self._think_life: Optional[ThinkLifeRuntime] = None
        self._runtime_profile_override = (
            str(runtime_profile).strip().lower() if runtime_profile else None
        )
        self._systems_override: Optional[SystemsBundle] = systems_override
        self._threads: Dict[str, ThreadSessionState] = {}
        self._force_stop_lock = threading.Lock()
        self._force_stop_events: Dict[str, threading.Event] = {}
        # Think-life async: user turns awaiting finalize reply (FIFO per thread).
        self._think_life_pending_users: Dict[str, Deque[Dict[str, Any]]] = {}
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
        if self._think_life is not None:
            self._wire_think_life_runtime()

    def _wire_think_life_runtime(self) -> None:
        if self._think_life is None:
            return

        def _emitter(thread_id: str, event_type: str, payload: Dict[str, Any]) -> None:
            if event_type == "reply_emitted" and isinstance(payload, dict) and payload.get("finalize"):
                self._capture_think_life_round(
                    thread_id,
                    assistant_message=str(payload.get("message", "") or ""),
                )
            self._emit_thread_event(thread_id, event_type, payload)

        self._think_life.set_thread_event_emitter(_emitter)

        def _history(thread_id: str) -> List[Dict[str, Any]]:
            session = self._get_or_create_thread(thread_id)
            with self._threads_lock:
                return self._build_history_messages(session)

        self._think_life.set_history_provider(_history)
        self._think_life.set_schedule_lifecycle(_ThinkLifeScheduleLifecycle(self))

    def _enqueue_think_life_user_turn(
        self,
        thread_id: str,
        *,
        user_message: str,
        user_turn: Dict[str, Any],
    ) -> None:
        tid = str(thread_id or "").strip()
        if not tid:
            return
        with self._threads_lock:
            queue = self._think_life_pending_users.setdefault(tid, deque())
            queue.append(
                {
                    "user_message": _normalize_text(user_message),
                    "user_turn": deepcopy(user_turn),
                    "submitted_at": _now_utc(),
                }
            )

    def _capture_think_life_round(self, thread_id: str, *, assistant_message: str) -> None:
        """Buffer a completed user/assistant round for flush (think_life async path)."""
        tid = str(thread_id or "").strip()
        if not tid:
            return
        with self._threads_lock:
            queue = self._think_life_pending_users.get(tid)
            if not queue:
                logger.warning(
                    "Think-life reply_emitted finalize with no pending user turn thread_id=%s",
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
                agent_result=None,
                user_turn=user_turn,
                assistant_turn=assistant_turn,
                source="user",
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
        profile = self._runtime_profile_override or ThinkLifeRuntime.profile_from_config(
            self.config_path
        )
        self._runtime_profile = profile
        if profile == "think_life":
            owner_slug = chat_user_slug(str(getattr(self._agent, "user_name", "") or "anonymous"))
            self._think_life = ThinkLifeRuntime(self._agent, owner_id=owner_slug)
            self._wire_think_life_runtime()
            logger.info("Chat runtime: Think-life profile enabled")
        else:
            self._think_life = None
        logger.info(
            "Chat runtime initialized: profile=%s default_thread_id=%s persist_memory=%s",
            profile,
            self.default_thread_id,
            bool(getattr(self._agent, "persist_memory", False)),
        )

    @property
    def agent(self) -> ThreeLayerChatAgent:
        if self._agent is None:
            raise RuntimeError("Chat runtime agent is not initialized")
        return self._agent

    @property
    def runtime_profile(self) -> str:
        return str(getattr(self, "_runtime_profile", "legacy") or "legacy")

    @property
    def think_life(self) -> Optional[ThinkLifeRuntime]:
        return self._think_life

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

    def _idle_flush_loop(self) -> None:
        while not self._stop_event.wait(self.idle_scan_interval_seconds):
            try:
                self.flush_idle_threads()
            except Exception:
                logger.exception("Idle flush loop failed")

    def _get_or_create_thread(self, thread_id: str) -> ThreadSessionState:
        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        with self._threads_lock:
            session = self._threads.get(active_thread_id)
            if session is None:
                session = ThreadSessionState(
                    thread_id=active_thread_id,
                    mode="manual",
                )
                self._threads[active_thread_id] = session
            return session

    # ------------------------------------------------------------------
    # Working-memory plumbing — WM lives inside the thinking layer's
    # ``ConversationState`` registry; the runtime just snapshots it for
    # API + SSE consumers.
    # ------------------------------------------------------------------

    def _current_wm_entries(self, session: ThreadSessionState) -> List[Dict[str, Any]]:
        """Return the WM entries owned by the thinking layer for this conversation."""
        if self._think_life is not None:
            transaction = self._think_life.registry.get_active_user_transaction(
                session.conversation_id
            )
            return list(transaction.wm_entries) if transaction is not None else []
        try:
            return list(self.agent.snapshot_working_memory(session.conversation_id) or [])
        except Exception:
            logger.exception(
                "snapshot_working_memory failed for conversation_id=%s",
                session.conversation_id,
            )
            return []

    def _current_task_progress(self, session: ThreadSessionState) -> Dict[str, Any]:
        """Return the task progress owned by the thinking layer for this conversation."""
        if self._think_life is not None:
            transaction = self._think_life.registry.get_active_user_transaction(
                session.conversation_id
            )
            return (
                transaction.task_state.to_dict()
                if transaction is not None
                else {"goal": "", "completed": [], "remaining": []}
            )
        snapshot = getattr(self.agent, "snapshot_task_progress", None)
        if not callable(snapshot):
            return {"goal": "", "completed": [], "remaining": []}
        try:
            payload = snapshot(session.conversation_id)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            logger.exception(
                "snapshot_task_progress failed for conversation_id=%s",
                session.conversation_id,
            )
            return {}

    # Allow-list of three-layer streaming events the runtime forwards from
    # ``ThinkingAgent.handle`` to SSE. Anything outside this set is silently
    # dropped so a misbehaving custom thinking layer cannot inject arbitrary
    # event types into the protocol.
    _THREE_LAYER_STREAM_EVENTS = frozenset(
        {
            "thinking_started",
            "thinking_task_state",
            "thinking_plan",
            "execution_started",
            "execution_completed",
            "thinking_summary",
            "thinking_completed",
        }
    )

    def _build_thinking_event_emitter(self, thread_id: str):
        """Return an emitter closure bound to ``thread_id`` for three-layer streaming."""

        def _emit(event_type: str, payload: Dict[str, Any]) -> None:
            if self._force_stop_requested(thread_id):
                raise ThinkingForceStoppedError("thinking force stopped")
            if event_type not in self._THREE_LAYER_STREAM_EVENTS:
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
        """Request cancellation for the active thread and clear queued Think-life work."""
        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        self._force_stop_event(active_thread_id).set()
        safe_reason = str(reason or "user_requested").strip() or "user_requested"
        cleared_pending_users = 0
        with self._threads_lock:
            pending = self._think_life_pending_users.pop(active_thread_id, None)
            cleared_pending_users = len(pending or [])

        if self._think_life is not None:
            result = self._think_life.force_stop_thread(active_thread_id, reason=safe_reason)
        else:
            result = {
                "success": True,
                "thread_id": active_thread_id,
                "runtime_profile": "legacy",
                "cancelled_in_flight": self._force_stop_requested(active_thread_id),
                "cleared_pending_stimuli": 0,
                "cancelled_transactions": [],
            }
            self._emit_thread_event(
                active_thread_id,
                "thinking_force_stopped",
                {
                    "thread_id": active_thread_id,
                    "reason": safe_reason,
                    "cancelled_in_flight": True,
                    "cleared_pending_stimuli": 0,
                    "cancelled_transactions": [],
                },
            )

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
        rounds = session.rounds
        if self._think_life is not None:
            rounds = [item for item in rounds if item.capture_state != "flushed"]
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
        agent_result: Optional[Dict[str, Any]],
        user_turn: Optional[Dict[str, Any]] = None,
        assistant_turn: Optional[Dict[str, Any]] = None,
        source: str = "user",
        capture_state_override: Optional[str] = None,
        user_at: Optional[datetime] = None,
        assistant_at: Optional[datetime] = None,
    ) -> BufferedRound:
        resolved_user_at = user_at if isinstance(user_at, datetime) else _now_utc()
        resolved_assistant_at = (
            assistant_at if isinstance(assistant_at, datetime) else resolved_user_at + timedelta(seconds=1)
        )
        capture_state = (
            str(capture_state_override or "").strip()
            or ("pending" if session.mode == "manual" else "skipped")
        )
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
            agent_result=deepcopy(agent_result) if isinstance(agent_result, dict) else None,
            capture_state=capture_state,
            source=str(source or "user").strip() or "user",
        )
        session.rounds.append(round_item)
        session.last_activity_at = resolved_assistant_at
        session.updated_at = resolved_assistant_at
        self._trim_history(session)
        return round_item

    def _pending_rounds(self, session: ThreadSessionState) -> List[BufferedRound]:
        return [item for item in session.rounds if item.is_pending]

    @staticmethod
    def _serialize_round(item: BufferedRound) -> Dict[str, Any]:
        return {
            "round_id": item.round_id,
            "capture_state": item.capture_state,
            "source": item.source,
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
        idle_deadline_at = None
        if self.idle_flush_seconds > 0 and pending_rounds and session.mode == "manual":
            idle_deadline_at = _to_iso(session.last_activity_at + timedelta(seconds=self.idle_flush_seconds))

        history_rounds_data = [
            self._serialize_round(item) for item in self._rounds_for_history(session)
        ]
        history_preview = history_rounds_data[-3:]

        has_pending_data = bool(pending_rounds)
        scene_pending_entries = 0
        scene_pending_turns = 0
        active_user_segment = False
        if self._think_life is not None:
            try:
                scene_metrics = self._think_life.scene_pending_flush_metrics(
                    session.thread_id,
                    conversation_id=session.conversation_id,
                )
                scene_pending_entries = int(scene_metrics.get("scene_pending_entries", 0) or 0)
                scene_pending_turns = int(scene_metrics.get("scene_pending_turns", 0) or 0)
                active_user_segment = bool(scene_metrics.get("active_user_segment"))
                has_pending_data = has_pending_data or bool(scene_metrics.get("can_flush"))
            except Exception:
                logger.exception(
                    "Think-life scene_pending_flush_metrics failed thread_id=%s",
                    session.thread_id,
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
            "last_flush_at": _to_iso(session.last_flush_at) if session.last_flush_at else None,
            "last_flush_attempt_at": _to_iso(session.last_flush_attempt_at) if session.last_flush_attempt_at else None,
            "last_flush_reason": session.last_flush_reason,
            "last_flush_success": bool(session.last_flush_result.get("success")) if isinstance(session.last_flush_result, dict) else None,
            "idle_flush_seconds": self.idle_flush_seconds,
            "idle_flush_deadline": idle_deadline_at,
            "history_rounds_data": history_rounds_data,
            "history_preview": history_preview,
            "working_memory": self._working_memory_api_payload(session),
            "episodic_persistence": self._episodic_persistence_payload(),
        }
        if self._think_life is not None:
            snap = THREAD_RUNTIME_STATUS.snapshot(
                session.thread_id,
                default_profile="think_life",
            )
            snapshot["think_life"] = {
                "pending_stimuli": snap.pending_stimuli,
                "busy": snap.busy,
                "busy_reason": snap.busy_reason,
                "runtime_profile": "think_life",
                "runtime_phase": snap.runtime_phase,
                "effective_depth": snap.effective_depth,
                "in_flight_stimulus_id": snap.in_flight_stimulus_id,
                "preempt_enabled": snap.preempt_enabled,
            }
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
        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
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

        if self._think_life is not None:
            THREAD_RUNTIME_STATUS.mark_busy(active_thread_id, reason="chat_run")
            try:
                self._enqueue_think_life_user_turn(
                    active_thread_id,
                    user_message=_normalize_text(normalized_user_turn.get("text")) or rendered_message,
                    user_turn=normalized_user_turn,
                )
                self._think_life.submit_user_message(
                    thread_id=active_thread_id,
                    conversation_id=conversation_id,
                    text=rendered_message,
                    schedule_drainer=False,
                )
                result = self._think_life.run_thread(
                    active_thread_id,
                    history_messages=history_messages,
                    event_emitter=self._build_thinking_event_emitter(active_thread_id),
                )
            finally:
                THREAD_RUNTIME_STATUS.clear_busy(active_thread_id, reason="chat_run")
                THREAD_RUNTIME_STATUS.set_pending_stimuli(
                    active_thread_id,
                    self._think_life.inbox.pending_count(active_thread_id),
                )
        else:
            with self._operation_lock:
                result = self.agent.chat(
                    message=rendered_message,
                    thread_id=active_thread_id,
                    history_messages=history_messages,
                    persist_memory=False,
                    conversation_id=conversation_id,
                    event_emitter=self._build_thinking_event_emitter(active_thread_id),
                )

        if cancel_event.is_set():
            raise ThinkingForceStoppedError("thinking force stopped")

        answer_text = str(result.get("answer", "") or "").strip()
        agent_result = result.get("agent_result") if isinstance(result.get("agent_result"), dict) else None
        assistant_turn = {
            "speaker": str(getattr(self.agent, "assistant_name", "assistant") or "assistant").strip() or "assistant",
            "text": answer_text,
        }
        with self._threads_lock:
            if self._think_life is None:
                self._append_round(
                    session,
                    user_message=_normalize_text(normalized_user_turn.get("text")) or rendered_message,
                    assistant_message=answer_text,
                    agent_result=agent_result,
                    user_turn=normalized_user_turn,
                    assistant_turn=assistant_turn,
                    source="user",
                )
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
        action_payload = getattr(schedule_item, "action_payload", None)
        metadata = getattr(schedule_item, "metadata", None)
        return {
            "trigger_source": "schedule",
            "schedule_id": str(getattr(schedule_item, "schedule_id", "") or "").strip(),
            "due_at_utc": str(getattr(schedule_item, "due_at_utc", "") or "").strip(),
            "timezone_name": str(getattr(schedule_item, "timezone_name", "") or "").strip(),
            "original_time_text": str(getattr(schedule_item, "original_time_text", "") or "").strip(),
            "action_type": str(getattr(schedule_item, "action_type", "") or "").strip(),
            "thread_id": str(getattr(schedule_item, "thread_id", "") or "").strip(),
            "source_text": str(getattr(schedule_item, "source_text", "") or "").strip(),
            "action_payload": deepcopy(action_payload) if isinstance(action_payload, dict) else {},
            "metadata": deepcopy(metadata) if isinstance(metadata, dict) else {},
        }

    @staticmethod
    def _schedule_prompt(schedule_item: Any) -> str:
        action_payload = getattr(schedule_item, "action_payload", None)
        if isinstance(action_payload, dict):
            prompt = str(action_payload.get("prompt", "") or "").strip()
            if prompt:
                return prompt
        for candidate in (
            getattr(schedule_item, "title", None),
            getattr(schedule_item, "source_text", None),
            getattr(schedule_item, "original_time_text", None),
        ):
            prompt = str(candidate or "").strip()
            if prompt:
                return prompt
        return "Scheduled reminder"

    def run_schedule_trigger(self, *, schedule_item: Any) -> Dict[str, Any]:
        active_thread_id = str(getattr(schedule_item, "thread_id", "") or self.default_thread_id).strip() or self.default_thread_id
        session = self._get_or_create_thread(active_thread_id)
        with self._threads_lock:
            history_messages = self._build_history_messages(session)
            conversation_id = session.conversation_id

        schedule_prompt = self._schedule_prompt(schedule_item)
        system_context = self._schedule_system_context(schedule_item)

        schedule_id = str(getattr(schedule_item, "schedule_id", "") or "").strip()
        owner_id = str(getattr(schedule_item, "owner_id", "") or "").strip()
        if self._think_life is not None:
            run_id = f"schedule_run_{uuid.uuid4().hex}"
            queued = self._think_life.enqueue_schedule(
                thread_id=active_thread_id,
                conversation_id=conversation_id,
                schedule_id=schedule_id,
                text=schedule_prompt,
                payload=system_context,
                run_id=run_id,
                owner_id=owner_id,
            )
            result = {
                "answer": "",
                "accepted": bool(queued.get("accepted")),
                "stimulus_id": queued.get("stimulus_id"),
                "pending_count": queued.get("pending_count"),
                "runtime_phase": queued.get("runtime_phase"),
            }
            agent_result = {"think_life_queued": queued}
        else:
            with self._operation_lock:
                result = self.agent.chat(
                    message=schedule_prompt,
                    thread_id=active_thread_id,
                    history_messages=history_messages,
                    persist_memory=False,
                    source="schedule",
                    system_context=system_context,
                    conversation_id=conversation_id,
                    event_emitter=self._build_thinking_event_emitter(active_thread_id),
                )
                agent_result = result.get("agent_result") if isinstance(result.get("agent_result"), dict) else None

        answer_text = str(result.get("answer", "") or "").strip()
        schedule_user_turn = {
            "speaker": str(getattr(self.agent, "user_name", "user") or "user").strip() or "user",
            "text": schedule_prompt,
        }
        schedule_assistant_turn = {
            "speaker": str(getattr(self.agent, "assistant_name", "assistant") or "assistant").strip() or "assistant",
            "text": answer_text,
        }
        with self._threads_lock:
            self._append_round(
                session,
                user_message=schedule_prompt,
                assistant_message=answer_text,
                agent_result=agent_result,
                user_turn=schedule_user_turn,
                assistant_turn=schedule_assistant_turn,
                source="schedule",
                capture_state_override="skipped",
            )
            thread_state = self._thread_state_snapshot(session)

        self._emit_thread_event(
            active_thread_id,
            "assistant_message",
            {
                "thread_id": active_thread_id,
                "answer": answer_text,
                "source": "schedule",
                "schedule_id": str(getattr(schedule_item, "schedule_id", "") or "").strip(),
            },
        )
        self._emit_thread_event(active_thread_id, "thread_state_updated", {"thread_state": thread_state})

        output = dict(result)
        output["thread_state"] = thread_state
        output["memory_capture"] = {
            "mode": session.mode,
            "status": "skipped",
            "reason": "schedule trigger is not persisted to memory buffer",
            "pending_rounds": thread_state["pending_rounds"],
            "pending_turns": thread_state["pending_turns"],
        }
        return output

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
        assistant_name = str(getattr(self.agent, "assistant_name", "Memory Assistant") or "Memory Assistant")

        if migrate_legacy:
            result = migrate_legacy_user_dialogues(
                user_name,
                assistant_name=assistant_name,
                index_rag=index_rag,
                rebuild_rag=rebuild_rag,
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
        assistant_name = str(getattr(self.agent, "assistant_name", "Memory Assistant") or "Memory Assistant")
        seq = 0
        for event in import_uploaded_dialogues_stream(
            user_name=user_name,
            uploads=uploads,
            assistant_name=assistant_name,
            index_rag=index_rag,
            rebuild_rag=rebuild_rag,
            source_label="chat_api_dialogue_upload",
        ):
            if event.get("type") == "upload_completed" and isinstance(event.get("payload"), dict):
                event["payload"]["episodic_persistence"] = self._episodic_persistence_payload()
            seq += 1
            yield {"seq": seq, **event}

    def _scene_flush_through_seq(self, conversation_id: str) -> int:
        if self._think_life is None:
            return 0
        reader = self._think_life.scene_system.reader
        entries_fn = getattr(reader, "entries_since_flush", None)
        if not callable(entries_fn):
            return 0
        entries = list(entries_fn(str(conversation_id or "").strip()))
        return max((int(getattr(entry, "seq", 0) or 0) for entry in entries), default=0)

    def _scene_flush_payload(
        self, thread_id: str, conversation_id: str
    ) -> Optional[Dict[str, Any]]:
        if self._think_life is None:
            return None
        try:
            return self._think_life.build_dialogue_flush_payload(
                thread_id,
                conversation_id=conversation_id,
                source="chat_api_thread_flush",
            )
        except Exception:
            logger.exception(
                "Think-life build_dialogue_flush_payload failed thread_id=%s",
                thread_id,
            )
            return None

    def flush_thread(self, thread_id: str, *, reason: str = "manual_api") -> Dict[str, Any]:
        session = self._get_or_create_thread(thread_id)
        operation_id = f"flush_{uuid.uuid4().hex}"
        with self._threads_lock:
            pending_rounds = list(self._pending_rounds(session))
            session.last_flush_attempt_at = _now_utc()
            session.updated_at = session.last_flush_attempt_at
            scene_payload = self._scene_flush_payload(
                session.thread_id, session.conversation_id
            )
            scene_flush_through_seq = (
                self._scene_flush_through_seq(session.conversation_id) if scene_payload else 0
            )

            if not scene_payload and not pending_rounds:
                think_life_segment: Optional[Dict[str, Any]] = None
                if self._think_life is not None:
                    try:
                        think_life_segment = self._think_life.on_flush_segment(
                            session.thread_id,
                            conversation_id=session.conversation_id,
                        )
                    except Exception:
                        logger.exception(
                            "Think-life on_flush_segment failed thread_id=%s (noop flush)",
                            session.thread_id,
                        )
                snapshot = self._thread_state_snapshot(session)
                message = "no pending rounds to flush"
                status = "noop"
                if think_life_segment and think_life_segment.get("completed_transaction_id"):
                    message = "think_life user segment closed (no legacy pending rounds)"
                    status = "think_life_segment"
                result = {
                    "success": True,
                    "thread_id": snapshot["thread_id"],
                    "flush_reason": reason,
                    "status": status,
                    "message": message,
                    "thread_state": snapshot,
                    "think_life_flush": think_life_segment,
                }
                self._emit_thread_event(
                    snapshot["thread_id"],
                    "flush_completed",
                    {
                        "operation_id": operation_id,
                        "thread_id": snapshot["thread_id"],
                        "flush_reason": reason,
                        "success": True,
                        "status": "noop",
                        "message": "no pending rounds to flush",
                        "rounds_flushed": 0,
                        "turns_flushed": 0,
                        "thread_state": snapshot,
                    },
                )
                self._emit_thread_event(snapshot["thread_id"], "thread_state_updated", {"thread_state": snapshot})
                return result

        if scene_payload:
            turns = scene_payload.get("turns") if isinstance(scene_payload.get("turns"), list) else []
            meta = scene_payload.get("meta") if isinstance(scene_payload.get("meta"), dict) else {}
            rounds_flushed = int(meta.get("round_count", 0) or 0)
            turns_flushed = len(turns)
            flush_mode = "scene"
        else:
            turns_flushed = len(pending_rounds) * 2
            rounds_flushed = len(pending_rounds)
            flush_mode = "legacy_rounds"

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

        with self._operation_lock:
            if scene_payload:
                persist_dialogue_payload = getattr(self.agent, "persist_dialogue_payload", None)
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
                        "agent_result": item.agent_result,
                    }
                    for item in pending_rounds
                ]
                persist_dialogue = getattr(self.agent, "persist_dialogue", None)
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
        drained_episode_notes: List[Dict[str, Any]] = []
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

                if scene_payload and self._think_life is not None:
                    try:
                        self._think_life.mark_scene_flushed(
                            session.conversation_id,
                            through_seq=scene_flush_through_seq,
                        )
                    except Exception:
                        logger.exception(
                            "Think-life mark_scene_flushed failed thread_id=%s",
                            session.thread_id,
                        )

                old_conversation_id = session.conversation_id
                try:
                    drained_episode_notes = list(
                        self._agent.on_flush(
                            conversation_id=old_conversation_id,
                            thread_id=session.thread_id,
                        )
                        or []
                    )
                except Exception:
                    logger.exception(
                        "ThreeLayerChatAgent.on_flush failed for conversation_id=%s thread_id=%s",
                        old_conversation_id,
                        session.thread_id,
                    )
                session.conversation_seq += 1

                if self._think_life is not None:
                    try:
                        self._think_life.on_flush_segment(
                            session.thread_id,
                            conversation_id=old_conversation_id,
                        )
                    except Exception:
                        logger.exception(
                            "Think-life on_flush_segment failed thread_id=%s",
                            session.thread_id,
                        )

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
            "thread_id": session.thread_id,
            "flush_reason": reason,
            "status": "written" if flush_success else "failed",
            "flush_mode": flush_mode,
            "rounds_flushed": rounds_flushed if flush_success else 0,
            "turns_flushed": turns_flushed if flush_success else 0,
            "memory_write": flush_result,
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
                has_legacy_pending = bool(self._pending_rounds(session))
                has_scene_pending = False
                if self._think_life is not None:
                    try:
                        metrics = self._think_life.scene_pending_flush_metrics(
                            thread_id,
                            conversation_id=session.conversation_id,
                        )
                        has_scene_pending = int(metrics.get("scene_pending_turns", 0) or 0) > 0
                    except Exception:
                        logger.exception(
                            "Idle flush scene metrics failed thread_id=%s",
                            thread_id,
                        )
                if not has_legacy_pending and not has_scene_pending:
                    continue
                if session.last_activity_at + timedelta(seconds=self.idle_flush_seconds) <= now:
                    candidates.append(thread_id)

        for thread_id in candidates:
            lock = _get_thread_lock(thread_id)
            if not lock.acquire(blocking=False):
                continue
            try:
                self.flush_thread(thread_id, reason="idle_timeout")
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
        if self._think_life is None:
            raise RuntimeError("profile_not_supported")

        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        normalized_user_turn = _normalize_turn_payload(
            user_turn,
            fallback_speaker=str(getattr(self.agent, "user_name", "user") or "user").strip() or "user",
            fallback_text=message,
        )
        rendered_message = _render_turn_for_llm(normalized_user_turn)
        user_text = _normalize_text(normalized_user_turn.get("text")) or rendered_message
        self._enqueue_think_life_user_turn(
            active_thread_id,
            user_message=user_text,
            user_turn=normalized_user_turn,
        )
        return self._think_life.submit_stimulus_async(
            thread_id=active_thread_id,
            conversation_id=self._get_or_create_thread(active_thread_id).conversation_id,
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
        if self._think_life is None:
            raise RuntimeError("profile_not_supported")
        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        return self._think_life.list_scene(
            active_thread_id,
            conversation_id=self._get_or_create_thread(active_thread_id).conversation_id,
            limit=limit,
            before_seq=before_seq,
            since_flush=since_flush,
        )

    def get_think_life_transactions(self, thread_id: str) -> Dict[str, Any]:
        if self._think_life is None:
            raise RuntimeError("profile_not_supported")
        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        return self._think_life.list_transactions(active_thread_id)

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
        if self._think_life is not None:
            tl_health = self._think_life.health()
            payload["think_life"] = {
                "pending_stimuli_total": tl_health.get("pending_stimuli", 0),
                "active_drainer_threads": tl_health.get("active_drainer_threads", 0),
                "preempt_enabled": tl_health.get("preempt_enabled", False),
            }
        return payload
