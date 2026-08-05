"""Simple RAG implementation of :class:`EpisodicMemoryBackend`."""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .rag_store import DEFAULT_MIN_SCORE, INDEX_FORMAT_VERSION, RagStore


def _truncate(text: str, limit: int = 1200) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _legacy_unsuffixed_thread_id(thread_id: str) -> str:
    """Return the old capability adapter's underlying thread id.

    The recall tool adapters historically pass ``<thread>:shallow`` and
    ``<thread>:deep`` while dialogue persistence stores ``<thread>``.  The old
    store ignored thread ids, masking that mismatch.  Normalising only these
    established suffixes preserves the public adapter contract while the
    store itself can enforce an exact, fail-closed thread filter.
    """

    normalized = str(thread_id or "").strip()
    for suffix in (":shallow", ":deep"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)].strip()
    return normalized


class SimpleRagEpisodicBackend:
    """Episodic backend: chunk dialogue on persist, scoped cosine retrieval."""

    def __init__(
        self,
        *,
        storage_dir: str = "data/rag/chat",
        workflow_id: str = "default",
        top_k: int = 5,
        min_score: float = DEFAULT_MIN_SCORE,
        embed_model: str = "hash",
        user_name: str = "User",
        assistant_name: str = "Memory Assistant",
    ) -> None:
        self.user_name = str(user_name or "User")
        self.assistant_name = str(assistant_name or "Memory Assistant")
        self.top_k = max(1, int(top_k))
        self.min_score = float(min_score)
        if not math.isfinite(self.min_score):
            raise ValueError("min_score must be finite")
        self.embed_model = str(embed_model or "hash")
        self.storage_dir = str(storage_dir or "data/rag/chat")
        self.workflow_id = str(workflow_id or "default").strip() or "default"
        self._store = RagStore(
            storage_dir=self.storage_dir,
            workflow_id=self.workflow_id,
            embed_model=self.embed_model,
        )

    @property
    def store(self) -> RagStore:
        return self._store

    @property
    def persistence_root(self) -> Path:
        """Directory holding chunks, embeddings, and index metadata."""

        return Path(self._store.root)

    def describe_persistence(self) -> Dict[str, Any]:
        """Paths, versions, and counts for HTTP clients / debugging."""

        return {
            "kind": "rag",
            "storage_dir": str(self.storage_dir),
            "workflow_id": self.workflow_id,
            "persistence_root": str(self.persistence_root),
            "chunks_path": str(self._store.chunks_path),
            "embeddings_path": str(self._store.embeddings_path),
            "metadata_path": str(self._store.metadata_path),
            "chunk_count": self._store.chunk_count,
            "embed_model": self._store.embed_model,
            "embed_version": self._store.embed_version,
            "index_format_version": INDEX_FORMAT_VERSION,
            "min_score": self.min_score,
            "last_rebuild_reason": self._store.last_rebuild_reason,
        }

    @staticmethod
    def _empty_recall(
        *,
        mode: str,
        thread_id: str,
        scope_thread_id: str,
        reason: str,
        supported: bool = True,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "answer": "",
            "evidence": [],
            "mode": mode,
            "backend": "rag",
            "thread_id": thread_id,
            "scope_thread_id": scope_thread_id,
            "hit": False,
            "supported": supported,
            "reason": reason,
        }
        return result

    def _storage_thread_id(self, thread_id: str) -> str:
        """Resolve an exact stored thread before applying legacy fallback."""

        normalized = str(thread_id or "").strip()
        if self._store.has_thread(normalized):
            return normalized
        return _legacy_unsuffixed_thread_id(normalized)

    def _recall(self, question: str, *, thread_id: str) -> Dict[str, Any]:
        requested_thread_id = str(thread_id or "").strip()
        scope_thread_id = self._storage_thread_id(requested_thread_id)
        query = str(question or "").strip()
        if not scope_thread_id:
            return self._empty_recall(
                mode="shallow_recall",
                thread_id=requested_thread_id,
                scope_thread_id=scope_thread_id,
                reason="empty_thread_id",
            )
        if not query:
            return self._empty_recall(
                mode="shallow_recall",
                thread_id=requested_thread_id,
                scope_thread_id=scope_thread_id,
                reason="empty_question",
            )

        hits = self._store.search(
            query,
            thread_id=scope_thread_id,
            top_k=self.top_k,
            min_score=self.min_score,
        )
        if not hits:
            result = self._empty_recall(
                mode="shallow_recall",
                thread_id=requested_thread_id,
                scope_thread_id=scope_thread_id,
                reason="no_relevant_memory",
            )
            result["min_score"] = self.min_score
            return result

        parts = [
            _truncate(hit.get("text", ""), limit=400)
            for hit in hits
            if hit.get("text")
        ]
        answer = "\n\n".join(parts).strip()
        evidence = [
            {
                "text": hit.get("text", ""),
                "score": hit.get("score", 0.0),
                "source": hit.get("source", ""),
                "thread_id": hit.get("thread_id", ""),
                "meta": dict(hit.get("meta") or {}),
            }
            for hit in hits
        ]
        return {
            "answer": answer,
            "evidence": evidence,
            "mode": "shallow_recall",
            "backend": "rag",
            "thread_id": requested_thread_id,
            "scope_thread_id": scope_thread_id,
            "hit": bool(answer),
            "supported": True,
            "min_score": self.min_score,
        }

    def shallow_recall(self, question: str, *, thread_id: str) -> Dict[str, Any]:
        return self._recall(question, thread_id=thread_id)

    def deep_recall(self, question: str, *, thread_id: str) -> Dict[str, Any]:
        """Retain the protocol method without claiming unsupported semantics.

        The local backend has no multi-hop planner or cross-chunk synthesis.
        Returning shallow results under a different name made mode selection
        misleading, so v0.2.1 makes the unsupported state explicit while
        keeping the method callable for protocol compatibility.
        """

        requested_thread_id = str(thread_id or "").strip()
        result = self._empty_recall(
            mode="deep_recall",
            thread_id=requested_thread_id,
            scope_thread_id=self._storage_thread_id(requested_thread_id),
            reason="deep_recall_not_supported",
            supported=False,
        )
        result.update(
            {
                "deprecated": True,
                "replacement": "shallow_recall",
            }
        )
        return result

    def persist_round(
        self,
        *,
        thread_id: str,
        user_message: str,
        assistant_message: str,
        agent_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        meta = {"agent_result": agent_result} if agent_result else {}
        result = self._store.append_round(
            thread_id=thread_id,
            user_message=user_message,
            assistant_message=assistant_message,
            meta=meta,
        )
        result["success"] = True
        return result

    def persist_dialogue(
        self,
        *,
        thread_id: str,
        rounds: List[Dict[str, Any]],
        reason: str,
        source: str,
        progress_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        _ = progress_callback
        result = self._store.append_dialogue(
            thread_id=thread_id,
            rounds=rounds,
            meta={"source": source, "reason": reason},
        )
        result["thread_id"] = thread_id
        return result

    def on_flush(
        self,
        *,
        thread_id: str,
        conversation_id: str,
        episode_notes: List[Dict[str, Any]],
    ) -> None:
        self._store.merge_notes_on_last_chunk(
            episode_notes,
            thread_id=self._storage_thread_id(thread_id),
            dialogue_id=conversation_id,
        )


__all__ = ["SimpleRagEpisodicBackend"]
