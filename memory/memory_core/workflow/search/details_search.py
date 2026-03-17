#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Action search workflow.

Searches scene `facts` by cosine similarity on atomic-fact embeddings.
"""

import json
import logging
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def search_details(
    detail_query: str,
    scene_dir: Path,
    embed_func: Callable[[str], List[float]],
    topk: int = 5,
) -> Dict[str, Any]:
    """
    Search action details by semantic similarity.

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
        logger.warning("search_details: query embedding is empty")
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

            action_embedding = action_item.get("embedding")
            if not _is_valid_embedding(action_embedding):
                action_embedding = _embed_text(
                    embed_func=embed_func,
                    text=atomic_fact_text,
                    context="atomic_fact",
                )
            if not _is_valid_embedding(action_embedding):
                continue

            similarity = _cosine_similarity(query_embedding, action_embedding)
            candidates.append(
                {
                    "scene_id": scene_id,
                    "similarity": float(similarity),
                    "Atomic fact": atomic_fact_text,
                    "evidence": action_item.get("evidence", {}),
                }
            )

    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    top_results = candidates[:safe_topk]

    result["results"] = top_results
    result["matched_count"] = len(top_results)
    result["hit"] = len(top_results) > 0
    return result


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


def search_actions(
    action_query: str,
    scene_dir: Path,
    embed_func: Callable[[str], List[float]],
    topk: int = 5,
) -> Dict[str, Any]:
    """Backward-compatible alias of `search_details`."""
    return search_details(
        detail_query=action_query,
        scene_dir=scene_dir,
        embed_func=embed_func,
        topk=topk,
    )
