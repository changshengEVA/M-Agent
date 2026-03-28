#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Utilities for memory build pipeline.

This module wraps episode build + optional qualification/filtering stages.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from m_agent.paths import PROJECT_ROOT

from .path_utils import get_output_path


logger = logging.getLogger(__name__)


def _build_default_all_available_results(dialogue_id: str, episodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build pass-through eligibility records so every episode is available."""
    results: List[Dict[str, Any]] = []
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        episode_id = str(episode.get("episode_id", "")).strip()
        if not episode_id:
            continue
        results.append(
            {
                "episode_id": episode_id,
                "dialogue_id": dialogue_id,
                "eligible": True,
                "reason": "all_available_default",
                "rule_hits": [],
                "scene_available": True,
                "kg_available": True,
                "emo_available": True,
                "factual_novelty": 2,
                "emotional_novelty": 1,
            }
        )
    return results


def _mark_all_episodes_available(
    episodes_root: Path,
    episode_version: str = "v1",
    eligibility_version: str = "v1",
) -> Tuple[int, int]:
    """
    Write eligibility + episode_situation with full pass-through policy.

    Returns:
        (dialogues_processed, episodes_marked)
    """
    from m_agent.memory.build_memory.filter_episode import save_eligibility, save_episode_situation

    by_dialogue_dir = episodes_root / "by_dialogue"
    if not by_dialogue_dir.exists():
        return 0, 0

    dialogues_processed = 0
    episodes_marked = 0

    for dialogue_dir in by_dialogue_dir.iterdir():
        if not dialogue_dir.is_dir():
            continue

        episode_file = dialogue_dir / f"episodes_{episode_version}.json"
        if not episode_file.exists():
            continue

        try:
            with open(episode_file, "r", encoding="utf-8") as f:
                episode_data = json.load(f)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", episode_file, exc)
            continue

        if not isinstance(episode_data, dict):
            continue

        dialogue_id = str(episode_data.get("dialogue_id", dialogue_dir.name)).strip()
        episodes = episode_data.get("episodes", [])
        if not isinstance(episodes, list):
            episodes = []

        results = _build_default_all_available_results(dialogue_id, episodes)
        eligibility_file = dialogue_dir / f"eligibility_{eligibility_version}.json"

        try:
            save_eligibility(results, dialogue_id, eligibility_file, eligibility_version)
            save_episode_situation(results, dialogue_id, episodes_root)
        except Exception as exc:
            logger.warning(
                "Failed to save pass-through eligibility for dialogue_id=%s: %s",
                dialogue_id,
                exc,
            )
            continue

        dialogues_processed += 1
        episodes_marked += len(results)

    return dialogues_processed, episodes_marked


def build_episodes_with_id(
    process_id: str,
    project_root: str | Path | None = None,
    memory_owner_name: str = "changshengEVA",
    llm_model: Optional[Callable[[str], str]] = None,
    enable_episode_scoring_filter: bool = False,
) -> bool:
    """
    Build episodes for a workflow under data/memory/{process_id}/.

    When enable_episode_scoring_filter is False (default), this function skips
    qualification/filtering and marks all episodes as scene/kg/emo available.
    """
    try:
        if project_root is None:
            project_root = PROJECT_ROOT

        dialogues_root = get_output_path(process_id, "dialogues", project_root=project_root)
        episodes_root = get_output_path(process_id, "episodes", project_root=project_root)

        dialogues_root.mkdir(parents=True, exist_ok=True)
        episodes_root.mkdir(parents=True, exist_ok=True)

        logger.info("Start building episodes for workflow=%s", process_id)
        logger.info("dialogues_root=%s", dialogues_root)
        logger.info("episodes_root=%s", episodes_root)
        logger.info("memory_owner_name=%s", memory_owner_name)
        logger.info("enable_episode_scoring_filter=%s", enable_episode_scoring_filter)

        from m_agent.memory.build_memory.build_episode import scan_and_build_episodes

        scan_and_build_episodes(
            use_tqdm=True,
            dialogues_root=dialogues_root,
            episodes_root=episodes_root,
            memory_owner_name=memory_owner_name,
            llm_model=llm_model,
        )

        if enable_episode_scoring_filter:
            logger.info("Run qualification stage")
            from m_agent.memory.build_memory.qualify_episode import scan_and_qualify_episodes

            scan_and_qualify_episodes(
                use_tqdm=True,
                dialogues_root=dialogues_root,
                episodes_root=episodes_root,
                memory_owner_name=memory_owner_name,
                llm_model=llm_model,
            )

            logger.info("Run eligibility filtering stage")
            from m_agent.memory.build_memory.filter_episode import scan_and_filter_episodes

            scan_and_filter_episodes(
                episode_version="v1",
                eligibility_version="v1",
                use_tqdm=True,
                force_update_situation=True,
                episodes_root=episodes_root,
            )
        else:
            logger.info("Skip qualification/filter. Mark all episodes as available by default.")
            dialogue_count, episode_count = _mark_all_episodes_available(
                episodes_root=episodes_root,
                episode_version="v1",
                eligibility_version="v1",
            )
            logger.info(
                "Pass-through eligibility generated: dialogues=%s episodes=%s",
                dialogue_count,
                episode_count,
            )

        logger.info("Episode build flow completed for workflow=%s", process_id)
        return True

    except Exception as exc:
        logger.error("Build episodes failed: %s", exc)
        import traceback

        logger.error(traceback.format_exc())
        return False


def build_episodes_custom(
    dialogues_root: Path,
    episodes_root: Path,
    memory_owner_name: str = "changshengEVA",
    llm_model: Optional[Callable[[str], str]] = None,
    enable_episode_scoring_filter: bool = False,
) -> bool:
    """Build episodes using explicit dialogue/episode roots."""
    try:
        logger.info("Build episodes with custom roots")
        logger.info("dialogues_root=%s", dialogues_root)
        logger.info("episodes_root=%s", episodes_root)
        logger.info("memory_owner_name=%s", memory_owner_name)
        logger.info("enable_episode_scoring_filter=%s", enable_episode_scoring_filter)

        dialogues_root.mkdir(parents=True, exist_ok=True)
        episodes_root.mkdir(parents=True, exist_ok=True)

        from m_agent.memory.build_memory.build_episode import scan_and_build_episodes

        scan_and_build_episodes(
            use_tqdm=True,
            dialogues_root=dialogues_root,
            episodes_root=episodes_root,
            memory_owner_name=memory_owner_name,
            llm_model=llm_model,
        )

        if enable_episode_scoring_filter:
            logger.info("Run qualification stage")
            from m_agent.memory.build_memory.qualify_episode import scan_and_qualify_episodes

            scan_and_qualify_episodes(
                use_tqdm=True,
                dialogues_root=dialogues_root,
                episodes_root=episodes_root,
                memory_owner_name=memory_owner_name,
                llm_model=llm_model,
            )

            logger.info("Run eligibility filtering stage")
            from m_agent.memory.build_memory.filter_episode import scan_and_filter_episodes

            scan_and_filter_episodes(
                episode_version="v1",
                eligibility_version="v1",
                use_tqdm=True,
                force_update_situation=True,
                episodes_root=episodes_root,
            )
        else:
            logger.info("Skip qualification/filter. Mark all episodes as available by default.")
            dialogue_count, episode_count = _mark_all_episodes_available(
                episodes_root=episodes_root,
                episode_version="v1",
                eligibility_version="v1",
            )
            logger.info(
                "Pass-through eligibility generated: dialogues=%s episodes=%s",
                dialogue_count,
                episode_count,
            )

        logger.info("Episode build flow completed")
        return True

    except Exception as exc:
        logger.error("Build episodes failed: %s", exc)
        import traceback

        logger.error(traceback.format_exc())
        return False


def run_memory_build_for_id(
    process_id: str,
    source_dialogues_dir: Path | None = None,
    llm_model: Optional[Callable[[str], str]] = None,
    enable_episode_scoring_filter: bool = False,
) -> bool:
    """Run memory build for one workflow id."""
    try:
        import shutil

        project_root = PROJECT_ROOT

        target_dialogues_root = get_output_path(process_id, "dialogues", project_root=project_root)
        episodes_root = get_output_path(process_id, "episodes", project_root=project_root)

        target_dialogues_root.mkdir(parents=True, exist_ok=True)
        episodes_root.mkdir(parents=True, exist_ok=True)

        if source_dialogues_dir and source_dialogues_dir.exists():
            logger.info("Copy dialogue files from %s to %s", source_dialogues_dir, target_dialogues_root)
            for item in source_dialogues_dir.iterdir():
                if item.is_dir():
                    dest = target_dialogues_root / item.name
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
                else:
                    dest = target_dialogues_root / item.name
                    shutil.copy2(item, dest)

        return build_episodes_with_id(
            process_id,
            project_root,
            llm_model=llm_model,
            enable_episode_scoring_filter=enable_episode_scoring_filter,
        )

    except Exception as exc:
        logger.error("Run memory build failed: %s", exc)
        import traceback

        logger.error(traceback.format_exc())
        return False

