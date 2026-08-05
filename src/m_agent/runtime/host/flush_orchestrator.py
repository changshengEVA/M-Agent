"""Durable single-runtime flush orchestration.

The orchestrator owns the product flush ordering independently of any runtime
routing concern::

    immutable snapshot -> runtime commit -> materialization -> complete

Both the snapshot and materialization payload are persisted before the first
irreversible step.  Reusing a flush id with different content is rejected by
the journal, while a process restart simply resumes the oldest pending record.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import threading
from typing import Any, Dict, Mapping, Optional

from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
)

from .flush_journal import FlushJournal, FlushRecord


logger = logging.getLogger(__name__)


class RuntimeFlushOrchestrator:
    """Coordinate a durable flush for one concrete runtime host."""

    def __init__(
        self,
        runtime: Any,
        *,
        journal_path: Optional[Path | str],
    ) -> None:
        self._runtime = runtime
        self._journal = FlushJournal(journal_path)
        self._recovery_lock = threading.RLock()
        self._last_recovery: Dict[str, Any] = {
            "attempted": False,
            "attempted_at": None,
            "pending_before": 0,
            "recovered": [],
            "recovered_count": 0,
            "failed": [],
            "failure_count": 0,
            "blocked": [],
            "blocked_count": 0,
            "blocked_threads": [],
            "pending_after": len(self._journal.list_pending()),
        }

    @property
    def journal(self) -> FlushJournal:
        return self._journal

    @property
    def journal_path(self) -> Optional[Path]:
        return self._journal.path

    @property
    def runtime_id(self) -> str:
        value = str(getattr(self._runtime, "runtime_engine_id", "") or "").strip()
        if not value:
            raise RuntimeError("runtime host has no stable runtime id")
        return value

    def _dialogue_payload_from_snapshot(
        self,
        *,
        thread_id: str,
        source: str,
        runtime_snapshot: Mapping[str, Any],
    ) -> Optional[Dict[str, Any]]:
        entries = []
        for raw_entry in runtime_snapshot.get("scene_entries", []):
            if not isinstance(raw_entry, Mapping):
                raise TypeError("flush snapshot Scene entry must be a mapping")
            entries.append(SceneEntry.from_dict(dict(raw_entry)))
        episode_notes = [
            {
                "note_id": str(entry.append_id or f"scene:{entry.seq}"),
                "note": str(entry.text or "").strip(),
                "turn_meta": {
                    "source": "committed_scene",
                    "scene_seq": int(entry.seq),
                    "occurred_at": str(entry.occurred_at or ""),
                    "transaction_id": str(entry.transaction_id or ""),
                },
            }
            for entry in entries
            if str(entry.payload_ref or "").strip() == "episode_note:v1"
            and str(entry.text or "").strip()
        ]
        dialogue_entries = [
            entry
            for entry in entries
            if entry.actor in {SceneActor.USER, SceneActor.ASSISTANT}
            or entry.entry_type in {SceneEntryType.UTTERANCE, SceneEntryType.REPLY}
        ]
        if not dialogue_entries:
            return None

        from m_agent.chat.chat_memory_persistence import (
            build_dialogue_id,
            build_dialogue_payload_from_scene_entries,
        )

        try:
            created_at = datetime.fromisoformat(
                str(dialogue_entries[0].occurred_at or "").replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Scene dialogue entry has an invalid occurred_at timestamp"
            ) from exc
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        agent = getattr(self._runtime, "agent", None)
        payload = build_dialogue_payload_from_scene_entries(
            dialogue_id=build_dialogue_id(
                thread_id=thread_id,
                created_at=created_at,
            ),
            thread_id=thread_id,
            entries=dialogue_entries,
            source=source,
            user_name=str(getattr(agent, "user_name", "User") or "User"),
            assistant_name=str(
                getattr(agent, "assistant_name", "Memory Assistant")
                or "Memory Assistant"
            ),
        )
        if episode_notes:
            meta = payload.setdefault("meta", {})
            trace = meta.setdefault("trace_summary", {})
            trace["episode_notes"] = episode_notes
        return payload

    def _capture_snapshot(
        self,
        *,
        thread_id: str,
        conversation_id: str,
        source: str,
    ) -> Dict[str, Any]:
        self._runtime.ensure_scene_thread_loaded(conversation_id)
        capture = getattr(self._runtime.runtime_store, "capture_flush_snapshot", None)
        if not callable(capture):
            raise RuntimeError("runtime host cannot capture a FlushSnapshot")
        runtime_snapshot = dict(capture(conversation_id))
        immutable = {
            "schema_version": 1,
            "thread_id": thread_id,
            "conversation_id": conversation_id,
            "source": source,
            "runtimes": {self.runtime_id: runtime_snapshot},
        }
        canonical = json.dumps(
            immutable,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        payload_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        snapshot = dict(immutable)
        snapshot.update(
            {
                "flush_id": f"runtime_flush_{payload_digest[:32]}",
                "payload_digest": payload_digest,
                "dialogue_payload": self._dialogue_payload_from_snapshot(
                    thread_id=thread_id,
                    source=source,
                    runtime_snapshot=runtime_snapshot,
                ),
            }
        )
        return snapshot

    @staticmethod
    def _plan(record: FlushRecord) -> Dict[str, Any]:
        snapshot = deepcopy(record.snapshot)
        runtimes = snapshot.get("runtimes")
        runtimes = dict(runtimes) if isinstance(runtimes, Mapping) else {}
        through = {
            str(runtime_id): int(
                dict(runtime_snapshot).get("through_seq", 0) or 0
            )
            for runtime_id, runtime_snapshot in runtimes.items()
        }
        dialogue_payload = snapshot.get("dialogue_payload")
        return {
            "flush_id": record.flush_id,
            "thread_id": record.thread_id,
            "conversation_id": record.conversation_id,
            "payload_digest": snapshot.get("payload_digest"),
            "dialogue_payload": (
                deepcopy(dialogue_payload)
                if isinstance(dialogue_payload, dict)
                else None
            ),
            "runtime_through_seq": through,
            "through_seq": max(through.values(), default=0),
            "flush_snapshot": snapshot,
            "durable_runtime_first": True,
            "journal_status": record.status,
            "pending_runtimes": list(record.pending_runtimes),
            "materializations": {
                destination: state.to_dict()
                for destination, state in record.materializations.items()
            },
            "pending_materializations": list(record.pending_materializations),
        }

    def prepare(
        self,
        thread_id: str,
        *,
        conversation_id: str,
        source: str = "chat_api_thread_flush",
    ) -> Dict[str, Any]:
        """Freeze a new boundary or resume the existing pending flush."""

        tid = str(thread_id or "").strip()
        cid = str(conversation_id or "").strip()
        if not tid or not cid:
            raise ValueError("thread_id and conversation_id are required")
        pending = self._journal.list_pending(conversation_id=cid)
        if len(pending) > 1:
            raise RuntimeError(
                f"multiple pending runtime flushes for conversation {cid!r}"
            )
        if pending:
            record = pending[0]
            if record.thread_id != tid:
                raise RuntimeError("pending runtime flush thread mismatch")
            return self._plan(record)
        snapshot = self._capture_snapshot(
            thread_id=tid,
            conversation_id=cid,
            source=str(source or "chat_api_thread_flush"),
        )
        record = self._journal.create_or_get_pending(
            cid,
            tid,
            str(snapshot["flush_id"]),
            snapshot,
        )
        return self._plan(record)

    def stage_materialization(
        self,
        flush_id: str,
        *,
        destination: str,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        return self._plan(
            self._journal.stage_materialization(
                flush_id,
                destination,
                dict(payload),
            )
        )

    def mark_materialization_delivered(
        self,
        flush_id: str,
        *,
        destination: str,
        result: Mapping[str, Any],
    ) -> Dict[str, Any]:
        return self._plan(
            self._journal.mark_materialization_delivered(
                flush_id,
                destination,
                dict(result),
            )
        )

    def commit_runtime(
        self,
        thread_id: str,
        *,
        conversation_id: str,
        flush_snapshot: Optional[Mapping[str, Any]] = None,
        defer_completion: bool = False,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Commit the immutable runtime boundary exactly once."""

        tid = str(thread_id or "").strip()
        cid = str(conversation_id or "").strip()
        if isinstance(flush_snapshot, Mapping):
            snapshot = deepcopy(dict(flush_snapshot))
            flush_id = str(snapshot.get("flush_id", "") or "").strip()
            if not flush_id:
                raise ValueError("FlushSnapshot flush_id is required")
            record = self._journal.create_or_get_pending(
                cid,
                tid,
                flush_id,
                snapshot,
            )
        else:
            plan = self.prepare(tid, conversation_id=cid)
            snapshot = dict(plan["flush_snapshot"])
            flush_id = str(plan["flush_id"])
            record = self._journal.get(flush_id)
            if record is None:
                raise RuntimeError(f"missing flush journal record: {flush_id}")

        snapshots = snapshot.get("runtimes")
        if not isinstance(snapshots, Mapping):
            raise TypeError("FlushSnapshot runtimes must be a mapping")
        if set(map(str, snapshots)) != {self.runtime_id}:
            raise RuntimeError("FlushSnapshot is not owned by this runtime")
        state = record.runtimes.get(self.runtime_id)
        result: Dict[str, Any]
        if state is not None and state.status == "committed":
            result = dict(state.result)
        else:
            boundary = dict(snapshots[self.runtime_id])
            commit = getattr(self._runtime, "_commit_flush_segment", None)
            if not callable(commit):
                raise RuntimeError("runtime host cannot commit a flush segment")
            result = dict(
                commit(
                    tid,
                    conversation_id=cid,
                    flush_id=f"{flush_id}:{self.runtime_id}",
                    through_seq=int(boundary.get("through_seq", 0) or 0),
                    eligible_revisions={
                        str(key): int(value)
                        for key, value in dict(
                            boundary.get("eligible_revisions") or {}
                        ).items()
                    },
                    payload={
                        "runtime_flush_id": flush_id,
                        "snapshot_digest": str(
                            snapshot.get("payload_digest", "") or ""
                        ),
                        "source": str(
                            snapshot.get("source", "chat_api_thread_flush")
                            or "chat_api_thread_flush"
                        ),
                        **dict(payload or {}),
                    },
                )
            )
            record = self._journal.mark_runtime_committed(
                flush_id,
                self.runtime_id,
                result,
            )
        if not defer_completion:
            record = self._journal.mark_completed(flush_id)
        return {
            "thread_id": tid,
            "flush_id": flush_id,
            "completed_transaction_id": result.get("completed_transaction_id"),
            "archived_transaction_ids": list(
                result.get("archived_transaction_ids", [])
            ),
            "episode_notes_drained": int(
                result.get("episode_notes_drained", 0) or 0
            ),
            "runtime": result,
            "journal_status": record.status,
            "pending_runtimes": list(record.pending_runtimes),
        }

    def complete(self, flush_id: str) -> Dict[str, Any]:
        return self._journal.mark_completed(flush_id).to_dict()

    def _dialogue_payload_for_recovery(
        self,
        record: FlushRecord,
    ) -> Optional[Dict[str, Any]]:
        snapshot = record.snapshot
        frozen = snapshot.get("dialogue_payload")
        if isinstance(frozen, Mapping):
            return deepcopy(dict(frozen))
        if frozen is not None:
            raise TypeError("FlushSnapshot dialogue_payload must be a mapping")

        runtimes = snapshot.get("runtimes")
        if not isinstance(runtimes, Mapping):
            raise TypeError("FlushSnapshot runtimes must be a mapping")
        boundary = runtimes.get(self.runtime_id)
        if not isinstance(boundary, Mapping):
            raise RuntimeError(
                f"FlushSnapshot is missing runtime boundary {self.runtime_id!r}"
            )
        return self._dialogue_payload_from_snapshot(
            thread_id=record.thread_id,
            source=str(
                snapshot.get("source", "chat_api_thread_flush")
                or "chat_api_thread_flush"
            ),
            runtime_snapshot=boundary,
        )

    @staticmethod
    def _dialogue_materialization_payload(
        dialogue_payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        frozen = deepcopy(dict(dialogue_payload))
        dialogue_id = str(frozen.get("dialogue_id", "") or "").strip()
        if not dialogue_id:
            raise ValueError("recovery dialogue_payload dialogue_id is required")
        turns = frozen.get("turns")
        turns = turns if isinstance(turns, list) else []
        meta = frozen.get("meta")
        meta = meta if isinstance(meta, Mapping) else {}
        return {
            "dialogue_payload": frozen,
            "flush_mode": "scene",
            "rounds_flushed": max(0, int(meta.get("round_count", 0) or 0)),
            "turns_flushed": len(turns),
        }

    @staticmethod
    def _summarize_dialogue_result(result: Mapping[str, Any]) -> Dict[str, Any]:
        payload = dict(result)
        import_result = payload.get("import_result")
        import_result = import_result if isinstance(import_result, Mapping) else {}
        scene_build_result = import_result.get("scene_build_result")
        scene_build_result = (
            scene_build_result
            if isinstance(scene_build_result, Mapping)
            else {}
        )
        fact_import_stats = scene_build_result.get("fact_import_stats")
        fact_import_stats = (
            fact_import_stats
            if isinstance(fact_import_stats, Mapping)
            else {}
        )
        align_result = fact_import_stats.get("entity_profile_align_result")
        align_result = align_result if isinstance(align_result, Mapping) else {}
        return {
            "success": bool(payload.get("success", False)),
            "dialogue_id": (
                str(payload.get("dialogue_id", "") or "").strip() or None
            ),
            "episode_id": (
                str(payload.get("episode_id", "") or "").strip() or None
            ),
            "round_count": int(payload.get("round_count", 0) or 0),
            "turn_count": int(payload.get("turn_count", 0) or 0),
            "import_success": (
                bool(import_result.get("success")) if import_result else None
            ),
            "scene_build_success": (
                bool(scene_build_result.get("success"))
                if scene_build_result
                else None
            ),
            "entity_profile_align_success": (
                bool(align_result.get("success")) if align_result else None
            ),
        }

    @staticmethod
    def _required_next_conversation_seq(record: FlushRecord) -> int:
        prefix, separator, raw_sequence = record.conversation_id.rpartition("::")
        if separator != "::" or prefix != record.thread_id:
            raise ValueError(
                "pending flush conversation_id must be thread_id::N"
            )
        try:
            sequence = int(raw_sequence)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "pending flush conversation_id sequence must be an integer"
            ) from exc
        if sequence < 0:
            raise ValueError(
                "pending flush conversation_id sequence must be non-negative"
            )
        return sequence + 1

    def _advance_conversation_sequence(
        self,
        record: FlushRecord,
    ) -> Dict[str, int]:
        load = getattr(self._runtime, "load_conversation_seq", None)
        persist = getattr(self._runtime, "persist_conversation_seq", None)
        if not callable(load) or not callable(persist):
            raise RuntimeError(
                "runtime host cannot persist a conversation sequence"
            )
        required = self._required_next_conversation_seq(record)
        before = max(0, int(load(record.thread_id) or 0))
        if before < required:
            persist(record.thread_id, required)
        after = max(0, int(load(record.thread_id) or 0))
        if after < required:
            raise RuntimeError(
                "conversation sequence persistence did not reach the flush boundary"
            )
        return {
            "before": before,
            "required": required,
            "after": after,
        }

    def _recover_record(self, record: FlushRecord) -> Dict[str, Any]:
        # Validate the final durable boundary before performing any replayable
        # or irreversible work for this record.
        self._required_next_conversation_seq(record)
        if not callable(getattr(self._runtime, "load_conversation_seq", None)):
            raise RuntimeError(
                "runtime host cannot load a conversation sequence"
            )
        if not callable(
            getattr(self._runtime, "persist_conversation_seq", None)
        ):
            raise RuntimeError(
                "runtime host cannot persist a conversation sequence"
            )
        unsupported = sorted(
            set(record.materializations).difference({"dialogue"})
        )
        if unsupported:
            raise RuntimeError(
                "unsupported flush materialization destination(s): "
                + ", ".join(unsupported)
            )

        if "dialogue" not in record.materializations:
            dialogue_payload = self._dialogue_payload_for_recovery(record)
            if dialogue_payload is not None:
                record = self._journal.stage_materialization(
                    record.flush_id,
                    "dialogue",
                    self._dialogue_materialization_payload(dialogue_payload),
                )

        runtime_state = record.runtimes.get(self.runtime_id)
        committed_before_journal_ack = False
        committed_result: Optional[Dict[str, Any]] = None
        if runtime_state is not None and runtime_state.status != "committed":
            load_committed = getattr(
                self._runtime,
                "load_committed_flush_segment",
                None,
            )
            if callable(load_committed):
                loaded = load_committed(
                    record.thread_id,
                    conversation_id=record.conversation_id,
                    flush_id=f"{record.flush_id}:{self.runtime_id}",
                )
                if isinstance(loaded, Mapping):
                    committed_result = dict(loaded)
                    self._journal.mark_runtime_committed(
                        record.flush_id,
                        self.runtime_id,
                        committed_result,
                    )
                    committed_before_journal_ack = True

        if committed_result is None:
            runtime_result = self.commit_runtime(
                record.thread_id,
                conversation_id=record.conversation_id,
                flush_snapshot=record.snapshot,
                defer_completion=True,
                payload={
                    "recovery": "startup",
                    "external_dialogue_written": bool(
                        record.materializations.get("dialogue") is not None
                        and record.materializations["dialogue"].status
                        == "delivered"
                    ),
                },
            )
            committed_result = dict(runtime_result.get("runtime") or {})
        else:
            runtime_result = {
                "runtime": committed_result,
            }
        refreshed = self._journal.get(record.flush_id)
        if refreshed is None:
            raise RuntimeError(
                f"missing flush journal record after commit: {record.flush_id}"
            )

        materialization_replayed = False
        dialogue_state = refreshed.materializations.get("dialogue")
        if dialogue_state is not None and dialogue_state.status == "delivered":
            delivered_result = dialogue_state.result
            if not isinstance(delivered_result, Mapping) or not bool(
                delivered_result.get("success", False)
            ):
                raise RuntimeError(
                    "delivered dialogue materialization has no successful result"
                )
        elif dialogue_state is not None:
            staged = dialogue_state.payload
            if not isinstance(staged, Mapping):
                raise TypeError(
                    "dialogue materialization payload must be a mapping"
                )
            dialogue_payload = staged.get("dialogue_payload")
            if not isinstance(dialogue_payload, Mapping):
                raise TypeError(
                    "dialogue materialization requires dialogue_payload"
                )
            frozen_dialogue = deepcopy(dict(dialogue_payload))
            dialogue_id = str(
                frozen_dialogue.get("dialogue_id", "") or ""
            ).strip()
            if not dialogue_id:
                raise ValueError(
                    "dialogue materialization dialogue_id is required"
                )
            persist = getattr(
                getattr(self._runtime, "agent", None),
                "persist_dialogue_payload",
                None,
            )
            if not callable(persist):
                raise RuntimeError(
                    "runtime agent does not support persist_dialogue_payload"
                )
            raw_result = persist(
                dialogue_payload=frozen_dialogue,
                thread_id=record.thread_id,
                reason="startup_flush_recovery",
                source="runtime_flush_startup_recovery",
            )
            if not isinstance(raw_result, Mapping):
                raise TypeError(
                    "persist_dialogue_payload must return a mapping"
                )
            write_result = dict(raw_result)
            if not bool(write_result.get("success", False)):
                raise RuntimeError(
                    str(
                        write_result.get(
                            "error",
                            "dialogue materialization recovery failed",
                        )
                        or "dialogue materialization recovery failed"
                    )
                )
            returned_dialogue_id = str(
                write_result.get("dialogue_id", "") or ""
            ).strip()
            if returned_dialogue_id and returned_dialogue_id != dialogue_id:
                raise RuntimeError(
                    "dialogue materialization returned a different dialogue_id"
                )
            self._journal.mark_materialization_delivered(
                record.flush_id,
                "dialogue",
                self._summarize_dialogue_result(write_result),
            )
            materialization_replayed = True

        sequence = self._advance_conversation_sequence(record)
        completed = self._journal.mark_completed(record.flush_id)
        return {
            "flush_id": record.flush_id,
            "thread_id": record.thread_id,
            "conversation_id": record.conversation_id,
            "status": completed.status,
            "runtime": dict(runtime_result.get("runtime") or {}),
            "runtime_commit_reconciled": committed_before_journal_ack,
            "materialization_replayed": materialization_replayed,
            "conversation_sequence": sequence,
        }

    def recover_pending(self) -> Dict[str, Any]:
        """Recover pending flush sagas oldest-first without crossing failures.

        A failed record remains pending.  Any newer record for the same thread
        is reported as blocked, while independent threads continue recovery.
        """

        with self._recovery_lock:
            pending = self._journal.list_pending()
            recovered = []
            failed = []
            blocked = []
            failed_by_thread: Dict[str, str] = {}
            for record in pending:
                blocking_flush_id = failed_by_thread.get(record.thread_id)
                if blocking_flush_id:
                    blocked.append(
                        {
                            "flush_id": record.flush_id,
                            "thread_id": record.thread_id,
                            "conversation_id": record.conversation_id,
                            "status": "blocked",
                            "blocked_by_flush_id": blocking_flush_id,
                        }
                    )
                    continue
                try:
                    recovered.append(self._recover_record(record))
                except Exception as exc:
                    logger.exception(
                        "Pending runtime flush recovery failed flush_id=%s thread_id=%s",
                        record.flush_id,
                        record.thread_id,
                    )
                    failed_by_thread[record.thread_id] = record.flush_id
                    failed.append(
                        {
                            "flush_id": record.flush_id,
                            "thread_id": record.thread_id,
                            "conversation_id": record.conversation_id,
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
            report = {
                "attempted": True,
                "attempted_at": datetime.now(timezone.utc).isoformat().replace(
                    "+00:00",
                    "Z",
                ),
                "pending_before": len(pending),
                "recovered": recovered,
                "recovered_count": len(recovered),
                "failed": failed,
                "failure_count": len(failed),
                "blocked": blocked,
                "blocked_count": len(blocked),
                "blocked_threads": sorted(failed_by_thread),
                "pending_after": len(self._journal.list_pending()),
            }
            self._last_recovery = deepcopy(report)
            return deepcopy(report)

    def has_pending(self, *, thread_id: Optional[str] = None) -> bool:
        """Return whether an unfinished durable flush fences this scope."""

        tid = str(thread_id or "").strip()
        return bool(
            self._journal.list_pending(
                thread_id=tid if tid else None,
            )
        )

    def health(self) -> Dict[str, Any]:
        with self._recovery_lock:
            return {
                "persistent": self._journal.path is not None,
                "path": str(self._journal.path) if self._journal.path else None,
                "pending_flushes": len(self._journal.list_pending()),
                "startup_recovery": deepcopy(self._last_recovery),
            }

    def close(self) -> None:
        self._journal.close()


__all__ = ["RuntimeFlushOrchestrator"]
