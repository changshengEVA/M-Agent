"""Persist flushed chat rounds as dialogue JSON under ``dialogues/``.

Used by :class:`~m_agent.chat.three_layer_chat_agent.ThreeLayerChatAgent` on
thread flush and by ``GET /v1/chat/dialogues``. Does not depend on
``m_agent.memory`` or MemoryCore import pipelines.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_slug(text: str, fallback: str = "chat") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(text or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    return cleaned[:48] or fallback


def _truncate_text(text: str, limit: int = 96) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _parse_utc_timestamp(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc)
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return None
    return None


def _parse_ts_from_turn(turn: Dict[str, Any]) -> Optional[datetime]:
    if not isinstance(turn, dict):
        return None
    return _parse_utc_timestamp(turn.get("timestamp") or turn.get("occurred_at"))


def normalize_dialogue_rounds(rounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(rounds, list):
        return normalized

    for item in rounds:
        if not isinstance(item, dict):
            continue
        user_message = str(item.get("user_message", "") or "").strip()
        assistant_message = str(item.get("assistant_message", "") or "").strip()
        if not user_message or not assistant_message:
            continue

        user_at = item.get("user_at")
        user_dt = user_at if isinstance(user_at, datetime) else _utc_now()
        user_turn_obj = item.get("user_turn") if isinstance(item.get("user_turn"), dict) else {}
        ts_from_turn = _parse_ts_from_turn(user_turn_obj)
        if ts_from_turn is not None:
            user_dt = ts_from_turn

        assistant_at = item.get("assistant_at")
        if isinstance(assistant_at, datetime):
            assistant_dt = assistant_at
        else:
            assistant_dt = user_dt + timedelta(seconds=1)
        assistant_turn_obj = item.get("assistant_turn") if isinstance(item.get("assistant_turn"), dict) else {}
        ts_assistant = _parse_ts_from_turn(assistant_turn_obj)
        if ts_assistant is not None:
            assistant_dt = ts_assistant

        if assistant_dt < user_dt:
            assistant_dt = user_dt

        normalized.append(
            {
                "user_message": user_message,
                "assistant_message": assistant_message,
                "user_turn": dict(item.get("user_turn")) if isinstance(item.get("user_turn"), dict) else None,
                "assistant_turn": dict(item.get("assistant_turn"))
                if isinstance(item.get("assistant_turn"), dict)
                else None,
                "user_at": user_dt,
                "assistant_at": assistant_dt,
                "agent_result": item.get("agent_result") if isinstance(item.get("agent_result"), dict) else None,
            }
        )
    return normalized


def build_dialogue_id(*, thread_id: str, created_at: datetime) -> str:
    time_key = created_at.strftime("%Y%m%d_%H%M%S_%f")
    safe_thread = _safe_slug(thread_id or "thread", fallback="thread")
    return f"chat_{safe_thread}_{time_key}"


def build_trace_summary(rounds: List[Dict[str, Any]]) -> Dict[str, Any]:
    tool_call_count = 0
    recall_modes: List[str] = []
    for item in rounds:
        agent_result = item.get("agent_result")
        if not isinstance(agent_result, dict):
            continue
        if isinstance(agent_result.get("tool_call_count"), int):
            tool_call_count += int(agent_result.get("tool_call_count", 0) or 0)
        recall_mode = str(agent_result.get("recall_mode", "") or "").strip()
        if recall_mode and recall_mode not in recall_modes:
            recall_modes.append(recall_mode)
    return {
        "tool_call_count": tool_call_count,
        "recall_modes": recall_modes,
    }


def scene_entry_to_dialogue_turn(
    entry: Any,
    *,
    user_name: str,
    assistant_name: str,
) -> Optional[Dict[str, Any]]:
    """Map a user utterance or assistant reply Scene entry to a v1 dialogue turn."""
    if hasattr(entry, "to_dict"):
        data = entry.to_dict()
    elif isinstance(entry, dict):
        data = dict(entry)
    else:
        data = {}

    actor = str(data.get("actor", "") or "").strip().lower()
    entry_type = str(data.get("entry_type", "") or "").strip().lower()
    text = str(data.get("text", "") or "").strip()
    if not text:
        return None

    is_user = entry_type == "utterance" or actor == "user"
    is_assistant = entry_type == "reply" or actor == "assistant"
    if is_user and not is_assistant:
        speaker = user_name
    elif is_assistant and not is_user:
        speaker = assistant_name
    else:
        return None

    ts_raw = data.get("occurred_at") or data.get("timestamp")
    ts = _parse_utc_timestamp(ts_raw)
    return {
        "speaker": speaker,
        "text": text,
        "timestamp": _to_utc_iso(ts) if ts is not None else str(ts_raw or ""),
    }


def build_dialogue_payload_from_scene_entries(
    *,
    dialogue_id: str,
    thread_id: str,
    entries: List[Any],
    source: str,
    user_name: str,
    assistant_name: str,
    trace_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build v1 dialogue JSON from Scene: user/reply only, chronological, real timestamps."""
    from m_agent.chat.dialogue_import import turns_to_rounds

    turns: List[Dict[str, Any]] = []
    for entry in entries:
        turn = scene_entry_to_dialogue_turn(
            entry,
            user_name=user_name,
            assistant_name=assistant_name,
        )
        if turn is None:
            continue
        turn["turn_id"] = len(turns)
        turns.append(turn)

    if not turns:
        raise ValueError("scene entries produced no user/assistant dialogue turns")

    start_ts = _parse_utc_timestamp(turns[0].get("timestamp")) or _utc_now()
    end_ts = _parse_utc_timestamp(turns[-1].get("timestamp")) or start_ts
    derived_rounds = turns_to_rounds(turns, user_speaker=user_name, assistant_speaker=assistant_name)
    trace_summary = build_trace_summary(derived_rounds) if derived_rounds else {}

    return {
        "dialogue_id": dialogue_id,
        "user_id": user_name,
        "participants": [user_name, assistant_name],
        "meta": {
            "start_time": _to_utc_iso(start_ts),
            "end_time": _to_utc_iso(end_ts),
            "language": "zh",
            "platform": "chat_api",
            "version": 1,
            "thread_id": str(thread_id or "").strip(),
            "source": str(source or "chat_api_thread_flush"),
            "round_count": len(derived_rounds),
            "trace_summary": trace_summary or {},
        },
        "turns": turns,
    }


def build_dialogue_payload(
    *,
    dialogue_id: str,
    thread_id: str,
    rounds: List[Dict[str, Any]],
    source: str,
    user_name: str,
    assistant_name: str,
) -> Dict[str, Any]:
    first_round = rounds[0]
    start_at = first_round["user_at"]
    end_at = rounds[-1]["assistant_at"]
    trace_summary = build_trace_summary(rounds)
    turns: List[Dict[str, Any]] = []
    turn_id = 0
    for round_item in rounds:
        user_turn = dict(round_item.get("user_turn")) if isinstance(round_item.get("user_turn"), dict) else {}
        if not user_turn:
            user_turn = {
                "speaker": user_name,
                "text": round_item["user_message"],
            }
        user_turn["turn_id"] = turn_id
        user_turn["speaker"] = str(user_turn.get("speaker", user_name) or user_name)
        user_turn["text"] = str(user_turn.get("text", round_item["user_message"]) or round_item["user_message"])
        user_turn["timestamp"] = _to_utc_iso(round_item["user_at"])
        turns.append(user_turn)
        turn_id += 1

        assistant_turn = (
            dict(round_item.get("assistant_turn")) if isinstance(round_item.get("assistant_turn"), dict) else {}
        )
        if not assistant_turn:
            assistant_turn = {
                "speaker": assistant_name,
                "text": round_item["assistant_message"],
            }
        assistant_turn["turn_id"] = turn_id
        assistant_turn["speaker"] = str(assistant_turn.get("speaker", assistant_name) or assistant_name)
        assistant_turn["text"] = str(
            assistant_turn.get("text", round_item["assistant_message"]) or round_item["assistant_message"]
        )
        assistant_turn["timestamp"] = _to_utc_iso(round_item["assistant_at"])
        turns.append(assistant_turn)
        turn_id += 1

    return {
        "dialogue_id": dialogue_id,
        "user_id": user_name,
        "participants": [user_name, assistant_name],
        "meta": {
            "start_time": _to_utc_iso(start_at),
            "end_time": _to_utc_iso(end_at),
            "language": "zh",
            "platform": "chat_api",
            "version": 1,
            "thread_id": str(thread_id or "").strip(),
            "source": str(source or "chat_api_thread_flush"),
            "round_count": len(rounds),
            "trace_summary": trace_summary,
        },
        "turns": turns,
    }


def dialogue_file_path(dialogues_dir: Path, dialogue_payload: Dict[str, Any]) -> Path:
    meta = dialogue_payload.get("meta", {})
    start_time = str(meta.get("start_time", "") or "")
    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        year_month = start_dt.strftime("%Y-%m")
    except Exception:
        year_month = "unknown"
    return dialogues_dir / year_month / f"{dialogue_payload['dialogue_id']}.json"


class ChatDialogueArchive:
    """Write flushed dialogue JSON under ``dialogues/`` for listing and audit."""

    def __init__(
        self,
        *,
        dialogues_dir: Path,
        user_name: str = "User",
        assistant_name: str = "Memory Assistant",
        workflow_id: str = "chat-api",
    ) -> None:
        self.dialogues_dir = Path(dialogues_dir)
        self.dialogues_dir.mkdir(parents=True, exist_ok=True)
        self.user_name = str(user_name or "User")
        self.assistant_name = str(assistant_name or "Memory Assistant")
        self.workflow_id = str(workflow_id or "chat-api").strip() or "chat-api"
        self._lock = threading.Lock()

    def persist_round(
        self,
        *,
        thread_id: str,
        user_message: str,
        assistant_message: str,
        agent_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.persist_dialogue(
            thread_id=thread_id,
            rounds=[
                {
                    "user_message": user_message,
                    "assistant_message": assistant_message,
                    "agent_result": agent_result,
                }
            ],
            reason="chat_round_memory",
            source="three_layer_chat_agent",
        )

    def persist_dialogue(
        self,
        *,
        thread_id: str,
        rounds: List[Dict[str, Any]],
        reason: str = "chat_thread_flush",
        source: str = "chat_api_thread_flush",
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        _ = reason
        normalized_rounds = normalize_dialogue_rounds(rounds)
        if not normalized_rounds:
            return {
                "success": False,
                "workflow_id": self.workflow_id,
                "error": "rounds are empty",
            }

        with self._lock:
            created_at = normalized_rounds[0]["user_at"]
            dialogue_id = build_dialogue_id(thread_id=thread_id, created_at=created_at)
            dialogue_payload = build_dialogue_payload(
                dialogue_id=dialogue_id,
                thread_id=thread_id,
                rounds=normalized_rounds,
                source=source,
                user_name=self.user_name,
                assistant_name=self.assistant_name,
            )
            dialogue_file = dialogue_file_path(self.dialogues_dir, dialogue_payload)
            try:
                _write_json(dialogue_file, dialogue_payload)
                if progress_callback is not None:
                    progress_callback(
                        "flush_stage",
                        {
                            "stage": "dialogue_json_written",
                            "status": "completed",
                            "dialogue_file": str(dialogue_file),
                        },
                    )
                return {
                    "success": True,
                    "workflow_id": self.workflow_id,
                    "memory_root": str(self.dialogues_dir.parent),
                    "dialogue_id": dialogue_id,
                    "round_count": len(normalized_rounds),
                    "turn_count": len(dialogue_payload.get("turns", [])),
                    "dialogue_file": str(dialogue_file),
                    "import_result": None,
                    "error": None,
                }
            except Exception as exc:
                logger.exception("Persist dialogue archive failed for dialogue_id=%s", dialogue_id)
                return {
                    "success": False,
                    "workflow_id": self.workflow_id,
                    "dialogue_id": dialogue_id,
                    "error": str(exc),
                }

    def persist_dialogue_payload(
        self,
        *,
        dialogue_payload: Dict[str, Any],
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Write a pre-built dialogue document (system layer canonical artifact)."""
        if not isinstance(dialogue_payload, dict):
            return {
                "success": False,
                "workflow_id": self.workflow_id,
                "error": "dialogue_payload must be a dict",
            }
        dialogue_id = str(dialogue_payload.get("dialogue_id", "") or "").strip()
        if not dialogue_id:
            return {
                "success": False,
                "workflow_id": self.workflow_id,
                "error": "dialogue_id is required",
            }

        with self._lock:
            dialogue_file = dialogue_file_path(self.dialogues_dir, dialogue_payload)
            try:
                _write_json(dialogue_file, dialogue_payload)
                if progress_callback is not None:
                    progress_callback(
                        "flush_stage",
                        {
                            "stage": "dialogue_json_written",
                            "status": "completed",
                            "dialogue_file": str(dialogue_file),
                        },
                    )
                meta = dialogue_payload.get("meta") if isinstance(dialogue_payload.get("meta"), dict) else {}
                return {
                    "success": True,
                    "workflow_id": self.workflow_id,
                    "memory_root": str(self.dialogues_dir.parent),
                    "dialogue_id": dialogue_id,
                    "round_count": int(meta.get("round_count", 0) or 0),
                    "turn_count": len(dialogue_payload.get("turns", []) or []),
                    "dialogue_file": str(dialogue_file),
                    "dialogue_payload": dialogue_payload,
                    "import_result": None,
                    "error": None,
                }
            except Exception as exc:
                logger.exception("Persist dialogue archive failed for dialogue_id=%s", dialogue_id)
                return {
                    "success": False,
                    "workflow_id": self.workflow_id,
                    "dialogue_id": dialogue_id,
                    "error": str(exc),
                }


# Backward-compatible alias for older imports / docs.
ChatMemoryPersistence = ChatDialogueArchive

__all__ = [
    "ChatDialogueArchive",
    "ChatMemoryPersistence",
    "build_dialogue_id",
    "build_dialogue_payload",
    "build_dialogue_payload_from_scene_entries",
    "normalize_dialogue_rounds",
    "scene_entry_to_dialogue_turn",
]
