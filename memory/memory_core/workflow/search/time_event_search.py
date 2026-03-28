#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Time-range scene search workflow.

Searches scene files by overlap with the query time window.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def search_events_by_time_range(
    start_time: str,
    end_time: str,
    scene_dir: Path,
) -> List[Dict[str, Any]]:
    """
    Return scenes whose time range overlaps [start_time, end_time].

    Output item format:
    {
      "scene_id": str,
      "theme": str,
      "starttime": str,
      "endtime": str
    }
    """
    query_start = _parse_iso_datetime(start_time)
    query_end = _parse_iso_datetime(end_time)
    if query_start is None or query_end is None:
        logger.warning(
            "search_events_by_time_range: invalid input time range start=%s end=%s",
            start_time,
            end_time,
        )
        return []

    if query_start > query_end:
        query_start, query_end = query_end, query_start

    if not scene_dir.exists() or not scene_dir.is_dir():
        logger.warning("search_events_by_time_range: scene_dir not found: %s", scene_dir)
        return []

    results: List[Tuple[datetime, Dict[str, Any]]] = []

    for scene_file in sorted(scene_dir.glob("*.json")):
        scene_data = _load_scene_file(scene_file)
        if not scene_data:
            continue

        scene_start_dt, scene_end_dt, scene_start_text, scene_end_text = _extract_scene_time_range(scene_data)
        if scene_start_dt is None or scene_end_dt is None:
            continue

        if scene_start_dt <= query_end and scene_end_dt >= query_start:
            results.append(
                (
                    scene_start_dt,
                    {
                        "scene_id": scene_data.get("scene_id") or scene_file.stem,
                        "theme": scene_data.get("theme", ""),
                        "starttime": scene_start_text,
                        "endtime": scene_end_text,
                    },
                )
            )

    results.sort(key=lambda x: x[0])
    return [item for _, item in results]


def _extract_scene_time_range(scene_data: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[datetime], str, str]:
    source = scene_data.get("source", {})
    episodes = source.get("episodes", []) if isinstance(source, dict) else []
    if not isinstance(episodes, list):
        return None, None, "", ""

    starts: List[Tuple[datetime, str]] = []
    ends: List[Tuple[datetime, str]] = []

    for ep in episodes:
        if not isinstance(ep, dict):
            continue

        start_text = (
            ep.get("start_time")
            or ep.get("starttime")
            or ep.get("Start time")
            or ""
        )
        end_text = (
            ep.get("end_time")
            or ep.get("endtime")
            or ep.get("End time")
            or ""
        )

        start_dt = _parse_iso_datetime(start_text)
        end_dt = _parse_iso_datetime(end_text)
        if start_dt is None or end_dt is None:
            continue

        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt
            start_text, end_text = end_text, start_text

        starts.append((start_dt, start_text))
        ends.append((end_dt, end_text))

    if not starts or not ends:
        return None, None, "", ""

    min_start_dt, min_start_text = min(starts, key=lambda x: x[0])
    max_end_dt, max_end_text = max(ends, key=lambda x: x[0])
    return min_start_dt, max_end_dt, min_start_text, max_end_text


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    # Python fromisoformat does not accept trailing "Z" directly.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return None

    # Normalize tz-aware datetime to naive UTC for stable comparison.
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _load_scene_file(scene_file: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(scene_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("search_events_by_time_range: load scene file failed: %s (%s)", scene_file, exc)
    return None
