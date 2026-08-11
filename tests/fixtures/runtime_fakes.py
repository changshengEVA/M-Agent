from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any, Dict, Iterator, Optional, Sequence, Tuple
from uuid import uuid4

from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE


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
    thread_id: str
    due_at_utc: str
    timezone_name: str
    text: str
    deferred_objective: Dict[str, str] = field(default_factory=dict)
    status: str = "pending"
    created_at: str = ""


class FakeScheduleStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], FakeScheduleItem] = {}

    def upsert(self, owner_id: str, item: FakeScheduleItem) -> FakeScheduleItem:
        self._items[(owner_id, item.schedule_id)] = item
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
            "thread_id": item.thread_id,
            "due_at_utc": item.due_at_utc,
            "timezone_name": item.timezone_name,
            "text": item.text,
            "deferred_objective": dict(item.deferred_objective),
            "status": item.status,
            "created_at": item.created_at,
            "due_at_local": item.due_at_utc,
            "due_display": item.due_at_utc,
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
            if normalized_keyword and normalized_keyword not in item.text.lower():
                continue
            filtered.append(item)
        return filtered[: max(1, int(limit or 20))]

    def create_schedule(
        self,
        *,
        owner_id: str,
        thread_id: str,
        due_at_utc: str,
        timezone_name: str,
        deferred_objective: str = "",
        text: str = "",
    ) -> FakeScheduleItem:
        objective = str(deferred_objective or text or "").strip()
        item = FakeScheduleItem(
            schedule_id=f"sch_{uuid4().hex[:10]}",
            thread_id=thread_id,
            due_at_utc=due_at_utc,
            timezone_name=timezone_name,
            text=objective,
            deferred_objective={
                "description": objective,
                "encoding": "native" if deferred_objective else "legacy_text",
            },
            status="pending",
            created_at=_now_iso(),
        )
        return self.store.upsert(owner_id, item)

    def cancel_schedule(
        self,
        *,
        owner_id: str,
        thread_id: str | None,
        schedule_id: str,
    ) -> FakeScheduleItem:
        del thread_id
        item = self.store.find_by_id(schedule_id, owner_id=owner_id)
        if item is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        item.status = "canceled"
        return self.store.upsert(owner_id, item)

    def lease_due_schedules(self, *, owner_id: str, limit: int) -> list[FakeScheduleItem]:
        del owner_id, limit
        return []

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
    ) -> None:
        self.config_path = Path(config_path)
        self.default_thread_id = default_thread_id
        self.agent = FakeRuntimeAgent(schedule_agent or FakeScheduleAgent())
        self._thread_event_sink = None
        self._threads: dict[str, dict[str, Any]] = {}
        self._scene_entries: dict[str, list[dict[str, Any]]] = {}
        self._transactions: dict[str, dict[str, Any]] = {}
        self._transaction_delete_replays: dict[
            tuple[str, str], tuple[str, int, dict[str, Any]]
        ] = {}
        self._threads_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._runs_failed = 0
        self._last_run_finished_at: str | None = None

    @property
    def runtime_profile(self) -> str:
        return LANGGRAPH_RUNTIME_ENGINE

    def set_thread_event_sink(self, sink) -> None:
        self._thread_event_sink = sink

    def shutdown(self) -> None:
        return None

    def health_payload(self) -> Dict[str, Any]:
        with self._threads_lock:
            pending_stimuli_total = sum(
                int(state.get("runtime", {}).get("pending_stimuli", 0) or 0)
                for state in self._threads.values()
            )
            payload: Dict[str, Any] = {
                "config_path": str(self.config_path),
                "default_thread_id": self.default_thread_id,
                "runtime_profile": LANGGRAPH_RUNTIME_ENGINE,
                "runtime_engine_id": LANGGRAPH_RUNTIME_ENGINE,
                "thread_count": len(self._threads),
                "runtime": {
                    "runtime_engine_id": LANGGRAPH_RUNTIME_ENGINE,
                    "pending_stimuli_total": pending_stimuli_total,
                    "active_drainer_threads": 0,
                    "preempt_enabled": False,
                },
            }
        return payload

    def submit_stimulus(
        self,
        *,
        thread_id: str,
        message: str,
        user_turn: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tid = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        stimulus_id = f"stim_{uuid4().hex[:12]}"
        with self._threads_lock:
            state = self._ensure_state(tid)
            runtime_state = state["runtime"]
            runtime_state["pending_stimuli"] = int(
                runtime_state.get("pending_stimuli", 0)
            ) + 1
            runtime_state["runtime_phase"] = "queued"
            runtime_state["effective_depth"] = int(
                runtime_state["pending_stimuli"]
            )
            pending = int(runtime_state["pending_stimuli"])
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
            "effective_depth": pending,
            "runtime_phase": "queued",
            "runtime_engine_id": LANGGRAPH_RUNTIME_ENGINE,
            "accepted": True,
        }

    def get_scene(
        self,
        thread_id: str,
        *,
        limit: int = 40,
        before_seq: Optional[int] = None,
    ) -> Dict[str, Any]:
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

    def seed_transaction(
        self,
        *,
        thread_id: str,
        transaction_id: str = "tx-test-1",
        conversation_id: str | None = None,
        revision: int = 1,
    ) -> Dict[str, Any]:
        tid = str(thread_id or self.default_thread_id).strip()
        record = {
            "transaction_id": transaction_id,
            "thread_id": tid,
            "conversation_id": conversation_id or f"{tid}::0",
            "state": "continue",
            "lifecycle_status": "active",
            "revision": int(revision),
            "current_activation_id": "act-test-1",
            "active_delegate_id": None,
            "deleted": False,
            "deleted_at": None,
            "updated_at": _now_iso(),
            "terminal_at": None,
            "wm_entries": [],
            "wm_entry_count": 0,
            "task_state": {
                "goal": "test",
                "completion_status": "processing",
                "completed": [],
                "remaining": [],
            },
            "kind": "user_task",
            "priority": 50,
            "runtime_engine": LANGGRAPH_RUNTIME_ENGINE,
            "think_rounds": 0,
            "delegate_count": 0,
            "is_active_user": True,
            "is_cpu_holder": False,
        }
        self._transactions[transaction_id] = record
        return deepcopy(record)

    def get_transactions(
        self,
        thread_id: str,
        *,
        include_history: bool = False,
    ) -> Dict[str, Any]:
        tid = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        conversation_id = f"{tid}::0"
        transactions = [
            deepcopy(item)
            for item in self._transactions.values()
            if item["thread_id"] == tid
            and (
                include_history
                or item["conversation_id"] == conversation_id
            )
        ]
        active = next(
            (
                item["transaction_id"]
                for item in transactions
                if not item.get("deleted")
                and item.get("state") == "continue"
            ),
            None,
        )
        return {
            "thread_id": tid,
            "conversation_id": conversation_id,
            "transactions": transactions,
            "active_transaction_id": active,
            "cpu_transaction_id": None,
            "transaction_count": len(transactions),
            "audit_transaction_count": sum(
                item["thread_id"] == tid
                for item in self._transactions.values()
            ),
            "include_history": bool(include_history),
            "runtime_phase": "ready",
            "effective_depth": 0,
        }

    def delete_transaction(
        self,
        thread_id: str = "",
        transaction_id: str = "",
        *,
        conversation_id: str | None = None,
        expected_revision: int,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        from m_agent.runtime.transaction_control import (
            RuntimeTransactionNotFoundError,
        )
        from m_agent.runtime.transaction.store import (
            IdempotencyConflictError,
            RevisionConflictError,
        )

        tid = str(thread_id or self.default_thread_id).strip()
        cid = str(conversation_id or "").strip() or f"{tid}::0"
        replay_key = (tid, str(idempotency_key))
        replay = self._transaction_delete_replays.get(replay_key)
        if replay is not None:
            replay_tx, replay_revision, replay_payload = replay
            if (
                replay_tx != transaction_id
                or replay_revision != int(expected_revision)
            ):
                raise IdempotencyConflictError(
                    str(idempotency_key),
                    stored_digest="stored",
                    requested_digest="requested",
                )
            payload = deepcopy(replay_payload)
            payload["replayed"] = True
            return payload
        record = self._transactions.get(transaction_id)
        if (
            record is None
            or record["thread_id"] != tid
            or record["conversation_id"] != cid
        ):
            raise RuntimeTransactionNotFoundError(transaction_id)
        if int(record["revision"]) != int(expected_revision):
            raise RevisionConflictError(
                transaction_id,
                expected_revision=int(expected_revision),
                actual_revision=int(record["revision"]),
            )
        already_deleted = bool(record.get("deleted"))
        if not already_deleted:
            record["revision"] = int(record["revision"]) + 1
            record["lifecycle_status"] = "deleted"
            record["deleted"] = True
            record["deleted_at"] = _now_iso()
            record["terminal_at"] = record["deleted_at"]
            record["updated_at"] = record["deleted_at"]
            record["current_activation_id"] = None
            record["active_delegate_id"] = None
            record["is_active_user"] = False
        payload = {
            "success": True,
            "outcome": "already_deleted" if already_deleted else "deleted",
            "transaction": deepcopy(record),
            "cleanup": {
                "cancelled_schedule_run_ids": [],
                "aborted_stimulus_ids": [],
                "terminal_feedback_outbox_ids": [],
                "cancelled_in_flight": False,
            },
            "thread_id": tid,
            "conversation_id": cid,
            "active_transaction_id": None,
            "cpu_transaction_id": None,
            "replayed": False,
            "already_deleted": already_deleted,
        }
        self._transaction_delete_replays[replay_key] = (
            transaction_id,
            int(expected_revision),
            deepcopy(payload),
        )
        if callable(self._thread_event_sink) and not already_deleted:
            self._thread_event_sink(tid, "transaction_deleted", deepcopy(payload))
        return payload

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
                "runtime_engine_id": LANGGRAPH_RUNTIME_ENGINE,
                "runtime": {
                    "pending_stimuli": 0,
                    "busy": False,
                    "busy_reason": "idle",
                    "runtime_profile": LANGGRAPH_RUNTIME_ENGINE,
                    "runtime_engine_id": LANGGRAPH_RUNTIME_ENGINE,
                    "runtime_phase": "ready",
                    "effective_depth": 0,
                    "in_flight_stimulus_id": None,
                    "preempt_enabled": False,
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
            runtime_state = state["runtime"]
            cleared = int(runtime_state.get("pending_stimuli", 0) or 0)
            runtime_state["pending_stimuli"] = 0
            runtime_state["busy"] = False
            runtime_state["busy_reason"] = "idle"
            runtime_state["runtime_phase"] = "ready"
            runtime_state["effective_depth"] = 0
            snapshot = deepcopy(state)
            thread_runtime = {
                "thread_id": tid,
                "busy": False,
                "busy_reason": "idle",
                "pending_stimuli": 0,
                "runtime_profile": LANGGRAPH_RUNTIME_ENGINE,
                "runtime_phase": "ready",
                "effective_depth": 0,
                "in_flight_stimulus_id": None,
                "preempt_enabled": False,
            }
        if callable(self._thread_event_sink):
            self._thread_event_sink(
                tid,
                "thinking_force_stopped",
                {
                    "thread_id": tid,
                    "reason": reason,
                    "cancelled_in_flight": False,
                    "cleared_pending_stimuli": cleared,
                    "paused_transactions": [],
                },
            )
        return {
            "success": True,
            "thread_id": tid,
            "runtime_profile": LANGGRAPH_RUNTIME_ENGINE,
            "cancelled_in_flight": False,
            "cleared_pending_stimuli": cleared,
            "paused_transactions": [],
            "thread_runtime": thread_runtime,
            "thread_state": snapshot,
        }

    def run_chat(
        self,
        *,
        message: str,
        thread_id: str,
        user_turn: Optional[Dict[str, Any]] = None,
        message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        del message_id
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
            "results": [],
            "replies": [answer],
            "answer": answer,
            "runtime_engine_id": LANGGRAPH_RUNTIME_ENGINE,
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
