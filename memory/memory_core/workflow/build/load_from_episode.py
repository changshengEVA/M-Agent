#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Episode import workflow.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from tqdm import tqdm

logger = logging.getLogger(__name__)


def _is_episode_file_payload(data: Dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    episodes = data.get("episodes")
    return isinstance(episodes, list)


def _collect_episode_files(path: Path) -> List[Path]:
    if path.is_file() and path.suffix.lower() == ".json":
        return [path]

    if not path.exists() or not path.is_dir():
        return []

    by_dialogue = path / "by_dialogue"
    if by_dialogue.exists() and by_dialogue.is_dir():
        files = sorted(by_dialogue.rglob("episodes_*.json"))
        if files:
            return files

    return sorted(path.rglob("*.json"))


def _infer_episodes_root(path: Path, episode_files: List[Path]) -> Path:
    """
    Infer episodes root for scene/fact builders.
    Builders expect a directory containing `by_dialogue/`.
    """
    if path.is_dir():
        return path

    probe_files = episode_files or ([path] if path.is_file() else [])
    for file_path in probe_files:
        if not file_path.exists():
            continue
        for ancestor in [file_path.parent, *file_path.parents]:
            if ancestor.name == "by_dialogue":
                parent = ancestor.parent
                if parent.exists() and parent.is_dir():
                    return parent
                break

    if path.is_file():
        return path.parent
    return path


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("Load json failed (%s): %s", path, exc)
    return None


def load_from_episode_path(
    path: Path,
    memory_core: Any,
    use_tqdm: bool = True,
) -> Dict[str, Any]:
    """
    Import from episodes path and trigger Scene/Atomic-facts generation.
    """
    logger.info("Start importing episode path: %s", path)

    if not path.exists():
        return {"success": False, "error": f"path not found: {path}"}

    episode_files = _collect_episode_files(path)
    if not episode_files:
        return {
            "success": False,
            "error": f"no episode json files found under: {path}",
            "files_processed": 0,
            "files_failed": 0,
        }

    results: Dict[str, Any] = {
        "success": True,
        "path": str(path),
        "input_type": "episodes",
        "total_files": len(episode_files),
        "files_processed": 0,
        "files_failed": 0,
        "episodes_processed": 0,
        "total_entities_processed": 0,
        "total_features_processed": 0,
        "total_attributes_processed": 0,
        "total_relations_processed": 0,
        "file_results": [],
        "resolution_applied": False,
    }

    file_iter = tqdm(episode_files, desc="Import episodes") if use_tqdm else episode_files
    for episode_file in file_iter:
        payload = _load_json(episode_file)
        if payload is None:
            results["files_failed"] += 1
            results["file_results"].append(
                {
                    "file": episode_file.name,
                    "success": False,
                    "error": "json load failed",
                }
            )
            continue

        if not _is_episode_file_payload(payload):
            results["file_results"].append(
                {
                    "file": episode_file.name,
                    "success": False,
                    "error": "not an episodes payload",
                }
            )
            results["files_failed"] += 1
            continue

        episodes_in_file = len(payload.get("episodes", []))
        results["files_processed"] += 1
        results["episodes_processed"] += episodes_in_file
        results["file_results"].append(
            {
                "file": episode_file.name,
                "success": True,
                "episodes_processed": episodes_in_file,
            }
        )

    try:
        episodes_root_for_build = _infer_episodes_root(path, episode_files)
        dialogues_root = memory_core.dialogues_dir
        scene_root = memory_core.scene_dir
        scene_prompt_version = str(getattr(memory_core, "scene_prompt_version", "v2"))
        action_prompt_version = str(getattr(memory_core, "action_prompt_version", "v1"))
        memory_owner_name = str(getattr(memory_core, "memory_owner_name", "changshengEVA"))

        # system-owned behavior: import flow controls force_update internally
        force_update = False

        from memory.build_memory.form_scene import scan_and_form_scenes
        from memory.build_memory.form_scene_action import scan_and_form_scene_actions

        if not episodes_root_for_build.exists():
            build_result = {
                "success": False,
                "error": f"Episodes path not found: {episodes_root_for_build}",
                "scene_file_count": 0,
                "fact_stats": {},
            }
        else:
            scan_and_form_scenes(
                use_tqdm=True,
                force_update=force_update,
                prompt_version=scene_prompt_version,
                dialogues_root=dialogues_root,
                episodes_root=episodes_root_for_build,
                scene_root=scene_root,
                memory_owner_name=memory_owner_name,
                embed_model=memory_core.embed_func,
                llm_model=memory_core.llm_func,
            )

            fact_stats = scan_and_form_scene_actions(
                workflow_id=memory_core.workflow_id,
                prompt_version=action_prompt_version,
                force_update=force_update,
                use_tqdm=True,
                embed_model=memory_core.embed_func,
                llm_model=memory_core.llm_func,
            )
            scene_file_count = len([p for p in scene_root.glob("*.json") if p.is_file()])
            build_result = {
                "success": True,
                "scene_file_count": scene_file_count,
                "fact_stats": fact_stats,
                "episodes_root": str(episodes_root_for_build),
                "scene_root": str(scene_root),
                "scene_prompt_version": scene_prompt_version,
                "action_prompt_version": action_prompt_version,
                "memory_owner_name": memory_owner_name,
                "force_update": force_update,
            }

        results["scene_build_result"] = build_result
        if not build_result.get("success", False):
            results["success"] = False
            results["error"] = f"scene/fact generation failed: {build_result}"
    except Exception as exc:
        logger.error("Scene/fact generation failed: %s", exc)
        results["success"] = False
        results["error"] = str(exc)

    results["resolution_note"] = (
        "Resolution pass skipped: KG candidate generation is disabled, current mode keeps 0 entities/relations."
    )
    return results
