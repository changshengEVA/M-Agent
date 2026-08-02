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
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
)

from .flush_journal import FlushJournal, FlushRecord


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
        return build_dialogue_payload_from_scene_entries(
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

    def health(self) -> Dict[str, Any]:
        return {
            "persistent": self._journal.path is not None,
            "path": str(self._journal.path) if self._journal.path else None,
            "pending_flushes": len(self._journal.list_pending()),
        }

    def close(self) -> None:
        self._journal.close()


__all__ = ["RuntimeFlushOrchestrator"]
