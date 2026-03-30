from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import threading
import traceback
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

from m_agent.agents.chat_controller_agent import DEFAULT_CHAT_CONFIG_PATH
from m_agent.chat.simple_chat_agent import SimpleMemoryChatAgent, create_simple_memory_chat_agent
from m_agent.paths import PROJECT_ROOT, resolve_project_path
from m_agent.utils.logging_trace import FunctionTraceHandler, TraceEvent


logger = logging.getLogger(__name__)
protocol_logger = logging.getLogger("m_agent.api.protocol")

TRACE_LOGGER_NAMES = (
    "m_agent.agents.memory_agent",
    "m_agent.agents.chat_controller_agent",
    "Agents.memory_agent",
    "Agents.chat_controller_agent",
)

_THREAD_LOCKS: Dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_PROTOCOL_SSE_EVENTS = {
    "run_started",
    "question_strategy",
    "plan_update",
    "recall_started",
    "tool_call",
    "tool_result",
    "sub_question_started",
    "sub_question_completed",
    "direct_answer_payload",
    "direct_answer_fallback",
    "final_answer_payload",
    "recall_completed",
    "assistant_message",
    "memory_capture_updated",
    "chat_result",
    "run_completed",
    "run_failed",
    "flush_started",
    "flush_stage",
    "flush_completed",
    "thread_state_updated",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat().replace("+00:00", "Z")


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_config_path(config_text: str) -> Path:
    raw_text = str(config_text or str(DEFAULT_CHAT_CONFIG_PATH)).strip() or str(DEFAULT_CHAT_CONFIG_PATH)
    path = Path(raw_text)
    if path.is_absolute():
        return path.resolve()
    return resolve_project_path(path).resolve()


def _thread_lock_key(thread_id: str) -> str:
    return str(thread_id or "").strip()


def _get_thread_lock(thread_id: str) -> threading.Lock:
    key = _thread_lock_key(thread_id)
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


def _normalize_memory_mode(raw_mode: Any, *, fallback: str = "manual") -> str:
    mode = str(raw_mode or fallback).strip().lower()
    return mode if mode in {"manual", "off"} else fallback


def _short_text(value: Any, limit: int = 72) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _stringify_scalar(value: Any, limit: int = 40) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return _short_text(value, limit=limit)


def _summarize_mapping_fields(
    payload: Dict[str, Any],
    *,
    skip_keys: set[str] | None = None,
    max_items: int = 3,
) -> str:
    if not isinstance(payload, dict):
        return ""
    parts: List[str] = []
    for key, value in payload.items():
        if skip_keys and key in skip_keys:
            continue
        if len(parts) >= max_items:
            break
        if isinstance(value, dict):
            parts.append(f"{key}_keys={','.join(list(value.keys())[:3])}")
        elif isinstance(value, list):
            parts.append(f"{key}_count={len(value)}")
        else:
            parts.append(f"{key}={_stringify_scalar(value)}")
    return " ".join(parts)


def _summarize_result_value(value: Any) -> str:
    if value is None:
        return "result=null"
    if isinstance(value, str):
        return f"result={_short_text(value)}"
    if isinstance(value, list):
        return f"result_count={len(value)}"
    if isinstance(value, dict):
        if value.get("answer"):
            return f"answer={_short_text(value.get('answer'))}"
        for list_key in ("results", "items", "matches", "records", "events", "chunks"):
            if isinstance(value.get(list_key), list):
                return f"{list_key}_count={len(value.get(list_key, []))}"
        return f"result_keys={','.join(list(value.keys())[:4])}"
    return f"result={_short_text(value)}"


def _should_protocol_log_path(path: str) -> bool:
    clean = str(path or "").strip()
    if not clean:
        return False
    if clean in {"/", "/healthz", "/docs", "/openapi.json"}:
        return False
    return clean.startswith("/v1/chat/")


def _summarize_event_payload(event_type: str, payload: Dict[str, Any]) -> str:
    if event_type == "run_started":
        return f"thread={payload.get('thread_id')} message={_short_text(payload.get('message'))}"
    if event_type == "question_strategy":
        return (
            f"decompose_first={payload.get('decompose_first')} "
            f"reason={_short_text(payload.get('reason'))} "
            f"question={_short_text(payload.get('question'))}"
        )
    if event_type == "plan_update":
        suggested_tools = payload.get("suggested_tool_order")
        tool_text = ",".join(str(item) for item in suggested_tools[:3]) if isinstance(suggested_tools, list) else ""
        return (
            f"type={payload.get('question_type')} "
            f"sub_questions={len(payload.get('sub_questions', [])) if isinstance(payload.get('sub_questions'), list) else 0} "
            f"tools={tool_text or '-'}"
        )
    if event_type == "recall_started":
        return (
            f"mode={payload.get('mode')} "
            f"question={_short_text(payload.get('question'))}"
        )
    if event_type == "tool_call":
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        params_text = _summarize_mapping_fields(params, max_items=3)
        suffix = f" {params_text}" if params_text else ""
        return (
            f"call_id={payload.get('call_id')} tool={payload.get('tool_name')} "
            f"status={payload.get('status')}{suffix}"
        )
    if event_type == "tool_result":
        result_text = _summarize_result_value(payload.get("result"))
        if payload.get("error"):
            result_text = f"error={_short_text(payload.get('error'))}"
        return (
            f"call_id={payload.get('call_id')} tool={payload.get('tool_name')} "
            f"status={payload.get('status')} {result_text}"
        )
    if event_type == "sub_question_started":
        return (
            f"index={payload.get('index')} status={payload.get('status')} "
            f"question={_short_text(payload.get('question'))}"
        )
    if event_type == "sub_question_completed":
        if payload.get("error"):
            return (
                f"index={payload.get('index')} status={payload.get('status')} "
                f"error={_short_text(payload.get('error'))}"
            )
        return (
            f"index={payload.get('index')} status={payload.get('status')} "
            f"answer={_short_text(payload.get('answer'))}"
        )
    if event_type == "direct_answer_payload":
        return (
            f"answer={_short_text(payload.get('answer'))} "
            f"gold_answer={_short_text(payload.get('gold_answer'))} "
            f"evidence_present={bool(str(payload.get('evidence', '') or '').strip())}"
        )
    if event_type == "direct_answer_fallback":
        return (
            f"reason={_short_text(payload.get('reason'))} "
            f"question={_short_text(payload.get('question'))}"
        )
    if event_type == "final_answer_payload":
        return (
            f"answer={_short_text(payload.get('answer'))} "
            f"gold_answer={_short_text(payload.get('gold_answer'))} "
            f"tool_calls={payload.get('tool_call_count')}"
        )
    if event_type == "recall_completed":
        return (
            f"mode={payload.get('mode')} "
            f"answer={_short_text(payload.get('answer'))}"
        )
    if event_type == "assistant_message":
        return f"thread={payload.get('thread_id')} answer={_short_text(payload.get('answer'))}"
    if event_type == "memory_capture_updated":
        return (
            f"mode={payload.get('mode')} status={payload.get('status')} "
            f"pending_rounds={payload.get('pending_rounds')}"
        )
    if event_type == "chat_result":
        agent_result = payload.get("agent_result") if isinstance(payload.get("agent_result"), dict) else {}
        return (
            f"tool_calls={agent_result.get('tool_call_count')} "
            f"plan_summary={_short_text(agent_result.get('plan_summary'))}"
        )
    if event_type == "run_completed":
        return f"thread={payload.get('thread_id')}"
    if event_type == "run_failed":
        return f"thread={payload.get('thread_id')} error={_short_text(payload.get('error'))}"
    if event_type == "flush_started":
        return (
            f"thread={payload.get('thread_id')} reason={payload.get('flush_reason')} "
            f"pending_rounds={payload.get('pending_rounds')}"
        )
    if event_type == "flush_stage":
        return (
            f"thread={payload.get('thread_id')} stage={payload.get('stage')} "
            f"status={payload.get('status')}"
        )
    if event_type == "flush_completed":
        return (
            f"thread={payload.get('thread_id')} status={payload.get('status')} "
            f"success={payload.get('success')}"
        )
    if event_type == "thread_state_updated":
        state = payload.get("thread_state") if isinstance(payload.get("thread_state"), dict) else payload
        return (
            f"thread={state.get('thread_id')} mode={state.get('mode')} "
            f"pending_rounds={state.get('pending_rounds')}"
        )
    return ""


def _log_protocol_event(channel: str, channel_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    if event_type not in _PROTOCOL_SSE_EVENTS:
        return
    summary = _summarize_event_payload(event_type, payload)
    suffix = f" {summary}" if summary else ""
    protocol_logger.info("SSE -> %s[%s] %s%s", channel, channel_id, event_type, suffix)


def _summarize_memory_write_result(result: Any) -> Dict[str, Any]:
    payload = result if isinstance(result, dict) else {}
    import_result = payload.get("import_result") if isinstance(payload.get("import_result"), dict) else {}
    scene_build_result = (
        import_result.get("scene_build_result")
        if isinstance(import_result.get("scene_build_result"), dict)
        else {}
    )
    fact_import_stats = (
        scene_build_result.get("fact_import_stats")
        if isinstance(scene_build_result.get("fact_import_stats"), dict)
        else {}
    )
    align_result = (
        fact_import_stats.get("entity_profile_align_result")
        if isinstance(fact_import_stats.get("entity_profile_align_result"), dict)
        else {}
    )
    return {
        "success": bool(payload.get("success", False)),
        "dialogue_id": str(payload.get("dialogue_id", "") or "") or None,
        "episode_id": str(payload.get("episode_id", "") or "") or None,
        "round_count": int(payload.get("round_count", 0) or 0),
        "turn_count": int(payload.get("turn_count", 0) or 0),
        "import_success": bool(import_result.get("success")) if import_result else None,
        "scene_build_success": bool(scene_build_result.get("success")) if scene_build_result else None,
        "entity_profile_align_success": bool(align_result.get("success")) if align_result else None,
    }


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
        self._warm_agent()
        self._idle_worker = threading.Thread(target=self._idle_flush_loop, name="chat-idle-flush", daemon=True)
        self._idle_worker.start()

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
        try:
            _THREAD_EVENTS.append_event(thread_id, event_type, payload)
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


class TraceEventProjector:
    QUESTION_STRATEGY_PREFIX = "QUESTION STRATEGY: "
    PLAN_UPDATE_PREFIX = "PLAN UPDATE: "
    SUBQ_START_PREFIX = "SUBQ START: "
    SUBQ_DONE_PREFIX = "SUBQ DONE: "
    TOOL_CALL_PREFIX = "TOOL CALL DETAIL: "
    TOOL_RESULT_PREFIX = "TOOL RESULT DETAIL: "
    DIRECT_ANSWER_PREFIX = "DIRECT ANSWER PAYLOAD: "
    DIRECT_FALLBACK_PREFIX = "DIRECT ANSWER FALLBACK: "
    FINAL_PAYLOAD_PREFIX = "FINAL ANSWER PAYLOAD: "
    RECALL_START_PREFIX = "RECALL START: "
    RECALL_DONE_PREFIX = "RECALL DONE: "

    @staticmethod
    def _load_json_payload(raw_message: str, prefix: str) -> Any:
        payload_text = str(raw_message[len(prefix):] or "").strip()
        if not payload_text:
            return {}
        try:
            return json.loads(payload_text)
        except Exception:
            return payload_text

    @classmethod
    def project(cls, event: TraceEvent) -> Optional[Dict[str, Any]]:
        raw = str(event.raw_message or "")
        if raw.startswith(cls.RECALL_START_PREFIX):
            return {
                "type": "recall_started",
                "payload": cls._load_json_payload(raw, cls.RECALL_START_PREFIX),
            }
        if raw.startswith(cls.RECALL_DONE_PREFIX):
            return {
                "type": "recall_completed",
                "payload": cls._load_json_payload(raw, cls.RECALL_DONE_PREFIX),
            }
        if raw.startswith(cls.QUESTION_STRATEGY_PREFIX):
            return {
                "type": "question_strategy",
                "payload": cls._load_json_payload(raw, cls.QUESTION_STRATEGY_PREFIX),
            }
        if raw.startswith(cls.PLAN_UPDATE_PREFIX):
            return {
                "type": "plan_update",
                "payload": cls._load_json_payload(raw, cls.PLAN_UPDATE_PREFIX),
            }
        if raw.startswith(cls.SUBQ_START_PREFIX):
            return {
                "type": "sub_question_started",
                "payload": cls._load_json_payload(raw, cls.SUBQ_START_PREFIX),
            }
        if raw.startswith(cls.SUBQ_DONE_PREFIX):
            return {
                "type": "sub_question_completed",
                "payload": cls._load_json_payload(raw, cls.SUBQ_DONE_PREFIX),
            }
        if raw.startswith(cls.TOOL_CALL_PREFIX):
            return {
                "type": "tool_call",
                "payload": cls._load_json_payload(raw, cls.TOOL_CALL_PREFIX),
            }
        if raw.startswith(cls.TOOL_RESULT_PREFIX):
            return {
                "type": "tool_result",
                "payload": cls._load_json_payload(raw, cls.TOOL_RESULT_PREFIX),
            }
        if raw.startswith(cls.DIRECT_ANSWER_PREFIX):
            return {
                "type": "direct_answer_payload",
                "payload": cls._load_json_payload(raw, cls.DIRECT_ANSWER_PREFIX),
            }
        if raw.startswith(cls.DIRECT_FALLBACK_PREFIX):
            return {
                "type": "direct_answer_fallback",
                "payload": cls._load_json_payload(raw, cls.DIRECT_FALLBACK_PREFIX),
            }
        if raw.startswith(cls.FINAL_PAYLOAD_PREFIX):
            return {
                "type": "final_answer_payload",
                "payload": cls._load_json_payload(raw, cls.FINAL_PAYLOAD_PREFIX),
            }
        return None


class ChatRunRecord:
    def __init__(self, *, run_id: str, config_path: str, thread_id: str, message: str) -> None:
        self.run_id = run_id
        self.config_path = config_path
        self.thread_id = thread_id
        self.message = message
        self.created_at = _now_iso()
        self.finished_at: Optional[str] = None
        self.status = "queued"
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self._seq = 0
        self._events: List[Dict[str, Any]] = []
        self._done = False
        self._cond = threading.Condition()

    def append_event(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._cond:
            self._seq += 1
            event = {
                "run_id": self.run_id,
                "seq": self._seq,
                "timestamp": _now_iso(),
                "type": event_type,
                "payload": deepcopy(payload),
            }
            self._events.append(event)
            self._cond.notify_all()
            _log_protocol_event("run", self.run_id, event_type, payload)
            return deepcopy(event)

    def start(self) -> None:
        self.status = "running"
        self.append_event(
            "run_started",
            {
                "thread_id": self.thread_id,
                "message": self.message,
                "config_path": self.config_path,
            },
        )

    def complete(self, result: Dict[str, Any]) -> None:
        with self._cond:
            self.status = "completed"
            self.result = deepcopy(result)
            self.finished_at = _now_iso()
        self.append_event(
            "run_completed",
            {
                "thread_id": self.thread_id,
                "answer": str(result.get("answer", "") or "").strip(),
                "result": deepcopy(result),
            },
        )
        with self._cond:
            self._done = True
            self._cond.notify_all()

    def fail(self, error_text: str) -> None:
        with self._cond:
            self.status = "failed"
            self.error = error_text
            self.finished_at = _now_iso()
        self.append_event(
            "run_failed",
            {
                "thread_id": self.thread_id,
                "error": error_text,
            },
        )
        with self._cond:
            self._done = True
            self._cond.notify_all()

    def append_trace_event(self, trace_event: TraceEvent) -> None:
        projected = TraceEventProjector.project(trace_event)
        if projected is None:
            return
        payload = projected.get("payload")
        if not isinstance(payload, dict):
            payload = {"value": payload}
        self.append_event(projected["type"], payload)

    def snapshot(self) -> Dict[str, Any]:
        with self._cond:
            return {
                "run_id": self.run_id,
                "status": self.status,
                "config_path": self.config_path,
                "thread_id": self.thread_id,
                "message": self.message,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "event_count": len(self._events),
                "result": deepcopy(self.result),
                "error": self.error,
            }

    def wait_for_events(self, after_seq: int, timeout: float = 15.0) -> tuple[List[Dict[str, Any]], bool]:
        with self._cond:
            if self._seq <= after_seq and not self._done:
                self._cond.wait(timeout=timeout)
            events = [deepcopy(item) for item in self._events if int(item.get("seq", 0)) > after_seq]
            return events, self._done


class ChatRunRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: Dict[str, ChatRunRecord] = {}

    def create(self, *, config_path: str, thread_id: str, message: str) -> ChatRunRecord:
        run_id = f"run_{uuid.uuid4().hex}"
        record = ChatRunRecord(
            run_id=run_id,
            config_path=config_path,
            thread_id=thread_id,
            message=message,
        )
        with self._lock:
            self._runs[run_id] = record
        return record

    def get(self, run_id: str) -> Optional[ChatRunRecord]:
        with self._lock:
            return self._runs.get(run_id)


class ThreadEventRecord:
    def __init__(self, *, thread_id: str) -> None:
        self.thread_id = thread_id
        self.created_at = _now_iso()
        self._seq = 0
        self._events: List[Dict[str, Any]] = []
        self._cond = threading.Condition()

    def append_event(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._cond:
            self._seq += 1
            event = {
                "thread_id": self.thread_id,
                "seq": self._seq,
                "timestamp": _now_iso(),
                "type": event_type,
                "payload": deepcopy(payload),
            }
            self._events.append(event)
            self._cond.notify_all()
            _log_protocol_event("thread", self.thread_id, event_type, payload)
            return deepcopy(event)

    def current_seq(self) -> int:
        with self._cond:
            return self._seq

    def wait_for_events(self, after_seq: int, timeout: float = 15.0) -> List[Dict[str, Any]]:
        with self._cond:
            if self._seq <= after_seq:
                self._cond.wait(timeout=timeout)
            return [deepcopy(item) for item in self._events if int(item.get("seq", 0)) > after_seq]


class ThreadEventRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, ThreadEventRecord] = {}

    def _normalize_key(self, thread_id: str) -> str:
        return str(thread_id or "").strip()

    def get_or_create(self, thread_id: str) -> ThreadEventRecord:
        key = self._normalize_key(thread_id)
        with self._lock:
            record = self._records.get(key)
            if record is None:
                record = ThreadEventRecord(thread_id=key)
                self._records[key] = record
            return record

    def append_event(self, thread_id: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.get_or_create(thread_id).append_event(event_type, payload)


_RUNS = ChatRunRegistry()
_THREAD_EVENTS = ThreadEventRegistry()


def _attach_trace_handler(handler: logging.Handler) -> list[tuple[logging.Logger, int, bool]]:
    root_allows_info = logging.getLogger().isEnabledFor(logging.INFO)
    attached: list[tuple[logging.Logger, int, bool]] = []
    for logger_name in TRACE_LOGGER_NAMES:
        trace_logger = logging.getLogger(logger_name)
        attached.append((trace_logger, trace_logger.level, trace_logger.propagate))
        trace_logger.addHandler(handler)
        if trace_logger.level == logging.NOTSET or trace_logger.level > logging.INFO:
            trace_logger.setLevel(logging.INFO)
        if not root_allows_info:
            # In concise mode we still need INFO traces for SSE, but they should not leak to stdout/stderr.
            trace_logger.propagate = False
    return attached


def _detach_trace_handler(
    handler: logging.Handler,
    attached: list[tuple[logging.Logger, int, bool]],
) -> None:
    for trace_logger, previous_level, previous_propagate in attached:
        trace_logger.removeHandler(handler)
        trace_logger.setLevel(previous_level)
        trace_logger.propagate = previous_propagate


def _run_chat_worker(record: ChatRunRecord, service_runtime: ChatServiceRuntime) -> None:
    # Do not filter trace logs by thread id here. The underlying agent/tool stack may hop
    # across worker threads, and chat execution is already serialized by _operation_lock.
    handler = FunctionTraceHandler(callback=record.append_trace_event, include_non_api=True)
    trace_loggers = _attach_trace_handler(handler)

    thread_lock = _get_thread_lock(record.thread_id)
    try:
        record.start()
        with thread_lock:
            result = service_runtime.run_chat(
                message=record.message,
                thread_id=record.thread_id or service_runtime.default_thread_id,
            )

        answer_text = str(result.get("answer", "") or "").strip()
        record.append_event(
            "assistant_message",
            {
                "answer": answer_text,
                "thread_id": str(result.get("thread_id", record.thread_id) or record.thread_id),
            },
        )

        memory_capture = result.get("memory_capture")
        if isinstance(memory_capture, dict):
            record.append_event("memory_capture_updated", memory_capture)

        thread_state = result.get("thread_state")
        if isinstance(thread_state, dict):
            record.append_event("thread_state_updated", {"thread_state": thread_state})

        agent_result = result.get("agent_result")
        if isinstance(agent_result, dict):
            record.append_event("chat_result", {"agent_result": agent_result})

        record.complete(result)
    except Exception:
        with service_runtime._stats_lock:
            service_runtime._runs_failed += 1
            service_runtime._last_run_finished_at = _now_iso()
        record.fail(traceback.format_exc())
    finally:
        _detach_trace_handler(handler, trace_loggers)


def _start_chat_run(*, service_runtime: ChatServiceRuntime, thread_id: str, message: str) -> ChatRunRecord:
    record = _RUNS.create(
        config_path=str(service_runtime.config_path),
        thread_id=thread_id,
        message=message,
    )
    worker = threading.Thread(target=_run_chat_worker, args=(record, service_runtime), daemon=True)
    worker.start()
    return record


def _json_response_payload(record: ChatRunRecord) -> Dict[str, Any]:
    return {
        "run_id": record.run_id,
        "status": record.status,
        "thread_id": record.thread_id,
        "events_url": f"/v1/chat/runs/{record.run_id}/events",
        "result_url": f"/v1/chat/runs/{record.run_id}",
    }


class ChatRunCreateRequest(BaseModel):
    thread_id: Optional[str] = None
    message: Optional[str] = None
    config: Optional[str] = None


class ThreadMemoryModeRequest(BaseModel):
    mode: Optional[str] = None
    discard_pending: bool = False


class ThreadMemoryFlushRequest(BaseModel):
    reason: Optional[str] = None


class PrivateNetworkAccessMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            bytes(key).decode("latin1").lower(): bytes(value).decode("latin1")
            for key, value in scope.get("headers", [])
        }
        requested_private_network = (
            str(headers.get("access-control-request-private-network", "") or "").strip().lower() == "true"
        )

        async def send_wrapper(message: Dict[str, Any]) -> None:
            if message.get("type") == "http.response.start" and requested_private_network:
                response_headers = list(message.get("headers", []))
                response_headers.append((b"access-control-allow-private-network", b"true"))
                vary_value = b"Origin, Access-Control-Request-Headers, Access-Control-Request-Private-Network"
                vary_updated = False
                for idx, (key, value) in enumerate(response_headers):
                    if bytes(key).lower() == b"vary":
                        merged = bytes(value)
                        if vary_value not in merged:
                            merged = merged + b", " + vary_value
                        response_headers[idx] = (key, merged)
                        vary_updated = True
                        break
                if not vary_updated:
                    response_headers.append((b"vary", vary_value))
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _encode_sse(event: Dict[str, Any]) -> bytes:
    event_type = str(event.get("type", "message") or "message")
    event_id = str(event.get("seq", "") or "")
    payload = json.dumps(event, ensure_ascii=False)
    return (
        f"id: {event_id}\n"
        f"event: {event_type}\n"
        f"data: {payload}\n\n"
    ).encode("utf-8")


def create_app(*, service_runtime: ChatServiceRuntime) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            service_runtime.shutdown()

    app = FastAPI(title="M-Agent Chat API", version="2.0", lifespan=lifespan)
    app.state.service_runtime = service_runtime

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
        max_age=600,
    )
    app.add_middleware(PrivateNetworkAccessMiddleware)

    @app.middleware("http")
    async def protocol_logging_middleware(request: Request, call_next: Any):
        path = str(request.url.path or "")
        query = f"?{request.url.query}" if request.url.query else ""
        should_log = _should_protocol_log_path(path)
        if should_log:
            protocol_logger.info("HTTP <- %s %s%s", request.method, path, query)
        try:
            response = await call_next(request)
        except Exception:
            if should_log:
                protocol_logger.info("HTTP -> 500 %s %s%s", request.method, path, query)
            raise
        if should_log:
            protocol_logger.info("HTTP -> %s %s %s%s", response.status_code, request.method, path, query)
        return response

    @app.get("/")
    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        return {
            "ok": True,
            "service": "m-agent-chat-api",
            "root": str(PROJECT_ROOT),
            "runtime": service_runtime.health_payload(),
            "endpoints": {
                "create_run": "/v1/chat/runs",
                "get_run": "/v1/chat/runs/{run_id}",
                "stream_events": "/v1/chat/runs/{run_id}/events",
                "thread_events": "/v1/chat/threads/{thread_id}/events",
                "thread_state": "/v1/chat/threads/{thread_id}/memory/state",
                "thread_mode": "/v1/chat/threads/{thread_id}/memory/mode",
                "thread_flush": "/v1/chat/threads/{thread_id}/memory/flush",
                "openapi": "/openapi.json",
                "docs": "/docs",
            },
        }

    @app.post("/v1/chat/runs")
    def create_run(body: ChatRunCreateRequest) -> JSONResponse:
        requested_config = str(body.config or "").strip()
        if requested_config:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "service config is fixed at startup; restart the API with --config to change it",
                    "config_path": str(service_runtime.config_path),
                },
            )

        thread_id = str(body.thread_id or service_runtime.default_thread_id).strip() or service_runtime.default_thread_id
        message = str(body.message or "").strip()
        if not message:
            return JSONResponse(status_code=400, content={"error": "message is empty"})

        record = _start_chat_run(
            service_runtime=service_runtime,
            thread_id=thread_id,
            message=message,
        )
        return JSONResponse(status_code=201, content=_json_response_payload(record))

    @app.get("/v1/chat/runs/{run_id}")
    def get_run(run_id: str) -> JSONResponse:
        record = _RUNS.get(run_id)
        if record is None:
            return JSONResponse(status_code=404, content={"error": f"run not found: {run_id}"})
        return JSONResponse(content=record.snapshot())

    @app.get("/v1/chat/runs/{run_id}/events", response_class=StreamingResponse, response_model=None)
    async def stream_events(run_id: str, request: Request, after_seq: int = 0):
        record = _RUNS.get(run_id)
        if record is None:
            return JSONResponse(status_code=404, content={"error": f"run not found: {run_id}"})

        async def event_stream():
            current_seq = max(0, int(after_seq))
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    events, done = await asyncio.to_thread(record.wait_for_events, current_seq, 10.0)
                    if events:
                        for event in events:
                            if await request.is_disconnected():
                                return
                            current_seq = max(current_seq, int(event.get("seq", 0) or 0))
                            yield _encode_sse(event)
                    else:
                        yield b": keep-alive\n\n"
                    if done and not events:
                        break
                    if done and events:
                        tail_events, _ = await asyncio.to_thread(record.wait_for_events, current_seq, 0.0)
                        if not tail_events:
                            break
            except asyncio.CancelledError:
                return

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/v1/chat/threads/{thread_id}/events", response_class=StreamingResponse, response_model=None)
    async def stream_thread_events(thread_id: str, request: Request, after_seq: int = -1):
        record = _THREAD_EVENTS.get_or_create(thread_id)

        async def event_stream():
            current_seq = record.current_seq() if int(after_seq) < 0 else max(0, int(after_seq))
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    events = await asyncio.to_thread(record.wait_for_events, current_seq, 10.0)
                    if events:
                        for event in events:
                            if await request.is_disconnected():
                                return
                            current_seq = max(current_seq, int(event.get("seq", 0) or 0))
                            yield _encode_sse(event)
                    else:
                        yield b": keep-alive\n\n"
            except asyncio.CancelledError:
                return

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/v1/chat/threads/{thread_id}/memory/state")
    def get_thread_state(thread_id: str) -> JSONResponse:
        return JSONResponse(content=service_runtime.get_thread_state(thread_id))

    @app.post("/v1/chat/threads/{thread_id}/memory/mode")
    def set_thread_mode(thread_id: str, body: ThreadMemoryModeRequest) -> JSONResponse:
        mode = _normalize_memory_mode(body.mode, fallback="manual")
        thread_lock = _get_thread_lock(thread_id)
        with thread_lock:
            result = service_runtime.set_thread_mode(
                thread_id,
                mode=mode,
                discard_pending=bool(body.discard_pending),
            )
        return JSONResponse(content=result)

    @app.post("/v1/chat/threads/{thread_id}/memory/flush")
    def flush_thread(thread_id: str, body: ThreadMemoryFlushRequest) -> JSONResponse:
        reason = str(body.reason or "manual_api").strip() or "manual_api"
        thread_lock = _get_thread_lock(thread_id)
        with thread_lock:
            result = service_runtime.flush_thread(thread_id, reason=reason)
        return JSONResponse(content=result)

    return app


def create_handler(*, service_runtime: ChatServiceRuntime) -> FastAPI:
    """Backward-compatible alias for the old stdlib server entrypoint."""
    return create_app(service_runtime=service_runtime)


def _configure_logging(debug: bool = False) -> None:
    root_level = logging.INFO if debug else logging.WARNING
    logging.basicConfig(
        level=root_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        force=True,
    )

    protocol_handler = logging.StreamHandler()
    protocol_handler.setLevel(logging.INFO)
    protocol_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
    protocol_logger.handlers = [protocol_handler]
    protocol_logger.setLevel(logging.INFO)
    protocol_logger.propagate = False

    if not debug:
        for noisy_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logging.getLogger(noisy_name).setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the M-Agent chat API server with SSE events.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8777, help="Bind port. Default: 8777")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CHAT_CONFIG_PATH),
        help=f"Startup-fixed chat config path. Default: {DEFAULT_CHAT_CONFIG_PATH}",
    )
    parser.add_argument(
        "--idle-flush-seconds",
        type=int,
        default=1800,
        help="Idle timeout in seconds before pending manual memory is auto-flushed. Default: 1800",
    )
    parser.add_argument(
        "--history-max-rounds",
        type=int,
        default=12,
        help="Max in-memory rounds retained per thread for chat history. Default: 12",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose backend/module logs. Default mode keeps only concise HTTP/SSE protocol logs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _configure_logging(debug=bool(args.debug))
    config_path = _resolve_config_path(str(args.config or "").strip() or str(DEFAULT_CHAT_CONFIG_PATH))
    service_runtime = ChatServiceRuntime(
        config_path=config_path,
        idle_flush_seconds=int(args.idle_flush_seconds),
        history_max_rounds=int(args.history_max_rounds),
    )
    app = create_app(service_runtime=service_runtime)
    url = f"http://{args.host}:{args.port}"
    logger.info("M-Agent chat API listening on %s", url)
    logger.info("Startup config locked to %s", config_path)
    logger.info(
        "Thread memory runtime: idle_flush_seconds=%s history_max_rounds=%s",
        service_runtime.idle_flush_seconds,
        service_runtime.history_max_rounds,
    )
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info" if args.debug else "warning",
            access_log=bool(args.debug),
        )
    except KeyboardInterrupt:
        logger.info("Shutting down chat API...")


if __name__ == "__main__":
    main()
