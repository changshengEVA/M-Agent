from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _runtime_prefix(username: Optional[str]) -> str:
    user = str(username or "").strip()
    return f"{user}::" if user else ""


def _to_public_thread_id(stored_thread_id: str, *, username: Optional[str]) -> str:
    value = str(stored_thread_id or "").strip()
    prefix = _runtime_prefix(username)
    if prefix and value.startswith(prefix):
        public = value[len(prefix) :].strip()
        return public or value
    return value


def _is_visible_to_user(stored_thread_id: str, *, username: Optional[str]) -> bool:
    value = str(stored_thread_id or "").strip()
    if not value:
        return True
    if "::" not in value:
        return True
    prefix = _runtime_prefix(username)
    if not prefix:
        return True
    return value.startswith(prefix)


def _normalize_turns(raw_turns: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_turns, list):
        return []
    turns: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_turns):
        if not isinstance(item, dict):
            continue
        turns.append(
            {
                "turn_id": int(item.get("turn_id", idx)),
                "speaker": str(item.get("speaker", "") or "").strip(),
                "text": str(item.get("text", "") or ""),
                "timestamp": str(item.get("timestamp", "") or "").strip() or None,
            }
        )
    return turns


def _dialogue_summary(
    *,
    path: Path,
    payload: Dict[str, Any],
    username: Optional[str],
) -> Optional[Dict[str, Any]]:
    dialogue_id = str(payload.get("dialogue_id", "") or "").strip() or path.stem
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    stored_thread_id = str(meta.get("thread_id", "") or "").strip()
    if not _is_visible_to_user(stored_thread_id, username=username):
        return None

    turns = _normalize_turns(payload.get("turns"))
    round_count_raw = meta.get("round_count")
    round_count = int(round_count_raw) if isinstance(round_count_raw, (int, float)) else max(len(turns) // 2, 0)
    start_time = str(meta.get("start_time", "") or "").strip() or None
    end_time = str(meta.get("end_time", "") or "").strip() or None
    source = str(meta.get("source", "") or "").strip() or None

    preview = ""
    for item in turns:
        if str(item.get("speaker", "")).strip():
            preview = str(item.get("text", "") or "").strip()
            if preview:
                break

    sort_dt = _parse_iso(start_time)
    if sort_dt is None:
        try:
            sort_dt = datetime.fromtimestamp(path.stat().st_mtime)
        except Exception:
            sort_dt = datetime.min

    return {
        "dialogue_id": dialogue_id,
        "thread_id": _to_public_thread_id(stored_thread_id, username=username),
        "start_time": start_time,
        "end_time": end_time,
        "source": source,
        "round_count": round_count,
        "turn_count": len(turns),
        "preview": preview,
        "dialogue_file": str(path),
        "_sort_key": sort_dt,
    }


def _find_dialogue_file(dialogues_dir: Path, *, dialogue_id: str) -> Optional[Path]:
    if not dialogues_dir.exists() or not dialogues_dir.is_dir():
        return None

    sanitized = str(dialogue_id or "").strip()
    if not sanitized:
        return None

    direct = dialogues_dir / f"{sanitized}.json"
    if direct.exists():
        return direct

    matches = sorted(dialogues_dir.rglob(f"{sanitized}.json"))
    if matches:
        return matches[0]

    for file_path in dialogues_dir.rglob("*.json"):
        payload = _load_json(file_path)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("dialogue_id", "") or "").strip() == sanitized:
            return file_path
    return None


def list_dialogues(
    *,
    dialogues_dir: Path,
    username: Optional[str] = None,
    internal_thread_id: Optional[str] = None,
    limit: int = 30,
    offset: int = 0,
) -> Dict[str, Any]:
    safe_limit = max(1, min(200, int(limit)))
    safe_offset = max(0, int(offset))
    expected_internal_thread_id = str(internal_thread_id or "").strip() or None

    summaries: List[Dict[str, Any]] = []
    if dialogues_dir.exists() and dialogues_dir.is_dir():
        for file_path in dialogues_dir.rglob("*.json"):
            payload = _load_json(file_path)
            if not isinstance(payload, dict):
                continue
            summary = _dialogue_summary(path=file_path, payload=payload, username=username)
            if summary is None:
                continue
            if expected_internal_thread_id:
                payload_meta = payload.get("meta")
                if not isinstance(payload_meta, dict):
                    continue
                stored_thread_id = str(payload_meta.get("thread_id", "") or "").strip()
                if stored_thread_id != expected_internal_thread_id:
                    continue
            summaries.append(summary)

    summaries.sort(
        key=lambda item: (
            item.get("_sort_key", datetime.min),
            str(item.get("dialogue_id", "")),
        ),
        reverse=True,
    )
    for item in summaries:
        item.pop("_sort_key", None)

    total = len(summaries)
    items = summaries[safe_offset : safe_offset + safe_limit]
    next_offset = safe_offset + len(items)
    has_more = next_offset < total

    return {
        "items": items,
        "offset": safe_offset,
        "limit": safe_limit,
        "next_offset": next_offset if has_more else None,
        "has_more": has_more,
        "total": total,
    }


def get_dialogue_detail(
    *,
    dialogues_dir: Path,
    dialogue_id: str,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    target = _find_dialogue_file(dialogues_dir, dialogue_id=str(dialogue_id or "").strip())
    if target is None:
        raise FileNotFoundError(f"dialogue not found: {dialogue_id}")

    payload = _load_json(target)
    if not isinstance(payload, dict):
        raise FileNotFoundError(f"dialogue payload invalid: {dialogue_id}")

    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    stored_thread_id = str(meta.get("thread_id", "") or "").strip()
    if not _is_visible_to_user(stored_thread_id, username=username):
        raise FileNotFoundError(f"dialogue not found: {dialogue_id}")

    turns = _normalize_turns(payload.get("turns"))
    round_count_raw = meta.get("round_count")
    round_count = int(round_count_raw) if isinstance(round_count_raw, (int, float)) else max(len(turns) // 2, 0)
    dialogue_key = str(payload.get("dialogue_id", "") or "").strip() or str(dialogue_id or "").strip()

    return {
        "dialogue_id": dialogue_key,
        "thread_id": _to_public_thread_id(stored_thread_id, username=username),
        "thread_id_internal": stored_thread_id or None,
        "user_id": str(payload.get("user_id", "") or "").strip() or None,
        "participants": payload.get("participants", []) if isinstance(payload.get("participants"), list) else [],
        "meta": meta,
        "turns": turns,
        "round_count": round_count,
        "turn_count": len(turns),
        "dialogue_file": str(target),
    }
