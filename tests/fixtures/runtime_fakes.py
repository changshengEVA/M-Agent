from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any, Dict, Iterator, Optional, Sequence, Tuple
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_turn_payload(
    turn: Optional[Dict[str, Any]],
    *,
    speaker: str,
    text: str,
) -> Dict[str, Any]:
    payload = dict(turn) if isinstance(turn, dict) else {}
    normalized: Dict[str, Any] = {
        "speaker": _normalize_text(payload.get("speaker")) or speaker,
        "text": _normalize_text(payload.get("text")) or _normalize_text(text),
    }
    for field_name in ("blip_caption", "img_url", "img_file", "upload_id", "mime_type"):
        value = payload.get(field_name)
        if isinstance(value, str) and value.strip():
            normalized[field_name] = value.strip()
    if isinstance(payload.get("width"), int):
        normalized["width"] = int(payload["width"])
    if isinstance(payload.get("height"), int):
        normalized["height"] = int(payload["height"])
    return normalized


@dataclass
class FakeScheduleItem:
    schedule_id: str
    owner_id: str
    thread_id: str
    title: str
    due_at_utc: str
    timezone_name: str
    original_time_text: str
    action_type: str
    action_payload: Dict[str, Any]
    source_text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"


class FakeScheduleStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], FakeScheduleItem] = {}

    def upsert(self, item: FakeScheduleItem) -> FakeScheduleItem:
        self._items[(item.owner_id, item.schedule_id)] = item
        return item

    def find_by_id(self, schedule_id: str, *, owner_id: str) -> Optional[FakeScheduleItem]:
        return self._items.get((owner_id, schedule_id))

    def iter_owner_items(self, owner_id: str) -> list[FakeScheduleItem]:
        return [item for (item_owner_id, _), item in self._items.items() if item_owner_id == owner_id]


class FakeScheduleService:
    def __init__(self, store: FakeScheduleStore) -> None:
        self.store = store

    @staticmethod
    def serialize_item(item: FakeScheduleItem) -> Dict[str, Any]:
        return {
            "schedule_id": item.schedule_id,
            "owner_id": item.owner_id,
            "thread_id": item.thread_id,
            "title": item.title,
            "status": item.status,
            "due_at_utc": item.due_at_utc,
            "timezone_name": item.timezone_name,
            "original_time_text": item.original_time_text,
            "due_display": item.original_time_text,
            "source_text": item.source_text,
            "action_type": item.action_type,
            "action_payload": deepcopy(item.action_payload),
            "metadata": deepcopy(item.metadata),
        }

    def list_schedules(
        self,
        *,
        owner_id: str,
        thread_id: str | None,
        statuses: list[str] | None,
        keyword: str,
        include_completed: bool,
        limit: int,
    ) -> list[FakeScheduleItem]:
        normalized_keyword = str(keyword or "").strip().lower()
        status_filter = {str(value).strip().lower() for value in (statuses or []) if str(value).strip()}
        items = self.store.iter_owner_items(owner_id)
        filtered: list[FakeScheduleItem] = []
        for item in items:
            if thread_id and item.thread_id != thread_id:
                continue
            if not include_completed and item.status in {"done", "failed", "canceled"}:
                continue
            if status_filter and item.status.lower() not in status_filter:
                continue
            if normalized_keyword and normalized_keyword not in item.title.lower():
                continue
            filtered.append(item)
        return filtered[: max(1, int(limit or 20))]

    def create_schedule(
        self,
        *,
        owner_id: str,
        thread_id: str,
        title: str,
        due_at_utc: str,
        timezone_name: str,
        original_time_text: str,
        action_type: str,
        action_payload: Dict[str, Any],
        source_text: str,
        metadata: Dict[str, Any] | None = None,
    ) -> FakeScheduleItem:
        item = FakeScheduleItem(
            schedule_id=f"sch_{uuid4().hex[:10]}",
            owner_id=owner_id,
            thread_id=thread_id,
            title=title,
            due_at_utc=due_at_utc,
            timezone_name=timezone_name,
            original_time_text=original_time_text,
            action_type=action_type,
            action_payload=deepcopy(action_payload),
            source_text=source_text,
            metadata=deepcopy(metadata or {}),
            status="pending",
        )
        return self.store.upsert(item)

    def update_schedule(
        self,
        *,
        owner_id: str,
        thread_id: str | None,
        schedule_id: str,
        title: str | None,
        due_at_utc: str | None,
        timezone_name: str | None,
        original_time_text: str | None,
        action_payload_patch: Dict[str, Any] | None,
        metadata_patch: Dict[str, Any] | None,
        source_text: str | None,
    ) -> FakeScheduleItem:
        item = self.store.find_by_id(schedule_id, owner_id=owner_id)
        if item is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        if title is not None:
            item.title = title
        if due_at_utc is not None:
            item.due_at_utc = due_at_utc
        if timezone_name is not None:
            item.timezone_name = timezone_name
        if original_time_text is not None:
            item.original_time_text = original_time_text
        if action_payload_patch:
            item.action_payload.update(action_payload_patch)
        if metadata_patch is not None:
            item.metadata = metadata_patch
        if source_text is not None:
            item.source_text = source_text
        return self.store.upsert(item)

    def cancel_schedule(
        self,
        *,
        owner_id: str,
        thread_id: str | None,
        schedule_id: str,
        source_text: str,
    ) -> FakeScheduleItem:
        item = self.store.find_by_id(schedule_id, owner_id=owner_id)
        if item is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        item.status = "canceled"
        item.source_text = source_text
        return self.store.upsert(item)

    def lease_due_schedules(self, *, owner_id: str, limit: int) -> list[FakeScheduleItem]:
        del owner_id, limit
        return []

    def release_lease(
        self,
        *,
        owner_id: str,
        thread_id: str,
        schedule_id: str,
        reason: str,
        retry_after_seconds: int,
    ) -> None:
        del owner_id, thread_id, schedule_id, reason, retry_after_seconds
        return None

    def mark_running(self, *, owner_id: str, thread_id: str, schedule_id: str) -> None:
        item = self.store.find_by_id(schedule_id, owner_id=owner_id)
        if item is not None:
            item.status = "running"

    def mark_done(
        self,
        *,
        owner_id: str,
        thread_id: str,
        schedule_id: str,
        run_id: str,
        result: Dict[str, Any],
    ) -> None:
        del thread_id, run_id, result
        item = self.store.find_by_id(schedule_id, owner_id=owner_id)
        if item is not None:
            item.status = "done"

    def mark_failed(self, *, owner_id: str, thread_id: str, schedule_id: str, error: str) -> None:
        del thread_id, error
        item = self.store.find_by_id(schedule_id, owner_id=owner_id)
        if item is not None:
            item.status = "failed"


class FakeScheduleAgent:
    def __init__(self) -> None:
        self.store = FakeScheduleStore()
        self.service = FakeScheduleService(self.store)


class FakeRuntimeAgent:
    def __init__(self, schedule_agent: FakeScheduleAgent) -> None:
        self._schedule_agent = schedule_agent
        self.user_name = "default"
        self.assistant_name = "Memory Assistant"
        # keep parity with production runtime shape for dialogue endpoints
        self.memory_persistence = type("MemoryPersistence", (), {"dialogues_dir": str(Path(".") / "tmp-dialogues")})()

    def get_schedule_agent(self) -> FakeScheduleAgent:
        return self._schedule_agent


class FakeRuntime:
    def __init__(
        self,
        *,
        config_path: Path,
        default_thread_id: str = "demo-thread",
        schedule_agent: FakeScheduleAgent | None = None,
        runtime_profile: str = "legacy",
    ) -> None:
        self.config_path = Path(config_path)
        self.default_thread_id = default_thread_id
        self._runtime_profile = str(runtime_profile or "legacy").strip().lower()
        self.agent = FakeRuntimeAgent(schedule_agent or FakeScheduleAgent())
        self._thread_event_sink = None
        self._threads: dict[str, dict[str, Any]] = {}
        self._scene_entries: dict[str, list[dict[str, Any]]] = {}
        self._threads_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._runs_failed = 0
        self._last_run_finished_at: str | None = None

    @property
    def runtime_profile(self) -> str:
        return self._runtime_profile

    def set_thread_event_sink(self, sink) -> None:
        self._thread_event_sink = sink

    def shutdown(self) -> None:
        return None

    def health_payload(self) -> Dict[str, Any]:
        with self._threads_lock:
            payload: Dict[str, Any] = {
                "config_path": str(self.config_path),
                "default_thread_id": self.default_thread_id,
                "runtime_profile": self._runtime_profile,
                "thread_count": len(self._threads),
            }
        if self._runtime_profile == "think_life":
            payload["think_life"] = {
                "pending_stimuli_total": 0,
                "active_drainer_threads": 0,
                "preempt_enabled": False,
            }
        return payload

    def submit_stimulus(
        self,
        *,
        thread_id: str,
        message: str,
        user_turn: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self._runtime_profile != "think_life":
            raise RuntimeError("profile_not_supported")
        tid = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        stimulus_id = f"stim_{uuid4().hex[:12]}"
        with self._threads_lock:
            state = self._ensure_state(tid)
            if "think_life" not in state:
                state["think_life"] = {
                    "pending_stimuli": 0,
                    "busy": False,
                    "busy_reason": "idle",
                    "runtime_profile": "think_life",
                }
            state["think_life"]["pending_stimuli"] = int(state["think_life"].get("pending_stimuli", 0)) + 1
            pending = int(state["think_life"]["pending_stimuli"])
        if callable(self._thread_event_sink):
            self._thread_event_sink(
                tid,
                "stimulus_queued",
                {"stimulus_id": stimulus_id, "kind": "user_message", "pending_count": pending},
            )
        return {
            "stimulus_id": stimulus_id,
            "thread_id": tid,
            "pending_count": pending,
            "accepted": True,
        }

    def get_scene(
        self,
        thread_id: str,
        *,
        limit: int = 40,
        before_seq: Optional[int] = None,
    ) -> Dict[str, Any]:
        if self._runtime_profile != "think_life":
            raise RuntimeError("profile_not_supported")
        tid = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        with self._threads_lock:
            entries = list(self._scene_entries.get(tid, []))
        if before_seq is not None:
            entries = [e for e in entries if int(e.get("seq", 0)) < int(before_seq)]
        cap = max(1, int(limit or 40))
        has_more = len(entries) > cap
        if has_more:
            entries = entries[-cap:]
        return {"thread_id": tid, "entries": entries, "has_more": has_more}

    def get_think_life_transactions(self, thread_id: str) -> Dict[str, Any]:
        if self._runtime_profile != "think_life":
            raise RuntimeError("profile_not_supported")
        tid = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        return {
            "thread_id": tid,
            "transactions": [],
            "active_transaction_id": None,
            "cpu_transaction_id": None,
            "transaction_count": 0,
        }

    def _ensure_state(self, thread_id: str) -> dict[str, Any]:
        normalized = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        state = self._threads.get(normalized)
        if state is None:
            state = {
                "thread_id": normalized,
                "mode": "manual",
                "history_rounds": 0,
                "history_messages": 0,
                "pending_rounds": 0,
                "pending_turns": 0,
                "has_pending_data": False,
                "last_activity_at": _now_iso(),
                "last_flush_at": None,
                "last_flush_attempt_at": None,
                "last_flush_reason": None,
                "last_flush_success": None,
                "idle_flush_seconds": 1800,
                "idle_flush_deadline": None,
                "history_rounds_data": [],
                "history_preview": [],
                "working_memory": {
                    "enabled": False,
                    "stored_entries": 0,
                    "inject_max_entries": 20,
                    "max_stored_entries": 200,
                    "ui_expose_max_entries": 200,
                    "entries": [],
                },
            }
            self._threads[normalized] = state
        return state

    def get_thread_state(self, thread_id: str) -> Dict[str, Any]:
        with self._threads_lock:
            state = deepcopy(self._ensure_state(thread_id))
        return state

    def set_thread_mode(self, thread_id: str, *, mode: str, discard_pending: bool = False) -> Dict[str, Any]:
        with self._threads_lock:
            state = self._ensure_state(thread_id)
            state["mode"] = mode
            if discard_pending:
                state["pending_rounds"] = 0
                state["pending_turns"] = 0
                state["has_pending_data"] = False
            snapshot = deepcopy(state)
        return {
            "success": True,
            "thread_id": snapshot["thread_id"],
            "mode": snapshot["mode"],
            "discard_pending": bool(discard_pending),
            "thread_state": snapshot,
        }

    def flush_thread(self, thread_id: str, *, reason: str = "manual_api") -> Dict[str, Any]:
        with self._threads_lock:
            state = self._ensure_state(thread_id)
            state["last_flush_attempt_at"] = _now_iso()
            state["last_flush_reason"] = reason
            pending_rounds = int(state["pending_rounds"])
            status = "noop"
            if pending_rounds > 0:
                status = "written"
                state["pending_rounds"] = 0
                state["pending_turns"] = 0
                state["has_pending_data"] = False
                state["last_flush_at"] = _now_iso()
                state["last_flush_success"] = True
            snapshot = deepcopy(state)
        return {
            "success": True,
            "thread_id": snapshot["thread_id"],
            "flush_reason": reason,
            "status": status,
            "rounds_flushed": pending_rounds,
            "turns_flushed": pending_rounds * 2,
            "memory_write": {"success": True},
            "thread_state": snapshot,
            "error": None,
        }

    def force_stop_thread(self, thread_id: str, *, reason: str = "user_requested") -> Dict[str, Any]:
        tid = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        with self._threads_lock:
            state = self._ensure_state(tid)
            if self._runtime_profile == "think_life":
                state.setdefault(
                    "think_life",
                    {
                        "pending_stimuli": 0,
                        "busy": False,
                        "busy_reason": "idle",
                        "runtime_profile": "think_life",
                    },
                )
                cleared = int(state["think_life"].get("pending_stimuli", 0) or 0)
                state["think_life"]["pending_stimuli"] = 0
                state["think_life"]["busy"] = False
                state["think_life"]["busy_reason"] = "idle"
            else:
                cleared = 0
            snapshot = deepcopy(state)
        if callable(self._thread_event_sink):
            self._thread_event_sink(
                tid,
                "thinking_force_stopped",
                {
                    "thread_id": tid,
                    "reason": reason,
                    "cancelled_in_flight": True,
                    "cleared_pending_stimuli": cleared,
                    "cancelled_transactions": [],
                },
            )
        return {
            "success": True,
            "thread_id": tid,
            "runtime_profile": self._runtime_profile,
            "cancelled_in_flight": True,
            "cleared_pending_stimuli": cleared,
            "cancelled_transactions": [],
            "thread_state": snapshot,
        }

    def run_chat(self, *, message: str, thread_id: str, user_turn: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._threads_lock:
            state = self._ensure_state(thread_id)
            state["history_rounds"] += 1
            state["history_messages"] += 2
            state["last_activity_at"] = _now_iso()
            if state["mode"] == "manual":
                state["pending_rounds"] += 1
                state["pending_turns"] = state["pending_rounds"] * 2
                state["has_pending_data"] = True
                memory_status = "buffered"
                memory_reason = None
            else:
                memory_status = "skipped"
                memory_reason = "memory mode is off"
            normalized_user_turn = _normalize_turn_payload(user_turn, speaker="user", text=message)
            assistant_turn = _normalize_turn_payload(None, speaker="assistant", text=f"echo:{message}")
            round_payload = {
                "round_id": f"round_{uuid4().hex[:8]}",
                "capture_state": "pending" if memory_status == "buffered" else "skipped",
                "flush_id": None,
                "source": "user",
                "user_message": message,
                "assistant_message": f"echo:{message}",
                "user_turn": normalized_user_turn,
                "assistant_turn": assistant_turn,
                "user_at": _now_iso(),
                "assistant_at": _now_iso(),
            }
            state["history_rounds_data"].append(round_payload)
            state["history_preview"] = state["history_rounds_data"][-3:]
            snapshot = deepcopy(state)
        answer = f"echo:{message}"
        return {
            "success": True,
            "thread_id": snapshot["thread_id"],
            "question": message,
            "answer": answer,
            "agent_result": {"answer": answer, "tool_call_count": 0},
            "memory_write": None,
            "memory_capture": {
                "mode": snapshot["mode"],
                "status": memory_status,
                "reason": memory_reason,
                "pending_rounds": snapshot["pending_rounds"],
                "pending_turns": snapshot["pending_turns"],
            },
            "thread_state": snapshot,
        }

    def run_schedule_trigger(self, *, schedule_item: Any) -> Dict[str, Any]:
        thread_id = str(getattr(schedule_item, "thread_id", "") or self.default_thread_id).strip() or self.default_thread_id
        answer = f"scheduled:{getattr(schedule_item, 'title', 'task')}"
        if callable(self._thread_event_sink):
            self._thread_event_sink(
                thread_id,
                "assistant_message",
                {
                    "thread_id": thread_id,
                    "answer": answer,
                    "source": "schedule",
                    "schedule_id": str(getattr(schedule_item, "schedule_id", "") or "").strip(),
                },
            )
        with self._threads_lock:
            state = self._ensure_state(thread_id)
            state["history_rounds"] += 1
            state["history_messages"] += 1
            state["last_activity_at"] = _now_iso()
            snapshot = deepcopy(state)
        return {
            "success": True,
            "thread_id": thread_id,
            "answer": answer,
            "thread_state": snapshot,
            "memory_capture": {
                "mode": snapshot["mode"],
                "status": "skipped",
                "reason": "schedule trigger is not persisted to memory buffer",
                "pending_rounds": snapshot["pending_rounds"],
                "pending_turns": snapshot["pending_turns"],
            },
        }

    def _episodic_persistence_payload(self) -> Dict[str, Any]:
        return {}

    def iter_upload_dialogues(
        self,
        uploads: Sequence[Tuple[str, bytes]],
        *,
        rebuild_rag: bool = False,
        index_rag: bool = True,
    ) -> Iterator[Dict[str, Any]]:
        from m_agent.chat.dialogue_import import import_uploaded_dialogues_stream

        user_name = str(getattr(self.agent, "user_name", "default") or "default")
        assistant_name = str(getattr(self.agent, "assistant_name", "Memory Assistant") or "Memory Assistant")
        seq = 0
        for event in import_uploaded_dialogues_stream(
            user_name=user_name,
            uploads=uploads,
            assistant_name=assistant_name,
            index_rag=index_rag,
            rebuild_rag=rebuild_rag,
            source_label="test_dialogue_upload",
        ):
            if event.get("type") == "upload_completed" and isinstance(event.get("payload"), dict):
                event["payload"]["episodic_persistence"] = self._episodic_persistence_payload()
            seq += 1
            yield {"seq": seq, **event}
