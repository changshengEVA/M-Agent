"""In-memory Scene log with optional JSONL persistence."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from m_agent.runtime.domain.contracts import SceneEntry

# Windows and cross-platform unsafe filename characters (incl. scoped ids like `user::thread`).
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def scene_persist_file_stem(conversation_id: str) -> str:
    """Return a filesystem-safe stem for per-conversation Scene JSONL files."""
    tid = str(conversation_id or "").strip()
    if not tid:
        return "thread"
    stem = tid.replace("::", "__")
    stem = _UNSAFE_FILENAME_CHARS.sub("_", stem)
    stem = stem.rstrip(". ") or "thread"
    if len(stem) > 200:
        digest = hashlib.sha256(tid.encode("utf-8")).hexdigest()[:16]
        stem = f"{stem[:180]}_{digest}"
    return stem


class SceneLogStore:
    """Conversation-scoped chronological Scene log (cross-transaction)."""

    def __init__(
        self,
        *,
        persist_dir: Optional[Path] = None,
        persist_enabled: bool = True,
        runtime_store: Optional[Any] = None,
    ) -> None:
        self._persist_dir = persist_dir
        self._persist_enabled = bool(persist_enabled and persist_dir is not None)
        self._runtime_store = runtime_store
        self._lock = threading.RLock()
        self._entries: Dict[str, List[SceneEntry]] = {}
        self._entries_by_append_id: Dict[str, Dict[str, SceneEntry]] = {}
        self._seq: Dict[str, int] = {}
        self._flush_seq: Dict[str, int] = {}
        self._loaded_threads: set[str] = set()
        self._load_locks: Dict[str, threading.RLock] = {}

    def _meta_path(self, thread_id: str) -> Optional[Path]:
        if not self._persist_enabled or self._persist_dir is None:
            return None
        tid = str(thread_id or "").strip()
        if not tid:
            return None
        return self._persist_dir / f"{scene_persist_file_stem(tid)}.meta.json"

    def _conversation_state_path(self, thread_id: str) -> Optional[Path]:
        if not self._persist_enabled or self._persist_dir is None:
            return None
        tid = str(thread_id or "").strip()
        if not tid:
            return None
        return self._persist_dir / f"{scene_persist_file_stem(tid)}.conversation.json"

    def _infer_conversation_seq(self, thread_id: str) -> int:
        if not self._persist_enabled or self._persist_dir is None:
            return 0
        tid = str(thread_id or "").strip()
        base_stem = scene_persist_file_stem(tid)
        pattern = re.compile(rf"^{re.escape(base_stem)}__(\d+)\.jsonl$")
        sequences = []
        for path in self._persist_dir.glob(f"{base_stem}__*.jsonl"):
            match = pattern.fullmatch(path.name)
            if match:
                sequences.append(int(match.group(1)))
        if not sequences:
            return 0
        latest = max(sequences)
        latest_conversation_id = f"{tid}::{latest}"
        return latest if self.entries_since_flush(latest_conversation_id) else latest + 1

    def load_conversation_seq(self, thread_id: str) -> int:
        """Restore the active conversation sequence for a long-lived thread."""
        tid = str(thread_id or "").strip()
        path = self._conversation_state_path(tid)
        persisted_seq = 0
        if path is not None and path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                persisted_seq = max(0, int(payload.get("conversation_seq", 0) or 0))
            except (AttributeError, json.JSONDecodeError, OSError, TypeError, ValueError):
                pass
        return max(persisted_seq, self._infer_conversation_seq(tid))

    def persist_conversation_seq(self, thread_id: str, conversation_seq: int) -> None:
        """Persist the next/current conversation sequence after a successful flush."""
        path = self._conversation_state_path(thread_id)
        if path is None:
            return
        payload = {"conversation_seq": max(0, int(conversation_seq))}
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(path)

    def _persist_flush_meta(self, thread_id: str) -> None:
        path = self._meta_path(thread_id)
        if path is None:
            return
        tid = str(thread_id or "").strip()
        payload = {"flush_seq": int(self._flush_seq.get(tid, 0))}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _load_flush_meta(self, thread_id: str) -> None:
        path = self._meta_path(thread_id)
        tid = str(thread_id or "").strip()
        if path is None or not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return
        if isinstance(payload, dict):
            self._flush_seq[tid] = max(int(self._flush_seq.get(tid, 0)), int(payload.get("flush_seq", 0) or 0))

    def ensure_thread_loaded(self, thread_id: str) -> None:
        tid = str(thread_id or "").strip()
        if not tid:
            return
        with self._lock:
            if tid in self._loaded_threads:
                return
        self.load_thread_from_disk(tid)

    def flush_watermark(self, thread_id: str) -> int:
        tid = str(thread_id or "").strip()
        if self._runtime_store is not None:
            self.ensure_thread_loaded(tid)
            state = self._runtime_store.load_conversation_state(tid)
            return int(state.get("flush_watermark", 0))
        self.ensure_thread_loaded(tid)
        with self._lock:
            return int(self._flush_seq.get(tid, 0))

    def entries_since_flush(self, thread_id: str) -> List[SceneEntry]:
        tid = str(thread_id or "").strip()
        if self._runtime_store is not None:
            self.ensure_thread_loaded(tid)
            state = self._runtime_store.load_conversation_state(tid)
            return self._runtime_store.read_scene(
                tid,
                after_seq=int(state.get("flush_watermark", 0)),
            )
        self.ensure_thread_loaded(tid)
        with self._lock:
            watermark = int(self._flush_seq.get(tid, 0))
            return [e for e in self._entries.get(tid, []) if e.seq > watermark]

    def mark_flushed(self, thread_id: str, *, through_seq: int) -> None:
        tid = str(thread_id or "").strip()
        if not tid:
            return
        if self._runtime_store is not None:
            self.ensure_thread_loaded(tid)
            self._runtime_store.mark_scene_flushed(
                tid,
                through_seq=through_seq,
            )
            return
        self.ensure_thread_loaded(tid)
        with self._lock:
            current = int(self._flush_seq.get(tid, 0))
            latest = int(self._seq.get(tid, 0))
            bounded = min(max(0, int(through_seq)), latest)
            self._flush_seq[tid] = max(current, bounded)
        self._persist_flush_meta(tid)

    def _next_seq(self, thread_id: str) -> int:
        current = int(self._seq.get(thread_id, 0))
        nxt = current + 1
        self._seq[thread_id] = nxt
        return nxt

    def _mirror_runtime_entry_to_jsonl(
        self,
        thread_id: str,
        entry: SceneEntry,
    ) -> None:
        """Keep the configured JSONL audit log in sync with SQLite authority."""

        if not self._persist_enabled or self._persist_dir is None:
            return
        append_id = str(entry.append_id or "").strip()
        if not append_id:
            return
        tid = str(thread_id or "").strip()
        with self._lock:
            mirrored = self._entries_by_append_id.setdefault(tid, {})
            if append_id in mirrored:
                return
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            path = self._persist_dir / f"{scene_persist_file_stem(tid)}.jsonl"
            with open(path, "a", encoding="utf-8") as file_obj:
                file_obj.write(
                    json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
                )
            mirrored[append_id] = entry

    def _sync_runtime_jsonl(
        self,
        thread_id: str,
        *,
        loaded: List[SceneEntry],
        canonical: List[SceneEntry],
        force_rewrite: bool = False,
    ) -> None:
        """Repair the JSONL mirror without changing canonical Scene order."""

        if not self._persist_enabled or self._persist_dir is None:
            return
        tid = str(thread_id or "").strip()
        loaded_payloads = [entry.to_dict() for entry in loaded]
        canonical_payloads = [entry.to_dict() for entry in canonical]
        is_canonical_prefix = bool(
            not force_rewrite
            and len(loaded_payloads) <= len(canonical_payloads)
            and loaded_payloads
            == canonical_payloads[: len(loaded_payloads)]
        )
        with self._lock:
            if not is_canonical_prefix:
                self._persist_dir.mkdir(parents=True, exist_ok=True)
                path = self._persist_dir / f"{scene_persist_file_stem(tid)}.jsonl"
                temporary = path.with_suffix(path.suffix + ".tmp")
                with open(temporary, "w", encoding="utf-8") as file_obj:
                    for entry in canonical:
                        file_obj.write(
                            json.dumps(entry.to_dict(), ensure_ascii=False)
                            + "\n"
                        )
                temporary.replace(path)
                self._entries_by_append_id[tid] = {
                    str(entry.append_id): entry
                    for entry in canonical
                    if entry.append_id
                }
                return
            self._entries_by_append_id[tid] = {
                str(entry.append_id): entry
                for entry in loaded
                if entry.append_id
            }
        for entry in canonical[len(loaded) :]:
            self._mirror_runtime_entry_to_jsonl(tid, entry)

    def append(
        self,
        thread_id: str,
        entry: SceneEntry,
        *,
        append_id: Optional[str] = None,
    ) -> SceneEntry:
        stored, _created = self.append_with_status(
            thread_id,
            entry,
            append_id=append_id,
        )
        return stored

    def append_with_status(
        self,
        thread_id: str,
        entry: SceneEntry,
        *,
        append_id: Optional[str] = None,
    ) -> Tuple[SceneEntry, bool]:
        tid = str(thread_id or "").strip()
        if not tid:
            raise ValueError("thread_id is required for Scene append")
        if self._runtime_store is not None:
            self.ensure_thread_loaded(tid)
            stored, created = self._runtime_store.append_scene_entry_with_status(
                tid,
                entry,
                append_id=append_id,
            )
            self._mirror_runtime_entry_to_jsonl(tid, stored)
            return stored, created
        self.ensure_thread_loaded(tid)
        with self._lock:
            stable_append_id = (
                str(append_id or entry.append_id or "").strip()
                or f"append_{uuid.uuid4().hex}"
            )
            existing = self._entries_by_append_id.setdefault(
                tid,
                {},
            ).get(stable_append_id)
            if existing is not None:
                return existing, False
            seq = self._next_seq(tid)
            stored = SceneEntry(
                seq=seq,
                occurred_at=entry.occurred_at,
                entry_type=entry.entry_type,
                actor=entry.actor,
                actor_name=entry.actor_name,
                text=entry.text,
                append_id=stable_append_id,
                transaction_id=entry.transaction_id,
                delegate_id=entry.delegate_id,
                tool_name=entry.tool_name,
                payload_ref=entry.payload_ref,
            )
            self._entries.setdefault(tid, []).append(stored)
            self._entries_by_append_id[tid][stable_append_id] = stored
            if self._persist_enabled and self._persist_dir is not None:
                self._persist_dir.mkdir(parents=True, exist_ok=True)
                path = self._persist_dir / f"{scene_persist_file_stem(tid)}.jsonl"
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(stored.to_dict(), ensure_ascii=False) + "\n")
            return stored, True

    def tail(
        self,
        thread_id: str,
        *,
        limit: int = 40,
        before_seq: Optional[int] = None,
    ) -> List[SceneEntry]:
        tid = str(thread_id or "").strip()
        cap = max(1, int(limit or 40))
        if self._runtime_store is not None:
            self.ensure_thread_loaded(tid)
            items = self._runtime_store.read_scene(
                tid,
                before_seq=before_seq,
            )
            return items[-cap:]
        self.ensure_thread_loaded(tid)
        with self._lock:
            items = list(self._entries.get(tid, []))
        if before_seq is not None:
            items = [e for e in items if e.seq < int(before_seq)]
        return items[-cap:]

    @staticmethod
    def _normalize_loaded_entries(
        thread_id: str,
        loaded: List[SceneEntry],
    ) -> List[SceneEntry]:
        if not loaded:
            return loaded
        normalized: List[SceneEntry] = []
        seen_append_ids: set[str] = set()
        for index, entry in enumerate(loaded, start=1):
            append_id = str(entry.append_id or "").strip()
            if not append_id:
                digest_input = json.dumps(
                    {
                        "conversation_id": thread_id,
                        "legacy_seq": entry.seq,
                        "index": index,
                        "occurred_at": entry.occurred_at,
                        "entry_type": entry.entry_type.value,
                        "actor": entry.actor.value,
                        "actor_name": entry.actor_name,
                        "text": entry.text,
                        "transaction_id": entry.transaction_id,
                        "delegate_id": entry.delegate_id,
                        "tool_name": entry.tool_name,
                        "payload_ref": entry.payload_ref,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                append_id = (
                    "legacy_"
                    + hashlib.sha256(
                        digest_input.encode("utf-8")
                    ).hexdigest()
                )
            if append_id in seen_append_ids:
                continue
            seen_append_ids.add(append_id)
            normalized.append(
                SceneEntry(
                    seq=len(normalized) + 1,
                    occurred_at=entry.occurred_at,
                    entry_type=entry.entry_type,
                    actor=entry.actor,
                    actor_name=entry.actor_name,
                    text=entry.text,
                    append_id=append_id,
                    transaction_id=entry.transaction_id,
                    delegate_id=entry.delegate_id,
                    tool_name=entry.tool_name,
                    payload_ref=entry.payload_ref,
                )
            )
        return normalized

    def load_thread_from_disk(self, thread_id: str) -> None:
        tid = str(thread_id or "").strip()
        if not tid:
            return
        with self._lock:
            load_lock = self._load_locks.setdefault(tid, threading.RLock())
        with load_lock:
            with self._lock:
                if tid in self._loaded_threads:
                    return
            self._load_thread_from_disk(tid)

    def _load_thread_from_disk(self, tid: str) -> None:
        loaded: List[SceneEntry] = []
        mirror_dirty = False
        if self._persist_enabled and self._persist_dir is not None:
            path = self._persist_dir / f"{scene_persist_file_stem(tid)}.jsonl"
            if path.is_file():
                with open(path, "r", encoding="utf-8") as f:
                    saw_line = False
                    last_line_terminated = True
                    for raw_line in f:
                        saw_line = True
                        last_line_terminated = raw_line.endswith(("\n", "\r"))
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            entry = SceneEntry.from_dict(json.loads(line))
                        except (json.JSONDecodeError, TypeError, ValueError):
                            mirror_dirty = True
                            continue
                        loaded.append(entry)
                    if saw_line and not last_line_terminated:
                        mirror_dirty = True
        loaded = self._normalize_loaded_entries(tid, loaded)
        if self._runtime_store is not None:
            existing = self._runtime_store.read_scene(tid)
            if not existing:
                for entry in loaded:
                    self._runtime_store.append_scene_entry(
                        tid,
                        entry,
                        append_id=entry.append_id,
                    )
                legacy_watermark = 0
                meta_path = self._meta_path(tid)
                if meta_path is not None and meta_path.is_file():
                    try:
                        meta = json.loads(
                            meta_path.read_text(encoding="utf-8")
                        )
                        legacy_watermark = int(
                            meta.get("flush_seq", 0) or 0
                        )
                    except (
                        AttributeError,
                        json.JSONDecodeError,
                        OSError,
                        TypeError,
                        ValueError,
                    ):
                        legacy_watermark = 0
                if legacy_watermark:
                    self._runtime_store.mark_scene_flushed(
                        tid,
                        through_seq=legacy_watermark,
                    )
            canonical = self._runtime_store.read_scene(tid)
            self._sync_runtime_jsonl(
                tid,
                loaded=loaded,
                canonical=canonical,
                force_rewrite=mirror_dirty,
            )
            # Publish the loaded state only after the mirror and append-id map
            # match the authoritative SQLite snapshot.  Concurrent appenders
            # wait on the per-conversation load lock until this point.
            with self._lock:
                self._loaded_threads.add(tid)
            return
        with self._lock:
            self._entries[tid] = loaded
            self._entries_by_append_id[tid] = {
                str(entry.append_id): entry
                for entry in loaded
                if entry.append_id
            }
            self._seq[tid] = max(
                (int(entry.seq) for entry in loaded),
                default=0,
            )
            if tid not in self._flush_seq:
                self._flush_seq[tid] = 0
            self._loaded_threads.add(tid)
        self._load_flush_meta(tid)


class SceneWriterAdapter:
    def __init__(self, store: SceneLogStore) -> None:
        self._store = store

    def append(
        self,
        thread_id: str,
        entry: SceneEntry,
        *,
        append_id: Optional[str] = None,
    ) -> SceneEntry:
        return self._store.append(
            thread_id,
            entry,
            append_id=append_id,
        )

    def append_with_status(
        self,
        thread_id: str,
        entry: SceneEntry,
        *,
        append_id: Optional[str] = None,
    ) -> Tuple[SceneEntry, bool]:
        return self._store.append_with_status(
            thread_id,
            entry,
            append_id=append_id,
        )


class SceneReaderAdapter:
    def __init__(self, store: SceneLogStore) -> None:
        self._store = store

    def tail(self, thread_id: str, *, limit: int = 40, before_seq: Optional[int] = None) -> List[SceneEntry]:
        return self._store.tail(thread_id, limit=limit, before_seq=before_seq)

    def entries_since_flush(self, thread_id: str) -> List[SceneEntry]:
        return self._store.entries_since_flush(thread_id)

    def mark_flushed(self, thread_id: str, *, through_seq: int) -> None:
        self._store.mark_flushed(thread_id, through_seq=through_seq)
