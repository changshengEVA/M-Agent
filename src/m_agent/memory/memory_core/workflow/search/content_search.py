#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Content search workflow.

Given dialogue_id and episode_id, return the concrete dialogue content
for the matched episode span.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def search_content(
    dialogue_id: str,
    episode_id: str,
    scene_dir: Path,
    dialogues_dir: Path,
    episodes_dir: Path,
    segment_id: str | None = None,
) -> Dict[str, Any]:
    """
    Search concrete dialogue content by dialogue_id + episode_id,
    optionally narrowed to a specific segment within the episode.
    """
    result: Dict[str, Any] = {
        "hit": False,
        "dialogue_id": dialogue_id,
        "episode_id": episode_id,
        "segment_id": segment_id or None,
        "turn_span": None,
        "event_time": {"start_time": "", "end_time": ""},
        "event_info": {},
        "turn_time_span": {"start_time": "", "end_time": ""},
        "source": {},
        "participants": [],
        "dialogue_meta": {},
        "turns": [],
    }

    if not isinstance(dialogue_id, str) or not dialogue_id.strip():
        result["error"] = "invalid_dialogue_id"
        return result
    if not isinstance(episode_id, str) or not episode_id.strip():
        result["error"] = "invalid_episode_id"
        return result

    dialogue_id = dialogue_id.strip()
    episode_id = episode_id.strip()
    safe_segment_id = str(segment_id or "").strip() or None
    result["dialogue_id"] = dialogue_id
    result["episode_id"] = episode_id
    result["segment_id"] = safe_segment_id

    span_info = _find_turn_span_from_scene(
        scene_dir=scene_dir,
        dialogue_id=dialogue_id,
        episode_id=episode_id,
    )
    if span_info is None:
        span_info = _find_turn_span_from_episode_file(
            episodes_dir=episodes_dir,
            dialogue_id=dialogue_id,
            episode_id=episode_id,
        )

    if span_info is None:
        result["error"] = "episode_not_found"
        return result

    # When a segment_id is requested, try to narrow turn_span to that segment.
    effective_turn_span = span_info.get("turn_span")
    if safe_segment_id:
        seg_span = _find_segment_turn_span(span_info, safe_segment_id)
        if seg_span is None:
            seg_span = _find_segment_turn_span_from_episode_file(
                episodes_dir=episodes_dir,
                dialogue_id=dialogue_id,
                episode_id=episode_id,
                segment_id=safe_segment_id,
            )
        if seg_span is not None:
            effective_turn_span = seg_span

    dialogue_file = _find_dialogue_file(dialogues_dir=dialogues_dir, dialogue_id=dialogue_id)
    if dialogue_file is None:
        result["error"] = "dialogue_file_not_found"
        result["turn_span"] = effective_turn_span
        result["event_time"] = span_info.get("event_time", {"start_time": "", "end_time": ""})
        result["event_info"] = span_info.get("event_info", {})
        result["source"] = span_info.get("source", {})
        return result

    dialogue_data = _load_json(dialogue_file)
    if dialogue_data is None:
        result["error"] = "dialogue_file_load_failed"
        result["turn_span"] = effective_turn_span
        result["event_time"] = span_info.get("event_time", {"start_time": "", "end_time": ""})
        result["event_info"] = span_info.get("event_info", {})
        result["source"] = span_info.get("source", {})
        return result

    turns = dialogue_data.get("turns", [])
    selected_turns = _slice_turns(turns=turns, turn_span=effective_turn_span)

    result["hit"] = True
    result["turn_span"] = effective_turn_span
    result["event_time"] = span_info.get("event_time", {"start_time": "", "end_time": ""})
    result["event_info"] = span_info.get("event_info", {})
    result["source"] = span_info.get("source", {})
    result["participants"] = dialogue_data.get("participants", [])
    result["dialogue_meta"] = dialogue_data.get("meta", {})
    result["turns"] = selected_turns
    result["turn_time_span"] = _extract_turn_time_span(selected_turns)

    if not result["event_time"].get("start_time") and not result["event_time"].get("end_time"):
        result["event_time"] = result["turn_time_span"]

    return result


def _find_turn_span_from_scene(
    scene_dir: Path,
    dialogue_id: str,
    episode_id: str,
) -> Optional[Dict[str, Any]]:
    if not scene_dir.exists() or not scene_dir.is_dir():
        return None

    for scene_file in sorted(scene_dir.glob("*.json")):
        scene_data = _load_json(scene_file)
        if not isinstance(scene_data, dict):
            continue

        source = scene_data.get("source", {})
        episodes = source.get("episodes", [])
        if not isinstance(episodes, list):
            continue

        for ep in episodes:
            if not isinstance(ep, dict):
                continue
            if ep.get("dialogue_id") != dialogue_id:
                continue
            if ep.get("episode_id") != episode_id:
                continue

            return {
                "turn_span": ep.get("turn_span"),
                "event_time": {
                    "start_time": str(ep.get("start_time", "") or ep.get("starttime", "") or ""),
                    "end_time": str(ep.get("end_time", "") or ep.get("endtime", "") or ""),
                },
                "event_info": {
                    "scene_id": scene_data.get("scene_id") or scene_file.stem,
                    "scene_theme": scene_data.get("theme", ""),
                    "episode_id": ep.get("episode_id", ""),
                    "dialogue_id": ep.get("dialogue_id", ""),
                },
                "source": {
                    "from": "scene",
                    "scene_id": scene_data.get("scene_id") or scene_file.stem,
                },
            }

    return None


def _find_turn_span_from_episode_file(
    episodes_dir: Path,
    dialogue_id: str,
    episode_id: str,
) -> Optional[Dict[str, Any]]:
    dialogue_episode_dir = episodes_dir / "by_dialogue" / dialogue_id
    if not dialogue_episode_dir.exists() or not dialogue_episode_dir.is_dir():
        return None

    episode_files = sorted(dialogue_episode_dir.glob("episodes_*.json"))
    if not episode_files:
        # Fallback for unexpected naming.
        episode_files = sorted(dialogue_episode_dir.glob("*.json"))

    for episode_file in reversed(episode_files):
        data = _load_json(episode_file)
        if not isinstance(data, dict):
            continue
        episodes = data.get("episodes", [])
        if not isinstance(episodes, list):
            continue

        for ep in episodes:
            if not isinstance(ep, dict):
                continue
            if ep.get("dialogue_id") != dialogue_id:
                continue
            if ep.get("episode_id") != episode_id:
                continue

            return {
                "turn_span": ep.get("turn_span"),
                "event_time": {
                    "start_time": str(ep.get("start_time", "") or ep.get("starttime", "") or ""),
                    "end_time": str(ep.get("end_time", "") or ep.get("endtime", "") or ""),
                },
                "event_info": {
                    "scene_id": "",
                    "scene_theme": "",
                    "episode_id": ep.get("episode_id", ""),
                    "dialogue_id": ep.get("dialogue_id", ""),
                },
                "source": {
                    "from": "episodes_file",
                    "episode_file": episode_file.name,
                },
            }

    return None


def _find_segment_turn_span(
    span_info: Dict[str, Any],
    segment_id: str,
) -> Optional[List[int]]:
    """Extract a segment's turn_span from the episode-level span_info if
    the span_info carries inline segment metadata (currently not stored in
    scene source, so this is a future-proof hook)."""
    segments = span_info.get("segments")
    if not isinstance(segments, list):
        return None
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        if str(seg.get("segment_id", "")).strip() == segment_id:
            ts = seg.get("turn_span")
            if isinstance(ts, list) and len(ts) == 2 and all(isinstance(x, int) for x in ts):
                return ts
    return None


def _find_segment_turn_span_from_episode_file(
    episodes_dir: Path,
    dialogue_id: str,
    episode_id: str,
    segment_id: str,
) -> Optional[List[int]]:
    """Look up a segment's turn_span from the episode JSON files."""
    dialogue_episode_dir = episodes_dir / "by_dialogue" / dialogue_id
    if not dialogue_episode_dir.exists() or not dialogue_episode_dir.is_dir():
        return None

    episode_files = sorted(dialogue_episode_dir.glob("episodes_*.json"))
    if not episode_files:
        episode_files = sorted(dialogue_episode_dir.glob("*.json"))

    for episode_file in reversed(episode_files):
        data = _load_json(episode_file)
        if not isinstance(data, dict):
            continue
        episodes = data.get("episodes", [])
        if not isinstance(episodes, list):
            continue
        for ep in episodes:
            if not isinstance(ep, dict):
                continue
            if ep.get("dialogue_id") != dialogue_id:
                continue
            if ep.get("episode_id") != episode_id:
                continue
            segments = ep.get("segments")
            if not isinstance(segments, list):
                continue
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                if str(seg.get("segment_id", "")).strip() == segment_id:
                    ts = seg.get("turn_span")
                    if isinstance(ts, list) and len(ts) == 2 and all(isinstance(x, int) for x in ts):
                        return ts
    return None


def _find_dialogue_file(dialogues_dir: Path, dialogue_id: str) -> Optional[Path]:
    if not dialogues_dir.exists() or not dialogues_dir.is_dir():
        return None

    direct = dialogues_dir / f"{dialogue_id}.json"
    if direct.exists():
        return direct

    for file_path in dialogues_dir.rglob(f"{dialogue_id}.json"):
        if file_path.is_file():
            return file_path
    return None


def _slice_turns(turns: Any, turn_span: Any) -> List[Dict[str, Any]]:
    if not isinstance(turns, list):
        return []

    if (
        isinstance(turn_span, list)
        and len(turn_span) == 2
        and all(isinstance(x, int) for x in turn_span)
    ):
        start_idx = max(0, turn_span[0])
        end_idx = min(len(turns) - 1, turn_span[1])
        if start_idx <= end_idx:
            selected = turns[start_idx:end_idx + 1]
            return [t for t in selected if isinstance(t, dict)]

    return [t for t in turns if isinstance(t, dict)]


def _extract_turn_time_span(turns: List[Dict[str, Any]]) -> Dict[str, str]:
    if not isinstance(turns, list) or not turns:
        return {"start_time": "", "end_time": ""}

    first_ts = ""
    last_ts = ""
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        ts = _extract_turn_timestamp(turn)
        if ts:
            if not first_ts:
                first_ts = ts
            last_ts = ts

    return {
        "start_time": first_ts,
        "end_time": last_ts,
    }


def _extract_turn_timestamp(turn: Dict[str, Any]) -> str:
    for key in ("timestamp", "time", "created_at", "datetime"):
        val = turn.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    meta = turn.get("meta", {})
    if isinstance(meta, dict):
        for key in ("timestamp", "time", "created_at", "datetime"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("search_content: failed to load %s: %s", path, exc)
    return None
