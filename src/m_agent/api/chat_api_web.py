from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, File, Form, Header, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from m_agent.api.user_access import AuthenticatedUser, UserAccessError, UserAccessService
from m_agent.paths import PROJECT_ROOT
from m_agent.runtime.transaction_control import (
    RuntimeTransactionNotFoundError,
)
from m_agent.runtime.transaction.store import (
    IdempotencyConflictError,
    RevisionConflictError,
)
from m_agent.runtime.transaction.registry import (
    TransactionTransitionError,
)

from .chat_api_models import (
    ChatRunCreateRequest,
    ChatImageAttachment,
    ScheduleCreateRequest,
    DialogueImportRequest,
    ThreadMemoryFlushRequest,
    ThreadMemoryModeRequest,
    ThreadStimulusRequest,
    UserConfigPatchRequest,
    UserLoginRequest,
    UserRegisterRequest,
)
from .thread_runtime_status import THREAD_RUNTIME_STATUS
from .chat_image_captioner import ChatImageCaptioner
from .chat_image_store import ChatImageStore
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
from .schedule_heartbeat import ScheduleHeartbeatCoordinator
from .chat_api_shared import (
    ensure_dialogue_archive,
    resolve_dialogues_dir_for_agent,
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


def _parse_if_match_revision(value: Any) -> int:
    """Parse a transaction revision from an HTTP If-Match value."""

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("If-Match is required")
    if raw[:2].lower() == "w/":
        raw = raw[2:].strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        raw = raw[1:-1].strip()
    if not raw or not raw.isdigit():
        raise ValueError(
            'If-Match must be a transaction revision such as W/"3"'
        )
    return int(raw)


def _with_public_transaction_scope(
    payload: Any,
    *,
    internal_thread_id: str,
    public_thread_id: str,
) -> Any:
    """Remove authenticated runtime thread prefixes from transaction data."""

    internal = str(internal_thread_id or "").strip()
    public = str(public_thread_id or "").strip()
    if isinstance(payload, list):
        return [
            _with_public_transaction_scope(
                item,
                internal_thread_id=internal,
                public_thread_id=public,
            )
            for item in payload
        ]
    if not isinstance(payload, dict):
        return payload
    result: Dict[str, Any] = {}
    for key, value in payload.items():
        if key == "thread_id" and str(value or "").strip() == internal:
            result[key] = public
            continue
        if key == "conversation_id":
            conversation_id = str(value or "").strip()
            if internal and conversation_id.startswith(f"{internal}::"):
                result[key] = f"{public}{conversation_id[len(internal):]}"
                continue
        result[key] = _with_public_transaction_scope(
            value,
            internal_thread_id=internal,
            public_thread_id=public,
        )
    return result


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


def _attachment_to_payload(attachment: ChatImageAttachment) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for field_name in ("upload_id", "image_url", "image_file", "blip_caption", "mime_type"):
        value = getattr(attachment, field_name, None)
        if isinstance(value, str) and value.strip():
            payload[field_name] = value.strip()
    if isinstance(getattr(attachment, "width", None), int):
        payload["width"] = int(attachment.width)
    if isinstance(getattr(attachment, "height", None), int):
        payload["height"] = int(attachment.height)
    return payload


def _build_user_turn_payload(*, message: str, attachments: list[ChatImageAttachment] | None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "text": str(message or "").strip(),
    }
    first = attachments[0] if attachments else None
    if first is not None:
        payload.update(_attachment_to_payload(first))
        if "image_url" in payload and "img_url" not in payload:
            payload["img_url"] = payload["image_url"]
        if "image_file" in payload and "img_file" not in payload:
            payload["img_file"] = payload["image_file"]
    return payload


def _has_effective_attachment(attachments: list[ChatImageAttachment] | None) -> bool:
    if not attachments:
        return False
    return any(bool(_attachment_to_payload(item)) for item in attachments)


def _truthy_env(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _build_image_captioner_from_env() -> ChatImageCaptioner:
    provider = str(os.getenv("CHAT_IMAGE_CAPTION_PROVIDER", "openai")).strip().lower() or "openai"
    enabled = _truthy_env("CHAT_IMAGE_CAPTION_ENABLED", True)
    model_name = str(
        os.getenv("CHAT_IMAGE_CAPTION_MODEL_NAME", "Salesforce/blip-image-captioning-base")
    ).strip() or "Salesforce/blip-image-captioning-base"
    device = str(os.getenv("CHAT_IMAGE_CAPTION_DEVICE", "")).strip() or None
    openai_model = str(os.getenv("CHAT_IMAGE_CAPTION_OPENAI_MODEL", "")).strip() or None
    openai_api_key = str(os.getenv("CHAT_IMAGE_CAPTION_OPENAI_API_KEY", "")).strip() or None
    openai_base_url = str(os.getenv("CHAT_IMAGE_CAPTION_OPENAI_BASE_URL", "")).strip() or None
    api_url = str(os.getenv("CHAT_IMAGE_CAPTION_API_URL", "")).strip() or None
    api_auth_token = str(os.getenv("CHAT_IMAGE_CAPTION_API_TOKEN", "")).strip() or None
    api_auth_header = str(os.getenv("CHAT_IMAGE_CAPTION_API_AUTH_HEADER", "Authorization")).strip() or "Authorization"
    api_caption_field = str(os.getenv("CHAT_IMAGE_CAPTION_API_FIELD", "blip_caption")).strip() or "blip_caption"
    api_mode = str(os.getenv("CHAT_IMAGE_CAPTION_API_MODE", "multipart")).strip().lower() or "multipart"
    try:
        api_timeout_seconds = float(str(os.getenv("CHAT_IMAGE_CAPTION_API_TIMEOUT_SECONDS", "30")).strip() or "30")
    except Exception:
        api_timeout_seconds = 30.0
    return ChatImageCaptioner(
        enabled=enabled,
        provider=provider,
        model_name=model_name,
        device=device,
        openai_model=openai_model,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        api_url=api_url,
        api_timeout_seconds=api_timeout_seconds,
        api_auth_token=api_auth_token,
        api_auth_header=api_auth_header,
        api_caption_field=api_caption_field,
        api_mode=api_mode,
    )


def create_app(
    *,
    service_runtime: ChatServiceRuntime,
    user_access: Optional[UserAccessService] = None,
    schedule_beat_seconds: int = 10,
) -> FastAPI:
    wire_runtime_event_sink(service_runtime)
    image_store = ChatImageStore(
        root_dir=PROJECT_ROOT / "data" / "memory",
        captioner=_build_image_captioner_from_env(),
    )
    schedule_heartbeat = ScheduleHeartbeatCoordinator(
        service_runtime=service_runtime,
        user_access=user_access,
        beat_interval_seconds=max(1, int(schedule_beat_seconds or 10)),
        thread_event_sink=_THREAD_EVENTS.append_event,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            schedule_heartbeat.shutdown()
            service_runtime.shutdown()
            if user_access is not None:
                user_access.shutdown()

    app = FastAPI(title="M-Agent Chat API", version="0.2.0", lifespan=lifespan)
    app.state.service_runtime = service_runtime
    app.state.user_access = user_access
    app.state.schedule_heartbeat = schedule_heartbeat
    app.state.image_store = image_store

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

    def _public_thread_id(user: Optional[AuthenticatedUser], requested_thread_id: str) -> str:
        requested = str(requested_thread_id or "").strip()
        if user is None:
            return requested
        canonical = str(getattr(user, "canonical_thread_id", "") or "").strip()
        if canonical:
            return canonical
        # AuthenticatedUser normally always carries the configured thread id. Keep
        # a deterministic account-owned fallback for older custom auth adapters.
        username = str(user.username or "").strip()
        return f"{username}-thread" if username else requested

    def _runtime_thread_id(user: Optional[AuthenticatedUser], public_thread_id: str) -> str:
        resolved_public_thread_id = _public_thread_id(user, public_thread_id)
        if user is None:
            return resolved_public_thread_id
        return _scoped_thread_id(user, resolved_public_thread_id)

    def _with_canonical_dialogue_thread_fields(
        payload: Any,
        *,
        user: Optional[AuthenticatedUser],
    ) -> Any:
        if user is None:
            return payload
        canonical_thread_id = _public_thread_id(user, "")

        def _rewrite(value: Any) -> Any:
            if isinstance(value, dict):
                rewritten: Dict[str, Any] = {}
                for key, item in value.items():
                    if key == "thread_id_internal":
                        continue
                    if key == "thread_id":
                        rewritten[key] = canonical_thread_id
                    else:
                        rewritten[key] = _rewrite(item)
                return rewritten
            if isinstance(value, list):
                return [_rewrite(item) for item in value]
            return value

        return _rewrite(payload)

    def _schedule_owner_id(user: Optional[AuthenticatedUser]) -> str:
        if user is None:
            return ANONYMOUS_OWNER_ID
        return str(user.username or "").strip() or ANONYMOUS_OWNER_ID

    def _serialize_schedule_item(
        schedule_agent: Any,
        item: Any,
        *,
        owner_id: str,
        public_thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = _public_schedule_item(
            schedule_agent.service.serialize_item(item),
            owner_id=owner_id,
        )
        if public_thread_id is not None:
            payload["thread_id"] = public_thread_id
        return payload

    def _serialize_schedule_heartbeat(
        thread_id: str,
        *,
        runtime_thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = dict(schedule_heartbeat.health_payload())
        internal_tid = str(runtime_thread_id or thread_id or "").strip()
        thread_runtime = THREAD_RUNTIME_STATUS.snapshot(internal_tid).to_dict()
        return {
            "thread_id": thread_id,
            "scope": "owner",
            "heartbeat": payload,
            "thread_runtime": thread_runtime,
        }

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
            "schedule_heartbeat": schedule_heartbeat.health_payload(),
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
                "thread_scene": "/v1/chat/threads/{thread_id}/scene",
                "thread_transactions": "/v1/chat/threads/{thread_id}/transactions",
                "delete_thread_transaction": "/v1/chat/threads/{thread_id}/transactions/{transaction_id}",
                "thread_stimuli": "/v1/chat/threads/{thread_id}/stimuli",
                "thread_thinking_stop": "/v1/chat/threads/{thread_id}/thinking/stop",
                "thread_state": "/v1/chat/threads/{thread_id}/memory/state",
                "thread_mode": "/v1/chat/threads/{thread_id}/memory/mode",
                "thread_flush": "/v1/chat/threads/{thread_id}/memory/flush",
                "list_schedules": "/v1/chat/threads/{thread_id}/schedules",
                "create_schedule": "/v1/chat/threads/{thread_id}/schedules",
                "get_schedule": "/v1/chat/threads/{thread_id}/schedules/{schedule_id}",
                "update_schedule": "/v1/chat/threads/{thread_id}/schedules/{schedule_id}",
                "cancel_schedule": "/v1/chat/threads/{thread_id}/schedules/{schedule_id}",
                "list_dialogues": "/v1/chat/dialogues",
                "import_dialogues": "/v1/chat/dialogues/import",
                "upload_dialogues": "/v1/chat/dialogues/upload",
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
                    "model": dict(body.model or {}),
                },
            )
        except UserAccessError as exc:
            return _error_response(status_code=exc.status_code, message=str(exc))
        return JSONResponse(content=payload)

    @app.post("/v1/chat/uploads/images", response_model=None)
    async def upload_chat_image(
        request: Request,
        file: UploadFile = File(...),
        thread_id: Optional[str] = Form(default=None),
    ) -> JSONResponse | FileResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        if file is None:
            return _error_response(status_code=400, message="image file is required")
        try:
            content = await file.read()
            requested_thread_id = str(thread_id or "").strip()
            public_thread_id = _public_thread_id(user, requested_thread_id)
            if user is not None and not public_thread_id:
                public_thread_id = _public_thread_id(user, active_runtime.default_thread_id)
            metadata = image_store.save_upload(
                content=content,
                content_type=str(file.content_type or "").strip(),
                original_filename=str(file.filename or "").strip(),
                username=user.username if user is not None else None,
                thread_id=(
                    _runtime_thread_id(user, public_thread_id)
                    if public_thread_id
                    else None
                ),
            )
        except ValueError as exc:
            return _error_response(status_code=400, message=str(exc))
        except RuntimeError as exc:
            return _error_response(status_code=503, message=str(exc))
        except Exception as exc:
            return _error_response(status_code=500, message=f"failed to store image upload: {exc}")
        response_metadata = deepcopy(metadata)
        if user is not None:
            response_metadata["thread_id"] = public_thread_id
        return JSONResponse(content=response_metadata)

    @app.get("/v1/chat/uploads/images/{upload_id}/content", response_model=None)
    def get_chat_image_content(upload_id: str, request: Request) -> JSONResponse | FileResponse:
        user, _, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        metadata = image_store.get_upload_metadata(upload_id)
        if not isinstance(metadata, dict):
            return _error_response(status_code=404, message=f"image upload not found: {upload_id}")
        owner = str(metadata.get("owner", "") or "").strip() or None
        if user_access is not None and owner and (user is None or user.username != owner):
            return _error_response(status_code=404, message=f"image upload not found: {upload_id}")
        image_file = str(metadata.get("image_file", "") or "").strip()
        if not image_file:
            return _error_response(status_code=404, message=f"image file missing: {upload_id}")
        path = Path(image_file)
        if not path.exists() or not path.is_file():
            return _error_response(status_code=404, message=f"image file missing: {upload_id}")
        media_type = str(metadata.get("mime_type", "") or "").strip() or None
        return FileResponse(path=str(path), media_type=media_type)

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

        requested_thread_id = str(body.thread_id or active_runtime.default_thread_id).strip()
        thread_id = _public_thread_id(user, requested_thread_id) or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, thread_id)
        attachments = list(body.attachments or [])
        message = str(body.message or "").strip()
        if not message and not _has_effective_attachment(attachments):
            return JSONResponse(status_code=400, content={"error": "message and attachments are both empty"})
        user_turn = _build_user_turn_payload(message=message, attachments=attachments)

        record = _start_chat_run(
            service_runtime=active_runtime,
            thread_id=thread_id,
            internal_thread_id=runtime_thread_id,
            message=message,
            user_turn=user_turn,
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
        ensure_dialogue_archive(active_runtime.agent)
        try:
            dialogues_dir = resolve_dialogues_dir_for_agent(active_runtime.agent)
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
        return JSONResponse(content=_with_canonical_dialogue_thread_fields(payload, user=user))

    @app.get("/v1/chat/dialogues/{dialogue_id}")
    def get_chat_dialogue(dialogue_id: str, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        username = user.username if user is not None else None
        ensure_dialogue_archive(active_runtime.agent)
        try:
            dialogues_dir = resolve_dialogues_dir_for_agent(active_runtime.agent)
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
        return JSONResponse(content=_with_canonical_dialogue_thread_fields(payload, user=user))

    @app.post("/v1/chat/dialogues/import")
    def import_chat_dialogues(body: DialogueImportRequest, request: Request) -> JSONResponse:
        """Import dialogue JSON (e.g. migrate ``data/memory/user_<name>/dialogues``) and index RAG."""
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        ensure_dialogue_archive(active_runtime.agent)
        result = active_runtime.import_dialogues(
            migrate_legacy=bool(body.migrate_legacy),
            rebuild_rag=bool(body.rebuild_rag),
            index_rag=bool(body.index_rag),
            copy_files=bool(body.copy_files),
            dialogue_ids=list(body.dialogue_ids or []) or None,
        )
        return JSONResponse(content=_with_canonical_dialogue_thread_fields(result, user=user))

    @app.post("/v1/chat/dialogues/upload", response_class=StreamingResponse, response_model=None)
    async def upload_chat_dialogues(
        request: Request,
        files: List[UploadFile] = File(...),
        rebuild_rag: bool = Form(default=False),
        index_rag: bool = Form(default=True),
    ) -> StreamingResponse | JSONResponse:
        """Batch-upload dialogue JSON files; stream per-file validation + RAG index progress (SSE)."""
        from m_agent.chat.dialogue_validation import MAX_DIALOGUE_FILE_BYTES

        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        if not files:
            return _error_response(status_code=400, message="at least one .json dialogue file is required")

        max_files = 100
        if len(files) > max_files:
            return _error_response(status_code=400, message=f"too many files (max {max_files})")

        ensure_dialogue_archive(active_runtime.agent)
        uploads: List[Tuple[str, bytes]] = []
        for item in files:
            if item is None:
                continue
            name = str(item.filename or "upload.json").strip()
            if not name.lower().endswith(".json"):
                return _error_response(status_code=400, message=f"only .json files are supported: {name}")
            try:
                content = await item.read()
            except Exception as exc:
                return _error_response(status_code=400, message=f"failed to read upload {name}: {exc}")
            if len(content) > MAX_DIALOGUE_FILE_BYTES:
                return _error_response(
                    status_code=400,
                    message=f"file too large (max {MAX_DIALOGUE_FILE_BYTES} bytes): {name}",
                )
            uploads.append((name, content))

        if not uploads:
            return _error_response(status_code=400, message="no readable dialogue files in request")

        def event_stream():
            try:
                for event in active_runtime.iter_upload_dialogues(
                    uploads,
                    rebuild_rag=bool(rebuild_rag),
                    index_rag=bool(index_rag),
                ):
                    public_event = _with_canonical_dialogue_thread_fields(event, user=user)
                    yield _encode_sse(public_event)
            except Exception as exc:
                yield _encode_sse(
                    {
                        "seq": 0,
                        "type": "upload_failed",
                        "payload": {"message": str(exc)},
                    }
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

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
                            public_event = (
                                _with_public_thread_event(
                                    event,
                                    public_thread_id=record.thread_id,
                                )
                                if user is not None
                                else event
                            )
                            yield _encode_sse(public_event)
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
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, public_thread_id)
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
                            public_event = _with_public_thread_event(event, public_thread_id=public_thread_id)
                            public_event = _with_public_transaction_scope(
                                public_event,
                                internal_thread_id=runtime_thread_id,
                                public_thread_id=public_thread_id,
                            )
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

    @app.get("/v1/chat/threads/{thread_id}/transactions")
    def get_thread_transactions(
        thread_id: str,
        request: Request,
        include_history: bool = False,
    ) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, public_thread_id)
        payload = active_runtime.get_transactions(
            runtime_thread_id,
            include_history=include_history,
        )
        public_payload = _with_public_transaction_scope(
            payload,
            internal_thread_id=runtime_thread_id,
            public_thread_id=public_thread_id,
        )
        public_payload["thread_id"] = public_thread_id
        return JSONResponse(content=public_payload)

    @app.delete(
        "/v1/chat/threads/{thread_id}/transactions/{transaction_id}"
    )
    def delete_thread_transaction(
        thread_id: str,
        transaction_id: str,
        request: Request,
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_header: Optional[str] = Header(
            default=None,
            alias="Idempotency-Key",
        ),
    ) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        raw_if_match = str(if_match or "").strip()
        if not raw_if_match:
            return _error_response(
                status_code=428,
                message="If-Match is required for transaction deletion",
            )
        try:
            expected_revision = _parse_if_match_revision(raw_if_match)
        except ValueError as exc:
            return _error_response(status_code=400, message=str(exc))
        idempotency_key = str(idempotency_header or "").strip()
        if not idempotency_key:
            return _error_response(
                status_code=400,
                message="Idempotency-Key is required for transaction deletion",
            )
        if len(idempotency_key) > 200:
            return _error_response(
                status_code=400,
                message="Idempotency-Key must be at most 200 characters",
            )

        public_thread_id = (
            _public_thread_id(user, thread_id)
            or active_runtime.default_thread_id
        )
        runtime_thread_id = _runtime_thread_id(user, public_thread_id)
        try:
            payload = active_runtime.delete_transaction(
                runtime_thread_id,
                transaction_id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
        except RuntimeTransactionNotFoundError:
            return _error_response(
                status_code=404,
                message="transaction not found",
            )
        except RevisionConflictError as exc:
            return _error_response(
                status_code=409,
                message=str(exc),
                extra={
                    "code": "revision_conflict",
                    "transaction_id": str(exc.record_id),
                    "expected_revision": int(exc.expected_revision),
                    "actual_revision": int(exc.actual_revision),
                },
            )
        except IdempotencyConflictError as exc:
            return _error_response(
                status_code=409,
                message=str(exc),
                extra={"code": "idempotency_conflict"},
            )
        except TransactionTransitionError as exc:
            return _error_response(
                status_code=409,
                message=str(exc),
                extra={"code": "transaction_transition_conflict"},
            )

        public_payload = _with_public_transaction_scope(
            payload,
            internal_thread_id=runtime_thread_id,
            public_thread_id=public_thread_id,
        )
        public_payload["thread_id"] = public_thread_id
        revision = int(
            dict(public_payload.get("transaction") or {}).get(
                "revision",
                expected_revision,
            )
        )
        return JSONResponse(
            content=public_payload,
            headers={"ETag": f'W/"{revision}"'},
        )

    @app.get("/v1/chat/threads/{thread_id}/scene")
    def get_thread_scene(
        thread_id: str,
        request: Request,
        limit: int = 40,
        before_seq: Optional[int] = None,
        since_flush: bool = True,
    ) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, public_thread_id)
        payload = active_runtime.get_scene(
            runtime_thread_id,
            limit=limit,
            before_seq=before_seq,
            since_flush=since_flush,
        )
        payload["thread_id"] = public_thread_id
        return JSONResponse(content=payload)

    @app.post("/v1/chat/threads/{thread_id}/stimuli")
    def post_thread_stimulus(
        thread_id: str,
        body: ThreadStimulusRequest,
        request: Request,
    ) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, public_thread_id)
        attachments = list(body.attachments or [])
        message = str(body.text or "").strip()
        if not message and not _has_effective_attachment(attachments):
            return JSONResponse(status_code=400, content={"error": "text and attachments are both empty"})
        user_turn = _build_user_turn_payload(message=message, attachments=attachments)
        result = active_runtime.submit_stimulus(
            thread_id=runtime_thread_id,
            message=message,
            user_turn=user_turn,
        )
        result["thread_id"] = public_thread_id
        return JSONResponse(status_code=202, content=result)

    @app.post("/v1/chat/threads/{thread_id}/thinking/stop")
    def stop_thread_thinking(thread_id: str, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, public_thread_id)
        result = active_runtime.force_stop_thread(runtime_thread_id)
        result = deepcopy(result)
        result["thread_id"] = public_thread_id
        if isinstance(result.get("thread_state"), dict):
            result["thread_state"] = _with_public_thread_state(
                result.get("thread_state"),
                public_thread_id=public_thread_id,
            )
        if isinstance(result.get("thread_runtime"), dict):
            result["thread_runtime"] = _with_public_thread_state(
                result.get("thread_runtime"),
                public_thread_id=public_thread_id,
            )
        return JSONResponse(content=result)

    @app.get("/v1/chat/threads/{thread_id}/memory/state")
    def get_thread_state(thread_id: str, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, public_thread_id)
        state = active_runtime.get_thread_state(runtime_thread_id)
        return JSONResponse(content=_with_public_thread_state(state, public_thread_id=public_thread_id))

    @app.post("/v1/chat/threads/{thread_id}/memory/mode")
    def set_thread_mode(thread_id: str, body: ThreadMemoryModeRequest, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, public_thread_id)
        mode = _normalize_memory_mode(body.mode, fallback="manual")
        thread_lock = _get_thread_lock(runtime_thread_id)
        with thread_lock:
            result = active_runtime.set_thread_mode(
                runtime_thread_id,
                mode=mode,
                discard_pending=bool(body.discard_pending),
            )
        result = deepcopy(result)
        result["thread_id"] = public_thread_id
        if isinstance(result.get("thread_state"), dict):
            result["thread_state"] = _with_public_thread_state(
                result.get("thread_state"),
                public_thread_id=public_thread_id,
            )
        return JSONResponse(content=result)

    @app.post("/v1/chat/threads/{thread_id}/memory/flush")
    def flush_thread(thread_id: str, body: ThreadMemoryFlushRequest, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, public_thread_id)
        reason = str(body.reason or "manual_api").strip() or "manual_api"
        result = active_runtime.flush_thread(runtime_thread_id, reason=reason)
        result = deepcopy(result)
        result["thread_id"] = public_thread_id
        if isinstance(result.get("thread_state"), dict):
            result["thread_state"] = _with_public_thread_state(
                result.get("thread_state"),
                public_thread_id=public_thread_id,
            )
        status_code = 409 if result.get("status") == "busy" else 200
        return JSONResponse(status_code=status_code, content=result)

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
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
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
                _serialize_schedule_item(
                    schedule_agent,
                    item,
                    owner_id=owner_id,
                    public_thread_id=public_thread_id if user is not None else None,
                )
                for item in items
            ]
        except ValueError as exc:
            return _error_response(status_code=400, message=str(exc))
        except Exception as exc:
            return _error_response(status_code=500, message=f"failed to list schedules: {exc}")
        return JSONResponse(
            content={
                "thread_id": public_thread_id,
                "scope": "owner",
                "owner_id": owner_id,
                "count": len(serialized),
                "include_completed": bool(include_completed),
                "keyword": str(keyword or "").strip(),
                "statuses": parsed_statuses or [],
                "items": serialized,
                "heartbeat": dict(schedule_heartbeat.health_payload()),
            }
        )

    @app.get("/v1/chat/threads/{thread_id}/schedules/heartbeat")
    def get_schedule_heartbeat(thread_id: str, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, public_thread_id)
        return JSONResponse(
            content=_serialize_schedule_heartbeat(
                public_thread_id,
                runtime_thread_id=runtime_thread_id,
            )
        )

    @app.get("/v1/chat/threads/{thread_id}/schedules/{schedule_id}")
    def get_thread_schedule(schedule_id: str, thread_id: str, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
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
                "thread_id": public_thread_id,
                "item": _serialize_schedule_item(
                    schedule_agent,
                    item,
                    owner_id=owner_id,
                    public_thread_id=public_thread_id if user is not None else None,
                ),
            }
        )

    @app.post("/v1/chat/threads/{thread_id}/schedules")
    def create_thread_schedule(thread_id: str, body: ScheduleCreateRequest, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, public_thread_id)
        owner_id = _schedule_owner_id(user)
        deferred_objective = str(body.deferred_objective or "").strip()
        legacy_text = str(body.text or "").strip()
        if (
            deferred_objective
            and legacy_text
            and deferred_objective != legacy_text
        ):
            return _error_response(
                status_code=400,
                message="deferred_objective conflicts with legacy text",
            )
        if not (deferred_objective or legacy_text):
            return _error_response(
                status_code=400,
                message="deferred_objective is required",
            )
        try:
            schedule_agent = _resolve_schedule_agent(active_runtime)
            normalized_due = _normalize_schedule_due_at(body.due_at, body.timezone_name)
            item = schedule_agent.service.create_schedule(
                owner_id=owner_id,
                thread_id=runtime_thread_id,
                due_at_utc=normalized_due["due_at_utc"],
                timezone_name=normalized_due["timezone_name"],
                deferred_objective=deferred_objective,
                text=(legacy_text if not deferred_objective else ""),
            )
            serialized = _serialize_schedule_item(
                schedule_agent,
                item,
                owner_id=owner_id,
                public_thread_id=public_thread_id if user is not None else None,
            )
            _THREAD_EVENTS.append_event(
                runtime_thread_id,
                "schedule_created",
                {
                    "thread_id": public_thread_id,
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
                "thread_id": public_thread_id,
                "item": serialized,
            },
        )

    @app.delete("/v1/chat/threads/{thread_id}/schedules/{schedule_id}")
    def cancel_thread_schedule(schedule_id: str, thread_id: str, request: Request) -> JSONResponse:
        user, active_runtime, auth_error = _resolve_user_and_runtime(request)
        if auth_error is not None:
            return auth_error
        public_thread_id = _public_thread_id(user, thread_id) or active_runtime.default_thread_id
        runtime_thread_id = _runtime_thread_id(user, public_thread_id)
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
            )
            serialized = _serialize_schedule_item(
                schedule_agent,
                canceled,
                owner_id=owner_id,
                public_thread_id=public_thread_id if user is not None else None,
            )
            event_thread_id = (
                runtime_thread_id
                if user is not None
                else str(getattr(canceled, "thread_id", "") or "").strip() or runtime_thread_id
            )
            _THREAD_EVENTS.append_event(
                event_thread_id,
                "schedule_canceled",
                {
                    "thread_id": public_thread_id,
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
                "thread_id": public_thread_id,
                "item": serialized,
            }
        )

    return app


def create_handler(
    *,
    service_runtime: ChatServiceRuntime,
    user_access: Optional[UserAccessService] = None,
    schedule_beat_seconds: int = 10,
) -> FastAPI:
    """Backward-compatible alias for the old stdlib server entrypoint."""
    return create_app(
        service_runtime=service_runtime,
        user_access=user_access,
        schedule_beat_seconds=schedule_beat_seconds,
    )
