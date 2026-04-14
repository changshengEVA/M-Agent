from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, TypedDict


WorkspaceStatus = Literal["SUFFICIENT", "INSUFFICIENT", "INVALID"]


class WorkspaceDocument(TypedDict):
    evidence_id: str
    content: str
    source_action_id: str
    recall_score: float | None
    rerank_score: float | None


class WorkspaceState(TypedDict):
    round_id: int
    original_question: str
    cur_query: str
    evidences: List[WorkspaceDocument]
    kept_evidence_ids: List[str]
    status: WorkspaceStatus
    gap_type: str | None


def build_episode_ref(dialogue_id: str, episode_id: str, segment_id: str | None = None) -> str:
    base = f"{str(dialogue_id or '').strip()}:{str(episode_id or '').strip()}".strip(":")
    seg = str(segment_id or "").strip()
    if seg and base:
        return f"{base}:{seg}"
    return base


def split_episode_ref(ref: str) -> tuple[str, str, str]:
    """Split a ref string into (dialogue_id, episode_id, segment_id).

    Supports both two-part ``dialogue_id:episode_id`` and three-part
    ``dialogue_id:episode_id:segment_id`` formats.
    """
    parts = str(ref or "").strip().split(":")
    dialogue_id = parts[0].strip() if len(parts) > 0 else ""
    episode_id = parts[1].strip() if len(parts) > 1 else ""
    segment_id = parts[2].strip() if len(parts) > 2 else ""
    return dialogue_id, episode_id, segment_id


@dataclass
class Workspace:
    max_keep: int = 6
    round_id: int = 0
    status: WorkspaceStatus = "INVALID"
    gap_type: Optional[str] = None
    original_question: str = ""
    cur_query: str = ""
    _documents: Dict[str, WorkspaceDocument] = field(default_factory=dict)
    _insert_order: List[str] = field(default_factory=list)
    kept_evidence_ids: List[str] = field(default_factory=list)

    def set_round(self, round_id: int) -> None:
        self.round_id = max(0, int(round_id))

    def upsert(self, doc: WorkspaceDocument) -> None:
        evidence_id = str(doc.get("evidence_id", "")).strip()
        if not evidence_id:
            return

        existing = self._documents.get(evidence_id)
        if existing is None:
            self._documents[evidence_id] = dict(doc)  # type: ignore[assignment]
            self._insert_order.append(evidence_id)
            return

        new_score = _best_score(doc)
        old_score = _best_score(existing)
        if new_score is not None and (old_score is None or new_score > old_score):
            self._documents[evidence_id] = dict(doc)  # type: ignore[assignment]
        elif new_score == old_score and len(doc.get("content", "")) > len(existing.get("content", "")):
            self._documents[evidence_id] = dict(doc)  # type: ignore[assignment]

    def extend(self, documents: List[WorkspaceDocument]) -> None:
        for item in documents:
            if isinstance(item, dict):
                self.upsert(item)

    def all_evidences(self) -> List[WorkspaceDocument]:
        return [self._documents[eid] for eid in self._insert_order if eid in self._documents]

    def keep_top(self, max_keep: Optional[int] = None, protected_ids: Optional[List[str]] = None) -> List[str]:
        limit = self.max_keep if max_keep is None else max(1, int(max_keep))
        protected = set(protected_ids or [])
        kept = [eid for eid in self._insert_order
                if eid in protected and eid in self._documents]
        remaining_slots = limit - len(kept)
        if remaining_slots > 0:
            candidates = []
            for index, eid in enumerate(self._insert_order):
                if eid in protected or eid not in self._documents:
                    continue
                score = _best_score(self._documents[eid])
                if score is None:
                    score = 0.0
                candidates.append((eid, float(score), -index))
            candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
            kept.extend(eid for eid, _, _ in candidates[:remaining_slots])
        self.kept_evidence_ids = kept
        return list(self.kept_evidence_ids)

    def kept_evidences(self) -> List[WorkspaceDocument]:
        ids = self.kept_evidence_ids or self.keep_top()
        return [self._documents[eid] for eid in ids if eid in self._documents]

    def mark(self, status: WorkspaceStatus, gap_type: Optional[str] = None) -> None:
        self.status = status
        self.gap_type = str(gap_type).strip() or None

    def snapshot(self) -> WorkspaceState:
        return {
            "round_id": self.round_id,
            "original_question": self.original_question,
            "cur_query": self.cur_query,
            "evidences": self.all_evidences(),
            "kept_evidence_ids": list(self.kept_evidence_ids),
            "status": self.status,
            "gap_type": self.gap_type,
        }

    def evidence_count(self) -> int:
        return len(self._documents)

    def extend_and_track_new(self, documents: List[WorkspaceDocument]) -> List[str]:
        """Extend workspace and return the IDs of genuinely new documents."""
        before = set(self._documents.keys())
        self.extend(documents)
        return [eid for eid in self._insert_order if eid not in before]

    def set_rerank_score(self, evidence_id: str, score: float) -> None:
        doc = self._documents.get(evidence_id)
        if doc is not None:
            doc["rerank_score"] = score

    def has_evidence(self, evidence_id: str) -> bool:
        return str(evidence_id or "").strip() in self._documents

    def get_document(self, evidence_id: str) -> Optional[WorkspaceDocument]:
        return self._documents.get(str(evidence_id or "").strip())

    def remove_evidence(self, evidence_id: str) -> bool:
        """Remove an evidence document and drop it from kept ids."""
        eid = str(evidence_id or "").strip()
        if not eid or eid not in self._documents:
            return False
        del self._documents[eid]
        self._insert_order = [x for x in self._insert_order if x != eid]
        if self.kept_evidence_ids:
            self.kept_evidence_ids = [x for x in self.kept_evidence_ids if x != eid]
        return True

    def to_evidence_summary(self, max_items: int = 6) -> str:
        blocks: List[str] = []
        for idx, doc in enumerate(self.kept_evidences()[: max(1, int(max_items))], start=1):
            eid = doc.get("evidence_id", "")
            content = str(doc.get("content", "")).strip()
            if not content:
                content = "(no content)"
            blocks.append(f"=== Evidence [{idx}]  ref: {eid} ===\n{content}")
        return "\n\n".join(blocks).strip()


def _best_score(doc: WorkspaceDocument) -> float | None:
    """Return the best available score (rerank preferred over recall)."""
    rerank = doc.get("rerank_score")
    if rerank is not None:
        return float(rerank)
    recall = doc.get("recall_score")
    if recall is not None:
        return float(recall)
    return None
