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
    resolve_project_path,
)

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
        help="Delete existing scene directory and regenerate from scratch.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved settings only, do not run warmup.",
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


def init_memory_core(mc_cfg: Dict[str, Any], mc_path: Path) -> Any:
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
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    payload, config_path = load_env_config(args.env_config)
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
    workflow_id = str(mc_cfg.get("workflow_id", "")).strip()
    if not workflow_id:
        raise ValueError("workflow_id must not be empty in MemoryCore config.")

    episodes_dir = resolve_project_path(f"data/memory/{workflow_id}/episodes")
    scene_dir = resolve_project_path(f"data/memory/{workflow_id}/scene")

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
    logger.info("force              = %s", args.force)

    if not episodes_dir.exists():
        logger.error("Episodes directory not found: %s", episodes_dir)
        logger.error("Run import_locomo.py first to generate episodes.")
        return 1

    existing_scenes = list(scene_dir.glob("*.json")) if scene_dir.exists() else []
    if existing_scenes and not args.force:
        logger.info(
            "Scene directory already has %d file(s). "
            "Use --force to regenerate. Skipping warmup.",
            len(existing_scenes),
        )
        return 0

    if args.dry_run:
        logger.info("Dry-run mode, skip warmup execution.")
        return 0

    if existing_scenes and args.force:
        logger.info("--force: removing existing scene directory: %s", scene_dir)
        shutil.rmtree(scene_dir)

    memory_core = init_memory_core(mc_cfg, mc_path)
    logger.info("MemoryCore initialized (workflow_id=%s)", workflow_id)

    logger.info("Loading episodes from: %s", episodes_dir)
    result = memory_core.load_from_episode_path(Path(episodes_dir))

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
