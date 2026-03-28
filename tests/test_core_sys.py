#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Minimal MemoryCore test: init MemoryCore -> load episodes -> build entity statements."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("memory.build_memory.form_scene").setLevel(logging.WARNING)

from m_agent.memory.memory_core.memory_system import MemoryCore


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "memory_core_config" / "test1.yaml"


def _load_simple_yaml(raw: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for line in str(raw or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        lower = value.lower()
        if lower in {"true", "false"}:
            data[key] = lower == "true"
            continue
        try:
            data[key] = int(value)
            continue
        except Exception:
            pass
        try:
            data[key] = float(value)
            continue
        except Exception:
            pass
        data[key] = value
    return data


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        cfg = yaml.safe_load(raw) or {}
        if not isinstance(cfg, dict):
            raise ValueError(f"config root must be dict: {path}")
        return cfg
    except ModuleNotFoundError:
        logger.warning("PyYAML not installed, using simple YAML parser for config file.")
        return _load_simple_yaml(raw)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    return default


def init_llm_func(cfg: Dict[str, Any]):
    provider = str(cfg.get("llm_provider", "openai")).strip().lower()
    temperature = float(cfg.get("memory_llm_temperature", 0.0))
    model_name = str(cfg.get("llm_model_name", "")).strip()

    if provider != "openai":
        raise ValueError(f"unsupported llm_provider: {provider}, only 'openai' is supported in this test")

    from m_agent.load_model.OpenAIcall import get_chat_llm, get_llm

    if model_name:
        return get_chat_llm(model_temperature=temperature, model_name=model_name)
    return get_llm(model_temperature=temperature)


def init_embed_func(cfg: Dict[str, Any]):
    provider = str(cfg.get("embed_provider", "aliyun")).strip().lower()
    model_name = str(cfg.get("embed_model_name", "")).strip()

    if provider in {"aliyun", "alibaba", "dashscope"}:
        from m_agent.load_model.AlibabaEmbeddingCall import get_embed_model as get_alibaba_embed_model

        return get_alibaba_embed_model(model_name=model_name)

    if provider in {"local", "bge"}:
        from m_agent.load_model.BGEcall import get_embed_model as get_local_embed_model

        return get_local_embed_model(model_name=model_name or None)

    raise ValueError(f"unsupported embed_provider: {provider}")


def _validate_fact_outputs(memory_core: MemoryCore, load_result: Dict[str, Any]) -> None:
    scene_build_result = load_result.get("scene_build_result", {})
    if not isinstance(scene_build_result, dict):
        raise AssertionError("scene_build_result is missing in load result")

    fact_entity_stats = scene_build_result.get("fact_entity_stats")
    if not isinstance(fact_entity_stats, dict):
        raise AssertionError("fact_entity_stats is missing in scene_build_result")

    facts_dir = memory_core.memory_root / "facts"
    if not facts_dir.exists():
        raise AssertionError(f"facts dir not found: {facts_dir}")

    fact_files = sorted([p for p in facts_dir.glob("*.json") if p.is_file()])
    logger.info(
        "fact_entity summary: scanned_scenes=%s facts_scanned=%s fact_files_written=%s llm_calls=%s",
        fact_entity_stats.get("scanned_scenes"),
        fact_entity_stats.get("facts_scanned"),
        fact_entity_stats.get("fact_files_written"),
        fact_entity_stats.get("llm_calls"),
    )
    if not fact_files:
        logger.warning("No fact files generated under %s", facts_dir)
        return

    sample_file = fact_files[0]
    sample_payload = json.loads(sample_file.read_text(encoding="utf-8"))
    required_fields = ["Atomic fact", "evidence", "embedding", "main_entity", "other_entities", "entity_UID"]
    missing_fields = [k for k in required_fields if k not in sample_payload]
    if missing_fields:
        raise AssertionError(f"fact file missing fields {missing_fields}: {sample_file}")

    if not isinstance(sample_payload.get("other_entities"), list):
        raise AssertionError(f"other_entities must be a list: {sample_file}")

    facts_situation_file = memory_core.memory_root / "facts_situation.json"
    if not facts_situation_file.exists():
        raise AssertionError(f"facts_situation.json not found: {facts_situation_file}")
    facts_situation_payload = json.loads(facts_situation_file.read_text(encoding="utf-8"))
    sample_fact_node = facts_situation_payload.get("facts", {}).get(sample_file.stem, {})
    if not isinstance(sample_fact_node, dict):
        raise AssertionError(f"fact status not found in facts_situation.json: {sample_file.stem}")
    if "entity_imported" not in sample_fact_node:
        raise AssertionError(f"entity_imported missing in facts_situation.json for fact: {sample_file.stem}")

    evidence = sample_payload.get("evidence", {})
    if not isinstance(evidence, dict):
        return
    dialogue_id = str(evidence.get("dialogue_id", "")).strip()
    episode_id = str(evidence.get("episode_id", "")).strip()
    if not dialogue_id or not episode_id:
        return

    episode_key = f"{dialogue_id}:{episode_id}"
    situation_file = memory_core.episodes_dir / "episode_situation.json"
    if not situation_file.exists():
        raise AssertionError(f"episode_situation.json not found: {situation_file}")

    situation_payload = json.loads(situation_file.read_text(encoding="utf-8"))
    episode_node = situation_payload.get("episodes", {}).get(episode_key, {})
    if not isinstance(episode_node, dict) or not episode_node:
        raise AssertionError(f"episode key not found in episode_situation.json: {episode_key}")

    status_fields = ["fact_entities_generated", "fact_entities_file_count"]
    status_missing = [k for k in status_fields if k not in episode_node]
    if status_missing:
        raise AssertionError(
            f"episode_situation missing fact-entity status fields {status_missing} for {episode_key}"
        )


def main() -> None:
    config_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_CONFIG_PATH
    cfg = load_config(config_path)

    llm_func = init_llm_func(cfg)
    embed_func = init_embed_func(cfg)

    memory_core = MemoryCore(
        workflow_id=str(cfg.get("workflow_id", "test6")),
        llm_func=llm_func,
        embed_func=embed_func,
        llm_temperature=float(cfg.get("memory_llm_temperature", 0.0)),
        similarity_threshold=float(cfg.get("memory_similarity_threshold", 0.88)),
        top_k=int(cfg.get("memory_top_k", 3)),
        use_threshold=_as_bool(cfg.get("memory_use_threshold", True), default=True),
        scene_prompt_version=str(cfg.get("scene_prompt_version", "v2")),
        fact_prompt_version=str(cfg.get("fact_prompt_version", "v2")),
        memory_owner_name=str(cfg.get("memory_owner_name", "changshengEVA")),
    )

    load_result = memory_core.load_from_episode_path(memory_core.episodes_dir)
    logger.info(
        "load_from_episode_path summary: success=%s files_processed=%s files_failed=%s episodes_processed=%s",
        load_result.get("success"),
        load_result.get("files_processed"),
        load_result.get("files_failed"),
        load_result.get("episodes_processed"),
    )
    _validate_fact_outputs(memory_core=memory_core, load_result=load_result)

    # entity_statement_force_update = _as_bool(cfg.get("entity_statement_force_update", False), default=False)
    # logger.info("make_entity_statement force_update=%s", entity_statement_force_update)
    # entity_statement_result = memory_core.make_entity_statement(
    #     memory_core.episodes_dir,
    #     force_update=entity_statement_force_update,
    # )
    # logger.info(
    #     "make_entity_statement summary: success=%s force_update=%s episodes_processed=%s episodes_skipped_existing=%s total_statements_generated=%s total_context_filtered=%s",
    #     entity_statement_result.get("success"),
    #     entity_statement_result.get("force_update"),
    #     entity_statement_result.get("episodes_processed"),
    #     entity_statement_result.get("episodes_skipped_existing"),
    #     entity_statement_result.get("total_statements_generated"),S
    #     entity_statement_result.get("total_context_filtered"),
    # )
    # memory_core.run_entity_resolution_pass()

if __name__ == "__main__":
    main()

