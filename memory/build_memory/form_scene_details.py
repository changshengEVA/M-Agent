#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scene fact extraction module.

Scan scene files under one workflow and write back a `facts` field for each
scene. Each fact item stores one extracted atomic fact from source episode
spans referenced by scene.source.episodes.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:
    yaml = None
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config" / "prompt" / "fact_extraction.yaml"


def get_memory_root(workflow_id: str) -> Path:
    return PROJECT_ROOT / "data" / "memory" / workflow_id


def get_scene_root(memory_root: Path) -> Path:
    return memory_root / "scene"


def get_dialogues_root(memory_root: Path) -> Path:
    return memory_root / "dialogues"


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be a dict: {path}")
    return data


def save_json(path: Path, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_prompts() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        raw = f.read()

    if yaml is not None:
        config = yaml.safe_load(raw) or {}
        if isinstance(config, dict):
            return config

    # Fallback parser for key: | multi-line blocks.
    config: Dict[str, Any] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        matched = re.match(r"^\s*([A-Za-z0-9_]+)\s*:\s*\|\s*$", line)
        if not matched:
            i += 1
            continue

        key = matched.group(1)
        i += 1
        block: List[str] = []
        while i < len(lines):
            current = lines[i]
            if current.startswith("  ") or current.startswith("\t") or not current.strip():
                if current.startswith("  "):
                    block.append(current[2:])
                elif current.startswith("\t"):
                    block.append(current.lstrip("\t"))
                else:
                    block.append("")
                i += 1
                continue
            break
        config[key] = "\n".join(block).strip()

    return config


def scan_scene_files(scene_root: Path) -> List[Path]:
    if not scene_root.exists():
        return []

    def _sort_key(path: Path) -> Tuple[int, Any]:
        try:
            return (0, int(path.stem))
        except ValueError:
            return (1, path.stem)

    return sorted([p for p in scene_root.glob("*.json") if p.is_file()], key=_sort_key)


def find_dialogue_file(dialogues_root: Path, dialogue_id: str) -> Optional[Path]:
    direct = dialogues_root / f"{dialogue_id}.json"
    if direct.exists():
        return direct

    for p in dialogues_root.rglob(f"{dialogue_id}.json"):
        if p.is_file():
            return p
    return None


def extract_turns(dialogue_data: Dict[str, Any], turn_span: List[int]) -> List[Dict[str, Any]]:
    turns = dialogue_data.get("turns", [])
    if not isinstance(turns, list):
        return []

    if isinstance(turn_span, list) and len(turn_span) == 2 and all(isinstance(x, int) for x in turn_span):
        start_id, end_id = turn_span
        selected: List[Dict[str, Any]] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            tid = turn.get("turn_id")
            if isinstance(tid, int) and start_id <= tid <= end_id:
                selected.append(turn)
        return selected

    return [t for t in turns if isinstance(t, dict)]


def normalize_line(speaker: str, text: str) -> str:
    speaker = (speaker or "Unknown").strip()
    text = (text or "").strip()
    if not text:
        return f"{speaker}:"

    for prefix in (f"{speaker}:", f"{speaker}："):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return f"{speaker}: {text}"


def turns_to_dialogue_block(turns: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for turn in turns:
        lines.append(normalize_line(str(turn.get("speaker", "Unknown")), str(turn.get("text", ""))))
    return "\n".join(lines)


def extract_json_from_text(text: str) -> Any:
    payload = (text or "").strip()
    if not payload:
        return []

    fenced = re.search(r"```(?:json)?\s*(.*?)```", payload, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        payload = fenced.group(1).strip()

    try:
        return json.loads(payload)
    except Exception:
        pass

    array_match = re.search(r"\[[\s\S]*\]", payload)
    if array_match:
        return json.loads(array_match.group(0))

    object_match = re.search(r"\{[\s\S]*\}", payload)
    if object_match:
        return json.loads(object_match.group(0))

    raise ValueError("No JSON payload found in LLM response")


def resolve_episode_time(
    source_ep: Dict[str, Any],
    turns: List[Dict[str, Any]],
    dialogue_data: Dict[str, Any],
    field_name: str,
) -> str:
    direct_value = str(source_ep.get(field_name, "")).strip()
    if direct_value:
        return direct_value

    if turns:
        turn_index = 0 if field_name == "start_time" else -1
        turn_value = str(turns[turn_index].get("timestamp", "")).strip()
        if turn_value:
            return turn_value

    meta = dialogue_data.get("meta", {})
    if isinstance(meta, dict):
        meta_value = str(meta.get(field_name, "")).strip()
        if meta_value:
            return meta_value
    return ""


def build_episode_payload(
    source_ep: Dict[str, Any],
    turns: List[Dict[str, Any]],
    dialogue_data: Dict[str, Any],
) -> Dict[str, Any]:
    normalized_turns: List[Dict[str, Any]] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        normalized_turns.append(
            {
                "turn_id": turn.get("turn_id"),
                "speaker": str(turn.get("speaker", "Unknown")),
                "text": str(turn.get("text", "")),
                "timestamp": str(turn.get("timestamp", "")),
            }
        )

    payload: Dict[str, Any] = {
        "episode_id": str(source_ep.get("episode_id", "")),
        "dialogue_id": str(source_ep.get("dialogue_id", "")),
        "turn_span": source_ep.get("turn_span", []),
        "start_time": resolve_episode_time(source_ep, turns, dialogue_data, "start_time"),
        "end_time": resolve_episode_time(source_ep, turns, dialogue_data, "end_time"),
        "turns": normalized_turns,
    }

    participants = dialogue_data.get("participants", [])
    if isinstance(participants, list) and participants:
        payload["participants"] = participants
    return payload


def normalize_fact_items(parsed_payload: Any) -> List[Dict[str, Any]]:
    candidate_payload = parsed_payload
    if isinstance(candidate_payload, dict):
        event_log = candidate_payload.get("event_log")
        if isinstance(event_log, dict):
            candidate_payload = (
                event_log.get("atomic_fact")
                or event_log.get("atomic_facts")
                or event_log.get("facts")
                or event_log.get("fact_list")
                or []
            )
        else:
            candidate_payload = (
                candidate_payload.get("atomic_fact")
                or candidate_payload.get("atomic_facts")
                or candidate_payload.get("facts")
                or candidate_payload.get("fact_list")
                or [candidate_payload]
            )

    if not isinstance(candidate_payload, list):
        raise ValueError("Fact extraction result is not a JSON list")

    normalized: List[Dict[str, Any]] = []
    for item in candidate_payload:
        if isinstance(item, dict):
            atomic_fact = extract_atomic_fact(item)
            evidence_sentence = str(item.get("evidence_sentence", "")).strip()
            if atomic_fact:
                normalized.append(
                    {
                        "Atomic fact": atomic_fact,
                        "evidence_sentence": evidence_sentence,
                    }
                )
        elif isinstance(item, str) and item.strip():
            normalized.append({"Atomic fact": item.strip()})
    return normalized


def call_fact_extraction(
    dialogue_block: str,
    episode_payload_text: str,
    start_time: str,
    prompt_template: str,
    llm_model: Optional[Callable[[str], str]] = None,
) -> List[Dict[str, Any]]:
    if llm_model is None:
        from load_model.OpenAIcall import get_llm

        llm_model = get_llm(model_temperature=0.1)

    full_prompt = (
        prompt_template.replace("{dialogue_block}", dialogue_block)
        .replace("{episode}", episode_payload_text)
        .replace("{start_time}", start_time)
    )
    response = llm_model(full_prompt)
    parsed = extract_json_from_text(response)
    return normalize_fact_items(parsed)


def fallback_extract_facts(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keywords = (
        "perform",
        "festival",
        "rehears",
        "choreograph",
        "practice",
        "plan",
        "准备",
        "练习",
        "参加",
        "打算",
    )
    results: List[Dict[str, Any]] = []

    for turn in turns:
        text = str(turn.get("text", "")).strip()
        lower = text.lower()
        if not text:
            continue

        if any(k in lower for k in keywords) or any(k in text for k in keywords):
            atomic_fact = text
            sentences = re.split(r"(?<=[.!?。！？])\s+", text)
            for sentence in sentences:
                if any(k in sentence.lower() for k in keywords) or any(k in sentence for k in keywords):
                    atomic_fact = sentence.strip()
                    break
            results.append(
                {
                    "Atomic fact": atomic_fact,
                    "evidence_sentence": atomic_fact,
                }
            )

    if results:
        return results

    if turns:
        first = turns[0]
        text = str(first.get("text", "")).strip()
        if text:
            return [
                {
                    "Atomic fact": text,
                    "evidence_sentence": text,
                }
            ]

    return []


def extract_atomic_fact(raw_item: Dict[str, Any]) -> str:
    if not isinstance(raw_item, dict):
        return ""

    candidate_keys = ("Atomic fact", "atomic_fact", "atomic fact", "Atomic_fact")
    for key in candidate_keys:
        value = raw_item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def build_atomic_fact_embedding_text(atomic_fact: str) -> str:
    return (atomic_fact or "").strip()


def complete_fact_item(
    raw_item: Dict[str, Any],
    source_ep: Dict[str, Any],
    embed_model: Callable[[Any], Any],
) -> Dict[str, Any]:
    evidence_sentence = str(raw_item.get("evidence_sentence", "")).strip()
    atomic_fact = extract_atomic_fact(raw_item)
    if not atomic_fact:
        atomic_fact = evidence_sentence or "unknown_atomic_fact"

    embedding: List[float] = []
    embedding_input = build_atomic_fact_embedding_text(atomic_fact=atomic_fact)
    try:
        vec = embed_model(embedding_input)
        if isinstance(vec, list):
            embedding = vec
    except Exception as exc:
        logger.warning("Embedding generation failed for atomic_fact '%s': %s", embedding_input[:80], exc)

    return {
        "Atomic fact": atomic_fact,
        "evidence": {
            "episode_id": str(source_ep.get("episode_id", "")),
            "dialogue_id": str(source_ep.get("dialogue_id", "")),
        },
        "embedding": embedding,
    }


def deduplicate_facts(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: List[Dict[str, Any]] = []
    seen = set()
    for item in facts:
        evidence = item.get("evidence", {})
        atomic_fact = extract_atomic_fact(item).strip().lower()
        key = (
            atomic_fact,
            str(evidence.get("episode_id", "")).strip(),
            str(evidence.get("dialogue_id", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def build_facts_from_source_episode(
    source_ep: Dict[str, Any],
    dialogues_root: Path,
    prompt_template: str,
    embed_model: Callable[[Any], Any],
    llm_model: Optional[Callable[[str], str]] = None,
) -> List[Dict[str, Any]]:
    dialogue_id = str(source_ep.get("dialogue_id", "")).strip()
    turn_span = source_ep.get("turn_span", [])
    if not dialogue_id:
        return []

    dialogue_file = find_dialogue_file(dialogues_root, dialogue_id)
    if dialogue_file is None:
        logger.warning("Dialogue file not found for dialogue_id=%s", dialogue_id)
        return []

    try:
        dialogue_data = load_json(dialogue_file)
    except Exception as exc:
        logger.warning("Failed to load dialogue file %s: %s", dialogue_file, exc)
        return []

    turns = extract_turns(dialogue_data, turn_span if isinstance(turn_span, list) else [])
    dialogue_block = turns_to_dialogue_block(turns)
    if not dialogue_block.strip():
        return []

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
            llm_model=llm_model,
        )
    except Exception as exc:
        logger.warning("LLM fact extraction failed for dialogue_id=%s, fallback enabled: %s", dialogue_id, exc)
        raw_facts = fallback_extract_facts(turns)

    if not raw_facts:
        return []

    completed = [
        complete_fact_item(
            raw_item=item,
            source_ep=source_ep,
            embed_model=embed_model,
        )
        for item in raw_facts
    ]
    return deduplicate_facts(completed)


def process_scene_file(
    scene_file: Path,
    dialogues_root: Path,
    prompt_template: str,
    force_update: bool,
    embed_model: Callable[[Any], Any],
    llm_model: Optional[Callable[[str], str]] = None,
) -> Tuple[str, int]:
    try:
        scene_data = load_json(scene_file)
    except Exception as exc:
        logger.error("Failed to load scene file %s: %s", scene_file, exc)
        return "failed", 0

    if not force_update and isinstance(scene_data.get("facts"), list):
        return "skipped", len(scene_data.get("facts", []))

    source = scene_data.get("source", {})
    source_episodes = source.get("episodes", []) if isinstance(source, dict) else []
    if not isinstance(source_episodes, list):
        source_episodes = []

    facts: List[Dict[str, Any]] = []
    for source_ep in source_episodes:
        if not isinstance(source_ep, dict):
            continue
        facts.extend(
            build_facts_from_source_episode(
                source_ep=source_ep,
                dialogues_root=dialogues_root,
                prompt_template=prompt_template,
                embed_model=embed_model,
                llm_model=llm_model,
            )
        )

    scene_data["facts"] = deduplicate_facts(facts)
    scene_data.pop("actions", None)

    try:
        save_json(scene_file, scene_data)
        return "updated", len(scene_data["facts"])
    except Exception as exc:
        logger.error("Failed to save scene file %s: %s", scene_file, exc)
        return "failed", 0


def scan_and_form_scene_facts(
    workflow_id: str = "default",
    prompt_version: str = "v2",
    force_update: bool = False,
    use_tqdm: bool = True,
    embed_model: Optional[Callable[[Any], Any]] = None,
    llm_model: Optional[Callable[[str], str]] = None,
) -> Dict[str, int]:
    memory_root = get_memory_root(workflow_id)
    scene_root = get_scene_root(memory_root)
    dialogues_root = get_dialogues_root(memory_root)

    if not memory_root.exists():
        logger.error("Memory root does not exist: %s", memory_root)
        return {"scanned": 0, "updated": 0, "skipped": 0, "failed": 0, "with_facts": 0, "empty_facts": 0}
    if not scene_root.exists():
        logger.error("Scene directory does not exist: %s", scene_root)
        return {"scanned": 0, "updated": 0, "skipped": 0, "failed": 0, "with_facts": 0, "empty_facts": 0}
    if not dialogues_root.exists():
        logger.error("Dialogues directory does not exist: %s", dialogues_root)
        return {"scanned": 0, "updated": 0, "skipped": 0, "failed": 0, "with_facts": 0, "empty_facts": 0}

    prompts = load_prompts()
    prompt_keys = [f"fact_extraction_{prompt_version}"]
    prompt_template = ""
    for prompt_key in prompt_keys:
        value = prompts.get(prompt_key, "")
        if isinstance(value, str) and value.strip():
            prompt_template = value
            break
    if not isinstance(prompt_template, str) or not prompt_template.strip():
        logger.error("Prompt template not found for keys: %s", prompt_keys)
        return {"scanned": 0, "updated": 0, "skipped": 0, "failed": 0, "with_facts": 0, "empty_facts": 0}

    if embed_model is None:
        try:
            from load_model.BGEcall import get_embed_model

            embed_model = get_embed_model()
        except Exception as exc:
            logger.warning("Embedding model init failed, use empty embedding fallback: %s", exc)
            embed_model = lambda _text: []

    scene_files = scan_scene_files(scene_root)
    stats = {
        "scanned": len(scene_files),
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "with_facts": 0,
        "empty_facts": 0,
    }
    if not scene_files:
        logger.info("No scene files found under %s", scene_root)
        return stats

    file_iter = tqdm(scene_files, desc="Extract scene facts") if use_tqdm else scene_files
    for scene_file in file_iter:
        status, fact_count = process_scene_file(
            scene_file=scene_file,
            dialogues_root=dialogues_root,
            prompt_template=prompt_template,
            force_update=force_update,
            embed_model=embed_model,
            llm_model=llm_model,
        )

        if status == "updated":
            stats["updated"] += 1
            if fact_count > 0:
                stats["with_facts"] += 1
            else:
                stats["empty_facts"] += 1
        elif status == "skipped":
            stats["skipped"] += 1
            if fact_count > 0:
                stats["with_facts"] += 1
            else:
                stats["empty_facts"] += 1
        else:
            stats["failed"] += 1

    logger.info(
        "Scene fact extraction complete: scanned=%s updated=%s skipped=%s failed=%s with_facts=%s empty_facts=%s",
        stats["scanned"],
        stats["updated"],
        stats["skipped"],
        stats["failed"],
        stats["with_facts"],
        stats["empty_facts"],
    )
    return stats

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Extract facts for scene files")
    parser.add_argument("--workflow-id", type=str, default="default", help="Workflow ID under data/memory")
    parser.add_argument("--prompt-version", type=str, default="v2", help="Fact prompt version suffix")
    parser.add_argument("--force-update", action="store_true", help="Regenerate facts even if already present")
    parser.add_argument("--no-tqdm", action="store_true", help="Disable tqdm progress bar")
    args = parser.parse_args()

    scan_and_form_scene_facts(
        workflow_id=args.workflow_id,
        prompt_version=args.prompt_version,
        force_update=args.force_update,
        use_tqdm=not args.no_tqdm,
    )
