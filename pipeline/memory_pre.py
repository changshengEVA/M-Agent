#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Memory pre-processing pipeline:
1. construct dialogues
2. construct episodes
3. form KG candidates
4. form scenes
5. extract scene features
6. extract scene actions and refresh action embeddings
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

load_dotenv()

# data loader
try:
    from load_data import load_dialogues
except ImportError:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    sys.path.append(project_root)
    from load_data.dialog_history_loader import load_dialogues

# pipeline utils
try:
    from utils import save_dialogue
    from utils.memory_build_utils import build_episodes_with_id
    from memory.build_memory.form_kg_candidate import scan_and_form_kg_candidates
    from memory.build_memory.form_scene import scan_and_form_scenes
    from memory.build_memory.form_scene_kg import scan_and_extract_features
    from memory.build_memory.form_scene_action import scan_and_form_scene_actions
except ImportError:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    sys.path.append(project_root)
    from utils.dialogue_utils import save_dialogue
    from utils.memory_build_utils import build_episodes_with_id
    from memory.build_memory.form_kg_candidate import scan_and_form_kg_candidates
    from memory.build_memory.form_scene import scan_and_form_scenes
    from memory.build_memory.form_scene_kg import scan_and_extract_features
    from memory.build_memory.form_scene_action import scan_and_form_scene_actions


PROJECT_ROOT = Path(__file__).parent.parent


def init_embed_model(embed_provider: str = "bge") -> Optional[Callable[[Any], Any]]:
    provider = (embed_provider or "bge").strip().lower()
    try:
        if provider in {"alibaba", "aliyun", "dashscope"}:
            from load_model.AlibabaEmbeddingCall import get_embed_model

            logger.info("Pre-initialize Alibaba embedding model")
            return get_embed_model()

        from load_model.BGEcall import get_embed_model

        logger.info("Pre-initialize BGE embedding model")
        return get_embed_model()
    except Exception as exc:
        logger.warning("Embedding pre-init failed (%s), fallback to lazy init: %s", provider, exc)
        return None


def init_llm_model(model_temperature: float = 0.1) -> Optional[Callable[[str], str]]:
    try:
        from load_model.OpenAIcall import get_llm

        logger.info("Pre-initialize LLM model (temperature=%s)", model_temperature)
        return get_llm(model_temperature=model_temperature)
    except Exception as exc:
        logger.warning("LLM pre-init failed, fallback to lazy init: %s", exc)
        return None


def get_output_path(process_id: str, stage_name: str) -> Path:
    return PROJECT_ROOT / "data" / "memory" / process_id / stage_name


def stage1_construct_dialogues_for_id(process_id: str, data_source: str = None, loader_type: str = "auto") -> bool:
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
    memory_owner_name: str = "changshengEVA",
    llm_model: Optional[Callable[[str], str]] = None,
    enable_episode_scoring_filter: bool = False,
) -> bool:
    logger.info("=" * 50)
    logger.info("Stage 2: construct episodes for process_id=%s", process_id)
    logger.info("memory_owner_name=%s", memory_owner_name)
    logger.info("enable_episode_scoring_filter=%s", enable_episode_scoring_filter)
    logger.info("=" * 50)

    if not build_episodes_with_id(
        process_id,
        str(PROJECT_ROOT),
        memory_owner_name,
        llm_model=llm_model,
        enable_episode_scoring_filter=enable_episode_scoring_filter,
    ):
        logger.error("Build episodes failed")
        return False

    episodes_root = get_output_path(process_id, "episodes")
    by_dialogue_dir = episodes_root / "by_dialogue"

    episode_files_count = 0
    qualification_files_count = 0
    eligibility_files_count = 0

    if by_dialogue_dir.exists():
        for dialogue_dir in by_dialogue_dir.iterdir():
            if not dialogue_dir.is_dir():
                continue
            for file_path in dialogue_dir.iterdir():
                if file_path.suffix != ".json":
                    continue
                if file_path.name == "episodes_v1.json":
                    episode_files_count += 1
                elif file_path.name == "qualifications_v1.json":
                    qualification_files_count += 1
                elif file_path.name.startswith("eligibility_"):
                    eligibility_files_count += 1

    logger.info("=" * 50)
    logger.info("Stage 2 complete")
    logger.info("episodes=%s qualifications=%s eligibility=%s", episode_files_count, qualification_files_count, eligibility_files_count)
    logger.info("output=%s", episodes_root)
    logger.info("=" * 50)
    return episode_files_count > 0


def stage3_form_kg_candidates_for_id(
    process_id: str,
    prompt_version: str = "v1",
    memory_owner_name: str = "changshengEVA",
    embed_model: Optional[Callable[[Any], Any]] = None,
    llm_model: Optional[Callable[[str], str]] = None,
) -> bool:
    logger.info("=" * 50)
    logger.info("Stage 3: form KG candidates for process_id=%s", process_id)
    logger.info("prompt_version=%s", prompt_version)
    logger.info("=" * 50)

    dialogues_root = get_output_path(process_id, "dialogues")
    episodes_root = get_output_path(process_id, "episodes")
    kg_candidates_root = get_output_path(process_id, "kg_candidates")

    dialogues_root.mkdir(parents=True, exist_ok=True)
    episodes_root.mkdir(parents=True, exist_ok=True)
    kg_candidates_root.mkdir(parents=True, exist_ok=True)

    try:
        scan_and_form_kg_candidates(
            prompt_version=prompt_version,
            dialogues_root=dialogues_root,
            episodes_root=episodes_root,
            kg_candidates_root=kg_candidates_root,
            memory_owner_name=memory_owner_name,
            embed_model=embed_model,
            llm_model=llm_model,
        )

        count = 0
        for file_path in kg_candidates_root.glob("*.json"):
            try:
                int(file_path.stem)
                count += 1
            except ValueError:
                continue

        logger.info("Stage 3 complete, kg_candidate files=%s", count)
        return count > 0
    except Exception as exc:
        logger.exception("Stage 3 failed: %s", exc)
        return False


def stage4_form_scenes_for_id(
    process_id: str,
    scene_prompt_version: str = "v1",
    memory_owner_name: str = "changshengEVA",
    embed_model: Optional[Callable[[Any], Any]] = None,
    llm_model: Optional[Callable[[str], str]] = None,
) -> bool:
    logger.info("=" * 50)
    logger.info("Stage 4: form scenes for process_id=%s", process_id)
    logger.info("scene_prompt_version=%s", scene_prompt_version)
    logger.info("=" * 50)

    dialogues_root = get_output_path(process_id, "dialogues")
    episodes_root = get_output_path(process_id, "episodes")
    scene_root = get_output_path(process_id, "scene")

    dialogues_root.mkdir(parents=True, exist_ok=True)
    episodes_root.mkdir(parents=True, exist_ok=True)
    scene_root.mkdir(parents=True, exist_ok=True)

    try:
        scan_and_form_scenes(
            prompt_version=scene_prompt_version,
            dialogues_root=dialogues_root,
            episodes_root=episodes_root,
            scene_root=scene_root,
            memory_owner_name=memory_owner_name,
            embed_model=embed_model,
            llm_model=llm_model,
        )

        count = 0
        for file_path in scene_root.glob("*.json"):
            try:
                int(file_path.stem)
                count += 1
            except ValueError:
                continue

        logger.info("Stage 4 complete, scene files=%s", count)
        return count > 0
    except Exception as exc:
        logger.exception("Stage 4 failed: %s", exc)
        return False


def stage5_form_scene_features_for_id(
    process_id: str,
    force_update: bool = False,
    memory_owner_name: str = "changshengEVA",
    embed_model: Optional[Callable[[Any], Any]] = None,
    llm_model: Optional[Callable[[str], str]] = None,
) -> bool:
    logger.info("=" * 50)
    logger.info("Stage 5: extract scene features for process_id=%s", process_id)
    logger.info("=" * 50)

    memory_root = PROJECT_ROOT / "data" / "memory" / process_id
    memory_root.mkdir(parents=True, exist_ok=True)

    try:
        scan_and_extract_features(
            workflow_id=process_id,
            force_update=force_update,
            use_tqdm=True,
            memory_owner_name=memory_owner_name,
            embed_model=embed_model,
            llm_model=llm_model,
        )

        kg_candidates_root = get_output_path(process_id, "kg_candidates")
        updated_files_count = 0
        for file_path in kg_candidates_root.glob("*.json"):
            try:
                int(file_path.stem)
            except ValueError:
                continue

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    kg_data = json.load(f)
                features = kg_data.get("kg_candidate", {}).get("facts", {}).get("features")
                if features is not None:
                    updated_files_count += 1
            except json.JSONDecodeError:
                continue

        logger.info("Stage 5 complete, updated files=%s", updated_files_count)
        return updated_files_count > 0
    except Exception as exc:
        logger.exception("Stage 5 failed: %s", exc)
        return False


def stage6_form_scene_actions_for_id(
    process_id: str,
    action_prompt_version: str = "v1",
    force_update: bool = False,
    embed_model: Optional[Callable[[Any], Any]] = None,
    llm_model: Optional[Callable[[str], str]] = None,
) -> bool:
    logger.info("=" * 50)
    logger.info("Stage 6: extract scene actions for process_id=%s", process_id)
    logger.info("action_prompt_version=%s", action_prompt_version)
    logger.info("=" * 50)

    memory_root = PROJECT_ROOT / "data" / "memory" / process_id
    memory_root.mkdir(parents=True, exist_ok=True)

    try:
        stage_stats = scan_and_form_scene_actions(
            workflow_id=process_id,
            prompt_version=action_prompt_version,
            force_update=force_update,
            use_tqdm=True,
            embed_model=embed_model,
            llm_model=llm_model,
        )

        scene_root = get_output_path(process_id, "scene")
        updated_files_count = 0
        non_empty_facts_count = 0
        for file_path in scene_root.glob("*.json"):
            try:
                int(file_path.stem)
            except ValueError:
                continue

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    scene_data = json.load(f)
                facts = scene_data.get("facts")
                if isinstance(facts, list):
                    updated_files_count += 1
                    if facts:
                        non_empty_facts_count += 1
            except json.JSONDecodeError:
                continue

        logger.info(
            "Stage 6 complete, scene files with facts=%s (non-empty=%s), fact_stats=%s",
            updated_files_count,
            non_empty_facts_count,
            stage_stats,
        )
        return updated_files_count > 0
    except Exception as exc:
        logger.exception("Stage 6 failed: %s", exc)
        return False


def run_full_pipeline_for_id(
    process_id: str,
    data_source: str = None,
    loader_type: str = "auto",
    prompt_version: str = "v1",
    include_stage5: bool = True,
    include_stage6: bool = True,
    scene_prompt_version: str = "v1",
    action_prompt_version: str = "v1",
    memory_owner_name: str = "changshengEVA",
    enable_episode_scoring_filter: bool = False,
    embed_provider: str = "bge",
    llm_temperature: float = 0.1,
) -> bool:
    logger.info("Run full pipeline for process_id=%s", process_id)
    logger.info("data_source=%s loader_type=%s", data_source if data_source else "default", loader_type)
    logger.info(
        "kg_prompt=%s scene_prompt=%s action_prompt=%s include_stage5=%s include_stage6=%s",
        prompt_version,
        scene_prompt_version,
        action_prompt_version,
        include_stage5,
        include_stage6,
    )
    logger.info("memory_owner_name=%s", memory_owner_name)
    logger.info("enable_episode_scoring_filter=%s", enable_episode_scoring_filter)
    logger.info("embed_provider=%s llm_temperature=%s", embed_provider, llm_temperature)
    llm_model = init_llm_model(llm_temperature)
    embed_model = init_embed_model(embed_provider)

    if not stage1_construct_dialogues_for_id(process_id, data_source, loader_type):
        logger.warning("Stage 1 failed")
        return False

    if not stage2_construct_episodes_for_id(
        process_id,
        memory_owner_name,
        llm_model=llm_model,
        enable_episode_scoring_filter=enable_episode_scoring_filter,
    ):
        logger.warning("Stage 2 failed")
        return False

    if not stage3_form_kg_candidates_for_id(
        process_id,
        prompt_version,
        memory_owner_name,
        embed_model=embed_model,
        llm_model=llm_model,
    ):
        logger.warning("Stage 3 failed")
        return False

    if not stage4_form_scenes_for_id(
        process_id,
        scene_prompt_version,
        memory_owner_name,
        embed_model=embed_model,
        llm_model=llm_model,
    ):
        logger.warning("Stage 4 failed")
        return False

    if include_stage5:
        if not stage5_form_scene_features_for_id(
            process_id,
            force_update=False,
            memory_owner_name=memory_owner_name,
            embed_model=embed_model,
            llm_model=llm_model,
        ):
            logger.warning("Stage 5 failed")
            return False

    if include_stage6:
        if not stage6_form_scene_actions_for_id(
            process_id,
            action_prompt_version=action_prompt_version,
            force_update=False,
            embed_model=embed_model,
            llm_model=llm_model,
        ):
            logger.warning("Stage 6 failed")
            return False

    logger.info("Pipeline complete for process_id=%s", process_id)
    return True


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Memory pre-processing pipeline"
    )
    parser.add_argument("--id", type=str, required=True, help="Process ID")
    parser.add_argument("--data-source", type=str, default=None, help="Input data source path")
    parser.add_argument(
        "--loader-type",
        type=str,
        default="auto",
        choices=["auto", "realtalk", "locomo", "default"],
        help="Dialogue loader type",
    )
    parser.add_argument("--kg-prompt-version", type=str, default="v3", help="KG prompt version")
    parser.add_argument("--scene-prompt-version", type=str, default="v2", help="Scene prompt version")
    parser.add_argument("--action-prompt-version", type=str, default="v1", help="Action prompt version")
    parser.add_argument("--no-stage5", action="store_true", help="Disable stage 5")
    parser.add_argument("--no-stage6", action="store_true", help="Disable stage 6")
    parser.add_argument(
        "--enable-episode-scoring-filter",
        action="store_true",
        help="Enable episode qualification scoring and eligibility filtering (disabled by default).",
    )
    parser.add_argument("--memory-owner-name", type=str, default="changshengEVA", help="Memory owner name")
    parser.add_argument(
        "--embed-provider",
        type=str,
        default=os.getenv("EMBED_PROVIDER", "bge"),
        choices=["bge", "local", "alibaba", "aliyun", "dashscope"],
        help="Embedding provider",
    )
    parser.add_argument(
        "--llm-temperature",
        type=float,
        default=float(os.getenv("LLM_TEMPERATURE", "0.0")),
        help="LLM temperature",
    )

    args = parser.parse_args()

    success = run_full_pipeline_for_id(
        args.id,
        data_source=args.data_source,
        loader_type=args.loader_type,
        prompt_version=args.kg_prompt_version,
        scene_prompt_version=args.scene_prompt_version,
        action_prompt_version=args.action_prompt_version,
        include_stage5=not args.no_stage5,
        include_stage6=not args.no_stage6,
        memory_owner_name=args.memory_owner_name,
        enable_episode_scoring_filter=args.enable_episode_scoring_filter,
        embed_provider=args.embed_provider,
        llm_temperature=args.llm_temperature,
    )

    if success:
        logger.info("Pipeline succeeded for process_id=%s", args.id)
    else:
        logger.error("Pipeline failed for process_id=%s", args.id)


if __name__ == "__main__":
    main()
##测试私有数据          python ./pipeline/memory_pre.py --id testdefault 
##测试realtalk数据      python ./pipeline/memory_pre.py --id testrt --data-source data\REALTALK\data\Chat_1_Emi_Elise.json --loader-type realtalk
##测试locomo数据        python ./pipeline/memory_pre.py --id evallocomo --data-source data\locomo\data\locomo10.json --loader-type locomo
