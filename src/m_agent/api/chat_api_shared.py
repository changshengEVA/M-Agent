from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import Request

from m_agent.agents.chat_controller_agent import DEFAULT_CHAT_CONFIG_PATH
from m_agent.api.user_access import AuthenticatedUser
from m_agent.paths import resolve_project_path

_THREAD_LOCKS: Dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


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


def _resolve_optional_path(path_text: str) -> Path:
    candidate = Path(str(path_text or "").strip())
    if candidate.is_absolute():
        return candidate.resolve()
    return resolve_project_path(candidate).resolve()


def _extract_access_token(request: Request) -> str:
    auth_header = str(request.headers.get("authorization", "") or "").strip()
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return str(request.headers.get("x-session-token", "") or "").strip()


def _scoped_thread_id(user: AuthenticatedUser, thread_id: str) -> str:
    public_thread_id = str(thread_id or "").strip() or "thread-default"
    return f"{user.username}::{public_thread_id}"


def _with_public_thread_state(state: Any, *, public_thread_id: str) -> Any:
    if not isinstance(state, dict):
        return state
    payload = deepcopy(state)
    payload["thread_id"] = public_thread_id
    return payload


def _with_public_result_thread_id(result: Dict[str, Any], *, public_thread_id: str) -> Dict[str, Any]:
    payload = deepcopy(result)
    payload["thread_id"] = public_thread_id
    if isinstance(payload.get("thread_state"), dict):
        payload["thread_state"] = _with_public_thread_state(payload.get("thread_state"), public_thread_id=public_thread_id)
    return payload


def _with_public_thread_event(event: Dict[str, Any], *, public_thread_id: str) -> Dict[str, Any]:
    payload = deepcopy(event)
    payload["thread_id"] = public_thread_id
    event_payload = payload.get("payload")
    if isinstance(event_payload, dict):
        if "thread_id" in event_payload:
            event_payload["thread_id"] = public_thread_id
        if isinstance(event_payload.get("thread_state"), dict):
            event_payload["thread_state"] = _with_public_thread_state(
                event_payload.get("thread_state"),
                public_thread_id=public_thread_id,
            )
        payload["payload"] = event_payload
    return payload


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
    parts: list[str] = []
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
