#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge/dedup strategies for entity profile attributes and events."""

from __future__ import annotations

import json
import logging
import math
import re
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .library import AttributeEntry, EventEntry

logger = logging.getLogger(__name__)


class ProfileMergeStrategy(ABC):
    """Strategy contract for profile item merge decisions."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def find_attribute_merge_target(
        self,
        candidate: AttributeEntry,
        existing_items: Sequence[AttributeEntry],
    ) -> Optional[int]:
        """Return index of existing item to merge, or None for new item."""

    @abstractmethod
    def find_event_merge_target(
        self,
        candidate: EventEntry,
        existing_items: Sequence[EventEntry],
    ) -> Optional[int]:
        """Return index of existing item to merge, or None for new item."""


class EmbedThenLLMProfileMergeStrategy(ProfileMergeStrategy):
    """Embedding filter + LLM confirmation dedup pipeline."""

    def __init__(
        self,
        llm_func: Callable[[str], str],
        embed_func: Callable[[str], List[float]],
        similarity_threshold: float = 0.78,
        top_k: int = 3,
        auto_merge_threshold: float = 0.93,
    ):
        super().__init__(name="EmbedThenLLMProfileMergeStrategy")
        self.llm_func = llm_func
        self.embed_func = embed_func
        self.similarity_threshold = float(similarity_threshold)
        self.top_k = int(max(1, top_k))
        self.auto_merge_threshold = float(auto_merge_threshold)

    @staticmethod
    def build_attribute_text(field: str, content: Sequence[str]) -> str:
        value = ", ".join([str(x).strip() for x in content if str(x).strip()])
        field_text = str(field or "").strip()
        if field_text and value:
            return f"{field_text}: {value}"
        if field_text:
            return field_text
        return value

    @staticmethod
    def build_event_text(event: str, abstract_time: Sequence[str]) -> str:
        event_text = str(event or "").strip()
        abstract = ", ".join([str(x).strip() for x in abstract_time if str(x).strip()])
        if event_text and abstract:
            return f"{event_text} | abstract_time={abstract}"
        if event_text:
            return event_text
        return abstract

    @staticmethod
    def _normalize_attribute_field(field: str) -> str:
        text = str(field or "").strip().lower()
        if not text:
            return ""
        text = re.sub(r"[\s\-]+", "_", text)
        text = re.sub(r"[^a-z0-9_]", "", text)
        text = re.sub(r"_+", "_", text).strip("_")
        return text

    def _attribute_field_for_match(self, item: AttributeEntry) -> str:
        field = self._normalize_attribute_field(getattr(item, "field", ""))
        if field:
            return field
        text = str(getattr(item, "text", "") or "").strip()
        if ":" in text:
            head = text.split(":", 1)[0]
            field_from_text = self._normalize_attribute_field(head)
            if field_from_text:
                return field_from_text
        return self._normalize_attribute_field(text)

    def prepare_attribute_candidate(self, item: AttributeEntry) -> AttributeEntry:
        item.text = str(item.text or "").strip() or self.build_attribute_text(item.field, item.content)
        if not item.embedding:
            item.embedding = self._embed_text(item.text)
        return item

    def prepare_event_candidate(self, item: EventEntry) -> EventEntry:
        item.text = str(item.text or "").strip() or self.build_event_text(item.event, item.abstract_time)
        if not item.embedding:
            item.embedding = self._embed_text(item.text)
        return item

    def find_attribute_merge_target(
        self,
        candidate: AttributeEntry,
        existing_items: Sequence[AttributeEntry],
    ) -> Optional[int]:
        if not existing_items:
            return None

        candidate = self.prepare_attribute_candidate(candidate)
        scored = self._collect_similar_attribute_candidates(candidate, existing_items)
        if not scored:
            return None

        llm_index = self._llm_decide_attribute(candidate, scored)
        if llm_index is not None:
            return llm_index

        if scored and scored[0][1] >= self.auto_merge_threshold:
            return scored[0][0]
        return None

    def find_event_merge_target(
        self,
        candidate: EventEntry,
        existing_items: Sequence[EventEntry],
    ) -> Optional[int]:
        if not existing_items:
            return None

        candidate = self.prepare_event_candidate(candidate)
        if not candidate.embedding:
            return None

        scored = self._collect_similar_event_candidates(candidate, existing_items)
        if not scored:
            return None

        llm_index = self._llm_decide_event(candidate, scored)
        if llm_index is not None:
            return llm_index

        if scored and scored[0][1] >= self.auto_merge_threshold:
            return scored[0][0]
        return None

    def _collect_similar_attribute_candidates(
        self,
        candidate: AttributeEntry,
        existing_items: Sequence[AttributeEntry],
    ) -> List[Tuple[int, float, AttributeEntry]]:
        candidate_field = self._attribute_field_for_match(candidate)
        if not candidate_field:
            return []

        candidate_field_embedding = self._embed_text(candidate_field)
        scored: List[Tuple[int, float, AttributeEntry]] = []
        for idx, item in enumerate(existing_items):
            if not isinstance(item, AttributeEntry):
                continue
            existing_field = self._attribute_field_for_match(item)
            if not existing_field:
                continue

            # Exact normalized field match should always be considered as top candidate.
            if existing_field == candidate_field:
                scored.append((idx, 1.0, item))
                continue

            if not candidate_field_embedding:
                continue

            existing_field_embedding = self._embed_text(existing_field)
            if not existing_field_embedding:
                continue

            similarity = self._cosine_similarity(candidate_field_embedding, existing_field_embedding)
            if similarity >= self.similarity_threshold:
                scored.append((idx, similarity, item))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[: self.top_k]

    def _collect_similar_event_candidates(
        self,
        candidate: EventEntry,
        existing_items: Sequence[EventEntry],
    ) -> List[Tuple[int, float, EventEntry]]:
        scored: List[Tuple[int, float, EventEntry]] = []
        for idx, item in enumerate(existing_items):
            if not isinstance(item, EventEntry):
                continue
            text = str(item.text or "").strip() or self.build_event_text(item.event, item.abstract_time)
            if not text:
                continue
            if not item.embedding:
                item.embedding = self._embed_text(text)
            if not item.embedding:
                continue
            similarity = self._cosine_similarity(candidate.embedding, item.embedding)
            if similarity >= self.similarity_threshold:
                scored.append((idx, similarity, item))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[: self.top_k]

    def _llm_decide_attribute(
        self,
        candidate: AttributeEntry,
        candidates: Sequence[Tuple[int, float, AttributeEntry]],
    ) -> Optional[int]:
        candidate_field = self._attribute_field_for_match(candidate)
        candidate_text = str(candidate.text or "").strip()
        candidate_lines: List[str] = []
        for rank, (idx, score, item) in enumerate(candidates, start=1):
            entry_field = self._attribute_field_for_match(item)
            entry_text = str(item.text or "").strip() or self.build_attribute_text(item.field, item.content)
            candidate_lines.append(
                f"{rank}. idx={idx}, score={score:.4f}, field={item.field}, match_field={entry_field}, content={item.content}, text={entry_text}"
            )

        prompt = (
            "You are a strict deduplication judge for entity attributes.\n"
            "Decide if CANDIDATE expresses the same FIELD semantic as one of EXISTING entries.\n"
            "Content can differ and should be merged when field semantic is same.\n\n"
            f"CANDIDATE:\nfield={candidate.field}, match_field={candidate_field}, content={candidate.content}, text={candidate_text}\n\n"
            "EXISTING:\n"
            + "\n".join(candidate_lines)
            + "\n\nOutput JSON only with this schema:\n"
            '{"decision":"SAME"|"NEW","match_idx":<int or null>}\n'
            "Rules:\n"
            "- SAME when field semantics are the same (e.g. like/likes/preference), even if content differs.\n"
            "- If uncertain, return NEW."
        )
        response = self._safe_llm_call(prompt)
        return self._parse_llm_decision(response)

    def _llm_decide_event(
        self,
        candidate: EventEntry,
        candidates: Sequence[Tuple[int, float, EventEntry]],
    ) -> Optional[int]:
        candidate_text = str(candidate.text or "").strip()
        candidate_lines: List[str] = []
        for rank, (idx, score, item) in enumerate(candidates, start=1):
            entry_text = str(item.text or "").strip() or self.build_event_text(item.event, item.abstract_time)
            candidate_lines.append(
                f"{rank}. idx={idx}, score={score:.4f}, event={item.event}, "
                f"actual_time={[x.to_dict() for x in item.actual_time]}, "
                f"abstract_time={item.abstract_time}, text={entry_text}"
            )

        prompt = (
            "You are a strict deduplication judge for entity events.\n"
            "Decide if CANDIDATE event is the same event type as one of EXISTING entries.\n"
            "Time information should NOT force NEW when the event itself is same.\n\n"
            f"CANDIDATE:\n{candidate_text}\n\n"
            "EXISTING:\n"
            + "\n".join(candidate_lines)
            + "\n\nOutput JSON only with this schema:\n"
            '{"decision":"SAME"|"NEW","match_idx":<int or null>}\n'
            "Rules:\n"
            "- SAME when core event semantics match.\n"
            "- If uncertain, return NEW."
        )
        response = self._safe_llm_call(prompt)
        return self._parse_llm_decision(response)

    def _safe_llm_call(self, prompt: str) -> str:
        try:
            result = self.llm_func(prompt)
            return str(result or "").strip()
        except Exception as exc:
            logger.warning("Profile dedup LLM call failed: %s", exc)
            return ""

    def _parse_llm_decision(self, response: str) -> Optional[int]:
        text = str(response or "").strip()
        if not text:
            return None

        payload = text
        fenced = re.search(r"```(?:json)?\s*(.*?)```", payload, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            payload = fenced.group(1).strip()

        parsed_idx = self._try_parse_json_idx(payload)
        if parsed_idx is not None:
            return parsed_idx

        if re.search(r"\bNEW\b", payload, flags=re.IGNORECASE):
            return None

        num_match = re.search(r"\b(\d+)\b", payload)
        if num_match:
            return int(num_match.group(1))

        return None

    @staticmethod
    def _try_parse_json_idx(payload: str) -> Optional[int]:
        try:
            parsed = json.loads(payload)
        except Exception:
            object_match = re.search(r"\{[\s\S]*\}", payload)
            if not object_match:
                return None
            try:
                parsed = json.loads(object_match.group(0))
            except Exception:
                return None

        if not isinstance(parsed, dict):
            return None

        decision = str(parsed.get("decision", "")).strip().upper()
        if decision == "NEW":
            return None
        idx = parsed.get("match_idx")
        if idx is None:
            return None
        try:
            return int(idx)
        except Exception:
            return None

    def _embed_text(self, text: str) -> Optional[List[float]]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return None
        try:
            vec = self.embed_func(cleaned)
        except Exception as exc:
            logger.warning("Profile dedup embedding failed: %s", exc)
            return None
        if isinstance(vec, list) and vec:
            try:
                return [float(x) for x in vec]
            except Exception:
                return None
        return None

    @staticmethod
    def _cosine_similarity(vec_a: Optional[List[float]], vec_b: Optional[List[float]]) -> float:
        if not isinstance(vec_a, list) or not isinstance(vec_b, list) or not vec_a or not vec_b:
            return 0.0
        limit = min(len(vec_a), len(vec_b))
        if limit <= 0:
            return 0.0
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for i in range(limit):
            a = float(vec_a[i])
            b = float(vec_b[i])
            dot += a * b
            norm_a += a * a
            norm_b += b * b
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
