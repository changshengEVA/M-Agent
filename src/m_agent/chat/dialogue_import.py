"""Import / migrate on-disk dialogue JSON into the chat-api user tree + RAG index."""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from m_agent.chat.dialogue_validation import (
    parse_dialogue_json_bytes,
    safe_upload_basename,
    validate_dialogue_payload,
)
from m_agent.paths import (
    chat_user_dialogues_dir,
    chat_user_episodic_rag_paths,
    chat_user_persistence_root,
    memory_root_dir,
)

logger = logging.getLogger(__name__)


def legacy_user_memory_root(user_name: str) -> Path:
    """Pre-2026 layout: ``data/memory/user_<username>/``."""
    return memory_root_dir() / f"user_{str(user_name or '').strip()}"


def legacy_user_dialogues_dir(user_name: str) -> Path:
    return legacy_user_memory_root(user_name) / "dialogues"


def turns_to_rounds(
    turns: Sequence[Dict[str, Any]],
    *,
    user_speaker: str,
    assistant_speaker: str,
) -> List[Dict[str, Any]]:
    """Pair user/assistant turns into persist_dialogue round dicts."""
    user_names = {str(user_speaker or "").strip(), "User", "user"}
    assistant_names = {str(assistant_speaker or "").strip(), "Memory Assistant", "Assistant", "assistant"}
    rounds: List[Dict[str, Any]] = []
    pending_user: Optional[Dict[str, Any]] = None

    for item in turns:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker", "") or "").strip()
        text = str(item.get("text", "") or "").strip()
        if not text:
            continue
        entry_type = str(item.get("entry_type", "") or "").strip().lower()
        actor = str(item.get("actor", "") or "").strip().lower()
        ts_raw = item.get("timestamp") or item.get("occurred_at")
        ts = None
        if isinstance(ts_raw, str) and ts_raw.strip():
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except Exception:
                ts = None

        is_user = entry_type == "utterance" or actor == "user" or (
            speaker in user_names and entry_type not in {"reply", "action", "thought"}
        )
        is_assistant = entry_type == "reply" or actor == "assistant" or speaker in assistant_names

        if is_user and not is_assistant:
            pending_user = {"text": text, "timestamp": ts, "turn": dict(item)}
            continue
        if is_assistant and pending_user is not None:
            user_at = pending_user.get("timestamp") or _utc_now()
            assistant_at = ts or user_at
            rounds.append(
                {
                    "user_message": str(pending_user.get("text", "") or ""),
                    "assistant_message": text,
                    "user_at": user_at,
                    "assistant_at": assistant_at,
                    "user_turn": pending_user.get("turn"),
                    "assistant_turn": dict(item),
                }
            )
            pending_user = None
            continue
        if speaker in user_names or (pending_user is None and speaker not in assistant_names):
            pending_user = {"text": text, "timestamp": ts, "turn": dict(item)}

    return rounds


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_dialogue(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except Exception:
        logger.exception("Failed to read dialogue file: %s", path)
        return None
    payload, errors = parse_dialogue_json_bytes(raw, filename=path.name)
    if errors:
        logger.warning("Dialogue validation failed for %s: %s", path, errors)
        return None
    return payload


def _import_single_dialogue_payload(
    *,
    backend: Any,
    dialogues_dir: Path,
    payload: Dict[str, Any],
    user_name: str,
    assistant_name: str,
    copy_to_user_dir: bool,
    index_rag: bool,
    source_label: str,
    source_file: str,
) -> Dict[str, Any]:
    dialogue_id = str(payload.get("dialogue_id", "") or "").strip()
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    thread_id = str(meta.get("thread_id", "") or "").strip() or "imported-thread"
    participants = payload.get("participants") if isinstance(payload.get("participants"), list) else []
    user_speaker = str(payload.get("user_id", "") or user_name)
    assistant_speaker = assistant_name
    if len(participants) >= 2:
        user_speaker = str(participants[0] or user_speaker)
        assistant_speaker = str(participants[1] or assistant_speaker)

    dest_path: Optional[Path] = None
    if copy_to_user_dir and dialogue_id:
        dest_path = _target_dialogue_path(
            dialogues_dir,
            dialogue_id,
            str(meta.get("start_time", "") or ""),
        )
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        migrated = dict(payload)
        migrated_meta = migrated.setdefault("meta", {})
        migrated_meta["import_source"] = source_label
        migrated_meta["imported_from"] = source_file
        with open(dest_path, "w", encoding="utf-8") as handle:
            json.dump(migrated, handle, ensure_ascii=False, indent=2)

    turns = payload.get("turns") if isinstance(payload.get("turns"), list) else []
    rounds = turns_to_rounds(turns, user_speaker=user_speaker, assistant_speaker=assistant_speaker)
    rag_result: Dict[str, Any] = {"skipped": True}
    if index_rag and rounds:
        rag_result = backend.persist_dialogue(
            thread_id=thread_id,
            rounds=rounds,
            reason=source_label,
            source=source_label,
        )

    return {
        "dialogue_id": dialogue_id,
        "thread_id": thread_id,
        "source_file": source_file,
        "dest_file": str(dest_path) if dest_path else None,
        "round_count": len(rounds),
        "rag": rag_result,
    }


def _target_dialogue_path(dialogues_dir: Path, dialogue_id: str, start_time: str) -> Path:
    year_month = "unknown"
    try:
        start_dt = datetime.fromisoformat(str(start_time or "").replace("Z", "+00:00"))
        year_month = start_dt.strftime("%Y-%m")
    except Exception:
        pass
    return dialogues_dir / year_month / f"{dialogue_id}.json"


def import_dialogue_files(
    *,
    user_name: str,
    source_paths: Sequence[Path],
    assistant_name: str = "Memory Assistant",
    copy_to_user_dir: bool = True,
    index_rag: bool = True,
    rebuild_rag: bool = False,
    source_label: str = "dialogue_import",
) -> Dict[str, Any]:
    """Copy dialogue JSON files (optional) and append rounds into the user's RAG store."""
    from m_agent.systems.episodic.default.rag_backend import SimpleRagEpisodicBackend

    dialogues_dir = chat_user_dialogues_dir(user_name)
    user_root, workflow_id, index_root = chat_user_episodic_rag_paths(user_name)

    if rebuild_rag and index_root.exists():
        shutil.rmtree(index_root)
        index_root.mkdir(parents=True, exist_ok=True)

    backend = SimpleRagEpisodicBackend(
        storage_dir=str(user_root),
        workflow_id=workflow_id,
        user_name=user_name,
        assistant_name=assistant_name,
    )

    imported: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for src in source_paths:
        path = Path(src)
        if not path.is_file() or path.suffix.lower() != ".json":
            continue
        try:
            raw = path.read_bytes()
        except Exception as exc:
            errors.append({"file": str(path), "error": str(exc)})
            continue
        payload, val_errors = parse_dialogue_json_bytes(raw, filename=path.name)
        if val_errors:
            errors.append({"file": str(path), "errors": val_errors})
            continue
        if payload is None:
            errors.append({"file": str(path), "error": "validation_failed"})
            continue

        try:
            item = _import_single_dialogue_payload(
                backend=backend,
                dialogues_dir=dialogues_dir,
                payload=payload,
                user_name=user_name,
                assistant_name=assistant_name,
                copy_to_user_dir=copy_to_user_dir,
                index_rag=index_rag,
                source_label=source_label,
                source_file=str(path),
            )
            imported.append(item)
        except Exception as exc:
            logger.exception("RAG index failed for %s", path)
            errors.append(
                {
                    "file": str(path),
                    "dialogue_id": str(payload.get("dialogue_id", "") or path.stem),
                    "error": str(exc),
                }
            )

    persistence = backend.describe_persistence() if hasattr(backend, "describe_persistence") else {}
    return {
        "success": len(errors) == 0,
        "user_name": user_name,
        "user_persistence_root": str(chat_user_persistence_root(user_name)),
        "dialogues_dir": str(dialogues_dir),
        "imported_count": len(imported),
        "error_count": len(errors),
        "imported": imported,
        "errors": errors,
        "episodic": persistence,
    }


def import_uploaded_dialogues_stream(
    *,
    user_name: str,
    uploads: Sequence[Tuple[str, bytes]],
    assistant_name: str = "Memory Assistant",
    index_rag: bool = True,
    rebuild_rag: bool = False,
    source_label: str = "chat_api_dialogue_upload",
) -> Iterator[Dict[str, Any]]:
    """Validate uploads, persist each dialogue, yield progress events for SSE."""
    from m_agent.systems.episodic.default.rag_backend import SimpleRagEpisodicBackend

    dialogues_dir = chat_user_dialogues_dir(user_name)
    user_root, workflow_id, index_root = chat_user_episodic_rag_paths(user_name)

    if rebuild_rag and index_root.exists():
        shutil.rmtree(index_root)
        index_root.mkdir(parents=True, exist_ok=True)

    backend = SimpleRagEpisodicBackend(
        storage_dir=str(user_root),
        workflow_id=workflow_id,
        user_name=user_name,
        assistant_name=assistant_name,
    )

    accepted: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    rejected: List[Dict[str, Any]] = []

    for raw_name, data in uploads:
        filename = safe_upload_basename(raw_name)
        if not filename.lower().endswith(".json"):
            rejected.append(
                {
                    "filename": filename,
                    "errors": [{"code": "invalid_extension", "message": "only .json dialogue files are supported"}],
                }
            )
            continue
        payload, errors = parse_dialogue_json_bytes(data, filename=filename)
        if errors or payload is None:
            rejected.append({"filename": filename, "errors": errors or [{"code": "validation_failed", "message": "invalid"}]})
            continue
        ok, summary, val_errors = validate_dialogue_payload(payload, source_name=filename)
        if not ok:
            rejected.append({"filename": filename, "errors": val_errors})
            continue
        if not str(payload.get("dialogue_id", "") or "").strip():
            payload = dict(payload)
            payload["dialogue_id"] = summary["dialogue_id"]
        accepted.append((filename, payload, summary))

    total = len(accepted)
    yield {
        "type": "upload_started",
        "payload": {
            "total": total,
            "rejected_count": len(rejected),
            "rejected": rejected,
        },
    }

    imported: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for index, (filename, payload, summary) in enumerate(accepted, start=1):
        dialogue_id = str(summary.get("dialogue_id", "") or payload.get("dialogue_id", "")).strip()
        yield {
            "type": "upload_progress",
            "payload": {
                "phase": "indexing",
                "current": index,
                "total": total,
                "dialogue_id": dialogue_id,
                "filename": filename,
                "status": "running",
            },
        }
        try:
            item = _import_single_dialogue_payload(
                backend=backend,
                dialogues_dir=dialogues_dir,
                payload=payload,
                user_name=user_name,
                assistant_name=assistant_name,
                copy_to_user_dir=True,
                index_rag=index_rag,
                source_label=source_label,
                source_file=filename,
            )
            imported.append(item)
            yield {
                "type": "upload_progress",
                "payload": {
                    "phase": "indexing",
                    "current": index,
                    "total": total,
                    "dialogue_id": dialogue_id,
                    "filename": filename,
                    "status": "ok",
                    "round_count": item.get("round_count", 0),
                },
            }
        except Exception as exc:
            logger.exception("Upload import failed for %s", filename)
            errors.append({"filename": filename, "dialogue_id": dialogue_id, "error": str(exc)})
            yield {
                "type": "upload_progress",
                "payload": {
                    "phase": "indexing",
                    "current": index,
                    "total": total,
                    "dialogue_id": dialogue_id,
                    "filename": filename,
                    "status": "error",
                    "message": str(exc),
                },
            }

    persistence = backend.describe_persistence() if hasattr(backend, "describe_persistence") else {}
    yield {
        "type": "upload_completed",
        "payload": {
            "success": len(errors) == 0,
            "user_name": user_name,
            "imported_count": len(imported),
            "error_count": len(errors) + len(rejected),
            "rejected": rejected,
            "imported": imported,
            "errors": errors,
            "episodic": persistence,
        },
    }


def migrate_legacy_user_dialogues(
    user_name: str,
    *,
    assistant_name: str = "Memory Assistant",
    index_rag: bool = True,
    rebuild_rag: bool = False,
) -> Dict[str, Any]:
    """Copy ``data/memory/user_<name>/dialogues/**/*.json`` → ``chat-api/<slug>/dialogues/``."""
    legacy_dir = legacy_user_dialogues_dir(user_name)
    if not legacy_dir.exists():
        return {
            "success": False,
            "error": f"legacy dialogues directory not found: {legacy_dir}",
            "legacy_dir": str(legacy_dir),
        }

    sources = sorted(legacy_dir.rglob("*.json"))
    if not sources:
        return {
            "success": False,
            "error": "no dialogue json files under legacy directory",
            "legacy_dir": str(legacy_dir),
        }

    return import_dialogue_files(
        user_name=user_name,
        source_paths=sources,
        assistant_name=assistant_name,
        copy_to_user_dir=True,
        index_rag=index_rag,
        rebuild_rag=rebuild_rag,
        source_label="migrated_from_user_memory_dir",
    )


__all__ = [
    "import_dialogue_files",
    "import_uploaded_dialogues_stream",
    "legacy_user_dialogues_dir",
    "legacy_user_memory_root",
    "migrate_legacy_user_dialogues",
    "turns_to_rounds",
]
