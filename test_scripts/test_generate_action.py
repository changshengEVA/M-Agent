#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Smoke test for the Episode -> facts extraction flow.

It reconstructs one source episode from a scene file, feeds the episode into
`fact_extraction_v2`, and prints normalized fact items.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SCENE_PATH = PROJECT_ROOT / "data" / "memory" / "testlocomo" / "scene" / "00076.json"
DIALOGUES_ROOT = PROJECT_ROOT / "data" / "memory" / "testlocomo" / "dialogues"
PROMPT_KEY = "fact_extraction_v2"

from memory.build_memory.form_scene_details import (
    build_episode_payload,
    call_fact_extraction,
    complete_fact_item,
    extract_turns,
    fallback_extract_facts,
    find_dialogue_file,
    load_json,
    load_prompts,
    turns_to_dialogue_block,
)


def build_embed_func():
    try:
        from load_model.AlibabaEmbeddingCall import get_embed_model

        return get_embed_model()
    except Exception as exc:
        logger.warning("Alibaba embedding model init failed, use empty embedding fallback: %s", exc)
        return lambda _text: []


def main() -> None:
    scene = load_json(SCENE_PATH)
    prompt_template = load_prompts().get(PROMPT_KEY, "")
    if not isinstance(prompt_template, str) or not prompt_template.strip():
        raise ValueError(f"Prompt '{PROMPT_KEY}' not found")

    source = scene.get("source", {})
    episodes = source.get("episodes", []) if isinstance(source, dict) else []
    if not episodes:
        raise ValueError("scene.source.episodes is empty")

    source_ep = episodes[0]
    dialogue_id = str(source_ep.get("dialogue_id", "")).strip()
    if not dialogue_id:
        raise ValueError("scene.source.episodes[0].dialogue_id is empty")

    dialogue_file = find_dialogue_file(DIALOGUES_ROOT, dialogue_id)
    if dialogue_file is None:
        raise FileNotFoundError(f"Dialogue file not found for {dialogue_id}")

    dialogue_data = load_json(dialogue_file)
    turns = extract_turns(dialogue_data, source_ep.get("turn_span", []))
    dialogue_block = turns_to_dialogue_block(turns)
    logger.info("Episode dialogue reconstructed:\n%s", dialogue_block)

    episode_payload = build_episode_payload(
        source_ep=source_ep,
        turns=turns,
        dialogue_data=dialogue_data,
    )
    episode_payload_text = json.dumps(episode_payload, ensure_ascii=False, indent=2)
    start_time = str(episode_payload.get("start_time", "")).strip()

    try:
        raw_facts = call_fact_extraction(
            dialogue_block=dialogue_block,
            episode_payload_text=episode_payload_text,
            start_time=start_time,
            prompt_template=prompt_template,
        )
    except Exception as exc:
        logger.warning("LLM extraction failed, fallback to rule-based extraction: %s", exc)
        raw_facts = fallback_extract_facts(turns)

    if not raw_facts:
        raise ValueError("No fact extracted from episode")

    embed_model = build_embed_func()
    completed: List[Dict[str, Any]] = [
        complete_fact_item(
            raw_item=item,
            source_ep=source_ep,
            embed_model=embed_model,
        )
        for item in raw_facts
    ]

    output = {
        "all_facts": completed,
        "top_fact": completed[0],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
