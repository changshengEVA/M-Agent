"""Lightweight, self-healing local vector store for chat episodic RAG."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from m_agent.paths import resolve_project_path


logger = logging.getLogger(__name__)

EmbedFn = Callable[[str], List[float]]

INDEX_FORMAT_VERSION = 1
CHUNK_FORMAT_VERSION = 1
OFFLINE_EMBED_VERSION = "blake2b-feature-hash-v1"
OFFLINE_EMBED_DIM = 256
DEFAULT_MIN_SCORE = 0.2


def _offline_tokens(text: str) -> List[str]:
    """Tokenize Latin words and overlapping CJK characters/bigrams."""

    tokens: List[str] = []
    for segment in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", str(text or "").lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", segment):
            tokens.extend(segment)
            tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
        else:
            tokens.append(segment)
    return tokens


def _default_embed(text: str) -> List[float]:
    """Return a cross-process-stable feature-hash embedding.

    Python's built-in ``hash()`` is salted per process, so it must never be
    used for a persisted index.  BLAKE2b gives every process the same bucket
    and sign for a token; ``OFFLINE_EMBED_VERSION`` is persisted alongside the
    matrix so a future algorithm change causes an automatic rebuild.
    """

    vec = np.zeros(OFFLINE_EMBED_DIM, dtype=np.float32)
    for token in _offline_tokens(text):
        digest = hashlib.blake2b(
            token.encode("utf-8"),
            digest_size=16,
            person=b"m-agent-rag-v1",
        ).digest()
        bucket = int.from_bytes(digest[:8], "little") % OFFLINE_EMBED_DIM
        sign = 1.0 if digest[8] & 1 else -1.0
        vec[bucket] += sign
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec.tolist()


def _canonical_embed_model(embed_model: str) -> str:
    key = str(embed_model or "hash").strip().lower()
    if key in {"hash", "offline", "test"}:
        return "hash"
    if key in {"alibaba", "dashscope"}:
        return "alibaba"
    if key in {"bge", "local"}:
        return "bge"
    logger.warning("Unknown embed_model=%r; using hash embedder", embed_model)
    return "hash"


def _resolve_embed_fn(embed_model: str) -> EmbedFn:
    key = _canonical_embed_model(embed_model)
    if key == "hash":
        return _default_embed
    if key == "alibaba":
        from m_agent.load_model.AlibabaEmbeddingCall import get_embed_model

        fn = get_embed_model()
        return lambda text: list(fn(text))  # type: ignore[arg-type]
    if key == "bge":
        from m_agent.load_model.BGEcall import get_embed_model

        fn = get_embed_model()
        return lambda text: list(fn(text))  # type: ignore[arg-type]
    raise AssertionError(f"unhandled embed model: {key}")


def _embedder_version(embed_model: str) -> str:
    if embed_model == "hash":
        return OFFLINE_EMBED_VERSION
    return f"{embed_model}-adapter-v1"


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


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    """Durably write one index artifact and atomically replace its target."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class RagStore:
    """Per-workflow chunk index stored as JSONL + numpy embeddings.

    ``chunks.jsonl`` is the recoverable source data.  ``embeddings.npy`` is a
    derived index and ``index.meta.json`` is its commit marker.  Metadata is
    replaced last; on startup any mismatch, truncation, or incompatible
    feature-hash version rebuilds the matrix from chunks.
    """

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
        self.metadata_path = self.root / "index.meta.json"
        self.embed_model = _canonical_embed_model(embed_model)
        self.embed_version = _embedder_version(self.embed_model)
        self._fixed_dimension = (
            OFFLINE_EMBED_DIM if self.embed_model == "hash" else None
        )
        self._embed = _resolve_embed_fn(self.embed_model)
        self._lock = threading.RLock()
        self._chunks: List[Dict[str, Any]] = []
        self._matrix = self._empty_matrix()
        self._last_rebuild_reason: Optional[str] = None
        self._load()

    @property
    def chunk_count(self) -> int:
        with self._lock:
            return len(self._chunks)

    @property
    def last_rebuild_reason(self) -> Optional[str]:
        """Why the current process rebuilt the derived index, if it did."""

        return self._last_rebuild_reason

    def _empty_matrix(self) -> np.ndarray:
        dimension = self._fixed_dimension or 0
        return np.zeros((0, dimension), dtype=np.float32)

    def _read_chunks(self) -> Tuple[List[Dict[str, Any]], bytes, bool]:
        if not self.chunks_path.exists():
            return [], b"", True
        raw = self.chunks_path.read_bytes()
        chunks: List[Dict[str, Any]] = []
        clean = True
        for line_number, raw_line in enumerate(raw.splitlines(), start=1):
            if not raw_line.strip():
                continue
            try:
                item = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.warning(
                    "Ignoring corrupt RAG chunk line %s in %s",
                    line_number,
                    self.chunks_path,
                )
                clean = False
                continue
            if not isinstance(item, dict):
                logger.warning(
                    "Ignoring non-object RAG chunk line %s in %s",
                    line_number,
                    self.chunks_path,
                )
                clean = False
                continue
            if not isinstance(item.get("meta", {}), dict):
                item = dict(item)
                item["meta"] = {}
                clean = False
            chunks.append(item)
        return chunks, raw, clean

    def _read_metadata(self) -> Optional[Dict[str, Any]]:
        if not self.metadata_path.exists():
            return None
        try:
            payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("RAG metadata is unreadable: %s", self.metadata_path)
            return None
        return payload if isinstance(payload, dict) else None

    def _metadata_is_compatible(
        self,
        metadata: Dict[str, Any],
        *,
        chunks: Sequence[Dict[str, Any]],
        chunks_bytes: bytes,
    ) -> bool:
        embedding = metadata.get("embedding")
        chunk_info = metadata.get("chunks")
        if not isinstance(embedding, dict) or not isinstance(chunk_info, dict):
            return False
        if metadata.get("format_version") != INDEX_FORMAT_VERSION:
            return False
        if metadata.get("chunk_format_version") != CHUNK_FORMAT_VERSION:
            return False
        if str(metadata.get("workflow_id", "")) != self.workflow_id:
            return False
        if embedding.get("model") != self.embed_model:
            return False
        if embedding.get("version") != self.embed_version:
            return False
        if embedding.get("dtype") != "float32":
            return False
        if self._fixed_dimension is not None:
            if embedding.get("dimension") != self._fixed_dimension:
                return False
        if chunk_info.get("count") != len(chunks):
            return False
        if chunk_info.get("sha256") != _sha256(chunks_bytes):
            return False
        return True

    def _load_matrix(
        self,
        metadata: Dict[str, Any],
        *,
        chunk_count: int,
    ) -> Optional[np.ndarray]:
        embedding_info = metadata.get("embeddings")
        if not isinstance(embedding_info, dict) or not self.embeddings_path.exists():
            return None
        try:
            raw = self.embeddings_path.read_bytes()
            if embedding_info.get("sha256") != _sha256(raw):
                return None
            loaded = np.load(io.BytesIO(raw), allow_pickle=False)
            matrix = np.asarray(loaded, dtype=np.float32)
        except (OSError, ValueError, TypeError):
            return None
        if matrix.ndim != 2 or matrix.shape[0] != chunk_count:
            return None
        if list(matrix.shape) != embedding_info.get("shape"):
            return None
        dimension = metadata.get("embedding", {}).get("dimension")
        if not isinstance(dimension, int) or dimension < 0:
            return None
        if matrix.shape[1] != dimension:
            return None
        if not bool(np.all(np.isfinite(matrix))):
            return None
        return matrix

    def _load(self) -> None:
        with self._lock:
            artifacts_present = any(
                path.exists()
                for path in (
                    self.chunks_path,
                    self.embeddings_path,
                    self.metadata_path,
                )
            )
            chunks, chunks_bytes, chunks_clean = self._read_chunks()
            metadata = self._read_metadata()
            matrix: Optional[np.ndarray] = None
            reason: Optional[str] = None

            if not chunks_clean:
                reason = "corrupt_chunks"
            elif metadata is None:
                reason = "missing_or_corrupt_metadata"
            elif not self._metadata_is_compatible(
                metadata,
                chunks=chunks,
                chunks_bytes=chunks_bytes,
            ):
                reason = "incompatible_metadata"
            else:
                matrix = self._load_matrix(metadata, chunk_count=len(chunks))
                if matrix is None:
                    reason = "missing_or_corrupt_embeddings"

            if reason is None and matrix is not None:
                self._chunks = chunks
                self._matrix = matrix
                return

            # A brand-new empty store has nothing to rebuild or persist yet.
            if not artifacts_present and not chunks:
                self._chunks = []
                self._matrix = self._empty_matrix()
                return

            rebuilt = self._build_matrix(chunks)
            self._persist_state_locked(chunks, rebuilt)
            self._chunks = chunks
            self._matrix = rebuilt
            self._last_rebuild_reason = reason
            logger.warning("Rebuilt RAG index (%s) at %s", reason, self.root)

    def _embed_vector(self, text: str) -> np.ndarray:
        vector = np.asarray(self._embed(str(text or "")), dtype=np.float32)
        if vector.ndim != 1 or vector.size == 0:
            raise ValueError("embedding function must return a non-empty 1-D vector")
        if not bool(np.all(np.isfinite(vector))):
            raise ValueError("embedding function returned non-finite values")
        if self._fixed_dimension is not None and vector.size != self._fixed_dimension:
            raise ValueError(
                f"embedding dimension mismatch: expected {self._fixed_dimension}, "
                f"got {vector.size}"
            )
        return vector

    def _build_matrix(self, chunks: Sequence[Dict[str, Any]]) -> np.ndarray:
        if not chunks:
            return self._empty_matrix()
        vectors = [
            self._embed_vector(str(chunk.get("text", "") or ""))
            for chunk in chunks
        ]
        dimension = int(vectors[0].size)
        if any(int(vector.size) != dimension for vector in vectors):
            raise ValueError("embedding function returned inconsistent dimensions")
        return np.vstack(vectors).astype(np.float32, copy=False)

    @staticmethod
    def _serialize_chunks(chunks: Sequence[Dict[str, Any]]) -> bytes:
        lines = [
            json.dumps(chunk, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for chunk in chunks
        ]
        text = "\n".join(lines)
        if lines:
            text += "\n"
        return text.encode("utf-8")

    @staticmethod
    def _serialize_matrix(matrix: np.ndarray) -> bytes:
        output = io.BytesIO()
        np.save(output, np.asarray(matrix, dtype=np.float32), allow_pickle=False)
        return output.getvalue()

    def _build_metadata(
        self,
        *,
        chunks: Sequence[Dict[str, Any]],
        chunks_bytes: bytes,
        matrix: np.ndarray,
        matrix_bytes: bytes,
    ) -> Dict[str, Any]:
        dimension = int(matrix.shape[1]) if matrix.ndim == 2 else 0
        return {
            "format_version": INDEX_FORMAT_VERSION,
            "chunk_format_version": CHUNK_FORMAT_VERSION,
            "workflow_id": self.workflow_id,
            "embedding": {
                "model": self.embed_model,
                "version": self.embed_version,
                "dimension": dimension,
                "dtype": "float32",
            },
            "chunks": {
                "count": len(chunks),
                "sha256": _sha256(chunks_bytes),
            },
            "embeddings": {
                "shape": list(matrix.shape),
                "sha256": _sha256(matrix_bytes),
            },
        }

    def _persist_state_locked(
        self,
        chunks: Sequence[Dict[str, Any]],
        matrix: np.ndarray,
    ) -> None:
        """Persist a complete candidate state, publishing metadata last."""

        chunks_bytes = self._serialize_chunks(chunks)
        matrix_bytes = self._serialize_matrix(matrix)
        metadata = self._build_metadata(
            chunks=chunks,
            chunks_bytes=chunks_bytes,
            matrix=matrix,
            matrix_bytes=matrix_bytes,
        )
        metadata_bytes = (
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")

        # Each replacement is atomic.  The metadata commit marker is last, so
        # an interruption between files is detected and rebuilt on next load.
        _atomic_write(self.chunks_path, chunks_bytes)
        _atomic_write(self.embeddings_path, matrix_bytes)
        _atomic_write(self.metadata_path, metadata_bytes)

    def _extend_matrix(
        self,
        chunks: Sequence[Dict[str, Any]],
        new_records: Sequence[Dict[str, Any]],
    ) -> np.ndarray:
        if not new_records:
            return self._matrix.copy()
        new_vectors = [
            self._embed_vector(str(record.get("text", "") or ""))
            for record in new_records
        ]
        dimension = int(new_vectors[0].size)
        if any(int(vector.size) != dimension for vector in new_vectors):
            raise ValueError("embedding function returned inconsistent dimensions")
        additions = np.vstack(new_vectors).astype(np.float32, copy=False)
        if self._matrix.shape[0] == 0:
            return additions
        if self._matrix.ndim != 2 or self._matrix.shape[1] != dimension:
            # The external provider changed dimensions without a config name
            # change.  Treat it as an incompatible derived index.
            return self._build_matrix(chunks)
        return np.vstack([self._matrix, additions]).astype(np.float32, copy=False)

    def _next_chunk_number_locked(self) -> int:
        highest = 0
        for chunk in self._chunks:
            match = re.fullmatch(r"chunk_(\d+)", str(chunk.get("chunk_id", "")))
            if match:
                highest = max(highest, int(match.group(1)))
        return max(highest, len(self._chunks)) + 1

    def has_thread(self, thread_id: str) -> bool:
        """Return whether the exact persisted thread has at least one chunk."""

        normalized = str(thread_id or "").strip()
        if not normalized:
            return False
        with self._lock:
            return any(
                str(chunk.get("thread_id", "") or "") == normalized
                for chunk in self._chunks
            )

    @staticmethod
    def _round_trace_notes(item: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw_notes = item.get("episode_notes")
        if not isinstance(raw_notes, list):
            item_meta = item.get("meta")
            trace = item_meta.get("trace_summary") if isinstance(item_meta, dict) else None
            raw_notes = trace.get("episode_notes") if isinstance(trace, dict) else None
        if not isinstance(raw_notes, list):
            return []
        return [copy.deepcopy(note) for note in raw_notes if isinstance(note, dict)]

    @staticmethod
    def _round_record(
        *,
        chunk_id: str,
        thread_id: str,
        item: Dict[str, Any],
        meta: Optional[Dict[str, Any]],
        round_index: Optional[int] = None,
        dialogue_id: str = "",
    ) -> Dict[str, Any]:
        text = (
            f"User: {str(item.get('user_message', '') or '').strip()}\n"
            f"Assistant: {str(item.get('assistant_message', '') or '').strip()}"
        ).strip()
        record_meta = copy.deepcopy(dict(meta or {}))
        if round_index is not None:
            record_meta["round_index"] = round_index
        if dialogue_id:
            record_meta["dialogue_id"] = dialogue_id
        notes = RagStore._round_trace_notes(item)
        if notes:
            trace = record_meta.get("trace_summary")
            trace = dict(trace) if isinstance(trace, dict) else {}
            trace["episode_notes"] = notes
            record_meta["trace_summary"] = trace
        return {
            "chunk_id": chunk_id,
            "thread_id": str(thread_id or "").strip(),
            "text": text,
            "meta": record_meta,
        }

    def append_round(
        self,
        *,
        thread_id: str,
        user_message: str,
        assistant_message: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            chunk_number = self._next_chunk_number_locked()
            chunk_id = f"chunk_{chunk_number:05d}"
            record = self._round_record(
                chunk_id=chunk_id,
                thread_id=thread_id,
                item={
                    "user_message": user_message,
                    "assistant_message": assistant_message,
                },
                meta=meta,
            )
            candidate_chunks = [*self._chunks, record]
            candidate_matrix = self._extend_matrix(candidate_chunks, [record])
            self._persist_state_locked(candidate_chunks, candidate_matrix)
            self._chunks = candidate_chunks
            self._matrix = candidate_matrix
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
            or (normalized[0].get("dialogue_id", "") if normalized else "")
            or ""
        ).strip()
        normalized_thread_id = str(thread_id or "").strip()
        with self._lock:
            if dialogue_id:
                existing_ids = [
                    str(item.get("chunk_id", "") or "")
                    for item in self._chunks
                    if str(item.get("thread_id", "") or "") == normalized_thread_id
                    and str(
                        dict(item.get("meta") or {}).get("dialogue_id", "") or ""
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

            start = self._next_chunk_number_locked()
            new_records: List[Dict[str, Any]] = []
            ids: List[str] = []
            for offset, item in enumerate(normalized):
                chunk_id = f"chunk_{start + offset:05d}"
                record = self._round_record(
                    chunk_id=chunk_id,
                    thread_id=normalized_thread_id,
                    item=item,
                    meta=meta,
                    round_index=offset + 1,
                    dialogue_id=dialogue_id,
                )
                new_records.append(record)
                ids.append(chunk_id)

            if new_records:
                candidate_chunks = [*self._chunks, *new_records]
                candidate_matrix = self._extend_matrix(
                    candidate_chunks,
                    new_records,
                )
                self._persist_state_locked(candidate_chunks, candidate_matrix)
                self._chunks = candidate_chunks
                self._matrix = candidate_matrix
        return {
            "success": True,
            "workflow_id": self.workflow_id,
            "chunk_ids": ids,
            "replayed": False,
        }

    def merge_notes_on_last_chunk(
        self,
        episode_notes: Sequence[Dict[str, Any]],
        *,
        thread_id: str,
        dialogue_id: str,
    ) -> bool:
        """Merge legacy flush notes into one precisely scoped last chunk."""

        notes = [copy.deepcopy(note) for note in episode_notes if isinstance(note, dict)]
        if not notes:
            return False
        normalized_thread_id = str(thread_id or "").strip()
        normalized_dialogue_id = str(dialogue_id or "").strip()
        if not normalized_thread_id or not normalized_dialogue_id:
            return False

        with self._lock:
            target_index: Optional[int] = None
            for index in range(len(self._chunks) - 1, -1, -1):
                chunk = self._chunks[index]
                chunk_meta = chunk.get("meta")
                chunk_dialogue_id = (
                    str(chunk_meta.get("dialogue_id", "") or "").strip()
                    if isinstance(chunk_meta, dict)
                    else ""
                )
                if (
                    str(chunk.get("thread_id", "") or "") == normalized_thread_id
                    and chunk_dialogue_id == normalized_dialogue_id
                ):
                    target_index = index
                    break
            if target_index is None:
                return False

            candidate_chunks = copy.deepcopy(self._chunks)
            target = candidate_chunks[target_index]
            meta = target.get("meta")
            meta = dict(meta) if isinstance(meta, dict) else {}
            trace = meta.get("trace_summary")
            trace = dict(trace) if isinstance(trace, dict) else {}
            trace["episode_notes"] = notes
            meta["trace_summary"] = trace
            target["meta"] = meta
            self._persist_state_locked(candidate_chunks, self._matrix)
            self._chunks = candidate_chunks
        return True

    def search(
        self,
        question: str,
        *,
        thread_id: str = "",
        top_k: int = 5,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> List[Dict[str, Any]]:
        query = str(question or "").strip()
        normalized_thread_id = str(thread_id or "").strip()
        if not query or not normalized_thread_id:
            return []
        threshold = float(min_score)
        if not np.isfinite(threshold):
            raise ValueError("min_score must be finite")

        with self._lock:
            candidate_indices = [
                index
                for index, chunk in enumerate(self._chunks)
                if str(chunk.get("thread_id", "") or "") == normalized_thread_id
            ]
            if not candidate_indices:
                return []

            query_vector = self._embed_vector(query)
            if self._matrix.ndim != 2 or self._matrix.shape[1] != query_vector.size:
                rebuilt = self._build_matrix(self._chunks)
                self._persist_state_locked(self._chunks, rebuilt)
                self._matrix = rebuilt
                self._last_rebuild_reason = "query_dimension_mismatch"

            candidate_matrix = self._matrix[candidate_indices]
            scores = _cosine_scores(candidate_matrix, query_vector)
            ranked = [
                (candidate_indices[offset], float(score))
                for offset, score in enumerate(scores)
                if np.isfinite(score) and float(score) >= threshold
            ]
            ranked.sort(key=lambda item: (-item[1], item[0]))
            selected = ranked[: max(1, int(top_k))]

            hits: List[Dict[str, Any]] = []
            for rank, (index, score) in enumerate(selected, start=1):
                chunk = self._chunks[index]
                hits.append(
                    {
                        "text": str(chunk.get("text", "") or ""),
                        "score": score,
                        "source": str(
                            chunk.get("chunk_id", "") or f"rank:{rank}"
                        ),
                        "thread_id": str(chunk.get("thread_id", "") or ""),
                        "meta": copy.deepcopy(dict(chunk.get("meta") or {})),
                    }
                )
            return hits


def _safe_slug(text: str, fallback: str = "default") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(text or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    return cleaned[:64] or fallback


__all__ = [
    "CHUNK_FORMAT_VERSION",
    "DEFAULT_MIN_SCORE",
    "INDEX_FORMAT_VERSION",
    "OFFLINE_EMBED_DIM",
    "OFFLINE_EMBED_VERSION",
    "RagStore",
]
