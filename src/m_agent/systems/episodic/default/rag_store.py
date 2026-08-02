"""Lightweight local vector store for chat episodic RAG."""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from m_agent.paths import resolve_project_path


logger = logging.getLogger(__name__)

EmbedFn = Callable[[str], List[float]]


def _default_embed(text: str) -> List[float]:
    """Deterministic bag-of-words embedding for offline/tests."""
    tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]+", str(text or "").lower())
    dim = 64
    vec = np.zeros(dim, dtype=np.float32)
    for tok in tokens:
        vec[hash(tok) % dim] += 1.0
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec.tolist()


def _resolve_embed_fn(embed_model: str) -> EmbedFn:
    key = str(embed_model or "hash").strip().lower()
    if key in {"hash", "offline", "test"}:
        return _default_embed
    if key in {"alibaba", "dashscope"}:
        from m_agent.load_model.AlibabaEmbeddingCall import get_embed_model

        fn = get_embed_model()
        return lambda text: list(fn(text))  # type: ignore[arg-type]
    if key in {"bge", "local"}:
        from m_agent.load_model.BGEcall import get_embed_model

        fn = get_embed_model()
        return lambda text: list(fn(text))  # type: ignore[arg-type]
    logger.warning("Unknown embed_model=%r; using hash embedder", embed_model)
    return _default_embed


def _cosine_scores(matrix: np.ndarray, query: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return np.array([], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    qnorm = float(np.linalg.norm(query))
    if qnorm <= 0:
        return np.zeros(matrix.shape[0], dtype=np.float32)
    denom = norms * qnorm
    denom = np.where(denom > 0, denom, 1.0)
    return (matrix @ query) / denom


class RagStore:
    """Per-workflow chunk index stored as JSONL + numpy embeddings."""

    def __init__(
        self,
        *,
        storage_dir: str | Path = "data/rag/chat",
        workflow_id: str = "default",
        embed_model: str = "hash",
    ) -> None:
        root = resolve_project_path(storage_dir)
        self.workflow_id = str(workflow_id or "default").strip() or "default"
        self.root = root / _safe_slug(self.workflow_id, fallback="default")
        self.root.mkdir(parents=True, exist_ok=True)
        self.chunks_path = self.root / "chunks.jsonl"
        self.embeddings_path = self.root / "embeddings.npy"
        self._embed = _resolve_embed_fn(embed_model)
        self._lock = threading.RLock()
        self._chunks: List[Dict[str, Any]] = []
        self._matrix: np.ndarray = np.zeros((0, 64), dtype=np.float32)
        self._load()

    @property
    def chunk_count(self) -> int:
        with self._lock:
            return len(self._chunks)

    def _load(self) -> None:
        if self.chunks_path.exists():
            lines = self.chunks_path.read_text(encoding="utf-8").splitlines()
            self._chunks = [
                json.loads(line)
                for line in lines
                if line.strip()
            ]
        else:
            self._chunks = []
        if self.embeddings_path.exists() and self._chunks:
            matrix = np.load(self.embeddings_path)
            if matrix.shape[0] == len(self._chunks):
                self._matrix = matrix.astype(np.float32)
                return
        self._rebuild_matrix()

    def _rebuild_matrix(self) -> None:
        if not self._chunks:
            self._matrix = np.zeros((0, 64), dtype=np.float32)
            return
        vectors = [self._embed(str(c.get("text", "") or "")) for c in self._chunks]
        self._matrix = np.asarray(vectors, dtype=np.float32)
        np.save(self.embeddings_path, self._matrix)

    def _persist_locked(self) -> None:
        with self.chunks_path.open("w", encoding="utf-8") as handle:
            for chunk in self._chunks:
                handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
        np.save(self.embeddings_path, self._matrix)

    def append_round(
        self,
        *,
        thread_id: str,
        user_message: str,
        assistant_message: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text = (
            f"User: {str(user_message or '').strip()}\n"
            f"Assistant: {str(assistant_message or '').strip()}"
        ).strip()
        with self._lock:
            chunk_id = f"chunk_{len(self._chunks) + 1:05d}"
            record = {
                "chunk_id": chunk_id,
                "thread_id": str(thread_id or "").strip(),
                "text": text,
                "meta": dict(meta or {}),
            }
            self._chunks.append(record)
            vec = np.asarray([self._embed(text)], dtype=np.float32)
            if self._matrix.size == 0:
                self._matrix = vec
            else:
                self._matrix = np.vstack([self._matrix, vec])
            self._persist_locked()
        return {"chunk_id": chunk_id, "workflow_id": self.workflow_id}

    def append_dialogue(
        self,
        *,
        thread_id: str,
        rounds: Sequence[Dict[str, Any]],
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized = [item for item in rounds if isinstance(item, dict)]
        dialogue_id = str(
            dict(meta or {}).get("dialogue_id", "")
            or (
                normalized[0].get("dialogue_id", "")
                if normalized
                else ""
            )
            or ""
        ).strip()
        with self._lock:
            if dialogue_id:
                existing_ids = [
                    str(item.get("chunk_id", "") or "")
                    for item in self._chunks
                    if str(item.get("thread_id", "") or "")
                    == str(thread_id or "").strip()
                    and str(
                        dict(item.get("meta") or {}).get(
                            "dialogue_id",
                            "",
                        )
                        or ""
                    ).strip()
                    == dialogue_id
                ]
                if existing_ids:
                    return {
                        "success": True,
                        "workflow_id": self.workflow_id,
                        "chunk_ids": existing_ids,
                        "replayed": True,
                    }
            ids: List[str] = []
            for idx, item in enumerate(normalized, start=1):
                round_meta = dict(meta or {})
                round_meta["round_index"] = idx
                if dialogue_id:
                    round_meta["dialogue_id"] = dialogue_id
                result = self.append_round(
                    thread_id=thread_id,
                    user_message=str(item.get("user_message", "") or ""),
                    assistant_message=str(
                        item.get("assistant_message", "") or ""
                    ),
                    meta=round_meta,
                )
                ids.append(str(result.get("chunk_id", "")))
        return {
            "success": True,
            "workflow_id": self.workflow_id,
            "chunk_ids": ids,
            "replayed": False,
        }

    def merge_notes_on_last_chunk(self, episode_notes: Sequence[Dict[str, Any]]) -> None:
        notes = [n for n in episode_notes if isinstance(n, dict)]
        if not notes:
            return
        with self._lock:
            if not self._chunks:
                return
            last = self._chunks[-1]
            meta = last.setdefault("meta", {})
            trace = meta.setdefault("trace_summary", {})
            trace["episode_notes"] = notes
            self._persist_locked()

    def search(self, question: str, *, top_k: int = 5) -> List[Dict[str, Any]]:
        query = str(question or "").strip()
        if not query or not self._chunks:
            return []
        qvec = np.asarray(self._embed(query), dtype=np.float32)
        with self._lock:
            scores = _cosine_scores(self._matrix, qvec)
        if scores.size == 0:
            return []
        order = np.argsort(-scores)[: max(1, int(top_k))]
        hits: List[Dict[str, Any]] = []
        for rank, idx in enumerate(order, start=1):
            chunk = self._chunks[int(idx)]
            hits.append(
                {
                    "text": str(chunk.get("text", "") or ""),
                    "score": float(scores[int(idx)]),
                    "source": str(chunk.get("chunk_id", "") or f"rank:{rank}"),
                    "thread_id": str(chunk.get("thread_id", "") or ""),
                    "meta": dict(chunk.get("meta") or {}),
                }
            )
        return hits


def _safe_slug(text: str, fallback: str = "default") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(text or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    return cleaned[:64] or fallback


__all__ = ["RagStore"]
