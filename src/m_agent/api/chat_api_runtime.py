from __future__ import annotations

import logging
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from m_agent.chat.simple_chat_agent import SimpleMemoryChatAgent, create_simple_memory_chat_agent

from .chat_api_shared import (
    _get_thread_lock,
    _normalize_memory_mode,
    _now_iso,
    _now_utc,
    _summarize_memory_write_result,
    _to_iso,
)

logger = logging.getLogger(__name__)

ThreadEventSink = Optional[Callable[[str, str, Dict[str, Any]], Any]]


@dataclass
class BufferedRound:
    round_id: str
    user_message: str
    assistant_message: str
    user_at: datetime
    assistant_at: datetime
    agent_result: Optional[Dict[str, Any]]
    capture_state: str
    flush_id: Optional[str] = None

    def to_history_messages(self) -> List[Dict[str, str]]:
        return [
            {"role": "user", "content": self.user_message},
            {"role": "assistant", "content": self.assistant_message},
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
    ) -> None:
        self.config_path = config_path.resolve()
        self.created_at = _now_iso()
        self.idle_flush_seconds = max(0, int(idle_flush_seconds))
        self.history_max_rounds = max(1, int(history_max_rounds))
        self.idle_scan_interval_seconds = max(1, int(idle_scan_interval_seconds))
        self._operation_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._threads_lock = threading.Lock()
        self._agent: Optional[SimpleMemoryChatAgent] = None
        self._threads: Dict[str, ThreadSessionState] = {}
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

    def _warm_agent(self) -> None:
        logger.info("Initializing chat runtime with config %s", self.config_path)
        self._agent = create_simple_memory_chat_agent(config_path=self.config_path)
        logger.info(
            "Chat runtime initialized: default_thread_id=%s persist_memory=%s",
            self.default_thread_id,
            bool(getattr(self._agent, "persist_memory", False)),
        )

    @property
    def agent(self) -> SimpleMemoryChatAgent:
        if self._agent is None:
            raise RuntimeError("Chat runtime agent is not initialized")
        return self._agent

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

    def _rounds_for_history(self, session: ThreadSessionState) -> List[BufferedRound]:
        return list(session.rounds[-self.history_max_rounds :])

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
    ) -> BufferedRound:
        user_at = _now_utc()
        assistant_at = user_at + timedelta(seconds=1)
        capture_state = "pending" if session.mode == "manual" else "skipped"
        round_item = BufferedRound(
            round_id=f"round_{uuid.uuid4().hex}",
            user_message=str(user_message or "").strip(),
            assistant_message=str(assistant_message or "").strip(),
            user_at=user_at,
            assistant_at=assistant_at,
            agent_result=deepcopy(agent_result) if isinstance(agent_result, dict) else None,
            capture_state=capture_state,
        )
        session.rounds.append(round_item)
        session.last_activity_at = assistant_at
        session.updated_at = assistant_at
        self._trim_history(session)
        return round_item

    def _pending_rounds(self, session: ThreadSessionState) -> List[BufferedRound]:
        return [item for item in session.rounds if item.is_pending]

    @staticmethod
    def _serialize_round(item: BufferedRound) -> Dict[str, Any]:
        return {
            "round_id": item.round_id,
            "capture_state": item.capture_state,
            "flush_id": item.flush_id,
            "user_message": item.user_message,
            "assistant_message": item.assistant_message,
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

        return {
            "thread_id": session.thread_id,
            "mode": session.mode,
            "history_rounds": len(session.rounds),
            "history_messages": len(self._build_history_messages(session)),
            "pending_rounds": len(pending_rounds),
            "pending_turns": pending_turns,
            "has_pending_data": bool(pending_rounds),
            "last_activity_at": _to_iso(session.last_activity_at),
            "last_flush_at": _to_iso(session.last_flush_at) if session.last_flush_at else None,
            "last_flush_attempt_at": _to_iso(session.last_flush_attempt_at) if session.last_flush_attempt_at else None,
            "last_flush_reason": session.last_flush_reason,
            "last_flush_success": bool(session.last_flush_result.get("success")) if isinstance(session.last_flush_result, dict) else None,
            "idle_flush_seconds": self.idle_flush_seconds,
            "idle_flush_deadline": idle_deadline_at,
            "history_rounds_data": history_rounds_data,
            "history_preview": history_preview,
        }

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

    def run_chat(self, *, message: str, thread_id: str) -> Dict[str, Any]:
        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        session = self._get_or_create_thread(active_thread_id)
        with self._threads_lock:
            history_messages = self._build_history_messages(session)

        with self._stats_lock:
            self._runs_started += 1
            self._last_run_started_at = _now_iso()

        with self._operation_lock:
            result = self.agent.chat(
                message=message,
                thread_id=active_thread_id,
                history_messages=history_messages,
                persist_memory=False,
            )

        answer_text = str(result.get("answer", "") or "").strip()
        agent_result = result.get("agent_result") if isinstance(result.get("agent_result"), dict) else None
        with self._threads_lock:
            self._append_round(
                session,
                user_message=message,
                assistant_message=answer_text,
                agent_result=agent_result,
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

    def flush_thread(self, thread_id: str, *, reason: str = "manual_api") -> Dict[str, Any]:
        session = self._get_or_create_thread(thread_id)
        operation_id = f"flush_{uuid.uuid4().hex}"
        with self._threads_lock:
            pending_rounds = list(self._pending_rounds(session))
            session.last_flush_attempt_at = _now_utc()
            session.updated_at = session.last_flush_attempt_at
            if not pending_rounds:
                snapshot = self._thread_state_snapshot(session)
                result = {
                    "success": True,
                    "thread_id": snapshot["thread_id"],
                    "flush_reason": reason,
                    "status": "noop",
                    "message": "no pending rounds to flush",
                    "thread_state": snapshot,
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

        self._emit_thread_event(
            session.thread_id,
            "flush_started",
            {
                "operation_id": operation_id,
                "thread_id": session.thread_id,
                "flush_reason": reason,
                "pending_rounds": len(pending_rounds),
                "pending_turns": len(pending_rounds) * 2,
            },
        )

        with self._stats_lock:
            self._flushes_started += 1

        round_payloads = [
            {
                "user_message": item.user_message,
                "assistant_message": item.assistant_message,
                "user_at": item.user_at,
                "assistant_at": item.assistant_at,
                "agent_result": item.agent_result,
            }
            for item in pending_rounds
        ]

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
            flush_result = self.agent.memory_persistence.persist_dialogue(
                thread_id=session.thread_id,
                rounds=round_payloads,
                reason=f"chat_thread_{reason}",
                source="chat_api_thread_flush",
                progress_callback=progress_callback,
            )

        flush_success = bool(flush_result.get("success", False))
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
                self._trim_history(session)
            snapshot = self._thread_state_snapshot(session)

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
            "rounds_flushed": len(pending_rounds),
            "turns_flushed": len(pending_rounds) * 2,
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
                if not self._pending_rounds(session):
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

    def health_payload(self) -> Dict[str, Any]:
        with self._threads_lock:
            thread_count = len(self._threads)
            pending_thread_count = sum(1 for session in self._threads.values() if self._pending_rounds(session))
        with self._stats_lock:
            return {
                "config_path": str(self.config_path),
                "created_at": self.created_at,
                "default_thread_id": self.default_thread_id,
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
