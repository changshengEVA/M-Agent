#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langchain.tools import tool

from load_model.BGEcall import get_embed_model
from load_model.OpenAIcall import get_llm
from memory.memory_core.memory_system import MemoryCore

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROMPT_CONFIG_PATH = Path("config/prompt/APP_LANGCHAIN_REALTALK_TEST.yaml")
memory_sys: MemoryCore | None = None
macro_search_defaults: Dict[str, Any] = {
    "use_threshold": True,
    "threshold": 0.7,
    "topk": 5,
}


@dataclass
class ResponseFormat:
    """Structured output schema for ReaLTalk memory QA."""

    answer: str
    evidence: str | None = None
    entity_uid: str | None = None


def _require_memory_sys() -> MemoryCore:
    if memory_sys is None:
        raise RuntimeError("memory_sys is not initialized.")
    return memory_sys


@tool
def resolve_entity(name: str) -> Dict[str, Any]:
    """Resolve a person/entity name to canonical entity UID in memory_sys."""

    return _require_memory_sys().resolve_entity(name=name)


@tool
def query_entity_property(entity_uid: str, query_text: str) -> Dict[str, Any]:
    """Query structured attributes/features for an entity by UID and query text."""

    return _require_memory_sys().query_entity_property(
        entity_uid=entity_uid,
        query_text=query_text,
    )


@tool
def search_macro_events(
    theme: str,
    use_threshold: bool | None = None,
    threshold: float | None = None,
    topk: int | None = None,
) -> Dict[str, Any]:
    """Search relevant scenes by semantic theme. Returns scene IDs and episode references."""

    cfg_use_threshold = (
        macro_search_defaults["use_threshold"] if use_threshold is None else bool(use_threshold)
    )
    cfg_threshold = macro_search_defaults["threshold"] if threshold is None else float(threshold)
    cfg_topk = macro_search_defaults["topk"] if topk is None else int(topk)

    return _require_memory_sys().search_macro_events(
        query={"theme": theme},
        use_threshold=cfg_use_threshold,
        threshold=cfg_threshold,
        topk=cfg_topk,
    )


@tool
def search_content(dialogue_id: str, episode_id: str) -> Dict[str, Any]:
    """Fetch original dialogue turns by dialogue_id + episode_id."""

    return _require_memory_sys().search_content(
        dialogue_id=dialogue_id,
        episode_id=episode_id,
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

    return MemoryCore(
        workflow_id=workflow_id,
        llm_func=get_llm(llm_temperature),
        embed_func=get_embed_model(),
        llm_temperature=llm_temperature,
        similarity_threshold=similarity_threshold,
        top_k=top_k,
        use_threshold=use_threshold,
    )


def main() -> None:
    global memory_sys, macro_search_defaults

    prompt_config = load_prompt_config(PROMPT_CONFIG_PATH)

    macro_cfg = prompt_config.get("macro_search_defaults", {})
    if isinstance(macro_cfg, dict):
        macro_search_defaults.update(macro_cfg)

    memory_sys = init_memory_sys(prompt_config)

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
        tools=[resolve_entity, query_entity_property, search_macro_events, search_content],
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
