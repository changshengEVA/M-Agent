#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Episode eligibility persistence helpers.

This module intentionally contains no LLM scoring/filtering logic.
It only writes:
1. per-dialogue eligibility files
2. merged episode_situation.json
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from m_agent.paths import memory_stage_dir


logger = logging.getLogger(__name__)

EPISODES_ROOT = memory_stage_dir("default", "episodes")


def ensure_directory(path: Path) -> None:
    """Ensure the target directory exists."""
    path.mkdir(parents=True, exist_ok=True)


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _normalize_result(result: Dict[str, Any], dialogue_id: str) -> Dict[str, Any]:
    episode_id = str(result.get("episode_id", "")).strip()
    normalized = {
        "episode_id": episode_id,
        "dialogue_id": str(result.get("dialogue_id", dialogue_id) or dialogue_id),
        "eligible": bool(result.get("eligible", True)),
        "reason": str(result.get("reason", "all_available_default") or "all_available_default"),
        "rule_hits": result.get("rule_hits", []),
        "scene_available": bool(result.get("scene_available", True)),
        "kg_available": bool(result.get("kg_available", True)),
        "emo_available": bool(result.get("emo_available", True)),
        "factual_novelty": int(result.get("factual_novelty", 2)),
        "emotional_novelty": int(result.get("emotional_novelty", 1)),
    }
    if not isinstance(normalized["rule_hits"], list):
        normalized["rule_hits"] = []
    return normalized


def save_eligibility(
    results: List[Dict[str, Any]],
    dialogue_id: str,
    eligibility_file: Path,
    eligibility_version: str = "v1",
) -> None:
    """Save one dialogue's eligibility results to disk."""
    ensure_directory(eligibility_file.parent)

    normalized_results: List[Dict[str, Any]] = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_result(item, dialogue_id)
        if not normalized["episode_id"]:
            continue
        normalized_results.append(normalized)

    payload = {
        "dialogue_id": dialogue_id,
        "eligibility_version": eligibility_version,
        "generated_at": _utc_now_iso(),
        "results": normalized_results,
    }

    with open(eligibility_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _empty_situation_stats() -> Dict[str, Any]:
    return {
        "total_episodes": 0,
        "scene_available": {"count": 0, "episode_keys": []},
        "kg_available": {"count": 0, "episode_keys": []},
        "emo_available": {"count": 0, "episode_keys": []},
        "by_novelty": {
            "factual_novelty_0": {"count": 0, "episode_keys": []},
            "factual_novelty_1": {"count": 0, "episode_keys": []},
            "factual_novelty_2": {"count": 0, "episode_keys": []},
            "emotional_novelty_0": {"count": 0, "episode_keys": []},
            "emotional_novelty_1": {"count": 0, "episode_keys": []},
        },
    }


def _load_existing_situation(situation_file: Path) -> Dict[str, Any]:
    if not situation_file.exists():
        return {"statistics": _empty_situation_stats(), "episodes": {}}

    try:
        with open(situation_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning("Failed to load existing episode_situation.json: %s", exc)
        data = {}

    if not isinstance(data, dict):
        data = {}

    if "statistics" not in data or not isinstance(data.get("statistics"), dict):
        data["statistics"] = _empty_situation_stats()
    if "episodes" not in data or not isinstance(data.get("episodes"), dict):
        data["episodes"] = {}

    return data


def _append_if(condition: bool, key: str, bucket: List[str]) -> None:
    if condition:
        bucket.append(key)


def save_episode_situation(results: List[Dict[str, Any]], dialogue_id: str, episodes_root: Path | None = None) -> None:
    """
    Merge one dialogue's eligibility into episodes/episode_situation.json.
    """
    if episodes_root is None:
        episodes_root = EPISODES_ROOT

    situation_file = episodes_root / "episode_situation.json"
    data = _load_existing_situation(situation_file)
    episodes_map: Dict[str, Any] = data.get("episodes", {})

    for item in results or []:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_result(item, dialogue_id)
        episode_id = normalized["episode_id"]
        if not episode_id:
            continue

        episode_key = f"{dialogue_id}:{episode_id}"
        existing = episodes_map.get(episode_key, {})
        if not isinstance(existing, dict):
            existing = {}

        existing.update(
            {
                "episode_key": episode_key,
                "episode_id": episode_id,
                "dialogue_id": dialogue_id,
                "scene_available": normalized["scene_available"],
                "kg_available": normalized["kg_available"],
                "emo_available": normalized["emo_available"],
                "factual_novelty": normalized["factual_novelty"],
                "emotional_novelty": normalized["emotional_novelty"],
                "eligible": normalized["eligible"],
                "reason": normalized["reason"],
                "updated_at": _utc_now_iso(),
            }
        )
        episodes_map[episode_key] = existing

    scene_available_keys: List[str] = []
    kg_available_keys: List[str] = []
    emo_available_keys: List[str] = []
    factual_novelty_0_keys: List[str] = []
    factual_novelty_1_keys: List[str] = []
    factual_novelty_2_keys: List[str] = []
    emotional_novelty_0_keys: List[str] = []
    emotional_novelty_1_keys: List[str] = []

    for episode_key, ep_data in episodes_map.items():
        if not isinstance(ep_data, dict):
            continue

        _append_if(bool(ep_data.get("scene_available")), episode_key, scene_available_keys)
        _append_if(bool(ep_data.get("kg_available")), episode_key, kg_available_keys)
        _append_if(bool(ep_data.get("emo_available")), episode_key, emo_available_keys)

        factual_novelty = int(ep_data.get("factual_novelty", 0))
        if factual_novelty == 0:
            factual_novelty_0_keys.append(episode_key)
        elif factual_novelty == 1:
            factual_novelty_1_keys.append(episode_key)
        elif factual_novelty == 2:
            factual_novelty_2_keys.append(episode_key)

        emotional_novelty = int(ep_data.get("emotional_novelty", 0))
        if emotional_novelty == 0:
            emotional_novelty_0_keys.append(episode_key)
        elif emotional_novelty == 1:
            emotional_novelty_1_keys.append(episode_key)

    data["statistics"] = {
        "total_episodes": len(episodes_map),
        "scene_available": {"count": len(scene_available_keys), "episode_keys": scene_available_keys},
        "kg_available": {"count": len(kg_available_keys), "episode_keys": kg_available_keys},
        "emo_available": {"count": len(emo_available_keys), "episode_keys": emo_available_keys},
        "by_novelty": {
            "factual_novelty_0": {"count": len(factual_novelty_0_keys), "episode_keys": factual_novelty_0_keys},
            "factual_novelty_1": {"count": len(factual_novelty_1_keys), "episode_keys": factual_novelty_1_keys},
            "factual_novelty_2": {"count": len(factual_novelty_2_keys), "episode_keys": factual_novelty_2_keys},
            "emotional_novelty_0": {"count": len(emotional_novelty_0_keys), "episode_keys": emotional_novelty_0_keys},
            "emotional_novelty_1": {"count": len(emotional_novelty_1_keys), "episode_keys": emotional_novelty_1_keys},
        },
    }
    data["episodes"] = episodes_map
    data["metadata"] = {
        "last_updated": _utc_now_iso(),
        "source_dialogue": dialogue_id,
        "episode_count": len(results or []),
    }

    ensure_directory(situation_file.parent)
    with open(situation_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

