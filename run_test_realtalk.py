#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langchain.tools import tool

from load_model.AlibabaEmbeddingCall import get_embed_model as get_alibaba_embed_model
from load_model.BGEcall import get_embed_model as get_local_embed_model
from load_model.OpenAIcall import get_llm
from memory.memory_core.memory_system import MemoryCore

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROMPT_CONFIG_PATH = Path("config/Eval_env/APP_LANGCHAIN_REALTALK_TEST.yaml")
memory_sys: MemoryCore | None = None
detail_search_defaults: Dict[str, Any] = {
    "topk": 5,
}


@dataclass
class ResponseFormat:
    """Structured output schema for ReaLTalk memory QA."""

    answer: str
    gold_answer: str | None = None
    evidence: str | None = None


def _require_memory_sys() -> MemoryCore:
    if memory_sys is None:
        raise RuntimeError("memory_sys is not initialized.")
    return memory_sys


@tool
def search_content(dialogue_id: str, episode_id: str) -> Dict[str, Any]:
    """Fetch original dialogue turns by dialogue_id + episode_id."""

    return _require_memory_sys().search_content(
        dialogue_id=dialogue_id,
        episode_id=episode_id,
    )


@tool
def search_events_by_time_range(start_time: str, end_time: str) -> list[Dict[str, Any]]:
    """Search scenes by time range. Returns scene summaries in the time window."""

    return _require_memory_sys().search_events_by_time_range(
        start_time=start_time,
        end_time=end_time,
    )


@tool
def search_details(detail: str, topk: Optional[int] = None) -> Dict[str, Any]:
    """Search concrete details from scene memories by semantic similarity."""

    cfg_topk = detail_search_defaults["topk"] if topk is None else int(topk)
    return _require_memory_sys().search_details(
        detail_query=detail,
        topk=cfg_topk,
    )


def load_prompt_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Prompt config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    if not isinstance(config, dict):
        raise ValueError(f"Prompt config must be a dict: {path}")

    if not isinstance(config.get("system_prompt"), str) or not config["system_prompt"].strip():
        raise ValueError("`system_prompt` is required in APP_LANGCHAIN_REALTALK_TEST.yaml")
    return config


def init_memory_sys(config: Dict[str, Any]) -> MemoryCore:
    workflow_id = str(config.get("workflow_id", "testrt"))
    llm_temperature = float(config.get("memory_llm_temperature", 0.0))
    similarity_threshold = float(config.get("memory_similarity_threshold", 0.88))
    top_k = int(config.get("memory_top_k", 3))
    use_threshold = bool(config.get("memory_use_threshold", True))

    embed_provider = str(config.get("embed_provider", os.getenv("EMBED_PROVIDER", "local"))).strip().lower()
    if embed_provider in {"alibaba", "aliyun", "dashscope"}:
        logger.info("Embedding provider: %s (Alibaba API)", embed_provider)
        embed_func = get_alibaba_embed_model()
    elif embed_provider in {"local", "bge"}:
        logger.info("Embedding provider: %s (local BGE)", embed_provider)
        embed_func = get_local_embed_model()
    else:
        raise ValueError(
            f"Unsupported embed_provider: {embed_provider}. "
            "Use one of: local, bge, alibaba, aliyun, dashscope."
        )

    return MemoryCore(
        workflow_id=workflow_id,
        llm_func=get_llm(llm_temperature),
        embed_func=embed_func,
        llm_temperature=llm_temperature,
        similarity_threshold=similarity_threshold,
        top_k=top_k,
        use_threshold=use_threshold,
    )


def ensure_kg_data_initialized(memory_core: MemoryCore) -> None:
    kg_data_path = memory_core.kg_data_path
    kg_candidates_path = memory_core.memory_root / "kg_candidates"
    kg_files = [p for p in kg_data_path.rglob("*") if p.is_file()]

    if kg_files:
        logger.info("kg_data already has %d file(s), skip bootstrap import.", len(kg_files))
        return

    logger.info("kg_data is empty, bootstrap import from: %s", kg_candidates_path)
    load_result = memory_core.load_from_dialogue_path(kg_candidates_path)
    if not load_result.get("success", False):
        raise RuntimeError(f"Failed to initialize kg_data from kg_candidates: {load_result}")
    logger.info(
        "Bootstrap import completed: processed=%s, failed=%s",
        load_result.get("files_processed", 0),
        load_result.get("files_failed", 0),
    )


def main() -> None:
    global memory_sys, detail_search_defaults

    prompt_config = load_prompt_config(PROMPT_CONFIG_PATH)

    detail_cfg = prompt_config.get("detail_search_defaults", {})
    if isinstance(detail_cfg, dict):
        detail_search_defaults.update(detail_cfg)

    memory_sys = init_memory_sys(prompt_config)
    ensure_kg_data_initialized(memory_sys)

    model_name = str(prompt_config.get("model_name", "deepseek-chat"))
    agent_temperature = float(prompt_config.get("agent_temperature", 0.0))
    system_prompt = prompt_config["system_prompt"]
    default_question = str(prompt_config.get("default_question", "What are Emi's hobbies?"))
    thread_id = str(prompt_config.get("thread_id", "realtalk-test-1"))

    question = " ".join(sys.argv[1:]).strip() or default_question

    model = init_chat_model(
        model_name,
        temperature=agent_temperature,
        max_tokens=None,
        timeout=None,
        max_retries=2,
    )

    agent = create_agent(
        model=model,
        system_prompt=system_prompt,
        tools=[search_content, search_events_by_time_range, search_details],
        response_format=ToolStrategy(ResponseFormat),
    )

    response = agent.invoke(
        {"messages": [{"role": "user", "content": question}]},
        config={"configurable": {"thread_id": thread_id}},
    )

    structured = response.get("structured_response")
    if is_dataclass(structured):
        print(json.dumps(asdict(structured), ensure_ascii=False, indent=2))
    else:
        print(structured)


if __name__ == "__main__":
    main()
