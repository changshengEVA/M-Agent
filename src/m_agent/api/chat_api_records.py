from __future__ import annotations

import json
import logging
import threading
import traceback
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional

from m_agent.utils.logging_trace import FunctionTraceHandler, TraceEvent

from .chat_api_protocol import _log_protocol_event
from .chat_api_runtime import ChatServiceRuntime
from .chat_api_shared import (
    _get_thread_lock,
    _now_iso,
    _with_public_result_thread_id,
)

TRACE_LOGGER_NAMES = (
    "m_agent.agents.memory_agent",
    "m_agent.agents.chat_controller_agent",
    "Agents.memory_agent",
    "Agents.chat_controller_agent",
)

logger = logging.getLogger(__name__)


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
    def __init__(
        self,
        *,
        run_id: str,
        config_path: str,
        thread_id: str,
        internal_thread_id: Optional[str] = None,
        message: str,
        user_id: Optional[str] = None,
    ) -> None:
        self.run_id = run_id
        self.config_path = config_path
        self.thread_id = thread_id
        self.internal_thread_id = str(internal_thread_id or thread_id or "").strip() or self.thread_id
        self.user_id = str(user_id or "").strip() or None
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
                "user_id": self.user_id,
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
                "user_id": self.user_id,
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

    def create(
        self,
        *,
        config_path: str,
        thread_id: str,
        internal_thread_id: Optional[str] = None,
        message: str,
        user_id: Optional[str] = None,
    ) -> ChatRunRecord:
        run_id = f"run_{uuid.uuid4().hex}"
        record = ChatRunRecord(
            run_id=run_id,
            config_path=config_path,
            thread_id=thread_id,
            internal_thread_id=internal_thread_id,
            message=message,
            user_id=user_id,
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


def wire_runtime_event_sink(service_runtime: ChatServiceRuntime) -> None:
    service_runtime.set_thread_event_sink(_THREAD_EVENTS.append_event)


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

    thread_lock = _get_thread_lock(record.internal_thread_id)
    try:
        record.start()
        with thread_lock:
            result = service_runtime.run_chat(
                message=record.message,
                thread_id=record.internal_thread_id or service_runtime.default_thread_id,
            )

        public_result = _with_public_result_thread_id(
            result,
            public_thread_id=record.thread_id,
        )
        answer_text = str(public_result.get("answer", "") or "").strip()
        record.append_event(
            "assistant_message",
            {
                "answer": answer_text,
                "thread_id": record.thread_id,
            },
        )

        memory_capture = public_result.get("memory_capture")
        if isinstance(memory_capture, dict):
            record.append_event("memory_capture_updated", memory_capture)

        thread_state = public_result.get("thread_state")
        if isinstance(thread_state, dict):
            record.append_event("thread_state_updated", {"thread_state": thread_state})

        agent_result = public_result.get("agent_result")
        if isinstance(agent_result, dict):
            record.append_event("chat_result", {"agent_result": agent_result})

        record.complete(public_result)
    except Exception:
        with service_runtime._stats_lock:
            service_runtime._runs_failed += 1
            service_runtime._last_run_finished_at = _now_iso()
        record.fail(traceback.format_exc())
    finally:
        _detach_trace_handler(handler, trace_loggers)


def _start_chat_run(
    *,
    service_runtime: ChatServiceRuntime,
    thread_id: str,
    internal_thread_id: Optional[str] = None,
    message: str,
    user_id: Optional[str] = None,
) -> ChatRunRecord:
    record = _RUNS.create(
        config_path=str(service_runtime.config_path),
        thread_id=thread_id,
        internal_thread_id=internal_thread_id,
        message=message,
        user_id=user_id,
    )
    worker = threading.Thread(target=_run_chat_worker, args=(record, service_runtime), daemon=True)
    worker.start()
    return record


def _json_response_payload(record: ChatRunRecord) -> Dict[str, Any]:
    return {
        "run_id": record.run_id,
        "status": record.status,
        "thread_id": record.thread_id,
        "user_id": record.user_id,
        "events_url": f"/v1/chat/runs/{record.run_id}/events",
        "result_url": f"/v1/chat/runs/{record.run_id}",
    }
