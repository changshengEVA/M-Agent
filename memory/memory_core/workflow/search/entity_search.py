#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entity search workflow.

Resolution pipeline (read-only, no merge writeback):
1. Exact entity-id hit
2. Exact/alias name hit
3. Fuzzy candidate recall
4. LLM final judgment on recalled candidates
"""

from __future__ import annotations

import json
import logging
import math
import re
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


def resolve_entity_id(
    entity_name_or_id: str,
    entity_library: Any,
    llm_func: Optional[Callable[[str], str]] = None,
    embed_func: Optional[Callable[[str], List[float]]] = None,
    max_candidates: int = 3,
    string_similarity_threshold: float = 0.72,
    embedding_similarity_threshold: float = 0.55,
) -> Dict[str, Any]:
    """
    Resolve entity query into canonical entity id.

    Returns:
    {
      "hit": bool,
      "query": str,
      "entity_id": str,
      "canonical_name": str,
      "aliases": [str],
      "match_type": str,
      "candidates": [
        {"entity_id": str, "score": float, "matched_name": str, "source": str}
      ],
      "judge": {
        "used": bool,
        "decision": "HIT"|"MISS",
        "entity_id": str,
        "confidence": float,
        "reason": str,
        "raw_response": str
      }
    }
    """
    query = str(entity_name_or_id or "").strip()
    safe_max_candidates = _safe_int(max_candidates, default=3, minimum=1, maximum=20)
    result: Dict[str, Any] = {
        "hit": False,
        "query": query,
        "entity_id": "",
        "canonical_name": "",
        "aliases": [],
        "match_type": "",
    }

    if not query:
        result["error"] = "empty_query"
        return result

    # 1) exact entity-id hit
    record = _safe_get_entity(entity_library, query)
    if record is not None:
        _fill_hit_result(result, record, match_type="entity_id_exact")
        return result

    # 2) exact name/alias hit
    record = _safe_get_entity_by_name(entity_library, query)
    if record is not None:
        _fill_hit_result(result, record, match_type="name_exact")
        return result

    # 2.1) case-insensitive exact name hit
    ci_matches = _case_insensitive_exact_name_matches(entity_library, query)
    if len(ci_matches) == 1:
        record = _safe_get_entity(entity_library, ci_matches[0]["entity_id"])
        if record is not None:
            _fill_hit_result(result, record, match_type="name_case_insensitive_exact")
            return result

    # 3) fuzzy recall candidates
    candidates = _recall_candidates(
        query=query,
        entity_library=entity_library,
        embed_func=embed_func,
        max_candidates=safe_max_candidates,
        string_similarity_threshold=float(string_similarity_threshold),
        embedding_similarity_threshold=float(embedding_similarity_threshold),
    )

    # include case-insensitive exact ambiguity as candidates
    for item in ci_matches:
        _merge_candidate(candidates, item)

    ranked_candidates = _rank_candidates(candidates, topk=safe_max_candidates)
    if ranked_candidates:
        result["candidates"] = ranked_candidates

    # 4) LLM final judgment
    if ranked_candidates and callable(llm_func):
        judge = _llm_judge_candidates(query=query, candidates=ranked_candidates, llm_func=llm_func)
        result["judge"] = judge

        if str(judge.get("decision", "")).upper() == "HIT":
            judged_entity_id = str(judge.get("entity_id", "") or "").strip()
            if judged_entity_id:
                record = _safe_get_entity(entity_library, judged_entity_id)
                if record is not None:
                    _fill_hit_result(result, record, match_type="llm_fuzzy_confirmed")
                    return result
    elif ranked_candidates:
        result["judge"] = {
            "used": False,
            "decision": "MISS",
            "entity_id": "",
            "confidence": 0.0,
            "reason": "llm_unavailable",
            "raw_response": "",
        }

    return result


def _recall_candidates(
    query: str,
    entity_library: Any,
    embed_func: Optional[Callable[[str], List[float]]],
    max_candidates: int,
    string_similarity_threshold: float,
    embedding_similarity_threshold: float,
) -> Dict[str, Dict[str, Any]]:
    pool: Dict[str, Dict[str, Any]] = {}

    # A. existing substring-based search
    try:
        raw_candidates = entity_library.search(query, max_results=max_candidates * 4)
    except Exception as exc:
        logger.warning("entity_search: library.search failed (%s)", exc)
        raw_candidates = []

    for item in raw_candidates or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        entity_id = str(item[0] or "").strip()
        if not entity_id:
            continue
        score = _safe_float(item[1], default=0.0)
        matched_name = str(item[2] or "").strip()
        _merge_candidate(
            pool,
            {
                "entity_id": entity_id,
                "score": max(0.0, min(1.0, score)),
                "matched_name": matched_name,
                "source": "substring",
            },
        )

    # B. string similarity recall on all names/aliases
    name_to_entity = getattr(entity_library, "name_to_entity", {})
    if isinstance(name_to_entity, dict):
        query_lower = query.lower()
        for name, entity_id_any in name_to_entity.items():
            name_text = str(name or "").strip()
            entity_id = str(entity_id_any or "").strip()
            if not name_text or not entity_id:
                continue

            ratio = SequenceMatcher(None, query_lower, name_text.lower()).ratio()
            if ratio < string_similarity_threshold:
                continue
            _merge_candidate(
                pool,
                {
                    "entity_id": entity_id,
                    "score": max(0.0, min(1.0, ratio)),
                    "matched_name": name_text,
                    "source": "string_fuzzy",
                },
            )

    # C. embedding recall on entity canonical-name embeddings (best-effort)
    if callable(embed_func):
        query_embedding = _embed_text(embed_func=embed_func, text=query)
        if query_embedding:
            entities = []
            try:
                entities = list(entity_library.get_all_entities())
            except Exception:
                entities = []

            embedding_scored_all: List[Tuple[str, float, str]] = []
            for record in entities:
                entity_id = str(getattr(record, "entity_id", "") or "").strip()
                canonical_name = str(getattr(record, "canonical_name", "") or entity_id).strip()
                entity_embedding = getattr(record, "embedding", None)
                if not entity_id or not canonical_name:
                    continue
                if not _is_valid_embedding(entity_embedding):
                    continue

                similarity = _cosine_similarity(query_embedding, entity_embedding)
                embedding_scored_all.append((entity_id, similarity, canonical_name))
                if similarity < embedding_similarity_threshold:
                    continue
                _merge_candidate(
                    pool,
                    {
                        "entity_id": entity_id,
                        "score": max(0.0, min(1.0, similarity)),
                        "matched_name": canonical_name,
                        "source": "embedding",
                    },
                )

            # Fallback: if thresholding produced no candidates, still return top-k embedding recalls.
            # This keeps downstream LLM judgment available instead of returning empty-candidate miss.
            if not pool and embedding_scored_all:
                embedding_scored_all.sort(key=lambda x: x[1], reverse=True)
                for entity_id, similarity, canonical_name in embedding_scored_all[:max_candidates]:
                    _merge_candidate(
                        pool,
                        {
                            "entity_id": entity_id,
                            "score": max(0.0, min(1.0, similarity)),
                            "matched_name": canonical_name,
                            "source": "embedding_topk_fallback",
                        },
                    )

    return pool


def _llm_judge_candidates(
    query: str,
    candidates: Sequence[Dict[str, Any]],
    llm_func: Callable[[str], str],
) -> Dict[str, Any]:
    candidate_lines: List[str] = []
    valid_ids: List[str] = []
    ranked: List[Dict[str, Any]] = []

    for rank, item in enumerate(candidates, start=1):
        entity_id = str(item.get("entity_id", "") or "").strip()
        score = _safe_float(item.get("score", 0.0), default=0.0)
        matched_name = str(item.get("matched_name", "") or "").strip()
        source = str(item.get("source", "") or "").strip()
        if not entity_id:
            continue
        valid_ids.append(entity_id)
        ranked.append(item)
        candidate_lines.append(
            f"{rank}. entity_id={entity_id}, matched_name={matched_name}, score={score:.4f}, source={source}"
        )

    prompt = (
        "You are an entity-grounding judge.\n"
        "Given a user query and recalled entity candidates, decide whether query truly hits an existing entity.\n"
        "Be strict and avoid over-matching.\n\n"
        f"QUERY:\n{query}\n\n"
        "CANDIDATES:\n"
        + ("\n".join(candidate_lines) if candidate_lines else "- none")
        + "\n\nOutput JSON only:\n"
        '{"decision":"HIT"|"MISS","entity_id":"<candidate id or empty>","match_rank":<int or null>,"confidence":<0~1>,"reason":"..."}\n'
        "Rules:\n"
        "- HIT only when query clearly refers to one candidate.\n"
        "- entity_id must come from candidate list.\n"
        "- If uncertain, return MISS."
    )

    raw_response = ""
    try:
        llm_output = llm_func(prompt)
        content = getattr(llm_output, "content", llm_output)
        raw_response = str(content or "").strip()
    except Exception as exc:
        return {
            "used": True,
            "decision": "MISS",
            "entity_id": "",
            "confidence": 0.0,
            "reason": f"llm_error: {exc}",
            "raw_response": "",
        }

    parsed = _parse_llm_judge_response(raw_response)
    decision = str(parsed.get("decision", "MISS") or "MISS").upper()
    entity_id = str(parsed.get("entity_id", "") or "").strip()
    match_rank = parsed.get("match_rank")
    confidence = _safe_float(parsed.get("confidence", 0.0), default=0.0)
    reason = str(parsed.get("reason", "") or "").strip()

    if not entity_id and isinstance(match_rank, int):
        idx = match_rank - 1
        if 0 <= idx < len(ranked):
            entity_id = str(ranked[idx].get("entity_id", "") or "").strip()

    if decision == "HIT" and entity_id not in valid_ids:
        decision = "MISS"
        entity_id = ""
        reason = reason or "entity_id_not_in_candidates"

    if decision != "HIT":
        entity_id = ""

    return {
        "used": True,
        "decision": decision if decision in {"HIT", "MISS"} else "MISS",
        "entity_id": entity_id,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": reason,
        "raw_response": raw_response,
    }


def _parse_llm_judge_response(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {"decision": "MISS", "entity_id": "", "match_rank": None, "confidence": 0.0, "reason": ""}

    payload = text
    fenced = re.search(r"```(?:json)?\s*(.*?)```", payload, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        payload = fenced.group(1).strip()

    parsed = _try_parse_json_object(payload)
    if isinstance(parsed, dict):
        decision = str(parsed.get("decision", "") or "").strip().upper()
        entity_id = str(parsed.get("entity_id", "") or "").strip()
        match_rank = _try_int(parsed.get("match_rank"))
        confidence = _safe_float(parsed.get("confidence", 0.0), default=0.0)
        reason = str(parsed.get("reason", "") or "").strip()
        return {
            "decision": decision if decision in {"HIT", "MISS"} else "MISS",
            "entity_id": entity_id,
            "match_rank": match_rank,
            "confidence": confidence,
            "reason": reason,
        }

    decision = "MISS"
    if re.search(r"\bHIT\b", text, flags=re.IGNORECASE):
        decision = "HIT"
    elif re.search(r"\bMISS\b", text, flags=re.IGNORECASE):
        decision = "MISS"

    return {
        "decision": decision,
        "entity_id": "",
        "match_rank": _extract_first_int(text),
        "confidence": 0.0,
        "reason": "",
    }


def _try_parse_json_object(payload: str) -> Optional[Dict[str, Any]]:
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    object_match = re.search(r"\{[\s\S]*\}", payload)
    if not object_match:
        return None
    try:
        parsed = json.loads(object_match.group(0))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _case_insensitive_exact_name_matches(entity_library: Any, query: str) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    name_to_entity = getattr(entity_library, "name_to_entity", {})
    if not isinstance(name_to_entity, dict):
        return output

    query_lower = query.lower()
    for name, entity_id_any in name_to_entity.items():
        name_text = str(name or "").strip()
        entity_id = str(entity_id_any or "").strip()
        if not name_text or not entity_id:
            continue
        if name_text.lower() != query_lower:
            continue
        output.append(
            {
                "entity_id": entity_id,
                "score": 1.0,
                "matched_name": name_text,
                "source": "name_case_insensitive",
            }
        )
    return output


def _rank_candidates(pool: Dict[str, Dict[str, Any]], topk: int) -> List[Dict[str, Any]]:
    ranked = sorted(
        pool.values(),
        key=lambda x: (_safe_float(x.get("score", 0.0), 0.0), str(x.get("matched_name", ""))),
        reverse=True,
    )
    return ranked[:topk]


def _merge_candidate(pool: Dict[str, Dict[str, Any]], item: Dict[str, Any]) -> None:
    entity_id = str(item.get("entity_id", "") or "").strip()
    if not entity_id:
        return

    score = _safe_float(item.get("score", 0.0), default=0.0)
    matched_name = str(item.get("matched_name", "") or "").strip()
    source = str(item.get("source", "") or "").strip()

    existing = pool.get(entity_id)
    if existing is None:
        pool[entity_id] = {
            "entity_id": entity_id,
            "score": score,
            "matched_name": matched_name,
            "source": source,
        }
        return

    prev_score = _safe_float(existing.get("score", 0.0), default=0.0)
    if score > prev_score:
        existing["score"] = score
        if matched_name:
            existing["matched_name"] = matched_name

    prev_source = str(existing.get("source", "") or "").strip()
    if source and source != prev_source:
        existing["source"] = ",".join([s for s in [prev_source, source] if s])


def _fill_hit_result(result: Dict[str, Any], record: Any, match_type: str) -> None:
    result.update(
        {
            "hit": True,
            "entity_id": str(getattr(record, "entity_id", "") or ""),
            "canonical_name": str(getattr(record, "canonical_name", "") or ""),
            "aliases": list(getattr(record, "aliases", []) or []),
            "match_type": str(match_type or ""),
        }
    )


def _safe_get_entity(entity_library: Any, entity_id: str) -> Optional[Any]:
    try:
        return entity_library.get_entity(entity_id)
    except Exception:
        return None


def _safe_get_entity_by_name(entity_library: Any, name: str) -> Optional[Any]:
    try:
        return entity_library.get_entity_by_name(name)
    except Exception:
        return None


def _embed_text(embed_func: Callable[[str], List[float]], text: str) -> Optional[List[float]]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return None
    try:
        vec = embed_func(cleaned)
    except Exception:
        return None
    if _is_valid_embedding(vec):
        return [float(x) for x in vec]
    return None


def _cosine_similarity(vec_a: Any, vec_b: Any) -> float:
    if not _is_valid_embedding(vec_a) or not _is_valid_embedding(vec_b):
        return 0.0
    if len(vec_a) != len(vec_b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b):
        a_f = float(a)
        b_f = float(b)
        dot += a_f * b_f
        norm_a += a_f * a_f
        norm_b += b_f * b_f
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _is_valid_embedding(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    return all(isinstance(v, (int, float)) for v in value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _extract_first_int(text: str) -> Optional[int]:
    matched = re.search(r"\b(\d+)\b", str(text or ""))
    if not matched:
        return None
    try:
        return int(matched.group(1))
    except Exception:
        return None


def _try_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _safe_int(value: Any, default: int, minimum: int = 1, maximum: int = 20) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    parsed = max(minimum, parsed)
    return min(maximum, parsed)
