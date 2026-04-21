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

import argparse
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from m_agent.utils.pipeline_logging import suppress_verbose_pipeline_loggers

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
suppress_verbose_pipeline_loggers(debug=False)

from m_agent.load_data import load_dialogues
from m_agent.memory.utils import build_episodes_with_id, get_output_path, save_dialogue
from m_agent.paths import ENV_PATH, PROJECT_ROOT
from m_agent.config_paths import DEFAULT_MEMORY_CORE_CONFIG_PATH, resolve_config_path


load_dotenv(ENV_PATH)

DEFAULT_MEMORY_OWNER_NAME = "changshengEVA"
DEFAULT_LLM_TEMPERATURE = 0.0


def _normalize_conv_ids(raw_values: Optional[Sequence[str]]) -> List[str]:
    conv_ids: List[str] = []
    seen = set()
    for raw in raw_values or []:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        conv_ids.append(value)
    return conv_ids


def _extract_conv_id_from_dialogue(dialogue: dict) -> Optional[str]:
    meta = dialogue.get("meta")
    if isinstance(meta, dict):
        sample_id = str(meta.get("sample_id", "") or "").strip()
        if sample_id:
            return sample_id

    dialogue_id = str(dialogue.get("dialogue_id", "") or "").strip()
    if not dialogue_id:
        return None

    # LoCoMo dialogue id format: dlg_<source>_<sample_id>_<session_num>
    # Example: dlg_locomo10_conv-30_1 -> conv-30
    conv_match = re.search(r"(conv-\d+)", dialogue_id)
    if conv_match:
        return conv_match.group(1)

    return None


def filter_dialogues_by_conv_ids(
    dialogues: Sequence[dict],
    conv_ids: Optional[Sequence[str]],
) -> List[dict]:
    normalized = _normalize_conv_ids(conv_ids)
    if not normalized:
        return list(dialogues)

    target = set(normalized)
    filtered: List[dict] = []
    hit_conv_ids = set()
    unknown_conv_dialogues = 0

    for dialogue in dialogues:
        conv_id = _extract_conv_id_from_dialogue(dialogue)
        if conv_id is None:
            unknown_conv_dialogues += 1
            continue
        if conv_id not in target:
            continue
        filtered.append(dialogue)
        hit_conv_ids.add(conv_id)

    missing = sorted(target - hit_conv_ids)
    logger.info(
        "Conv filter applied: keep %s/%s dialogues for conv_ids=%s",
        len(filtered),
        len(dialogues),
        normalized,
    )
    if unknown_conv_dialogues:
        logger.info("Dialogues without resolvable conv_id skipped: %s", unknown_conv_dialogues)
    if missing:
        logger.warning("Requested conv_ids not found in loaded dialogues: %s", missing)

    return filtered


def cleanup_pre_outputs(
    process_id: str,
    stages: Sequence[str] = ("dialogues", "episodes"),
) -> None:
    for stage_name in stages:
        stage_dir = get_output_path(process_id, stage_name)
        if not stage_dir.exists():
            continue
        logger.info("Clean existing stage output: %s", stage_dir)
        shutil.rmtree(stage_dir)


def init_llm_model() -> Optional[Callable[[str], str]]:
    try:
        from m_agent.load_model.OpenAIcall import get_chat_llm, get_llm

        logger.info("Pre-initialize LLM model (temperature=%s)", DEFAULT_LLM_TEMPERATURE)
        return get_llm(model_temperature=DEFAULT_LLM_TEMPERATURE)
    except Exception as exc:
        logger.warning("LLM pre-init failed, fallback to lazy init: %s", exc)
        return None


def init_llm_model_from_memory_core_config(
    memory_core_config: Dict[str, Any] | None,
) -> Optional[Callable[[str], str]]:
    """Init the episode-building LLM from MemoryCore config (provider/model only).

    Connection settings (API key / base URL) are still sourced from .env.
    """
    if not isinstance(memory_core_config, dict):
        return init_llm_model()

    provider = str(memory_core_config.get("episode_llm_provider") or memory_core_config.get("llm_provider") or "openai").strip().lower()
    model_name = str(memory_core_config.get("episode_llm_model_name") or memory_core_config.get("llm_model_name") or "").strip() or None
    temperature_raw = memory_core_config.get("episode_llm_temperature", memory_core_config.get("memory_llm_temperature", DEFAULT_LLM_TEMPERATURE))
    try:
        temperature = float(temperature_raw)
    except Exception:
        temperature = float(DEFAULT_LLM_TEMPERATURE)

    if provider not in {"openai", "openai_compatible"}:
        raise ValueError(
            f"Unsupported episode_llm_provider: {provider}. "
            "Currently supported: openai (OpenAI-compatible via .env BASE_URL/API_SECRET_KEY)."
        )

    try:
        from m_agent.load_model.OpenAIcall import get_chat_llm, get_llm
        if model_name:
            logger.info("Episode LLM from memory_core config: provider=%s model=%s temp=%s", provider, model_name, temperature)
            return get_chat_llm(model_temperature=temperature, model_name=model_name)
        logger.info("Episode LLM from memory_core config: provider=%s model=(default env) temp=%s", provider, temperature)
        return get_llm(model_temperature=temperature)
    except Exception as exc:
        logger.warning("Episode LLM init from memory_core config failed, fallback to default: %s", exc)
        return init_llm_model()


def stage1_construct_dialogues_for_id(
    process_id: str,
    data_source: Optional[str] = None,
    loader_type: str = "auto",
    include_conv_ids: Optional[Sequence[str]] = None,
) -> bool:
    logger.info("=" * 50)
    logger.info("Stage 1: construct dialogues for process_id=%s", process_id)
    logger.info("data_source=%s", data_source if data_source else "default")
    logger.info("loader_type=%s", loader_type)
    logger.info("=" * 50)

    dialogues = load_dialogues(data_source, loader_type, include_conv_ids=include_conv_ids)
    if not dialogues:
        logger.error("No dialogues loaded")
        return False

    dialogues = filter_dialogues_by_conv_ids(dialogues, include_conv_ids)
    if not dialogues:
        logger.error("No dialogues left after conv_id filter")
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
    prompt_language: str = "en",
) -> bool:
    logger.info("=" * 50)
    logger.info("Stage 2: construct episodes for process_id=%s", process_id)
    logger.info("memory_owner_name=%s", DEFAULT_MEMORY_OWNER_NAME)
    logger.info("prompt_language=%s", prompt_language)
    try:
        episode_max_workers = max(1, int(os.environ.get("M_AGENT_EPISODE_MAX_WORKERS", "1")))
    except ValueError:
        episode_max_workers = 1
    logger.info("episode_max_workers=%s (env M_AGENT_EPISODE_MAX_WORKERS)", episode_max_workers)
    logger.info("=" * 50)

    if not build_episodes_with_id(
        process_id,
        str(PROJECT_ROOT),
        DEFAULT_MEMORY_OWNER_NAME,
        llm_model=llm_model,
        prompt_language=prompt_language,
        episode_max_workers=episode_max_workers,
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
    include_conv_ids: Optional[Sequence[str]] = None,
    clean_output: bool = False,
    memory_core_config_path: Optional[str] = None,
) -> bool:
    logger.info("Run simplified pipeline for process_id=%s", process_id)
    logger.info("data_source=%s loader_type=%s", data_source if data_source else "default", loader_type)
    logger.info("memory_owner_name=%s", DEFAULT_MEMORY_OWNER_NAME)
    logger.info("include_conv_ids=%s", _normalize_conv_ids(include_conv_ids))
    logger.info("clean_output=%s", clean_output)

    if clean_output:
        cleanup_pre_outputs(process_id=process_id, stages=("dialogues", "episodes"))

    memory_core_config: Dict[str, Any] | None = None
    if memory_core_config_path:
        try:
            import yaml
            mc_path = resolve_config_path(memory_core_config_path)
            with open(mc_path, "r", encoding="utf-8") as f:
                payload = yaml.safe_load(f) or {}
            if isinstance(payload, dict):
                memory_core_config = payload
                logger.info("Loaded memory_core_config for episode build: %s", mc_path)
        except Exception as exc:
            logger.warning("Failed to load memory_core_config_path=%s: %s", memory_core_config_path, exc)
            memory_core_config = None

    llm_model = init_llm_model_from_memory_core_config(memory_core_config)

    if not stage1_construct_dialogues_for_id(
        process_id,
        data_source,
        loader_type,
        include_conv_ids=include_conv_ids,
    ):
        logger.warning("Stage 1 failed")
        return False

    if not stage2_construct_episodes_for_id(
        process_id,
        llm_model=llm_model,
    ):
        logger.warning("Stage 2 failed")
        return False

    logger.info("Simplified pipeline complete for process_id=%s", process_id)
    logger.info("Scene/Atomic-facts generation has been moved to MemoryCore import flow.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Memory pre-processing pipeline (dialogues + episodes)")
    parser.add_argument("--id", type=str, required=True, help="Process ID")
    parser.add_argument("--data-source", type=str, default=None, help="Input data source path")
    parser.add_argument(
        "--loader-type",
        type=str,
        default="auto",
        choices=["auto", "realtalk", "locomo", "longmemeval", "default"],
        help="Dialogue loader type",
    )
    parser.add_argument(
        "--conv-ids",
        type=str,
        default="",
        help="Optional comma-separated LoCoMo conv ids, e.g. conv-30,conv-48.",
    )
    parser.add_argument(
        "--clean-output",
        action="store_true",
        help="Delete existing data/memory/<id>/dialogues and episodes before rebuild.",
    )
    parser.add_argument(
        "--memory-core-config",
        type=str,
        default="",
        help="Optional MemoryCore config path to pick episode LLM provider/model (connection still from .env). "
             f"Default is none; for a default core config use {DEFAULT_MEMORY_CORE_CONFIG_PATH}.",
    )

    args = parser.parse_args()
    include_conv_ids = [
        item.strip() for item in str(args.conv_ids or "").split(",") if item.strip()
    ]

    success = run_full_pipeline_for_id(
        args.id,
        data_source=args.data_source,
        loader_type=args.loader_type,
        include_conv_ids=include_conv_ids,
        clean_output=args.clean_output,
        memory_core_config_path=(str(args.memory_core_config or "").strip() or None),
    )

    if success:
        logger.info("Pipeline succeeded for process_id=%s", args.id)
    else:
        logger.error("Pipeline failed for process_id=%s", args.id)


if __name__ == "__main__":
    main()

