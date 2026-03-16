#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Minimal MemoryCore test: load config -> init models -> init MemoryCore -> load episodes."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("memory.build_memory.form_scene").setLevel(logging.WARNING)

from memory.memory_core.memory_system import MemoryCore


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


def init_llm_func(cfg: Dict[str, Any]):
    provider = str(cfg.get("llm_provider", "openai")).strip().lower()
    temperature = float(cfg.get("memory_llm_temperature", 0.0))
    model_name = str(cfg.get("llm_model_name", "")).strip()

    if provider != "openai":
        raise ValueError(f"unsupported llm_provider: {provider}, only 'openai' is supported in this test")

    from load_model.OpenAIcall import get_chat_llm, get_llm

    if model_name:
        return get_chat_llm(model_temperature=temperature, model_name=model_name)
    return get_llm(model_temperature=temperature)


def init_embed_func(cfg: Dict[str, Any]):
    provider = str(cfg.get("embed_provider", "aliyun")).strip().lower()
    model_name = str(cfg.get("embed_model_name", "")).strip()

    if provider in {"aliyun", "alibaba", "dashscope"}:
        from load_model.AlibabaEmbeddingCall import get_embed_model as get_alibaba_embed_model

        return get_alibaba_embed_model(model_name=model_name)

    if provider in {"local", "bge"}:
        from load_model.BGEcall import get_embed_model as get_local_embed_model

        return get_local_embed_model(model_name=model_name or None)

    raise ValueError(f"unsupported embed_provider: {provider}")


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
        use_threshold=bool(cfg.get("memory_use_threshold", True)),
        scene_prompt_version=str(cfg.get("scene_prompt_version", "v2")),
        action_prompt_version=str(cfg.get("action_prompt_version", "v1")),
        memory_owner_name=str(cfg.get("memory_owner_name", "changshengEVA")),
    )

    result = memory_core.load_from_episode_path(memory_core.episodes_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
