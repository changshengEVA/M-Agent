"""In-memory Scene log with optional JSONL persistence."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

from m_agent.runtime.think_life.contracts import SceneEntry

# Windows and cross-platform unsafe filename characters (incl. scoped ids like `user::thread`).
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def scene_persist_file_stem(thread_id: str) -> str:
    """Return a filesystem-safe stem for per-thread Scene JSONL files."""
    tid = str(thread_id or "").strip()
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
    """Thread-scoped chronological Scene log (cross-transaction)."""

    def __init__(self, *, persist_dir: Optional[Path] = None, persist_enabled: bool = True) -> None:
        self._persist_dir = persist_dir
        self._persist_enabled = bool(persist_enabled and persist_dir is not None)
        self._lock = threading.RLock()
        self._entries: Dict[str, List[SceneEntry]] = {}
        self._seq: Dict[str, int] = {}
        self._flush_seq: Dict[str, int] = {}

    def flush_watermark(self, thread_id: str) -> int:
        tid = str(thread_id or "").strip()
        with self._lock:
            return int(self._flush_seq.get(tid, 0))

    def entries_since_flush(self, thread_id: str) -> List[SceneEntry]:
        tid = str(thread_id or "").strip()
        with self._lock:
            watermark = int(self._flush_seq.get(tid, 0))
            return [e for e in self._entries.get(tid, []) if e.seq > watermark]

    def mark_flushed(self, thread_id: str, *, through_seq: int) -> None:
        tid = str(thread_id or "").strip()
        if not tid:
            return
        with self._lock:
            current = int(self._flush_seq.get(tid, 0))
            self._flush_seq[tid] = max(current, int(through_seq))

    def _next_seq(self, thread_id: str) -> int:
        current = int(self._seq.get(thread_id, 0))
        nxt = current + 1
        self._seq[thread_id] = nxt
        return nxt

    def append(self, thread_id: str, entry: SceneEntry) -> SceneEntry:
        tid = str(thread_id or "").strip()
        if not tid:
            raise ValueError("thread_id is required for Scene append")
        with self._lock:
            seq = self._next_seq(tid)
            stored = SceneEntry(
                seq=seq,
                occurred_at=entry.occurred_at,
                entry_type=entry.entry_type,
                actor=entry.actor,
                text=entry.text,
                transaction_id=entry.transaction_id,
                delegate_id=entry.delegate_id,
                tool_name=entry.tool_name,
                payload_ref=entry.payload_ref,
            )
            self._entries.setdefault(tid, []).append(stored)
            if self._persist_enabled and self._persist_dir is not None:
                self._persist_dir.mkdir(parents=True, exist_ok=True)
                path = self._persist_dir / f"{scene_persist_file_stem(tid)}.jsonl"
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(stored.to_dict(), ensure_ascii=False) + "\n")
            return stored

    def tail(
        self,
        thread_id: str,
        *,
        limit: int = 40,
        before_seq: Optional[int] = None,
    ) -> List[SceneEntry]:
        tid = str(thread_id or "").strip()
        cap = max(1, int(limit or 40))
        with self._lock:
            items = list(self._entries.get(tid, []))
        if before_seq is not None:
            items = [e for e in items if e.seq < int(before_seq)]
        return items[-cap:]

    def load_thread_from_disk(self, thread_id: str) -> None:
        if not self._persist_enabled or self._persist_dir is None:
            return
        tid = str(thread_id or "").strip()
        path = self._persist_dir / f"{scene_persist_file_stem(tid)}.jsonl"
        if not path.is_file():
            return
        loaded: List[SceneEntry] = []
        max_seq = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = SceneEntry.from_dict(json.loads(line))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                loaded.append(entry)
                max_seq = max(max_seq, entry.seq)
        with self._lock:
            self._entries[tid] = loaded
            self._seq[tid] = max_seq
            if tid not in self._flush_seq:
                self._flush_seq[tid] = 0


class SceneWriterAdapter:
    def __init__(self, store: SceneLogStore) -> None:
        self._store = store

    def append(self, thread_id: str, entry: SceneEntry) -> SceneEntry:
        return self._store.append(thread_id, entry)


class SceneReaderAdapter:
    def __init__(self, store: SceneLogStore) -> None:
        self._store = store

    def tail(self, thread_id: str, *, limit: int = 40, before_seq: Optional[int] = None) -> List[SceneEntry]:
        return self._store.tail(thread_id, limit=limit, before_seq=before_seq)

    def entries_since_flush(self, thread_id: str) -> List[SceneEntry]:
        return self._store.entries_since_flush(thread_id)

    def mark_flushed(self, thread_id: str, *, through_seq: int) -> None:
        self._store.mark_flushed(thread_id, through_seq=through_seq)
