#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Action search workflow.

Searches scene `facts` with hybrid retrieval:
- dense cosine similarity on atomic-fact embeddings
- sparse BM25 lexical matching
then fuses rankings with RRF.
"""

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_DENSE_RECALL_TOPN = 30
_DEFAULT_SPARSE_RECALL_TOPN = 30
_DEFAULT_RRF_K = 60
_DEFAULT_DENSE_WEIGHT = 1.0
_DEFAULT_SPARSE_WEIGHT = 1.0

_DEFAULT_BM25_K1 = 1.5
_DEFAULT_BM25_B = 0.75

_WORD_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", flags=re.IGNORECASE)
_CJK_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def search_details(
    detail_query: str,
    scene_dir: Path,
    embed_func: Callable[[str], List[float]],
    topk: int = 5,
    hybrid_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Search action details by hybrid retrieval.

    Output format:
    {
      "hit": bool,
      "topk": int,
      "total_scene_count": int,
      "total_fact_count": int,
      "matched_count": int,
      "results": [
        {
          "scene_id": str,
          "similarity": float,
          "Atomic fact": str,
          "evidence": {"episode_id": str, "dialogue_id": str}
        }
      ]
    }
    """
    safe_topk = _safe_int(topk, default=5, minimum=1)
    resolved_hybrid = _resolve_hybrid_search_config(hybrid_config)
    query_text = (detail_query or "").strip()

    result: Dict[str, Any] = {
        "hit": False,
        "topk": safe_topk,
        "total_scene_count": 0,
        "total_fact_count": 0,
        "matched_count": 0,
        "results": [],
    }

    if not query_text:
        logger.warning("search_details: empty detail_query")
        return result

    if not scene_dir.exists() or not scene_dir.is_dir():
        logger.warning("search_details: scene_dir not found: %s", scene_dir)
        return result

    query_embedding = _embed_text(embed_func=embed_func, text=query_text, context="query")
    if not query_embedding:
        logger.warning("search_details: query embedding is empty, fallback to sparse retrieval only")

    query_tokens = _tokenize_sparse_text(query_text)
    if not query_embedding and not query_tokens:
        return result

    scene_files = sorted(scene_dir.glob("*.json"))
    result["total_scene_count"] = len(scene_files)

    candidates: List[Dict[str, Any]] = []
    for scene_file in scene_files:
        scene_data = _load_scene_file(scene_file)
        if not scene_data:
            continue

        scene_id = scene_data.get("scene_id") or scene_file.stem
        facts = scene_data.get("facts")
        if not isinstance(facts, list):
            continue

        for action_item in facts:
            if not isinstance(action_item, dict):
                continue

            atomic_fact_text = _extract_atomic_fact(action_item)
            if not atomic_fact_text:
                continue

            result["total_fact_count"] += 1

            dense_similarity = 0.0
            has_dense_score = False
            if query_embedding:
                action_embedding = action_item.get("embedding")
                if not _is_valid_embedding(action_embedding):
                    action_embedding = _embed_text(
                        embed_func=embed_func,
                        text=atomic_fact_text,
                        context="atomic_fact",
                    )
                if _is_valid_embedding(action_embedding):
                    dense_similarity = _cosine_similarity(query_embedding, action_embedding)
                    has_dense_score = True

            candidates.append(
                {
                    "scene_id": scene_id,
                    "dense_similarity": float(dense_similarity),
                    "has_dense_score": bool(has_dense_score),
                    "sparse_score": 0.0,
                    "sparse_text": _compose_sparse_text(action_item=action_item, atomic_fact_text=atomic_fact_text),
                    "Atomic fact": atomic_fact_text,
                    "evidence": action_item.get("evidence", {}),
                }
            )

    if not candidates:
        return result

    top_n_dense = max(safe_topk, int(resolved_hybrid["dense_recall_topn"]))
    top_n_sparse = max(safe_topk, int(resolved_hybrid["sparse_recall_topn"]))

    dense_top_indices = _dense_recall_indices(candidates=candidates, top_n=top_n_dense)
    sparse_top_indices = _sparse_recall_indices(
        candidates=candidates,
        query_tokens=query_tokens,
        top_n=top_n_sparse,
        bm25_k1=float(resolved_hybrid["bm25_k1"]),
        bm25_b=float(resolved_hybrid["bm25_b"]),
    )
    fused_indices = _fuse_with_rrf(
        candidates=candidates,
        dense_top_indices=dense_top_indices,
        sparse_top_indices=sparse_top_indices,
        rrf_k=int(resolved_hybrid["rrf_k"]),
        dense_weight=float(resolved_hybrid["dense_weight"]),
        sparse_weight=float(resolved_hybrid["sparse_weight"]),
    )

    top_results: List[Dict[str, Any]] = []
    for idx in fused_indices[:safe_topk]:
        item = candidates[idx]
        top_results.append(
            {
                "scene_id": item.get("scene_id", ""),
                # Keep backward-compatible field name/value shape.
                "similarity": float(item.get("dense_similarity", 0.0)),
                "Atomic fact": item.get("Atomic fact", ""),
                "evidence": item.get("evidence", {}),
            }
        )

    result["results"] = top_results
    result["matched_count"] = len(top_results)
    result["hit"] = len(top_results) > 0
    return result


def _compose_sparse_text(action_item: Dict[str, Any], atomic_fact_text: str) -> str:
    parts: List[str] = [atomic_fact_text]

    evidence_sentence = action_item.get("evidence_sentence")
    if isinstance(evidence_sentence, str) and evidence_sentence.strip():
        parts.append(evidence_sentence.strip())

    main_entity = action_item.get("main_entity")
    if isinstance(main_entity, str) and main_entity.strip():
        parts.append(main_entity.strip())

    other_entities = action_item.get("other_entities")
    if isinstance(other_entities, list):
        for value in other_entities:
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())

    for key in ("keywords", "event_tags"):
        value = action_item.get(key)
        if isinstance(value, list):
            for token in value:
                if isinstance(token, str) and token.strip():
                    parts.append(token.strip())

    for key in ("fact_type", "relation", "time_norm"):
        value = action_item.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())

    return " ".join(parts)


def _dense_recall_indices(candidates: List[Dict[str, Any]], top_n: int) -> List[int]:
    scored = [
        (idx, float(item.get("dense_similarity", 0.0)))
        for idx, item in enumerate(candidates)
        if bool(item.get("has_dense_score"))
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [idx for idx, _ in scored[:top_n]]


def _sparse_recall_indices(
    candidates: List[Dict[str, Any]],
    query_tokens: List[str],
    top_n: int,
    bm25_k1: float,
    bm25_b: float,
) -> List[int]:
    if not query_tokens or not candidates:
        return []

    tokenized_docs = [_tokenize_sparse_text(str(item.get("sparse_text", "") or "")) for item in candidates]
    bm25_index = _build_bm25_index(tokenized_docs)
    if bm25_index is None:
        return []

    sparse_scores = _score_bm25(
        query_tokens=query_tokens,
        bm25_index=bm25_index,
        bm25_k1=bm25_k1,
        bm25_b=bm25_b,
    )
    for idx, score in enumerate(sparse_scores):
        candidates[idx]["sparse_score"] = float(score)

    scored = [(idx, score) for idx, score in enumerate(sparse_scores) if score > 0.0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [idx for idx, _ in scored[:top_n]]


def _fuse_with_rrf(
    candidates: List[Dict[str, Any]],
    dense_top_indices: Sequence[int],
    sparse_top_indices: Sequence[int],
    rrf_k: int,
    dense_weight: float,
    sparse_weight: float,
) -> List[int]:
    dense_rank = {idx: rank for rank, idx in enumerate(dense_top_indices, start=1)}
    sparse_rank = {idx: rank for rank, idx in enumerate(sparse_top_indices, start=1)}
    all_indices = sorted(set(dense_rank) | set(sparse_rank))
    if not all_indices:
        return []

    fused: List[Tuple[int, float, float, float]] = []
    for idx in all_indices:
        dense_component = 0.0
        sparse_component = 0.0

        d_rank = dense_rank.get(idx)
        if d_rank is not None:
            dense_component = float(dense_weight) / float(rrf_k + d_rank)

        s_rank = sparse_rank.get(idx)
        if s_rank is not None:
            sparse_component = float(sparse_weight) / float(rrf_k + s_rank)

        fused_score = dense_component + sparse_component
        dense_similarity = float(candidates[idx].get("dense_similarity", 0.0))
        sparse_score = float(candidates[idx].get("sparse_score", 0.0))
        fused.append((idx, fused_score, dense_similarity, sparse_score))

    fused.sort(key=lambda x: (x[1], x[2], x[3]), reverse=True)
    return [idx for idx, _, _, _ in fused]


def _tokenize_sparse_text(text: str) -> List[str]:
    if not isinstance(text, str):
        return []

    normalized = text.strip().lower()
    if not normalized:
        return []

    word_tokens = _WORD_TOKEN_PATTERN.findall(normalized)
    cjk_tokens = _CJK_TOKEN_PATTERN.findall(normalized)
    return word_tokens + cjk_tokens


def _build_bm25_index(tokenized_docs: List[List[str]]) -> Optional[Dict[str, Any]]:
    if not tokenized_docs:
        return None

    total_docs = len(tokenized_docs)
    doc_lengths = [len(doc) for doc in tokenized_docs]
    avg_doc_len = float(sum(doc_lengths)) / float(total_docs) if total_docs > 0 else 0.0
    if avg_doc_len <= 0.0:
        return None

    postings: Dict[str, Dict[int, int]] = {}
    doc_freq: Dict[str, int] = {}
    for doc_idx, tokens in enumerate(tokenized_docs):
        term_count: Dict[str, int] = {}
        for token in tokens:
            term_count[token] = term_count.get(token, 0) + 1

        for token, tf in term_count.items():
            posting = postings.setdefault(token, {})
            posting[doc_idx] = tf
            doc_freq[token] = doc_freq.get(token, 0) + 1

    idf: Dict[str, float] = {}
    for token, df in doc_freq.items():
        numerator = float(total_docs - df) + 0.5
        denominator = float(df) + 0.5
        idf[token] = math.log(1.0 + (numerator / denominator))

    return {
        "postings": postings,
        "idf": idf,
        "doc_lengths": doc_lengths,
        "avg_doc_len": avg_doc_len,
        "total_docs": total_docs,
    }


def _score_bm25(
    query_tokens: List[str],
    bm25_index: Dict[str, Any],
    bm25_k1: float,
    bm25_b: float,
) -> List[float]:
    total_docs = int(bm25_index.get("total_docs", 0))
    scores = [0.0] * total_docs
    if total_docs <= 0:
        return scores

    postings = bm25_index.get("postings", {})
    idf_map = bm25_index.get("idf", {})
    doc_lengths = bm25_index.get("doc_lengths", [])
    avg_doc_len = float(bm25_index.get("avg_doc_len", 0.0))
    if avg_doc_len <= 0.0:
        return scores

    for token in query_tokens:
        posting = postings.get(token)
        if not isinstance(posting, dict) or not posting:
            continue

        idf = float(idf_map.get(token, 0.0))
        if idf <= 0.0:
            continue

        for doc_idx, tf_any in posting.items():
            try:
                tf = float(tf_any)
                dl = float(doc_lengths[doc_idx])
            except Exception:
                continue

            denominator = tf + bm25_k1 * (1.0 - bm25_b + bm25_b * (dl / avg_doc_len))
            if denominator <= 0.0:
                continue

            scores[doc_idx] += idf * ((tf * (bm25_k1 + 1.0)) / denominator)

    return scores


def _load_scene_file(scene_file: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(scene_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("search_details: load scene file failed: %s (%s)", scene_file, exc)
    return None


def _embed_text(
    embed_func: Callable[[str], List[float]],
    text: str,
    context: str,
) -> Optional[List[float]]:
    cleaned = (text or "").strip()
    if not cleaned:
        return None

    try:
        embedding = embed_func(cleaned)
    except Exception as exc:
        logger.warning("search_details: generate %s embedding failed: %s", context, exc)
        return None

    if _is_valid_embedding(embedding):
        return [float(v) for v in embedding]
    return None


def _extract_atomic_fact(item: Dict[str, Any]) -> str:
    candidate_keys = ("Atomic fact", "atomic_fact", "atomic fact", "Atomic_fact")
    for key in candidate_keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


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


def _safe_int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, parsed)


def _safe_float(value: Any, default: float, minimum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    if parsed < minimum:
        return minimum
    return parsed


def _resolve_hybrid_search_config(hybrid_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = hybrid_config if isinstance(hybrid_config, dict) else {}
    dense_recall_topn = _safe_int(
        raw.get("dense_recall_topn"),
        default=_DEFAULT_DENSE_RECALL_TOPN,
        minimum=1,
    )
    sparse_recall_topn = _safe_int(
        raw.get("sparse_recall_topn"),
        default=_DEFAULT_SPARSE_RECALL_TOPN,
        minimum=1,
    )
    rrf_k = _safe_int(
        raw.get("rrf_k"),
        default=_DEFAULT_RRF_K,
        minimum=1,
    )
    dense_weight = _safe_float(
        raw.get("dense_weight"),
        default=_DEFAULT_DENSE_WEIGHT,
        minimum=0.0,
    )
    sparse_weight = _safe_float(
        raw.get("sparse_weight"),
        default=_DEFAULT_SPARSE_WEIGHT,
        minimum=0.0,
    )
    if dense_weight <= 0.0 and sparse_weight <= 0.0:
        dense_weight = _DEFAULT_DENSE_WEIGHT
        sparse_weight = _DEFAULT_SPARSE_WEIGHT

    bm25_k1 = _safe_float(
        raw.get("bm25_k1"),
        default=_DEFAULT_BM25_K1,
        minimum=0.0,
    )
    bm25_b = _safe_float(
        raw.get("bm25_b"),
        default=_DEFAULT_BM25_B,
        minimum=0.0,
    )
    if bm25_b > 1.0:
        bm25_b = 1.0

    return {
        "dense_recall_topn": dense_recall_topn,
        "sparse_recall_topn": sparse_recall_topn,
        "rrf_k": rrf_k,
        "dense_weight": dense_weight,
        "sparse_weight": sparse_weight,
        "bm25_k1": bm25_k1,
        "bm25_b": bm25_b,
    }
