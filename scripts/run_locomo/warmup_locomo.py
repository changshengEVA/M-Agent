#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Warmup: generate scenes + atomic facts from episodes.

Replicates the exact MemoryCore initialization path used by
``eval_locomo.py`` (via ``create_memory_agent``), so the scene/fact
outputs are identical regardless of whether warmup is run standalone
or implicitly during eval.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict

import yaml

from _shared import (
    DEFAULT_ENV_CONFIG_PATH,
    load_env_config,
    log_env_config_summary,
    resolve_project_path,
)
from m_agent.paths import memory_workflow_dir
from m_agent.utils.pipeline_logging import suppress_verbose_pipeline_loggers

logger = logging.getLogger("run_locomo.warmup")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate scenes and atomic facts from episodes (warmup step)."
    )
    parser.add_argument(
        "--env-config",
        type=str,
        default=DEFAULT_ENV_CONFIG_PATH,
        help="Config path under config/eval/memory_agent/locomo.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Delete existing scene directory and regenerate from scratch; also passes "
            "force_rebuild into episode load (clears isolated Neo4j workflow DB when "
            "configured, removes segment build state, re-runs segment entity pipeline)."
        ),
    )
    parser.add_argument("--force-scene", action="store_true", help="Force rebuild scene outputs.")
    parser.add_argument("--force-facts", action="store_true", help="Force rebuild atomic facts outputs.")
    parser.add_argument("--force-kg", action="store_true", help="Force rebuild KG/segment entity pipeline.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved settings only, do not run warmup.",
    )
    parser.add_argument(
        "--memory-root",
        type=str,
        default="",
        help=(
            "Override MemoryCore storage root directory. If set, workflow data is read/written under "
            "<memory_root>/<workflow_id> (workflow_id may contain slashes like locomo/conv-48). "
            "This sets env var M_AGENT_MEMORY_ROOT for the process."
        ),
    )
    parser.add_argument(
        "--workflow-id",
        type=str,
        default="",
        help="Override workflow_id in MemoryCore config (must match import --process-id).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Verbose logging: show HTTP client INFO and per-chunk fact extraction lines.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Config resolution — mirrors MemoryAgentConfigMixin._load_config / _init_memory_sys
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a mapping in {path}")
    return payload


def _resolve_relative(base: Path, raw: str) -> Path:
    candidate = Path(raw.strip())
    if candidate.is_absolute():
        return candidate.resolve()
    return (base.parent / candidate).resolve()


def resolve_memory_core_config(agent_config_path: Path) -> tuple[Dict[str, Any], Path]:
    """Walk the same base_config_path chain as MemoryAgentConfigMixin._load_config
    to resolve the final memory_core_config_path."""

    def _merge_with_base(cfg_path: Path, visited: set[Path]) -> Dict[str, Any]:
        raw = _load_yaml(cfg_path)
        for key in ("memory_core_config_path", "runtime_prompt_config_path"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                p = Path(val.strip())
                if not p.is_absolute():
                    raw[key] = str((cfg_path.parent / p).resolve())
        base_raw = raw.get("base_config_path")
        if not isinstance(base_raw, str) or not base_raw.strip():
            return raw
        base_path = _resolve_relative(cfg_path, base_raw).resolve()
        if base_path in visited:
            raise ValueError(f"Cyclic base_config_path: {visited}")
        base = _merge_with_base(base_path, visited | {base_path})
        merged = dict(base)
        for k, v in raw.items():
            if k == "base_config_path":
                continue
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                sub = dict(merged[k])
                sub.update(v)
                merged[k] = sub
                continue
            merged[k] = v
        return merged

    resolved = agent_config_path.resolve()
    agent_cfg = _merge_with_base(resolved, {resolved})

    mc_path_raw = str(agent_cfg.get("memory_core_config_path", "")).strip()
    if not mc_path_raw:
        raise ValueError(f"memory_core_config_path missing in {agent_config_path}")
    mc_path = Path(mc_path_raw).resolve()
    if not mc_path.exists():
        raise FileNotFoundError(f"MemoryCore config not found: {mc_path}")

    mc_cfg = _load_yaml(mc_path)
    return mc_cfg, mc_path


def init_memory_core(mc_cfg: Dict[str, Any], mc_path: Path, *, skip_entity_library_align_on_init: bool = False) -> Any:
    """Initialize MemoryCore with the same parameters as
    MemoryAgentConfigMixin._init_memory_sys."""
    from m_agent.config_paths import MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH, resolve_related_config_path
    from m_agent.load_model.OpenAIcall import get_llm
    from m_agent.memory.memory_core.memory_system import MemoryCore
    from m_agent.prompt_utils import normalize_prompt_language

    embed_provider = str(
        mc_cfg.get("embed_provider", os.getenv("EMBED_PROVIDER", "local"))
    ).strip().lower()

    if embed_provider in {"alibaba", "aliyun", "dashscope"}:
        from m_agent.load_model.AlibabaEmbeddingCall import get_embed_model as get_alibaba_embed_model
        embed_func = get_alibaba_embed_model()
    elif embed_provider in {"local", "bge"}:
        from m_agent.load_model.BGEcall import get_embed_model as get_local_embed_model
        embed_func = get_local_embed_model()
    else:
        raise ValueError(f"Unsupported embed_provider: {embed_provider}")

    hybrid_config = mc_cfg.get("detail_search_hybrid")
    if not isinstance(hybrid_config, dict):
        hybrid_config = mc_cfg.get("detail_search_hybrid_config")
    if not isinstance(hybrid_config, dict):
        hybrid_config = {}

    multi_route_config = mc_cfg.get("detail_search_multi_route")
    if not isinstance(multi_route_config, dict):
        multi_route_config = {}

    runtime_prompt_config_path = resolve_related_config_path(
        mc_path,
        mc_cfg.get("runtime_prompt_config_path"),
        default_path=MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH,
    )

    return MemoryCore(
        workflow_id=str(mc_cfg.get("workflow_id", "testrt")),
        llm_func=get_llm(float(mc_cfg.get("memory_llm_temperature", 0.0))),
        embed_func=embed_func,
        llm_temperature=float(mc_cfg.get("memory_llm_temperature", 0.0)),
        similarity_threshold=float(mc_cfg.get("memory_similarity_threshold", 0.88)),
        top_k=int(mc_cfg.get("memory_top_k", 3)),
        use_threshold=bool(mc_cfg.get("memory_use_threshold", True)),
        scene_prompt_version=str(mc_cfg.get("scene_prompt_version", "v2")),
        fact_prompt_version=str(mc_cfg.get("fact_prompt_version", "v2")),
        memory_owner_name=str(mc_cfg.get("memory_owner_name", "changshengEVA")),
        prompt_language=normalize_prompt_language(mc_cfg.get("prompt_language", "zh")),
        runtime_prompt_config_path=runtime_prompt_config_path,
        detail_search_hybrid_config=hybrid_config,
        detail_search_multi_route_config=multi_route_config,
        facts_only_mode=bool(mc_cfg.get("facts_only_mode", False)),
        skip_entity_library_align_on_init=bool(skip_entity_library_align_on_init),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    payload, config_path = load_env_config(args.env_config)
    warmup_cfg = payload.get("warmup", {})
    if not isinstance(warmup_cfg, dict):
        warmup_cfg = {}
    debug = bool(args.debug) or bool(warmup_cfg.get("debug", False))
    suppress_verbose_pipeline_loggers(debug=debug)

    scene_workers = warmup_cfg.get("scene_max_workers", None)
    if scene_workers is not None:
        try:
            sw = max(1, int(scene_workers))
            os.environ["M_AGENT_SCENE_MAX_WORKERS"] = str(sw)
        except (TypeError, ValueError):
            logger.warning("Invalid warmup.scene_max_workers: %r — ignored.", scene_workers)

    sf_workers = warmup_cfg.get("scene_fact_max_workers", None)
    if sf_workers is not None:
        try:
            sf_i = max(1, int(sf_workers))
            os.environ["M_AGENT_SCENE_FACT_MAX_WORKERS"] = str(sf_i)
        except (TypeError, ValueError):
            logger.warning("Invalid warmup.scene_fact_max_workers: %r — ignored.", sf_workers)

    legacy_force_all = bool(args.force) or bool(warmup_cfg.get("force", False))
    force_scene = bool(args.force_scene) or bool(warmup_cfg.get("force_scene", False)) or legacy_force_all
    force_facts = bool(args.force_facts) or bool(warmup_cfg.get("force_facts", False)) or legacy_force_all
    force_kg = bool(args.force_kg) or bool(warmup_cfg.get("force_KG", False)) or legacy_force_all

    if str(args.memory_root or "").strip():
        os.environ["M_AGENT_MEMORY_ROOT"] = str(args.memory_root).strip()

    eval_cfg = payload.get("eval", {})
    if not isinstance(eval_cfg, dict):
        eval_cfg = {}

    agent_config_raw = str(
        eval_cfg.get("memory_agent_config", "config/agents/memory/locomo_eval_memory_agent.yaml")
        or "config/agents/memory/locomo_eval_memory_agent.yaml"
    ).strip()
    agent_config_path = resolve_project_path(agent_config_raw)
    if not agent_config_path.exists():
        raise FileNotFoundError(f"MemoryAgent config not found: {agent_config_path}")

    mc_cfg, mc_path = resolve_memory_core_config(agent_config_path)
    if args.workflow_id.strip():
        mc_cfg = dict(mc_cfg)
        mc_cfg["workflow_id"] = args.workflow_id.strip()
    workflow_id = str(mc_cfg.get("workflow_id", "")).strip()
    if not workflow_id:
        raise ValueError("workflow_id must not be empty in MemoryCore config (or pass --workflow-id).")

    # --force should mean "from scratch" for local derived artifacts too.
    # Keep episodes/ and dialogues/ (import outputs), but wipe local_store so profiles/libraries do not
    # start from previously saved files.
    if force_kg:
        from m_agent.paths import memory_workflow_dir

        wf_root = memory_workflow_dir(workflow_id)
        local_store = wf_root / "local_store"
        if local_store.exists() and local_store.is_dir():
            try:
                logger.info("--force-kg: removing existing local_store directory: %s", local_store)
                shutil.rmtree(local_store)
            except Exception as exc:
                logger.warning("--force: failed to remove local_store %s: %s", local_store, exc)

    workflow_root = memory_workflow_dir(workflow_id)
    episodes_dir = workflow_root / "episodes"
    scene_dir = workflow_root / "scene"

    logger.info("=" * 60)
    logger.info("Warmup: generate scenes + atomic facts")
    logger.info("=" * 60)
    logger.info("env_config         = %s", config_path)
    logger.info("agent_config       = %s", agent_config_path)
    logger.info("memory_core_config = %s", mc_path)
    logger.info("workflow_id        = %s", workflow_id)
    logger.info("episodes_dir       = %s", episodes_dir)
    logger.info("scene_dir          = %s", scene_dir)
    logger.info("embed_provider     = %s", mc_cfg.get("embed_provider", "local"))
    logger.info("fact_prompt_version= %s", mc_cfg.get("fact_prompt_version", "v2"))
    logger.info("scene_prompt_version=%s", mc_cfg.get("scene_prompt_version", "v2"))
    logger.info("facts_only_mode    = %s", mc_cfg.get("facts_only_mode", False))
    logger.info("force_scene        = %s", force_scene)
    logger.info("force_facts        = %s", force_facts)
    logger.info("force_KG           = %s", force_kg)
    logger.info("force(all legacy)  = %s (CLI --force or warmup.force)", legacy_force_all)
    logger.info(
        "scene_max_workers env: M_AGENT_SCENE_MAX_WORKERS=%s | scene_fact_max_workers env: M_AGENT_SCENE_FACT_MAX_WORKERS=%s",
        os.environ.get("M_AGENT_SCENE_MAX_WORKERS", "(unset → defaults to 1)"),
        os.environ.get("M_AGENT_SCENE_FACT_MAX_WORKERS", "(unset → defaults to 1)"),
    )

    log_env_config_summary(
        logger,
        payload,
        config_path,
        step="LoCoMo warmup",
        footer={
            "workflow_id": workflow_id,
            "episodes_dir": str(episodes_dir),
            "scene_dir": str(scene_dir),
            "force_scene": force_scene,
            "force_facts": force_facts,
            "force_KG": force_kg,
        },
    )

    if not episodes_dir.exists():
        logger.error("Episodes directory not found: %s", episodes_dir)
        logger.error("Run import_locomo.py first to generate episodes.")
        return 1

    existing_scenes = list(scene_dir.glob("*.json")) if scene_dir.exists() else []
    facts_dir = workflow_root / "facts"
    existing_facts = list(facts_dir.glob("*.json")) if facts_dir.exists() else []

    # Decide whether to build scenes/facts this run.
    # - If force_scene/force_facts is explicitly set, rebuild those stages.
    # - If the user only forces KG (segment entity pipeline), do NOT auto-rebuild scene/facts
    #   even when they are missing — KG can be rebuilt directly from episodes.
    # - Otherwise (normal warmup), build missing stages.
    build_scenes = bool(force_scene or ((not force_kg) and (not existing_scenes)))
    build_facts = bool(force_facts or ((not force_kg) and (not existing_facts)))

    should_run = bool(force_scene or force_facts or force_kg or build_scenes or build_facts)
    if (existing_scenes or existing_facts) and not should_run:
        logger.info("Nothing to do (no force flags set). Skipping warmup.")
        return 0

    if args.dry_run:
        logger.info("Dry-run mode, skip warmup execution.")
        return 0

    if existing_scenes and force_scene:
        logger.info("--force-scene: removing existing scene directory: %s", scene_dir)
        shutil.rmtree(scene_dir)

    # When forcing rebuild, avoid aligning EntityLibrary from the old KG during MemoryCore init.
    memory_core = init_memory_core(
        mc_cfg,
        mc_path,
        skip_entity_library_align_on_init=force_kg,
    )
    logger.info("MemoryCore initialized (workflow_id=%s)", workflow_id)

    logger.info("Loading episodes from: %s", episodes_dir)
    result = memory_core.load_from_episode_path(
        Path(episodes_dir),
        force_rebuild=force_kg,
        build_scenes=build_scenes,
        build_facts=build_facts,
        force_scene=force_scene,
        force_facts=force_facts,
    )

    if not result.get("success", False):
        logger.error("Warmup failed: %s", result.get("error", "unknown"))
        return 1

    scene_files = list(scene_dir.glob("*.json")) if scene_dir.exists() else []
    build_result = result.get("scene_build_result", {})
    fact_stats = build_result.get("fact_stats", {})

    logger.info("=" * 60)
    logger.info("Warmup complete")
    logger.info("  episodes processed : %s", result.get("episodes_processed", 0))
    logger.info("  scene files created: %s", len(scene_files))
    logger.info("  fact stats         : %s", fact_stats)
    logger.info("  scene_dir          : %s", scene_dir)
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
