from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from m_agent.api.user_access import AuthenticatedUser, UserAccessError, UserAccessService
from m_agent.paths import PROJECT_ROOT

from .chat_api_models import (
    ChatRunCreateRequest,
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
    ThreadMemoryFlushRequest,
    ThreadMemoryModeRequest,
    UserConfigPatchRequest,
    UserLoginRequest,
    UserRegisterRequest,
)
from .chat_dialogue_store import get_dialogue_detail, list_dialogues
from .chat_api_protocol import _should_protocol_log_path, protocol_logger
from .chat_api_records import (
    ChatRunRecord,
    _json_response_payload,
    _RUNS,
    _start_chat_run,
    _THREAD_EVENTS,
    wire_runtime_event_sink,
)
from .chat_api_runtime import ChatServiceRuntime
from .chat_api_shared import (
    _extract_access_token,
    _get_thread_lock,
    _normalize_memory_mode,
    _scoped_thread_id,
    _with_public_thread_event,
    _with_public_thread_state,
)
from m_agent.schedule.store import ANONYMOUS_OWNER_ID
from m_agent.utils.time_utils import resolve_timezone


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


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_schedule_agent(active_runtime: ChatServiceRuntime) -> Any:
    agent = getattr(active_runtime, "agent", None)
    getter = getattr(agent, "get_schedule_agent", None)
    if callable(getter):
        return getter()
    chat_controller = getattr(agent, "chat_controller", None)
    getter = getattr(chat_controller, "get_schedule_agent", None)
    if callable(getter):
        return getter()
    raise RuntimeError("schedule agent is unavailable for this runtime")


def _normalize_schedule_due_at(raw_due_at: Any, timezone_name: Optional[str]) -> Dict[str, str]:
    safe_due_at = str(raw_due_at or "").strip()
    if not safe_due_at:
        raise ValueError("due_at is required")
    try:
        parsed = datetime.fromisoformat(safe_due_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("due_at must be an ISO datetime string") from exc

    tz, resolved_timezone_name, _ = resolve_timezone(timezone_name)
    if parsed.tzinfo is None:
        local_dt = parsed.replace(tzinfo=tz)
    else:
        local_dt = parsed.astimezone(tz)
    return {
        "timezone_name": resolved_timezone_name,
        "due_at_utc": _iso_utc(local_dt),
        "due_at_local": local_dt.isoformat(),
        "due_display": local_dt.strftime("%Y-%m-%d %H:%M"),
        "original_time_text": local_dt.strftime("%Y-%m-%d %H:%M"),
    }


def _parse_schedule_statuses(raw_statuses: Optional[str]) -> Optional[list[str]]:
    safe = str(raw_statuses or "").strip()
    if not safe:
        return None
    parsed = [part.strip() for part in safe.split(",") if part.strip()]
    return parsed or None


def _publicize_schedule_thread_id(*, owner_id: str, internal_thread_id: str) -> str:
    safe_owner_id = str(owner_id or "").strip()
    safe_internal_thread_id = str(internal_thread_id or "").strip()
    if safe_owner_id and safe_internal_thread_id.startswith(f"{safe_owner_id}::"):
        public_thread_id = safe_internal_thread_id[len(safe_owner_id) + 2 :].strip()
        if public_thread_id:
            return public_thread_id
    if "::" in safe_internal_thread_id:
        _, _, public_thread_id = safe_internal_thread_id.partition("::")
        if public_thread_id.strip():
            return public_thread_id.strip()
    return safe_internal_thread_id


def _public_schedule_item(payload: Dict[str, Any], *, owner_id: str) -> Dict[str, Any]:
    result = deepcopy(payload)
    result["thread_id"] = _publicize_schedule_thread_id(
        owner_id=owner_id,
        internal_thread_id=str(result.get("thread_id", "") or ""),
    )
    return result


def create_app(
    *,
    service_runtime: ChatServiceRuntime,
    user_access: Optional[UserAccessService] = None,
) -> FastAPI:
    wire_runtime_event_sink(service_runtime)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            service_runtime.shutdown()
            if user_access is not None:
                user_access.shutdown()

    app = FastAPI(title="M-Agent Chat API", version="2.0", lifespan=lifespan)
    app.state.service_runtime = service_runtime
    app.state.user_access = user_access

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
        max_age=600,
    )
    app.add_middleware(PrivateNetworkAccessMiddleware)

    def _error_response(*, status_code: int, message: str, extra: Optional[Dict[str, Any]] = None) -> JSONResponse:
        payload = {"error": message}
        if isinstance(extra, dict):
            payload.update(extra)
        return JSONResponse(status_code=int(status_code), content=payload)

    def _resolve_user_only(request: Request) -> Tuple[Optional[AuthenticatedUser], Optional[JSONResponse]]:
        if user_access is None:
            return None, None
        token = _extract_access_token(request)
        if not token:
            return None, _error_response(
                status_code=401,
                message="missing bearer token; call /v1/auth/login first",
            )
        try:
            user = user_access.authenticate(token)
        except UserAccessError as exc:
            return None, _error_response(status_code=exc.status_code, message=str(exc))
        return user, None

    def _resolve_user_and_runtime(
        request: Request,
    ) -> Tuple[Optional[AuthenticatedUser], ChatServiceRuntime, Optional[JSONResponse]]:
        user, auth_error = _resolve_user_only(request)
        if auth_error is not None:
            return None, service_runtime, auth_error
        if user is None:
            return None, service_runtime, None
        if user_access is None:
            return user, service_runtime, None
        runtime = user_access.get_runtime(user=user)
        wire_runtime_event_sink(runtime)
        return user, runtime, None

    def _record_is_visible(record: ChatRunRecord, user: Optional[AuthenticatedUser]) -> bool:
        if user_access is None:
            return True
        if user is None:
            return False
        return str(record.user_id or "").strip() == user.username

    def _runtime_thread_id(user: Optional[AuthenticatedUser], public_thread_id: str) -> str:
        if user is None:
            return public_thread_id
        return _scoped_thread_id(user, public_thread_id)

    def _schedule_owner_id(user: Optional[AuthenticatedUser]) -> str:
        if user is None:
            return ANONYMOUS_OWNER_ID
        return str(user.username or "").strip() or ANONYMOUS_OWNER_ID

    def _serialize_schedule_item(schedule_agent: Any, item: Any, *, owner_id: str) -> Dict[str, Any]:
        return _public_schedule_item(
            schedule_agent.service.serialize_item(item),
            owner_id=owner_id,
        )

    def _load_thread_schedule_item(
        *,
        schedule_agent: Any,
        owner_id: str,
        schedule_id: str,
    ) -> Any:
        item = schedule_agent.store.find_by_id(schedule_id, owner_id=owner_id)
        if item is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        return item

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
            "auth": user_access.health_payload() if user_access is not None else None,
            "endpoints": {
                "auth_register": "/v1/auth/register",
                "auth_login": "/v1/auth/login",
                "auth_me": "/v1/auth/me",
                "auth_logout": "/v1/auth/logout",
                "user_config_patch": "/v1/users/me/config",
                "user_config_schema": "/v1/users/me/config/schema",
                "create_run": "/v1/chat/runs",
                "get_run": "/v1/chat/runs/{run_id}",
                "stream_events": "/v1/chat/runs/{run_id}/events",
                "thread_events": "/v1/chat/threads/{thread_id}/events",
                "thread_state": "/v1/chat/threads/{thread_id}/memory/state",
                "thread_mode": "/v1/chat/threads/{thread_id}/memory/mode",
                "thread_flush": "/v1/chat/threads/{thread_id}/memory/flush",
                "list_schedules": "/v1/chat/threads/{thread_id}/schedules",
                "create_schedule": "/v1/chat/threads/{thread_id}/schedules",
                "get_schedule": "/v1/chat/threads/{thread_id}/schedules/{schedule_id}",
                "update_schedule": "/v1/chat/threads/{thread_id}/schedules/{schedule_id}",
                "cancel_schedule": "/v1/chat/threads/{thread_id}/schedules/{schedule_id}",
                "list_dialogues": "/v1/chat/dialogues",
                "get_dialogue": "/v1/chat/dialogues/{dialogue_id}",
                "openapi": "/openapi.json",
                "docs": "/docs",
            },
            "auth_required_for_chat": bool(user_access is not None),
        }

    @app.post("/v1/auth/register")
    def register_user(body: UserRegisterRequest) -> JSONResponse:
        if user_access is None:
            return _error_response(status_code=503, message="user auth service is disabled")
        username = str(body.username or "").strip()
        password = str(body.password or "")
        if not username or not password:
            return _error_response(status_code=400, message="username and password are required")
        try:
            payload = user_access.register_user(
                username=username,
                password=password,
                role=str(body.role or "basic"),
                display_name=body.display_name,
                assistant_name=body.assistant_name,
                persona_prompt=body.persona_prompt,
                workflow_id=body.workflow_id,
            )
        except UserAccessError as exc:
            return _error_response(status_code=exc.status_code, message=str(exc))
        return JSONResponse(status_code=201, content=payload)

    @app.post("/v1/auth/login")
    def login_user(body: UserLoginRequest) -> JSONResponse:
        if user_access is None:
            return _error_response(status_code=503, message="user auth service is disabled")
        username = str(body.username or "").strip()
        password = str(body.password or "")
        if not username or not password:
            return _error_response(status_code=400, message="username and password are required")
        try:
            payload = user_access.login(username=username, password=password)
        except UserAccessError as exc:
            return _error_response(status_code=exc.status_code, message=str(exc))
        return JSONResponse(content=payload)

    @app.get("/v1/auth/me")
    def who_am_i(request: Request) -> JSONResponse:
        if user_access is None:
            return _error_response(status_code=503, message="user auth service is disabled")
        user, auth_error = _resolve_user_only(request)
        if auth_error is not None:
            return auth_error
        return JSONResponse(content={"user": user.to_payload() if user is not None else None})

    @app.post("/v1/auth/logout")
    def logout_user(request: Request) -> JSONResponse:
        if user_access is None:
            return _error_response(status_code=503, message="user auth service is disabled")
        token = _extract_access_token(request)
        if not token:
            return _error_response(status_code=401, message="missing bearer token")
        user_access.logout(token)
        return JSONResponse(content={"success": True})

    @app.get("/v1/users/me/config/schema")
    def get_my_config_schema(request: Request) -> JSONResponse:
        if user_access is None:
            return _error_response(status_code=503, message="user auth service is disabled")
        user, auth_error = _resolve_user_only(request)
        if auth_error is not None:
            return auth_error
        try:
            payload = user_access.get_user_config_schema(user=user)
        except UserAccessError as exc:
            return _error_response(status_code=exc.status_code, message=str(exc))
        return JSONResponse(content=payload)

    @app.patch("/v1/users/me/config")
    def patch_my_config(request: Request, body: UserConfigPatchRequest) -> JSONResponse:
        if user_access is None:
            return _error_response(status_code=503, message="user auth service is disabled")
        user, auth_error = _resolve_user_only(request)
        if auth_error is not None:
            return auth_error
        try:
            payload = user_access.update_user_config(
                user=user,
                updates={
                    "chat": dict(body.chat or {}),
                    "memory_agent": dict(body.memory_agent or {}),
                    "memory_core": dict(body.memory_core or {}),
                },
            )
        except UserAccessError as exc:
            return _error_response(status_code=exc.status_code, message=str(exc))
        return JSONResponse(content=payload)

    @app.post("/v1/chat/runs")
    def create_run(body: ChatRunCreateRequest, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        requested_config = str(body.config or "").strip()
        if requested_config:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "service config is fixed at startup; restart the API with --config to change it",
                    "config_path": str(active_runtime.config_path),
                },
            )

        thread_id = str(body.thread_id or active_runtime.default_thread_id).strip() or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, thread_id)
        message = str(body.message or "").strip()
        if not message:
            return JSONResponse(status_code=400, content={"error": "message is empty"})

        record = _start_chat_run(
            service_runtime=active_runtime,
            thread_id=thread_id,
            internal_thread_id=runtime_thread_id,
            message=message,
            user_id=user.username if user is not None else None,
        )
        return JSONResponse(status_code=201, content=_json_response_payload(record))

    @app.get("/v1/chat/dialogues")
    def list_chat_dialogues(
        request: Request,
        thread_id: Optional[str] = None,
        limit: int = 30,
        offset: int = 0,
    ) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        username = user.username if user is not None else None
        try:
            memory_persistence = getattr(active_runtime.agent, "memory_persistence", None)
            dialogues_dir = Path(getattr(memory_persistence, "dialogues_dir"))
        except Exception as exc:
            return _error_response(status_code=500, message=f"failed to resolve dialogues directory: {exc}")

        normalized_thread_id = str(thread_id or "").strip()
        internal_thread_id = _runtime_thread_id(user, normalized_thread_id) if normalized_thread_id else None
        payload = list_dialogues(
            dialogues_dir=dialogues_dir,
            username=username,
            internal_thread_id=internal_thread_id,
            limit=limit,
            offset=offset,
        )
        return JSONResponse(content=payload)

    @app.get("/v1/chat/dialogues/{dialogue_id}")
    def get_chat_dialogue(dialogue_id: str, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        username = user.username if user is not None else None
        try:
            memory_persistence = getattr(active_runtime.agent, "memory_persistence", None)
            dialogues_dir = Path(getattr(memory_persistence, "dialogues_dir"))
        except Exception as exc:
            return _error_response(status_code=500, message=f"failed to resolve dialogues directory: {exc}")

        try:
            payload = get_dialogue_detail(
                dialogues_dir=dialogues_dir,
                dialogue_id=dialogue_id,
                username=username,
            )
        except FileNotFoundError:
            return _error_response(status_code=404, message=f"dialogue not found: {dialogue_id}")
        return JSONResponse(content=payload)

    @app.get("/v1/chat/runs/{run_id}")
    def get_run(run_id: str, request: Request) -> JSONResponse:
        user, auth_error = _resolve_user_only(request)
        if auth_error is not None:
            return auth_error
        record = _RUNS.get(run_id)
        if record is None or not _record_is_visible(record, user):
            return JSONResponse(status_code=404, content={"error": f"run not found: {run_id}"})
        return JSONResponse(content=record.snapshot())

    @app.get("/v1/chat/runs/{run_id}/events", response_class=StreamingResponse, response_model=None)
    async def stream_events(run_id: str, request: Request, after_seq: int = 0):
        user, auth_error = _resolve_user_only(request)
        if auth_error is not None:
            return auth_error
        record = _RUNS.get(run_id)
        if record is None or not _record_is_visible(record, user):
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
        user, _, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        runtime_thread_id = _runtime_thread_id(user, thread_id)
        record = _THREAD_EVENTS.get_or_create(runtime_thread_id)

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
                            public_event = _with_public_thread_event(event, public_thread_id=thread_id)
                            yield _encode_sse(public_event)
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
    def get_thread_state(thread_id: str, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        runtime_thread_id = _runtime_thread_id(user, thread_id)
        state = active_runtime.get_thread_state(runtime_thread_id)
        return JSONResponse(content=_with_public_thread_state(state, public_thread_id=thread_id))

    @app.post("/v1/chat/threads/{thread_id}/memory/mode")
    def set_thread_mode(thread_id: str, body: ThreadMemoryModeRequest, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        runtime_thread_id = _runtime_thread_id(user, thread_id)
        mode = _normalize_memory_mode(body.mode, fallback="manual")
        thread_lock = _get_thread_lock(runtime_thread_id)
        with thread_lock:
            result = active_runtime.set_thread_mode(
                runtime_thread_id,
                mode=mode,
                discard_pending=bool(body.discard_pending),
            )
        result = deepcopy(result)
        result["thread_id"] = thread_id
        if isinstance(result.get("thread_state"), dict):
            result["thread_state"] = _with_public_thread_state(result.get("thread_state"), public_thread_id=thread_id)
        return JSONResponse(content=result)

    @app.post("/v1/chat/threads/{thread_id}/memory/flush")
    def flush_thread(thread_id: str, body: ThreadMemoryFlushRequest, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        runtime_thread_id = _runtime_thread_id(user, thread_id)
        reason = str(body.reason or "manual_api").strip() or "manual_api"
        thread_lock = _get_thread_lock(runtime_thread_id)
        with thread_lock:
            result = active_runtime.flush_thread(runtime_thread_id, reason=reason)
        result = deepcopy(result)
        result["thread_id"] = thread_id
        if isinstance(result.get("thread_state"), dict):
            result["thread_state"] = _with_public_thread_state(result.get("thread_state"), public_thread_id=thread_id)
        return JSONResponse(content=result)

    @app.get("/v1/chat/threads/{thread_id}/schedules")
    def list_thread_schedules(
        thread_id: str,
        request: Request,
        include_completed: bool = False,
        limit: int = 20,
        keyword: str = "",
        statuses: Optional[str] = None,
    ) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        owner_id = _schedule_owner_id(user)
        try:
            schedule_agent = _resolve_schedule_agent(active_runtime)
            parsed_statuses = _parse_schedule_statuses(statuses)
            items = schedule_agent.service.list_schedules(
                owner_id=owner_id,
                thread_id=None,
                statuses=parsed_statuses,
                keyword=str(keyword or "").strip(),
                include_completed=bool(include_completed),
                limit=max(1, min(100, int(limit or 20))),
            )
            serialized = [
                _serialize_schedule_item(schedule_agent, item, owner_id=owner_id)
                for item in items
            ]
        except ValueError as exc:
            return _error_response(status_code=400, message=str(exc))
        except Exception as exc:
            return _error_response(status_code=500, message=f"failed to list schedules: {exc}")
        return JSONResponse(
            content={
                "thread_id": thread_id,
                "scope": "owner",
                "owner_id": owner_id,
                "count": len(serialized),
                "include_completed": bool(include_completed),
                "keyword": str(keyword or "").strip(),
                "statuses": parsed_statuses or [],
                "items": serialized,
            }
        )

    @app.get("/v1/chat/threads/{thread_id}/schedules/{schedule_id}")
    def get_thread_schedule(schedule_id: str, thread_id: str, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        owner_id = _schedule_owner_id(user)
        try:
            schedule_agent = _resolve_schedule_agent(active_runtime)
            item = _load_thread_schedule_item(
                schedule_agent=schedule_agent,
                owner_id=owner_id,
                schedule_id=schedule_id,
            )
        except FileNotFoundError:
            return _error_response(status_code=404, message=f"schedule not found: {schedule_id}")
        except Exception as exc:
            return _error_response(status_code=500, message=f"failed to get schedule: {exc}")
        return JSONResponse(
            content={
                "thread_id": thread_id,
                "item": _serialize_schedule_item(schedule_agent, item, owner_id=owner_id),
            }
        )

    @app.post("/v1/chat/threads/{thread_id}/schedules")
    def create_thread_schedule(thread_id: str, body: ScheduleCreateRequest, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        runtime_thread_id = _runtime_thread_id(user, thread_id)
        owner_id = _schedule_owner_id(user)
        title = str(body.title or "").strip()
        prompt = str(body.prompt or "").strip()
        source_text = str(body.source_text or "").strip()
        effective_title = title or prompt or source_text
        if not effective_title:
            return _error_response(status_code=400, message="title or prompt is required")
        try:
            schedule_agent = _resolve_schedule_agent(active_runtime)
            normalized_due = _normalize_schedule_due_at(body.due_at, body.timezone_name)
            original_time_text = str(body.original_time_text or "").strip() or normalized_due["original_time_text"]
            item = schedule_agent.service.create_schedule(
                owner_id=owner_id,
                thread_id=runtime_thread_id,
                title=effective_title,
                due_at_utc=normalized_due["due_at_utc"],
                timezone_name=normalized_due["timezone_name"],
                original_time_text=original_time_text,
                action_type="chat_prompt",
                action_payload={
                    "prompt": prompt or effective_title,
                    "source": "schedule_api",
                    "hidden_context": {
                        "trigger_kind": "time_due",
                        "created_via": "chat_api_web",
                    },
                },
                source_text=source_text or effective_title,
                metadata=dict(body.metadata or {}),
            )
            serialized = _serialize_schedule_item(schedule_agent, item, owner_id=owner_id)
            _THREAD_EVENTS.append_event(
                runtime_thread_id,
                "schedule_created",
                {
                    "thread_id": thread_id,
                    "schedule": serialized,
                },
            )
        except ValueError as exc:
            return _error_response(status_code=400, message=str(exc))
        except Exception as exc:
            return _error_response(status_code=500, message=f"failed to create schedule: {exc}")
        return JSONResponse(
            status_code=201,
            content={
                "success": True,
                "thread_id": thread_id,
                "item": serialized,
            },
        )

    @app.patch("/v1/chat/threads/{thread_id}/schedules/{schedule_id}")
    def update_thread_schedule(
        schedule_id: str,
        thread_id: str,
        body: ScheduleUpdateRequest,
        request: Request,
    ) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        owner_id = _schedule_owner_id(user)
        try:
            schedule_agent = _resolve_schedule_agent(active_runtime)
            existing = _load_thread_schedule_item(
                schedule_agent=schedule_agent,
                owner_id=owner_id,
                schedule_id=schedule_id,
            )
            due_at_utc = None
            timezone_name = None
            original_time_text = None
            if body.due_at is not None:
                effective_timezone_name = str(body.timezone_name or existing.timezone_name or "").strip() or None
                normalized_due = _normalize_schedule_due_at(body.due_at, effective_timezone_name)
                due_at_utc = normalized_due["due_at_utc"]
                timezone_name = normalized_due["timezone_name"]
                original_time_text = (
                    str(body.original_time_text or "").strip() or normalized_due["original_time_text"]
                )
            elif body.timezone_name is not None:
                _, timezone_name, _ = resolve_timezone(body.timezone_name)
                if body.original_time_text is not None:
                    original_time_text = str(body.original_time_text or "").strip()
            elif body.original_time_text is not None:
                original_time_text = str(body.original_time_text or "").strip()

            action_payload_patch: Dict[str, Any] = {}
            if body.prompt is not None:
                safe_prompt = str(body.prompt or "").strip()
                if not safe_prompt:
                    return _error_response(status_code=400, message="prompt cannot be empty")
                action_payload_patch["prompt"] = safe_prompt

            metadata_patch = dict(body.metadata or {}) if body.metadata is not None else None
            updated = schedule_agent.service.update_schedule(
                owner_id=owner_id,
                thread_id=None,
                schedule_id=schedule_id,
                title=str(body.title or "").strip() if body.title is not None else None,
                due_at_utc=due_at_utc,
                timezone_name=timezone_name,
                original_time_text=original_time_text,
                action_payload_patch=action_payload_patch or None,
                metadata_patch=metadata_patch,
                source_text=str(body.source_text or "").strip() if body.source_text is not None else None,
            )
            serialized = _serialize_schedule_item(schedule_agent, updated, owner_id=owner_id)
            event_thread_id = str(getattr(updated, "thread_id", "") or "").strip() or _runtime_thread_id(user, thread_id)
            _THREAD_EVENTS.append_event(
                event_thread_id,
                "schedule_updated",
                {
                    "thread_id": thread_id,
                    "schedule": serialized,
                },
            )
        except FileNotFoundError:
            return _error_response(status_code=404, message=f"schedule not found: {schedule_id}")
        except ValueError as exc:
            return _error_response(status_code=400, message=str(exc))
        except Exception as exc:
            return _error_response(status_code=500, message=f"failed to update schedule: {exc}")
        return JSONResponse(
            content={
                "success": True,
                "thread_id": thread_id,
                "item": serialized,
            }
        )

    @app.delete("/v1/chat/threads/{thread_id}/schedules/{schedule_id}")
    def cancel_thread_schedule(schedule_id: str, thread_id: str, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        owner_id = _schedule_owner_id(user)
        try:
            schedule_agent = _resolve_schedule_agent(active_runtime)
            _load_thread_schedule_item(
                schedule_agent=schedule_agent,
                owner_id=owner_id,
                schedule_id=schedule_id,
            )
            canceled = schedule_agent.service.cancel_schedule(
                owner_id=owner_id,
                thread_id=None,
                schedule_id=schedule_id,
                source_text="schedule_api_cancel",
            )
            serialized = _serialize_schedule_item(schedule_agent, canceled, owner_id=owner_id)
            event_thread_id = str(getattr(canceled, "thread_id", "") or "").strip() or _runtime_thread_id(user, thread_id)
            _THREAD_EVENTS.append_event(
                event_thread_id,
                "schedule_canceled",
                {
                    "thread_id": thread_id,
                    "schedule": serialized,
                },
            )
        except FileNotFoundError:
            return _error_response(status_code=404, message=f"schedule not found: {schedule_id}")
        except Exception as exc:
            return _error_response(status_code=500, message=f"failed to cancel schedule: {exc}")
        return JSONResponse(
            content={
                "success": True,
                "thread_id": thread_id,
                "item": serialized,
            }
        )

    return app


def create_handler(
    *,
    service_runtime: ChatServiceRuntime,
    user_access: Optional[UserAccessService] = None,
) -> FastAPI:
    """Backward-compatible alias for the old stdlib server entrypoint."""
    return create_app(service_runtime=service_runtime, user_access=user_access)
