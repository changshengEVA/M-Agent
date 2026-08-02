"""Shared Chat API product views over registry + Scene (engine-neutral)."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Mapping, Optional

from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS, ThreadRuntimeSnapshot
from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntryType,
    TransactionKind,
    TransactionRecord,
)
from m_agent.runtime.transaction.predicates import (
    is_live_record,
    is_open_continue,
    project_compat_status,
)

logger = logging.getLogger(__name__)


def ensure_scene_thread_loaded(runtime: Any, conversation_id: str) -> None:
    cid = str(conversation_id or "").strip()
    if not cid:
        return
    store = getattr(getattr(runtime, "scene_system", None), "store", None)
    ensure_fn = getattr(store, "ensure_thread_loaded", None)
    if callable(ensure_fn):
        ensure_fn(cid)


def serialize_transaction(
    record: TransactionRecord,
    *,
    active_transaction_id: Optional[str] = None,
    cpu_transaction_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the complete durable record plus UI/runtime projections."""

    txn_id = str(record.transaction_id)
    payload = record.to_dict()
    payload.update(
        {
            "status": project_compat_status(record),
            "deleted": record.deleted,
            "wm_entry_count": len(record.wm_entries),
            "schedule_id": record.correlation.schedule_id,
            "schedule_run_id": record.correlation.schedule_run_id,
            "is_active_user": txn_id == active_transaction_id,
            "is_cpu_holder": bool(
                cpu_transaction_id and txn_id == cpu_transaction_id
            ),
        }
    )
    return payload


def list_transactions(
    runtime: Any,
    thread_id: str,
    *,
    conversation_id: Optional[str] = None,
    include_history: bool = False,
) -> Dict[str, Any]:
    """Per-transaction WM + status snapshot for UI."""

    tid = str(thread_id or "").strip()
    all_records = runtime.registry.list_for_thread(tid)
    cid = str(conversation_id or "").strip() or None
    records = (
        list(all_records)
        if include_history or cid is None
        else [
            record
            for record in all_records
            if record.conversation_id == cid
        ]
    )
    active = next(
        (
            record
            for record in reversed(records)
            if record.kind == TransactionKind.USER_TASK and is_open_continue(record)
        ),
        None,
    )
    active_id = str(active.transaction_id) if active is not None else None
    runtime_snap: ThreadRuntimeSnapshot = THREAD_RUNTIME_STATUS.snapshot(tid)
    raw_cpu_txn = (
        str(runtime_snap.active_transaction_id or "").strip() or None
    )
    visible_transaction_ids = {
        str(record.transaction_id)
        for record in records
        if is_live_record(record)
    }
    cpu_txn = (
        raw_cpu_txn
        if raw_cpu_txn in visible_transaction_ids
        else None
    )
    runtime_phase = str(runtime_snap.runtime_phase or "ready")

    items = sorted(
        [
            serialize_transaction(
                record,
                active_transaction_id=active_id,
                cpu_transaction_id=cpu_txn,
            )
            for record in records
        ],
        key=lambda item: str(item.get("updated_at", "") or ""),
        reverse=True,
    )
    return {
        "thread_id": tid,
        "conversation_id": cid,
        "transactions": items,
        "active_transaction_id": active_id,
        "cpu_transaction_id": cpu_txn,
        "transaction_count": len(items),
        "audit_transaction_count": len(all_records),
        "include_history": bool(include_history),
        "runtime_phase": runtime_phase,
        "effective_depth": int(runtime_snap.effective_depth),
    }


def list_scene(
    runtime: Any,
    thread_id: str,
    *,
    conversation_id: Optional[str] = None,
    limit: int = 40,
    before_seq: Optional[int] = None,
    since_flush: bool = True,
) -> Dict[str, Any]:
    cap = max(1, min(200, int(limit or 40)))
    tid = str(thread_id or "").strip()
    cid = str(conversation_id or "").strip() or f"{tid}::0"
    reader = runtime.scene_system.reader
    if since_flush:
        ensure_scene_thread_loaded(runtime, cid)
        since_fn = getattr(reader, "entries_since_flush", None)
        entries = list(since_fn(cid)) if callable(since_fn) else []
        if before_seq is not None:
            entries = [e for e in entries if e.seq < int(before_seq)]
    else:
        entries = list(reader.tail(cid, limit=cap + 1, before_seq=before_seq))
    has_more = len(entries) > cap
    if has_more:
        entries = entries[-cap:]
    return {
        "thread_id": thread_id,
        "conversation_id": cid,
        "entries": [e.to_dict() for e in entries],
        "has_more": has_more,
        "since_flush": bool(since_flush),
    }


def scene_pending_flush_metrics(
    runtime: Any,
    thread_id: str,
    *,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    from m_agent.chat.chat_memory_persistence import scene_entry_to_dialogue_turn

    tid = str(thread_id or "").strip()
    cid = str(conversation_id or "").strip() or f"{tid}::0"
    empty = {
        "scene_pending_entries": 0,
        "scene_pending_turns": 0,
        "active_user_segment": False,
        "latest_stimulus_at": None,
        "latest_activity_at": None,
        "can_flush": False,
    }
    if not tid:
        return empty
    ensure_scene_thread_loaded(runtime, cid)
    reader = runtime.scene_system.reader
    entries_fn = getattr(reader, "entries_since_flush", None)
    entries: List[Any] = list(entries_fn(cid)) if callable(entries_fn) else []
    agent = getattr(runtime, "agent", None)
    user_name = str(getattr(agent, "user_name", "User") or "User")
    assistant_name = str(
        getattr(agent, "assistant_name", "Memory Assistant") or "Memory Assistant"
    )
    pending_turns = sum(
        1
        for entry in entries
        if scene_entry_to_dialogue_turn(
            entry,
            user_name=user_name,
            assistant_name=assistant_name,
        )
        is not None
    )
    active_user_segment = any(
        record.kind == TransactionKind.USER_TASK and is_open_continue(record)
        for record in runtime.registry.list_for_thread(tid)
        if record.conversation_id == cid
    )
    stimulus_entries = [
        entry
        for entry in entries
        if entry.actor == SceneActor.USER
        or entry.entry_type == SceneEntryType.UTTERANCE
    ]
    visible_entries = [
        entry
        for entry in entries
        if entry.actor in {SceneActor.USER, SceneActor.ASSISTANT}
        or entry.entry_type in {SceneEntryType.UTTERANCE, SceneEntryType.REPLY}
    ]
    return {
        "scene_pending_entries": len(entries),
        "scene_pending_turns": pending_turns,
        "active_user_segment": active_user_segment,
        "latest_stimulus_at": (
            stimulus_entries[-1].occurred_at if stimulus_entries else None
        ),
        "latest_activity_at": (
            visible_entries[-1].occurred_at if visible_entries else None
        ),
        "can_flush": pending_turns > 0 or active_user_segment,
    }


def build_dialogue_flush_payload(
    runtime: Any,
    thread_id: str,
    *,
    conversation_id: Optional[str] = None,
    source: str = "chat_api_thread_flush",
) -> Optional[Dict[str, Any]]:
    tid = str(thread_id or "").strip()
    cid = str(conversation_id or "").strip() or f"{tid}::0"
    if not tid:
        return None
    ensure_scene_thread_loaded(runtime, cid)
    reader = runtime.scene_system.reader
    entries_fn = getattr(reader, "entries_since_flush", None)
    if not callable(entries_fn):
        return None
    entries = list(entries_fn(cid))
    if not entries:
        return None
    dialogue_entries = [
        entry
        for entry in entries
        if entry.actor in {SceneActor.USER, SceneActor.ASSISTANT}
        or entry.entry_type
        in {SceneEntryType.UTTERANCE, SceneEntryType.REPLY}
    ]
    if not dialogue_entries:
        return None

    from m_agent.chat.chat_memory_persistence import (
        build_dialogue_id,
        build_dialogue_payload_from_scene_entries,
    )

    agent = getattr(runtime, "agent", None)
    user_name = str(getattr(agent, "user_name", "User") or "User")
    assistant_name = str(
        getattr(agent, "assistant_name", "Memory Assistant") or "Memory Assistant"
    )
    start_ts = dialogue_entries[0].occurred_at
    from datetime import datetime, timezone

    try:
        created_at = datetime.fromisoformat(
            str(start_ts).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Scene dialogue entry has an invalid occurred_at timestamp"
        ) from exc
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    dialogue_id = build_dialogue_id(thread_id=tid, created_at=created_at)
    return build_dialogue_payload_from_scene_entries(
        dialogue_id=dialogue_id,
        thread_id=tid,
        entries=dialogue_entries,
        source=source,
        user_name=user_name,
        assistant_name=assistant_name,
    )


def mark_scene_flushed(
    runtime: Any,
    conversation_id: str,
    *,
    through_seq: int,
) -> None:
    cid = str(conversation_id or "").strip()
    ensure_scene_thread_loaded(runtime, cid)
    mark_fn = getattr(runtime.scene_system.reader, "mark_flushed", None)
    if callable(mark_fn) and int(through_seq) > 0:
        mark_fn(cid, through_seq=int(through_seq))


def on_flush_segment(
    runtime: Any,
    thread_id: str,
    *,
    conversation_id: Optional[str] = None,
    flush_id: Optional[str] = None,
    through_seq: Optional[int] = None,
    eligible_revisions: Optional[Mapping[str, int]] = None,
    payload: Optional[Dict[str, Any]] = None,
    emit_runtime_updated: Optional[Any] = None,
) -> Dict[str, Any]:
    tid = str(thread_id or "").strip()
    cid = str(conversation_id or "").strip()
    stable_flush_id = (
        str(flush_id or "").strip()
        or f"flush_{uuid.uuid4().hex}"
    )
    coordinator = getattr(runtime, "flush_coordinator", None)
    if cid and coordinator is None:
        raise RuntimeError(
            "runtime flush coordinator is required for an atomic boundary"
        )
    if cid:
        committed = coordinator.trigger(
            conversation_id=cid,
            flush_id=stable_flush_id,
            through_seq=through_seq,
            eligible_revisions=eligible_revisions,
            payload=dict(payload or {}),
        )
        archived_ids = list(
            committed.get("archived_transaction_ids", []) or []
        )
    else:
        archived = (
            runtime.registry.archive_completed_for_flush(cid)
            if cid
            else []
        )
        archived_ids = [record.transaction_id for record in archived]

    if callable(emit_runtime_updated):
        emit_runtime_updated(tid)
    return {
        "thread_id": tid,
        "flush_id": stable_flush_id,
        "completed_transaction_id": None,
        "archived_transaction_ids": archived_ids,
        # Product-level episode materialisation runs after this durable commit
        # in ChatServiceRuntime. It must not be drained inside the engine or a
        # later backend callback would observe an empty buffer.
        "episode_notes_drained": 0,
    }


__all__ = [
    "build_dialogue_flush_payload",
    "ensure_scene_thread_loaded",
    "list_scene",
    "list_transactions",
    "mark_scene_flushed",
    "on_flush_segment",
    "scene_pending_flush_metrics",
]
