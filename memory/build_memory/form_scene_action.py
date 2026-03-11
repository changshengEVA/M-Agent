#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scene action extraction module.

Scan scene files under one workflow and write back an `actions` field for each
scene. Actions are extracted from source dialogue spans referenced by
scene.source.episodes.
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

CONFIG_PATH = PROJECT_ROOT / "config" / "prompt" / "action_extraction.yaml"


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
        return [json.loads(object_match.group(0))]

    raise ValueError("No JSON payload found in LLM response")


def call_action_extraction(
    dialogue_block: str,
    prompt_template: str,
    llm_model: Optional[Callable[[str], str]] = None,
) -> List[Dict[str, Any]]:
    if llm_model is None:
        from load_model.OpenAIcall import get_llm

        llm_model = get_llm(model_temperature=0.1)

    full_prompt = prompt_template.replace("{dialogue_block}", dialogue_block)
    response = llm_model(full_prompt)
    parsed = extract_json_from_text(response)

    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError("Action extraction result is not a JSON list")
    return [x for x in parsed if isinstance(x, dict)]


def fallback_extract_actions(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
        speaker = str(turn.get("speaker", "")).strip()
        text = str(turn.get("text", "")).strip()
        lower = text.lower()
        if not text:
            continue

        if any(k in lower for k in keywords) or any(k in text for k in keywords):
            action = text
            sentences = re.split(r"(?<=[.!?。！？])\s+", text)
            for sentence in sentences:
                if any(k in sentence.lower() for k in keywords) or any(k in sentence for k in keywords):
                    action = sentence.strip()
                    break
            results.append(
                {
                    "actor": speaker,
                    "action": action,
                    "evidence_sentence": action,
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
                    "actor": str(first.get("speaker", "")).strip(),
                    "action": text,
                    "evidence_sentence": text,
                }
            ]

    return []


def infer_actor_from_evidence(evidence_sentence: str, turns: List[Dict[str, Any]], participants: List[str]) -> str:
    evidence = evidence_sentence.strip().lower()
    if evidence:
        for turn in turns:
            speaker = str(turn.get("speaker", "")).strip()
            text = str(turn.get("text", "")).strip().lower()
            if evidence in text and speaker:
                return speaker
    return participants[0] if participants else ""


def normalize_actor(actor: str, participants: List[str]) -> str:
    actor = actor.strip()
    if not actor:
        return actor
    mapping = {p.lower(): p for p in participants}
    return mapping.get(actor.lower(), actor)


def build_action_embedding_text(actor: str, action: str) -> str:
    actor_text = (actor or "").strip()
    action_text = (action or "").strip()
    if actor_text and action_text:
        return f"{actor_text}: {action_text}"
    return action_text or actor_text


def complete_action_item(
    raw_item: Dict[str, Any],
    source_ep: Dict[str, Any],
    turns: List[Dict[str, Any]],
    participants: List[str],
    embed_model: Callable[[Any], Any],
) -> Dict[str, Any]:
    evidence_sentence = str(raw_item.get("evidence_sentence", "")).strip()
    actor = str(raw_item.get("actor", "")).strip()
    action = str(raw_item.get("action", "")).strip()

    if not actor:
        actor = infer_actor_from_evidence(evidence_sentence, turns, participants)
    actor = normalize_actor(actor, participants)

    if not action:
        action = evidence_sentence or "unknown_action"

    embedding: List[float] = []
    embedding_input = build_action_embedding_text(actor=actor, action=action)
    try:
        vec = embed_model(embedding_input)
        if isinstance(vec, list):
            embedding = vec
    except Exception as exc:
        logger.warning("Embedding generation failed for action '%s': %s", embedding_input[:80], exc)

    return {
        "actor": actor,
        "action": action,
        "evidence": {
            "episode_id": str(source_ep.get("episode_id", "")),
            "dialogue_id": str(source_ep.get("dialogue_id", "")),
        },
        "embedding": embedding,
    }


def deduplicate_actions(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: List[Dict[str, Any]] = []
    seen = set()
    for item in actions:
        evidence = item.get("evidence", {})
        key = (
            str(item.get("actor", "")).strip().lower(),
            str(item.get("action", "")).strip().lower(),
            str(evidence.get("episode_id", "")).strip(),
            str(evidence.get("dialogue_id", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def build_actions_from_source_episode(
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

    participants = dialogue_data.get("participants", [])
    if not isinstance(participants, list):
        participants = []
    participants = [str(p) for p in participants]

    turns = extract_turns(dialogue_data, turn_span if isinstance(turn_span, list) else [])
    dialogue_block = turns_to_dialogue_block(turns)
    if not dialogue_block.strip():
        return []

    try:
        raw_actions = call_action_extraction(dialogue_block, prompt_template, llm_model=llm_model)
    except Exception as exc:
        logger.warning("LLM extraction failed for dialogue_id=%s, fallback enabled: %s", dialogue_id, exc)
        raw_actions = fallback_extract_actions(turns)

    if not raw_actions:
        return []

    completed = [
        complete_action_item(
            raw_item=item,
            source_ep=source_ep,
            turns=turns,
            participants=participants,
            embed_model=embed_model,
        )
        for item in raw_actions
    ]
    return deduplicate_actions(completed)


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

    if not force_update and isinstance(scene_data.get("actions"), list):
        return "skipped", len(scene_data.get("actions", []))

    source = scene_data.get("source", {})
    source_episodes = source.get("episodes", []) if isinstance(source, dict) else []
    if not isinstance(source_episodes, list):
        source_episodes = []

    actions: List[Dict[str, Any]] = []
    for source_ep in source_episodes:
        if not isinstance(source_ep, dict):
            continue
        actions.extend(
            build_actions_from_source_episode(
                source_ep=source_ep,
                dialogues_root=dialogues_root,
                prompt_template=prompt_template,
                embed_model=embed_model,
                llm_model=llm_model,
            )
        )

    scene_data["actions"] = deduplicate_actions(actions)

    try:
        save_json(scene_file, scene_data)
        return "updated", len(scene_data["actions"])
    except Exception as exc:
        logger.error("Failed to save scene file %s: %s", scene_file, exc)
        return "failed", 0


def scan_and_form_scene_actions(
    workflow_id: str = "default",
    prompt_version: str = "v1",
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
        return {"scanned": 0, "updated": 0, "skipped": 0, "failed": 0, "with_actions": 0, "empty_actions": 0}
    if not scene_root.exists():
        logger.error("Scene directory does not exist: %s", scene_root)
        return {"scanned": 0, "updated": 0, "skipped": 0, "failed": 0, "with_actions": 0, "empty_actions": 0}
    if not dialogues_root.exists():
        logger.error("Dialogues directory does not exist: %s", dialogues_root)
        return {"scanned": 0, "updated": 0, "skipped": 0, "failed": 0, "with_actions": 0, "empty_actions": 0}

    prompts = load_prompts()
    prompt_keys = [f"action_extractio_{prompt_version}", f"action_extraction_{prompt_version}"]
    prompt_template = ""
    for prompt_key in prompt_keys:
        value = prompts.get(prompt_key, "")
        if isinstance(value, str) and value.strip():
            prompt_template = value
            break
    if not isinstance(prompt_template, str) or not prompt_template.strip():
        logger.error("Prompt template not found for keys: %s", prompt_keys)
        return {"scanned": 0, "updated": 0, "skipped": 0, "failed": 0, "with_actions": 0, "empty_actions": 0}

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
        "with_actions": 0,
        "empty_actions": 0,
    }
    if not scene_files:
        logger.info("No scene files found under %s", scene_root)
        return stats

    file_iter = tqdm(scene_files, desc="Extract scene actions") if use_tqdm else scene_files
    for scene_file in file_iter:
        status, action_count = process_scene_file(
            scene_file=scene_file,
            dialogues_root=dialogues_root,
            prompt_template=prompt_template,
            force_update=force_update,
            embed_model=embed_model,
            llm_model=llm_model,
        )

        if status == "updated":
            stats["updated"] += 1
            if action_count > 0:
                stats["with_actions"] += 1
            else:
                stats["empty_actions"] += 1
        elif status == "skipped":
            stats["skipped"] += 1
            if action_count > 0:
                stats["with_actions"] += 1
            else:
                stats["empty_actions"] += 1
        else:
            stats["failed"] += 1

    logger.info(
        "Scene action extraction complete: scanned=%s updated=%s skipped=%s failed=%s with_actions=%s empty_actions=%s",
        stats["scanned"],
        stats["updated"],
        stats["skipped"],
        stats["failed"],
        stats["with_actions"],
        stats["empty_actions"],
    )
    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Extract actions for scene files")
    parser.add_argument("--workflow-id", type=str, default="default", help="Workflow ID under data/memory")
    parser.add_argument("--prompt-version", type=str, default="v1", help="Action prompt version suffix")
    parser.add_argument("--force-update", action="store_true", help="Regenerate actions even if already present")
    parser.add_argument("--no-tqdm", action="store_true", help="Disable tqdm progress bar")
    args = parser.parse_args()

    scan_and_form_scene_actions(
        workflow_id=args.workflow_id,
        prompt_version=args.prompt_version,
        force_update=args.force_update,
        use_tqdm=not args.no_tqdm,
    )
