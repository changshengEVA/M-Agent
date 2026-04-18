from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
import traceback
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from m_agent.config_paths import resolve_config_path
from m_agent.utils.logging_trace import FunctionTraceHandler, TraceEvent

from .core import DEFAULT_CONFIG_PATH, create_memory_agent

logger = logging.getLogger(__name__)

_TRACE_LOGGER_NAMES = (
    "m_agent.agents.memory_agent",
    "Agents.memory_agent",
)

_RUN_OPERATION_LOCK = threading.Lock()


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _encode_sse(event: Dict[str, Any]) -> bytes:
    event_type = str(event.get("type", "message") or "message")
    event_id = str(event.get("seq", "") or "")
    payload = json.dumps(event, ensure_ascii=False)
    return (
        f"id: {event_id}\n"
        f"event: {event_type}\n"
        f"data: {payload}\n\n"
    ).encode("utf-8")


def _normalize_recall_mode(raw_mode: Any) -> str:
    mode = str(raw_mode or "").strip().lower()
    if mode in {"shallow", "deep"}:
        return mode
    return "deep"


def _safe_int(raw_value: Any, default: int = 0) -> int:
    try:
        return int(str(raw_value).strip())
    except Exception:
        return int(default)


class MemoryRunCreateRequest(BaseModel):
    question: Optional[str] = None
    recall_mode: Optional[str] = "deep"
    config_path: Optional[str] = None
    thread_id: Optional[str] = None


class MemoryTraceProjector:
    TOOL_CALL_PREFIX = "TOOL CALL DETAIL: "
    TOOL_RESULT_PREFIX = "TOOL RESULT DETAIL: "
    WORKSPACE_STATE_PREFIX = "WORKSPACE STATE: "
    FINAL_PAYLOAD_PREFIX = "FINAL ANSWER PAYLOAD: "

    @staticmethod
    def _load_json_payload(raw_message: str, prefix: str) -> Any:
        payload_text = str(raw_message[len(prefix) :] or "").strip()
        if not payload_text:
            return {}
        try:
            return json.loads(payload_text)
        except Exception:
            return payload_text

    @classmethod
    def project(cls, trace_event: TraceEvent) -> Optional[Dict[str, Any]]:
        raw = str(trace_event.raw_message or "")
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
        if raw.startswith(cls.WORKSPACE_STATE_PREFIX):
            return {
                "type": "workspace_state",
                "payload": cls._load_json_payload(raw, cls.WORKSPACE_STATE_PREFIX),
            }
        if raw.startswith(cls.FINAL_PAYLOAD_PREFIX):
            return {
                "type": "final_answer_payload",
                "payload": cls._load_json_payload(raw, cls.FINAL_PAYLOAD_PREFIX),
            }
        return None


class MemoryRunRecord:
    def __init__(
        self,
        *,
        run_id: str,
        question: str,
        recall_mode: str,
        config_path: str,
        thread_id: str,
    ) -> None:
        self.run_id = run_id
        self.question = question
        self.recall_mode = recall_mode
        self.config_path = config_path
        self.thread_id = thread_id
        self.status = "queued"
        self.created_at = _now_iso()
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.latest_workspace: Optional[Dict[str, Any]] = None
        self._workspace_history: List[Dict[str, Any]] = []
        self._tool_calls_by_id: Dict[str, Dict[str, Any]] = {}
        self._seq = 0
        self._events: List[Dict[str, Any]] = []
        self._done = False
        self._cond = threading.Condition()

    def _record_workspace(self, payload: Dict[str, Any]) -> None:
        snapshot = deepcopy(payload)
        self.latest_workspace = snapshot
        self._workspace_history.append(snapshot)
        max_history = 200
        if len(self._workspace_history) > max_history:
            self._workspace_history = self._workspace_history[-max_history:]

    def _record_tool_call(self, payload: Dict[str, Any]) -> None:
        call_id_text = str(payload.get("call_id", "") or "").strip()
        call_key = call_id_text or f"unknown-{len(self._tool_calls_by_id) + 1}"
        existing = self._tool_calls_by_id.get(call_key, {})
        merged = dict(existing)
        merged.update(deepcopy(payload))
        self._tool_calls_by_id[call_key] = merged

    def append_event(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        safe_payload = deepcopy(payload if isinstance(payload, dict) else {"value": payload})
        with self._cond:
            self._seq += 1
            event = {
                "run_id": self.run_id,
                "seq": self._seq,
                "timestamp": _now_iso(),
                "type": str(event_type),
                "payload": safe_payload,
            }
            self._events.append(event)

            if event_type in {"tool_call", "tool_result"}:
                self._record_tool_call(safe_payload)
            elif event_type == "workspace_state":
                self._record_workspace(safe_payload)

            self._cond.notify_all()
            return deepcopy(event)

    def append_trace_event(self, trace_event: TraceEvent) -> None:
        projected = MemoryTraceProjector.project(trace_event)
        if projected is None:
            return
        payload = projected.get("payload")
        if not isinstance(payload, dict):
            payload = {"value": payload}
        self.append_event(str(projected.get("type", "trace")), payload)

    def start(self) -> None:
        self.status = "running"
        self.started_at = _now_iso()
        self.append_event(
            "run_started",
            {
                "question": self.question,
                "recall_mode": self.recall_mode,
                "config_path": self.config_path,
                "thread_id": self.thread_id,
            },
        )

    def complete(self, result: Dict[str, Any]) -> None:
        safe_result = deepcopy(result if isinstance(result, dict) else {"answer": str(result)})
        self.append_event(
            "run_completed",
            {
                "answer": str(safe_result.get("answer", "") or "").strip(),
                "result": safe_result,
            },
        )
        with self._cond:
            self.status = "completed"
            self.result = safe_result
            self.finished_at = _now_iso()
            self._done = True
            self._cond.notify_all()

    def fail(self, error_text: str) -> None:
        error_msg = str(error_text)
        self.append_event("run_failed", {"error": error_msg})
        with self._cond:
            self.status = "failed"
            self.error = error_msg
            self.finished_at = _now_iso()
            self._done = True
            self._cond.notify_all()

    def wait_for_events(self, after_seq: int, timeout: float = 15.0) -> Tuple[List[Dict[str, Any]], bool]:
        with self._cond:
            if self._seq <= after_seq and not self._done:
                self._cond.wait(timeout=timeout)
            events = [deepcopy(item) for item in self._events if int(item.get("seq", 0)) > after_seq]
            return events, self._done

    def _sorted_tool_calls(self) -> List[Dict[str, Any]]:
        calls = [deepcopy(item) for item in self._tool_calls_by_id.values()]

        def _sort_key(item: Dict[str, Any]) -> Tuple[int, int, str]:
            raw_call_id = item.get("call_id")
            call_id_text = str(raw_call_id or "").strip()
            try:
                return (0, int(call_id_text), "")
            except Exception:
                return (1, 0, call_id_text)

        calls.sort(key=_sort_key)
        return calls

    def snapshot(self) -> Dict[str, Any]:
        with self._cond:
            return {
                "run_id": self.run_id,
                "status": self.status,
                "question": self.question,
                "recall_mode": self.recall_mode,
                "config_path": self.config_path,
                "thread_id": self.thread_id,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "event_count": len(self._events),
                "tool_call_count": len(self._tool_calls_by_id),
                "workspace_event_count": len(self._workspace_history),
                "latest_workspace": deepcopy(self.latest_workspace),
                "result": deepcopy(self.result),
                "error": self.error,
            }

    def tool_calls_snapshot(self) -> Dict[str, Any]:
        with self._cond:
            calls = self._sorted_tool_calls()
            return {
                "run_id": self.run_id,
                "status": self.status,
                "tool_call_count": len(calls),
                "calls": calls,
            }

    def workspace_snapshot(self, *, limit: int = 20) -> Dict[str, Any]:
        safe_limit = max(1, int(limit))
        with self._cond:
            history = [deepcopy(item) for item in self._workspace_history[-safe_limit:]]
            return {
                "run_id": self.run_id,
                "status": self.status,
                "workspace_event_count": len(self._workspace_history),
                "latest_workspace": deepcopy(self.latest_workspace),
                "history": history,
            }


class MemoryRunRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, MemoryRunRecord] = {}
        self._order: List[str] = []

    def create(
        self,
        *,
        question: str,
        recall_mode: str,
        config_path: str,
        thread_id: str,
    ) -> MemoryRunRecord:
        run_id = f"memory_run_{uuid.uuid4().hex}"
        record = MemoryRunRecord(
            run_id=run_id,
            question=question,
            recall_mode=recall_mode,
            config_path=config_path,
            thread_id=thread_id,
        )
        with self._lock:
            self._records[run_id] = record
            self._order.append(run_id)
            max_records = 200
            if len(self._order) > max_records:
                stale = self._order[:-max_records]
                self._order = self._order[-max_records:]
                for stale_run_id in stale:
                    self._records.pop(stale_run_id, None)
        return record

    def get(self, run_id: str) -> Optional[MemoryRunRecord]:
        with self._lock:
            return self._records.get(str(run_id or "").strip())

    def list_recent(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        safe_limit = max(1, int(limit))
        with self._lock:
            run_ids = self._order[-safe_limit:]
            records = [self._records.get(run_id) for run_id in run_ids]
        snapshots = [item.snapshot() for item in records if item is not None]
        snapshots.reverse()
        return snapshots


_RUNS = MemoryRunRegistry()


def _attach_trace_handler(handler: logging.Handler) -> List[Tuple[logging.Logger, int, bool]]:
    attached: List[Tuple[logging.Logger, int, bool]] = []
    for logger_name in _TRACE_LOGGER_NAMES:
        trace_logger = logging.getLogger(logger_name)
        attached.append((trace_logger, trace_logger.level, trace_logger.propagate))
        trace_logger.addHandler(handler)
        if trace_logger.level == logging.NOTSET or trace_logger.level > logging.INFO:
            trace_logger.setLevel(logging.INFO)
    return attached


def _detach_trace_handler(
    handler: logging.Handler,
    attached: List[Tuple[logging.Logger, int, bool]],
) -> None:
    for trace_logger, prev_level, prev_propagate in attached:
        trace_logger.removeHandler(handler)
        trace_logger.setLevel(prev_level)
        trace_logger.propagate = prev_propagate


def _run_memory_worker(record: MemoryRunRecord) -> None:
    with _RUN_OPERATION_LOCK:
        handler = FunctionTraceHandler(callback=record.append_trace_event, include_non_api=True)
        attached = _attach_trace_handler(handler)
        try:
            record.start()
            agent = create_memory_agent(config_path=record.config_path)
            if record.recall_mode == "shallow":
                result = agent.shallow_recall(
                    question=record.question,
                    thread_id=record.thread_id,
                )
            else:
                result = agent.deep_recall(
                    question=record.question,
                    thread_id=record.thread_id,
                )
            if not isinstance(result, dict):
                result = {"answer": str(result)}
            record.complete(result)
        except Exception:
            record.fail(traceback.format_exc())
        finally:
            _detach_trace_handler(handler, attached)


def _start_memory_run(record: MemoryRunRecord) -> None:
    worker = threading.Thread(
        target=_run_memory_worker,
        args=(record,),
        daemon=True,
        name=f"memory-agent-run-{record.run_id}",
    )
    worker.start()


def create_app(*, default_config_path: str | Path = DEFAULT_CONFIG_PATH) -> FastAPI:
    default_path = resolve_config_path(default_config_path)

    app = FastAPI(
        title="M-Agent Memory-Agent Visual API",
        version="1.0",
    )
    app.state.default_config_path = default_path

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
        max_age=600,
    )

    def _error_response(*, status_code: int, message: str, extra: Optional[Dict[str, Any]] = None) -> JSONResponse:
        payload = {"error": message}
        if isinstance(extra, dict):
            payload.update(extra)
        return JSONResponse(status_code=int(status_code), content=payload)

    @app.get("/")
    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse(
            content={
                "status": "ok",
                "service": "memory-agent-visual-api",
                "default_config_path": str(default_path),
            }
        )

    @app.get("/v1/memory-agent/config")
    def get_default_config() -> JSONResponse:
        return JSONResponse(content={"default_config_path": str(default_path)})

    @app.get("/v1/memory-agent/runs")
    def list_runs(limit: int = 20) -> JSONResponse:
        snapshots = _RUNS.list_recent(limit=max(1, int(limit)))
        return JSONResponse(content={"count": len(snapshots), "runs": snapshots})

    @app.post("/v1/memory-agent/runs")
    def create_run(body: MemoryRunCreateRequest) -> JSONResponse:
        question = str(body.question or "").strip()
        if not question:
            return _error_response(status_code=400, message="question is required")

        recall_mode = _normalize_recall_mode(body.recall_mode)
        raw_config = str(body.config_path or "").strip() or str(default_path)
        resolved_config_path = resolve_config_path(raw_config)
        if not resolved_config_path.exists():
            return _error_response(
                status_code=400,
                message=f"config file not found: {resolved_config_path}",
            )

        thread_id = str(body.thread_id or "memory-agent-visual-thread").strip() or "memory-agent-visual-thread"
        record = _RUNS.create(
            question=question,
            recall_mode=recall_mode,
            config_path=str(resolved_config_path),
            thread_id=thread_id,
        )
        _start_memory_run(record)
        return JSONResponse(
            status_code=202,
            content={
                "run_id": record.run_id,
                "status": record.status,
                "question": question,
                "recall_mode": recall_mode,
                "config_path": str(resolved_config_path),
                "thread_id": thread_id,
                "created_at": record.created_at,
                "result_url": f"/v1/memory-agent/runs/{record.run_id}",
                "events_url": f"/v1/memory-agent/runs/{record.run_id}/events",
                "tool_calls_url": f"/v1/memory-agent/runs/{record.run_id}/tool-calls",
                "workspace_url": f"/v1/memory-agent/runs/{record.run_id}/workspace",
            },
        )

    @app.get("/v1/memory-agent/runs/{run_id}")
    def get_run(run_id: str) -> JSONResponse:
        record = _RUNS.get(run_id)
        if record is None:
            return _error_response(status_code=404, message=f"run not found: {run_id}")
        return JSONResponse(content=record.snapshot())

    @app.get("/v1/memory-agent/runs/{run_id}/tool-calls")
    def get_tool_calls(run_id: str) -> JSONResponse:
        record = _RUNS.get(run_id)
        if record is None:
            return _error_response(status_code=404, message=f"run not found: {run_id}")
        return JSONResponse(content=record.tool_calls_snapshot())

    @app.get("/v1/memory-agent/runs/{run_id}/workspace")
    def get_workspace(run_id: str, limit: int = 20) -> JSONResponse:
        record = _RUNS.get(run_id)
        if record is None:
            return _error_response(status_code=404, message=f"run not found: {run_id}")
        return JSONResponse(content=record.workspace_snapshot(limit=max(1, int(limit))))

    @app.get("/v1/memory-agent/runs/{run_id}/events", response_class=StreamingResponse, response_model=None)
    async def stream_events(run_id: str, request: Request, after_seq: int = 0):
        record = _RUNS.get(run_id)
        if record is None:
            return _error_response(status_code=404, message=f"run not found: {run_id}")

        header_last_event_id = request.headers.get("last-event-id")
        header_after_seq = _safe_int(header_last_event_id, 0)
        start_after_seq = max(0, int(after_seq), header_after_seq)

        snapshot = record.snapshot()
        run_status = str(snapshot.get("status", "") or "").strip().lower()
        if run_status in {"completed", "failed"}:
            event_count = max(0, _safe_int(snapshot.get("event_count"), 0))
            if start_after_seq >= event_count:
                # Tell EventSource this stream is finished and it should not reconnect.
                return Response(status_code=204)

        async def event_stream():
            current_seq = start_after_seq
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

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Memory-Agent visual API backend.")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind (0.0.0.0 = all interfaces). Open the API in a browser at http://127.0.0.1:PORT, not http://0.0.0.0:PORT.",
    )
    parser.add_argument("--port", type=int, default=8092, help="Port to bind.")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Default Memory-Agent config path.",
    )
    args = parser.parse_args()

    import uvicorn

    port = max(1, int(args.port))
    host = str(args.host)
    if host in ("0.0.0.0", "::", "::0"):
        browse_base = f"http://127.0.0.1:{port}"
    else:
        browse_base = f"http://{host}:{port}"
    print(
        "\nMemory-Agent Visual API\n"
        f"  Listening on http://{host}:{port}\n"
        f"  In your browser open: {browse_base}/healthz\n"
        f"  WebUI (API field): {browse_base}\n"
        "  Note: http://0.0.0.0:... is invalid in most browsers (use 127.0.0.1 or localhost).\n",
        flush=True,
    )

    app = create_app(default_config_path=args.config)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
