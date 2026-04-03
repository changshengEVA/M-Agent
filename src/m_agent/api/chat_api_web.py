from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from copy import deepcopy
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from m_agent.api.user_access import AuthenticatedUser, UserAccessError, UserAccessService
from m_agent.paths import PROJECT_ROOT

from .chat_api_models import (
    ChatRunCreateRequest,
    ThreadMemoryFlushRequest,
    ThreadMemoryModeRequest,
    UserConfigPatchRequest,
    UserLoginRequest,
    UserRegisterRequest,
)
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

    return app


def create_handler(
    *,
    service_runtime: ChatServiceRuntime,
    user_access: Optional[UserAccessService] = None,
) -> FastAPI:
    """Backward-compatible alias for the old stdlib server entrypoint."""
    return create_app(service_runtime=service_runtime, user_access=user_access)
