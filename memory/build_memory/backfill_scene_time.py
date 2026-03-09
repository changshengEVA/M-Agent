#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backfill start_time/end_time into existing scene files.

For each scene file:
- read source.episodes[*].dialogue_id and turn_span
- load source dialogue JSON
- infer episode-level start/end time from turn timestamps (fallback to dialogue meta)
- write start_time/end_time into source.episodes[*]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MEMORY_ROOT = PROJECT_ROOT / "data" / "memory"


@dataclass
class Stats:
    workflows: int = 0
    scene_files_scanned: int = 0
    scene_files_updated: int = 0
    episodes_scanned: int = 0
    episodes_updated: int = 0
    missing_dialogue: int = 0
    invalid_scene: int = 0


def load_json(path: Path) -> Optional[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def find_dialogue_file(dialogue_id: str, workflow_dialogues_dir: Path, memory_root: Path) -> Optional[Path]:
    if not dialogue_id:
        return None

    if workflow_dialogues_dir.exists():
        for path in workflow_dialogues_dir.rglob(f"{dialogue_id}.json"):
            if path.is_file():
                return path

    for path in memory_root.rglob(f"{dialogue_id}.json"):
        if path.is_file() and "dialogues" in path.parts:
            return path
    return None


def extract_episode_time_range(dialogue_data: Dict, turn_span: List) -> Tuple[str, str]:
    start_time = ""
    end_time = ""

    meta = dialogue_data.get("meta", {})
    if not isinstance(meta, dict):
        meta = {}

    turns = dialogue_data.get("turns", [])
    if not isinstance(turns, list):
        turns = []

    if not (isinstance(turn_span, list) and len(turn_span) >= 2):
        return meta.get("start_time", "") or "", meta.get("end_time", "") or ""

    try:
        start_id = int(turn_span[0])
        end_id = int(turn_span[1])
    except (TypeError, ValueError):
        return meta.get("start_time", "") or "", meta.get("end_time", "") or ""

    for turn in turns:
        if not isinstance(turn, dict):
            continue
        turn_id = turn.get("turn_id")
        if turn_id == start_id and not start_time:
            start_time = turn.get("timestamp", "") or ""
        if turn_id == end_id and not end_time:
            end_time = turn.get("timestamp", "") or ""
        if start_time and end_time:
            break

    if not start_time or not end_time:
        span_turns: List[Dict] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            try:
                turn_id = int(turn.get("turn_id", -1))
            except (TypeError, ValueError):
                continue
            if start_id <= turn_id <= end_id:
                span_turns.append(turn)
        if span_turns:
            if not start_time:
                start_time = span_turns[0].get("timestamp", "") or ""
            if not end_time:
                end_time = span_turns[-1].get("timestamp", "") or ""

    if not start_time:
        start_time = meta.get("start_time", "") or ""
    if not end_time:
        end_time = meta.get("end_time", "") or ""

    return start_time, end_time


def iter_workflow_dirs(memory_root: Path, workflow_id: Optional[str], all_workflows: bool) -> List[Path]:
    if workflow_id:
        return [memory_root / workflow_id]

    if all_workflows:
        return sorted([p for p in memory_root.iterdir() if p.is_dir()])

    if (memory_root / "default").exists():
        return [memory_root / "default"]
    return sorted([p for p in memory_root.iterdir() if p.is_dir()])


def backfill(
    workflow_dirs: List[Path],
    memory_root: Path,
    dry_run: bool = False,
    force: bool = False,
) -> Stats:
    stats = Stats(workflows=len(workflow_dirs))
    dialogue_cache: Dict[str, Optional[Dict]] = {}

    for workflow_dir in workflow_dirs:
        scene_dir = workflow_dir / "scene"
        dialogues_dir = workflow_dir / "dialogues"
        if not scene_dir.exists():
            continue

        for scene_file in sorted(scene_dir.glob("*.json")):
            stats.scene_files_scanned += 1
            scene_data = load_json(scene_file)
            if not scene_data:
                stats.invalid_scene += 1
                continue

            source = scene_data.get("source", {})
            episodes = source.get("episodes", []) if isinstance(source, dict) else []
            if not isinstance(episodes, list):
                continue

            file_changed = False
            for ep in episodes:
                if not isinstance(ep, dict):
                    continue
                stats.episodes_scanned += 1

                has_start = bool(ep.get("start_time"))
                has_end = bool(ep.get("end_time"))
                if not force and has_start and has_end:
                    continue

                dialogue_id = ep.get("dialogue_id", "")
                turn_span = ep.get("turn_span", [])
                if not dialogue_id:
                    continue

                if dialogue_id not in dialogue_cache:
                    dialogue_path = find_dialogue_file(dialogue_id, dialogues_dir, memory_root)
                    dialogue_cache[dialogue_id] = load_json(dialogue_path) if dialogue_path else None

                dialogue_data = dialogue_cache.get(dialogue_id)
                if not dialogue_data:
                    stats.missing_dialogue += 1
                    continue

                start_time, end_time = extract_episode_time_range(dialogue_data, turn_span)
                if force or not has_start:
                    ep["start_time"] = start_time
                if force or not has_end:
                    ep["end_time"] = end_time

                stats.episodes_updated += 1
                file_changed = True

            if file_changed:
                stats.scene_files_updated += 1
                if not dry_run:
                    with open(scene_file, "w", encoding="utf-8") as f:
                        json.dump(scene_data, f, ensure_ascii=False, indent=2)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill start_time/end_time for existing scene files.")
    parser.add_argument("--workflow-id", type=str, default="", help="Only process one workflow under data/memory/{workflow_id}")
    parser.add_argument("--all", action="store_true", help="Process all workflows under data/memory")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not write files")
    parser.add_argument("--force", action="store_true", help="Overwrite existing start_time/end_time")
    args = parser.parse_args()

    workflow_id = args.workflow_id.strip() or None
    workflow_dirs = iter_workflow_dirs(MEMORY_ROOT, workflow_id=workflow_id, all_workflows=args.all)
    stats = backfill(
        workflow_dirs=workflow_dirs,
        memory_root=MEMORY_ROOT,
        dry_run=args.dry_run,
        force=args.force,
    )

    print(f"workflows={stats.workflows}")
    print(f"scene_files_scanned={stats.scene_files_scanned}")
    print(f"scene_files_updated={stats.scene_files_updated}")
    print(f"episodes_scanned={stats.episodes_scanned}")
    print(f"episodes_updated={stats.episodes_updated}")
    print(f"missing_dialogue={stats.missing_dialogue}")
    print(f"invalid_scene={stats.invalid_scene}")


if __name__ == "__main__":
    main()
