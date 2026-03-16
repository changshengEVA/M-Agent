#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Memory pre-processing pipeline (simplified).

Current scope:
1. construct dialogues
2. construct episodes

Scene/Atomic-facts generation is moved into MemoryCore runtime import flow.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

load_dotenv()

try:
    from load_data import load_dialogues
except ImportError:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    sys.path.append(project_root)
    from load_data.dialog_history_loader import load_dialogues

try:
    from utils import save_dialogue
    from utils.memory_build_utils import build_episodes_with_id
except ImportError:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    sys.path.append(project_root)
    from utils.dialogue_utils import save_dialogue
    from utils.memory_build_utils import build_episodes_with_id


PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_MEMORY_OWNER_NAME = "changshengEVA"
DEFAULT_LLM_TEMPERATURE = 0.0


def init_llm_model() -> Optional[Callable[[str], str]]:
    try:
        from load_model.OpenAIcall import get_llm

        logger.info("Pre-initialize LLM model (temperature=%s)", DEFAULT_LLM_TEMPERATURE)
        return get_llm(model_temperature=DEFAULT_LLM_TEMPERATURE)
    except Exception as exc:
        logger.warning("LLM pre-init failed, fallback to lazy init: %s", exc)
        return None


def get_output_path(process_id: str, stage_name: str) -> Path:
    return PROJECT_ROOT / "data" / "memory" / process_id / stage_name


def stage1_construct_dialogues_for_id(
    process_id: str,
    data_source: Optional[str] = None,
    loader_type: str = "auto",
) -> bool:
    logger.info("=" * 50)
    logger.info("Stage 1: construct dialogues for process_id=%s", process_id)
    logger.info("data_source=%s", data_source if data_source else "default")
    logger.info("loader_type=%s", loader_type)
    logger.info("=" * 50)

    dialogues = load_dialogues(data_source, loader_type)
    if not dialogues:
        logger.error("No dialogues loaded")
        return False

    target_dir = get_output_path(process_id, "dialogues")
    target_dir.mkdir(parents=True, exist_ok=True)

    success = 0
    failed = 0
    for i, dialogue in enumerate(dialogues, start=1):
        logger.info("Save dialogue %s/%s: %s", i, len(dialogues), dialogue.get("dialogue_id"))
        if save_dialogue(dialogue, str(target_dir)):
            success += 1
        else:
            failed += 1

    logger.info("=" * 50)
    logger.info("Stage 1 complete")
    logger.info("saved=%s failed=%s", success, failed)
    logger.info("output=%s", target_dir)
    logger.info("=" * 50)
    return success > 0


def stage2_construct_episodes_for_id(
    process_id: str,
    llm_model: Optional[Callable[[str], str]] = None,
    enable_episode_scoring_filter: bool = False,
) -> bool:
    logger.info("=" * 50)
    logger.info("Stage 2: construct episodes for process_id=%s", process_id)
    logger.info("memory_owner_name=%s", DEFAULT_MEMORY_OWNER_NAME)
    logger.info("enable_episode_scoring_filter=%s", enable_episode_scoring_filter)
    logger.info("=" * 50)

    if not build_episodes_with_id(
        process_id,
        str(PROJECT_ROOT),
        DEFAULT_MEMORY_OWNER_NAME,
        llm_model=llm_model,
        enable_episode_scoring_filter=enable_episode_scoring_filter,
    ):
        logger.error("Build episodes failed")
        return False

    episodes_root = get_output_path(process_id, "episodes")
    by_dialogue_dir = episodes_root / "by_dialogue"

    episode_files_count = 0
    if by_dialogue_dir.exists():
        for dialogue_dir in by_dialogue_dir.iterdir():
            if not dialogue_dir.is_dir():
                continue
            if (dialogue_dir / "episodes_v1.json").exists():
                episode_files_count += 1

    logger.info("=" * 50)
    logger.info("Stage 2 complete")
    logger.info("episodes=%s", episode_files_count)
    logger.info("output=%s", episodes_root)
    logger.info("=" * 50)
    return episode_files_count > 0


def run_full_pipeline_for_id(
    process_id: str,
    data_source: Optional[str] = None,
    loader_type: str = "auto",
    enable_episode_scoring_filter: bool = False,
) -> bool:
    logger.info("Run simplified pipeline for process_id=%s", process_id)
    logger.info("data_source=%s loader_type=%s", data_source if data_source else "default", loader_type)
    logger.info("memory_owner_name=%s", DEFAULT_MEMORY_OWNER_NAME)
    logger.info("enable_episode_scoring_filter=%s", enable_episode_scoring_filter)

    llm_model = init_llm_model()

    if not stage1_construct_dialogues_for_id(process_id, data_source, loader_type):
        logger.warning("Stage 1 failed")
        return False

    if not stage2_construct_episodes_for_id(
        process_id,
        llm_model=llm_model,
        enable_episode_scoring_filter=enable_episode_scoring_filter,
    ):
        logger.warning("Stage 2 failed")
        return False

    logger.info("Simplified pipeline complete for process_id=%s", process_id)
    logger.info("Scene/Atomic-facts generation has been moved to MemoryCore import flow.")
    return True


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Memory pre-processing pipeline (dialogues + episodes)")
    parser.add_argument("--id", type=str, required=True, help="Process ID")
    parser.add_argument("--data-source", type=str, default=None, help="Input data source path")
    parser.add_argument(
        "--loader-type",
        type=str,
        default="auto",
        choices=["auto", "realtalk", "locomo", "default"],
        help="Dialogue loader type",
    )
    parser.add_argument(
        "--enable-episode-scoring-filter",
        action="store_true",
        help="Enable episode qualification scoring and eligibility filtering (disabled by default).",
    )

    args = parser.parse_args()

    success = run_full_pipeline_for_id(
        args.id,
        data_source=args.data_source,
        loader_type=args.loader_type,
        enable_episode_scoring_filter=args.enable_episode_scoring_filter,
    )

    if success:
        logger.info("Pipeline succeeded for process_id=%s", args.id)
    else:
        logger.error("Pipeline failed for process_id=%s", args.id)


if __name__ == "__main__":
    main()
