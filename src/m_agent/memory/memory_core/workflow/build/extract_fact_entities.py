#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fact-entity extraction workflow.

Scan scene files under one workflow, enrich each fact with:
- main_entity
- other_entities

Then dump one fact per json file under:
data/memory/{workflow_id}/facts
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from tqdm import tqdm

from m_agent.config_paths import MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH
from m_agent.paths import PROJECT_ROOT as DEFAULT_PROJECT_ROOT
from m_agent.prompt_utils import (
    load_resolved_prompt_config,
    normalize_prompt_language,
    render_prompt_template,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = DEFAULT_PROJECT_ROOT

_OPTIONAL_FACT_FIELDS = (
    "evidence_sentence",
    "fact_type",
    "keywords",
    "entities",
    "time_norm",
    "relation",
    "event_tags",
)


def get_memory_root(workflow_id: str) -> Path:
    return Path(PROJECT_ROOT) / "data" / "memory" / str(workflow_id)


def get_scene_root(memory_root: Path) -> Path:
    return memory_root / "scene"


def get_facts_root(memory_root: Path) -> Path:
    return memory_root / "facts"


def get_episode_situation_file(memory_root: Path) -> Path:
    return memory_root / "episodes" / "episode_situation.json"


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be a dict: {path}")
    return data


def save_json(path: Path, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_episode_situation(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                if "episodes" not in loaded or not isinstance(loaded.get("episodes"), dict):
                    loaded["episodes"] = {}
                return loaded
        except Exception as exc:
            logger.warning("Load episode_situation failed (%s): %s", path, exc)
    return {"statistics": {}, "episodes": {}, "metadata": {}}


def _save_episode_situation(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if "metadata" not in data or not isinstance(data.get("metadata"), dict):
        data["metadata"] = {}
    data["metadata"]["last_updated"] = datetime.utcnow().isoformat() + "Z"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _parse_episode_key(episode_key: str) -> Tuple[str, str]:
    if ":" in episode_key:
        left, right = episode_key.split(":", 1)
        return left, right
    return episode_key, "ep_001"


def _ensure_episode_node(situation_data: Dict[str, Any], episode_key: str) -> Dict[str, Any]:
    episodes = situation_data.setdefault("episodes", {})
    if not isinstance(episodes, dict):
        situation_data["episodes"] = {}
        episodes = situation_data["episodes"]

    node = episodes.get(episode_key)
    if not isinstance(node, dict):
        dialogue_id, episode_id = _parse_episode_key(episode_key)
        node = {
            "episode_key": episode_key,
            "dialogue_id": dialogue_id,
            "episode_id": episode_id,
        }
        episodes[episode_key] = node
    else:
        node["episode_key"] = episode_key
        if "dialogue_id" not in node or "episode_id" not in node:
            dialogue_id, episode_id = _parse_episode_key(episode_key)
            node.setdefault("dialogue_id", dialogue_id)
            node.setdefault("episode_id", episode_id)
    return node


def scan_scene_files(scene_root: Path) -> List[Path]:
    if not scene_root.exists():
        return []

    def _sort_key(path: Path) -> Tuple[int, Any]:
        try:
            return (0, int(path.stem))
        except ValueError:
            return (1, path.stem)

    return sorted([p for p in scene_root.glob("*.json") if p.is_file()], key=_sort_key)


def extract_json_from_text(text: str) -> Any:
    payload = (text or "").strip()
    if not payload:
        return {}

    fenced = re.search(r"```(?:json)?\s*(.*?)```", payload, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        payload = fenced.group(1).strip()

    try:
        return json.loads(payload)
    except Exception:
        pass

    object_match = re.search(r"\{[\s\S]*\}", payload)
    if object_match:
        return json.loads(object_match.group(0))

    raise ValueError("No JSON payload found in LLM response")


def extract_atomic_fact(raw_item: Dict[str, Any]) -> str:
    if not isinstance(raw_item, dict):
        return ""

    candidate_keys = ("Atomic fact", "atomic_fact", "atomic fact", "Atomic_fact")
    for key in candidate_keys:
        value = raw_item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_entity_list(raw_value: Any) -> List[str]:
    if isinstance(raw_value, list):
        source_items = raw_value
    elif isinstance(raw_value, str):
        source_items = [raw_value]
    else:
        source_items = []

    results: List[str] = []
    seen: Set[str] = set()
    for item in source_items:
        entity = str(item or "").strip()
        if not entity:
            continue
        norm = entity.lower()
        if norm in seen:
            continue
        seen.add(norm)
        results.append(entity)
    return results


def normalize_entities(main_entity: Any, other_entities: Any) -> Tuple[str, List[str]]:
    main = str(main_entity or "").strip()
    others = normalize_entity_list(other_entities)
    if main:
        main_norm = main.lower()
        others = [x for x in others if x.lower() != main_norm]
    return main, others


def normalize_entity_uid(raw_value: Any) -> str:
    return str(raw_value or "").strip()


def _normalize_optional_fact_fields(raw_payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key in _OPTIONAL_FACT_FIELDS:
        if key not in raw_payload:
            continue
        value = raw_payload.get(key)
        if isinstance(value, str):
            clean = value.strip()
            if clean:
                normalized[key] = clean
            continue
        if isinstance(value, list):
            values = [str(v).strip() for v in value if str(v).strip()]
            if values:
                normalized[key] = values
            continue
        if isinstance(value, dict) and value:
            normalized[key] = value
    return normalized


def normalize_fact_output_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_payload = payload if isinstance(payload, dict) else {}
    evidence = raw_payload.get("evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
    embedding = raw_payload.get("embedding", [])
    if not isinstance(embedding, list):
        embedding = []
    main_entity, other_entities = normalize_entities(
        raw_payload.get("main_entity"),
        raw_payload.get("other_entities"),
    )
    normalized_output = {
        "scene_id": str(raw_payload.get("scene_id", "") or "").strip(),
        "fact_id": str(raw_payload.get("fact_id", "") or "").strip(),
        "Atomic fact": extract_atomic_fact(raw_payload),
        "evidence": evidence,
        "embedding": embedding,
        "main_entity": main_entity,
        "other_entities": other_entities,
        "entity_UID": normalize_entity_uid(raw_payload.get("entity_UID")),
    }
    normalized_output.update(_normalize_optional_fact_fields(raw_payload))
    return normalized_output


def should_write_fact_file(path: Path, output_payload: Dict[str, Any]) -> bool:
    normalized_output = normalize_fact_output_payload(output_payload)
    if not path.exists():
        return True
    try:
        existing_payload = load_json(path)
    except Exception:
        return True
    return normalize_fact_output_payload(existing_payload) != normalized_output


def parse_entity_payload(parsed_payload: Any) -> Tuple[str, List[str]]:
    if isinstance(parsed_payload, list) and parsed_payload:
        parsed_payload = parsed_payload[0]

    if not isinstance(parsed_payload, dict):
        return "", []

    main_entity = parsed_payload.get("main_entity", "")
    other_entities = parsed_payload.get("other_entities", [])
    return normalize_entities(main_entity, other_entities)


def call_fact_entity_extraction(
    atomic_fact: str,
    llm_model: Callable[[str], str],
    prompt_template: str,
) -> Tuple[str, List[str]]:
    full_prompt = render_prompt_template(prompt_template, {"<sentence>": atomic_fact})
    response = llm_model(full_prompt)
    parsed = extract_json_from_text(response)
    return parse_entity_payload(parsed)


def has_entity_fields(item: Dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    main_entity = item.get("main_entity")
    other_entities = item.get("other_entities")
    return isinstance(main_entity, str) and isinstance(other_entities, list)


def extract_episode_keys_from_scene(scene_data: Dict[str, Any]) -> List[str]:
    source = scene_data.get("source", {})
    if not isinstance(source, dict):
        return []

    episodes = source.get("episodes", [])
    if not isinstance(episodes, list):
        return []

    results: List[str] = []
    seen: Set[str] = set()
    for item in episodes:
        if not isinstance(item, dict):
            continue
        dialogue_id = str(item.get("dialogue_id", "")).strip()
        episode_id = str(item.get("episode_id", "")).strip()
        if not dialogue_id or not episode_id:
            continue
        key = f"{dialogue_id}:{episode_id}"
        if key in seen:
            continue
        seen.add(key)
        results.append(key)
    return results


def build_fact_filename(scene_stem: str, fact_index: int) -> str:
    return f"{scene_stem}_{fact_index + 1:04d}.json"


def resolve_episode_key_for_fact(
    fact_item: Dict[str, Any],
    scene_episode_keys: List[str],
) -> List[str]:
    evidence = fact_item.get("evidence", {})
    if isinstance(evidence, dict):
        dialogue_id = str(evidence.get("dialogue_id", "")).strip()
        episode_id = str(evidence.get("episode_id", "")).strip()
        if dialogue_id and episode_id:
            return [f"{dialogue_id}:{episode_id}"]
    return scene_episode_keys[:]


def scan_and_extract_fact_entities(
    workflow_id: str = "default",
    force_update: bool = False,
    use_tqdm: bool = True,
    llm_model: Optional[Callable[[str], str]] = None,
    prompt_language: str = "zh",
    runtime_prompt_config_path: str | Path | None = None,
) -> Dict[str, Any]:
    memory_root = get_memory_root(workflow_id)
    scene_root = get_scene_root(memory_root)
    facts_root = get_facts_root(memory_root)
    situation_file = get_episode_situation_file(memory_root)

    stats: Dict[str, Any] = {
        "scanned_scenes": 0,
        "updated_scenes": 0,
        "failed_scenes": 0,
        "scenes_without_facts": 0,
        "facts_scanned": 0,
        "facts_extracted": 0,
        "facts_with_main_entity": 0,
        "facts_failed": 0,
        "fact_files_written": 0,
        "fact_files_failed": 0,
        "llm_calls": 0,
        "episodes_status_updated": 0,
    }

    if not memory_root.exists():
        logger.error("Memory root does not exist: %s", memory_root)
        return stats
    if not scene_root.exists():
        logger.error("Scene root does not exist: %s", scene_root)
        return stats

    facts_root.mkdir(parents=True, exist_ok=True)

    if llm_model is None:
        from m_agent.load_model.OpenAIcall import get_llm

        llm_model = get_llm(model_temperature=0.0)

    prompt_template = _load_fact_entity_prompt(
        prompt_language=prompt_language,
        runtime_prompt_config_path=runtime_prompt_config_path,
    )

    scene_files = scan_scene_files(scene_root)
    stats["scanned_scenes"] = len(scene_files)
    stats["facts_root"] = str(facts_root)
    stats["episode_status_file"] = str(situation_file)

    if not scene_files:
        logger.info("No scene files found under %s", scene_root)
        return stats

    entity_cache: Dict[str, Tuple[str, List[str]]] = {}
    episode_fact_index: Dict[str, Dict[str, Any]] = {}

    file_iter = tqdm(scene_files, desc="Extract fact entities") if use_tqdm else scene_files
    for scene_file in file_iter:
        try:
            scene_data = load_json(scene_file)
        except Exception as exc:
            logger.error("Failed to load scene file %s: %s", scene_file, exc)
            stats["failed_scenes"] += 1
            continue

        scene_episode_keys = extract_episode_keys_from_scene(scene_data)
        for episode_key in scene_episode_keys:
            episode_fact_index.setdefault(
                episode_key,
                {"file_count": 0, "last_file": None, "error": ""},
            )

        facts = scene_data.get("facts", [])
        if not isinstance(facts, list):
            stats["scenes_without_facts"] += 1
            continue

        if not facts:
            stats["scenes_without_facts"] += 1

        scene_changed = False
        scene_id = str(scene_data.get("scene_id", scene_file.stem)).strip()
        for idx, fact_item in enumerate(facts):
            if not isinstance(fact_item, dict):
                continue

            stats["facts_scanned"] += 1
            atomic_fact = extract_atomic_fact(fact_item)
            if not atomic_fact:
                continue

            do_extract = force_update or not has_entity_fields(fact_item)
            if do_extract:
                cache_key = atomic_fact.strip().lower()
                try:
                    if cache_key in entity_cache:
                        main_entity, other_entities = entity_cache[cache_key]
                    else:
                        main_entity, other_entities = call_fact_entity_extraction(
                            atomic_fact,
                            llm_model=llm_model,
                            prompt_template=prompt_template,
                        )
                        entity_cache[cache_key] = (main_entity, other_entities)
                        stats["llm_calls"] += 1
                    stats["facts_extracted"] += 1
                except Exception as exc:
                    logger.warning("Fact entity extraction failed (%s): %s", atomic_fact[:80], exc)
                    main_entity, other_entities = "", []
                    stats["facts_failed"] += 1
            else:
                main_entity, other_entities = normalize_entities(
                    fact_item.get("main_entity"),
                    fact_item.get("other_entities"),
                )

            existing_main, existing_others = normalize_entities(
                fact_item.get("main_entity"),
                fact_item.get("other_entities"),
            )
            if existing_main != main_entity or existing_others != other_entities:
                scene_changed = True
            raw_entity_uid = fact_item.get("entity_UID")
            entity_uid = normalize_entity_uid(raw_entity_uid)
            if raw_entity_uid is None:
                scene_changed = True
            fact_item["main_entity"] = main_entity
            fact_item["other_entities"] = other_entities
            fact_item["entity_UID"] = entity_uid
            if main_entity:
                stats["facts_with_main_entity"] += 1

            fact_file_name = build_fact_filename(scene_file.stem, idx)
            output_payload: Dict[str, Any] = {
                "scene_id": scene_id,
                "fact_id": Path(fact_file_name).stem,
                "Atomic fact": atomic_fact,
                "evidence": fact_item.get("evidence", {}),
                "embedding": fact_item.get("embedding", []),
                "main_entity": main_entity,
                "other_entities": other_entities,
                "entity_UID": entity_uid,
            }
            output_payload.update(_normalize_optional_fact_fields(fact_item))

            fact_output_path = facts_root / fact_file_name
            if should_write_fact_file(fact_output_path, output_payload):
                try:
                    save_json(fact_output_path, output_payload)
                    stats["fact_files_written"] += 1
                except Exception as exc:
                    logger.error("Failed to save fact file %s: %s", fact_file_name, exc)
                    stats["fact_files_failed"] += 1
                    for episode_key in resolve_episode_key_for_fact(fact_item, scene_episode_keys):
                        tracked = episode_fact_index.setdefault(
                            episode_key,
                            {"file_count": 0, "last_file": None, "error": ""},
                        )
                        tracked["error"] = str(exc)
                    continue

            for episode_key in resolve_episode_key_for_fact(fact_item, scene_episode_keys):
                tracked = episode_fact_index.setdefault(
                    episode_key,
                    {"file_count": 0, "last_file": None, "error": ""},
                )
                tracked["file_count"] = int(tracked.get("file_count", 0)) + 1
                tracked["last_file"] = fact_file_name

        if scene_changed:
            try:
                save_json(scene_file, scene_data)
                stats["updated_scenes"] += 1
            except Exception as exc:
                logger.error("Failed to save scene file %s: %s", scene_file, exc)
                stats["failed_scenes"] += 1

    created_at = datetime.utcnow().isoformat() + "Z"
    situation_data = _load_episode_situation(situation_file)
    for episode_key, tracked in episode_fact_index.items():
        file_count = int(tracked.get("file_count", 0))
        last_file = tracked.get("last_file")
        error_text = str(tracked.get("error", "")).strip()
        episode_node = _ensure_episode_node(situation_data, episode_key)
        episode_node["fact_entities_file_count"] = file_count
        episode_node["fact_entities_last_file"] = last_file
        episode_node["updated_at"] = created_at

        if error_text:
            episode_node["fact_entities_generated"] = False
            episode_node["fact_entities_generated_at"] = None
            episode_node["fact_entities_extraction_error"] = error_text
        else:
            episode_node["fact_entities_generated"] = True
            episode_node["fact_entities_generated_at"] = created_at
            episode_node["fact_entities_extraction_error"] = ""
        stats["episodes_status_updated"] += 1

    _save_episode_situation(situation_file, situation_data)

    logger.info(
        "Fact entity extraction complete: scanned_scenes=%s updated_scenes=%s failed_scenes=%s "
        "facts_scanned=%s fact_files_written=%s llm_calls=%s episodes_status_updated=%s",
        stats["scanned_scenes"],
        stats["updated_scenes"],
        stats["failed_scenes"],
        stats["facts_scanned"],
        stats["fact_files_written"],
        stats["llm_calls"],
        stats["episodes_status_updated"],
    )
    return stats


def extract_fact_entities(
    memory_core: Any,
    force_update: bool = False,
    use_tqdm: bool = True,
) -> Dict[str, Any]:
    """
    MemoryCore-facing interface for fact-entity extraction.
    """
    workflow_id = str(getattr(memory_core, "workflow_id", "default"))
    llm_func = getattr(memory_core, "llm_func", None)
    if llm_func is None:
        return {"success": False, "error": "memory_core.llm_func is not available"}

    stats = scan_and_extract_fact_entities(
        workflow_id=workflow_id,
        force_update=force_update,
        use_tqdm=use_tqdm,
        llm_model=llm_func,
        prompt_language=getattr(memory_core, "prompt_language", "zh"),
        runtime_prompt_config_path=getattr(memory_core, "runtime_prompt_config_path", None),
    )
    stats["success"] = True
    return stats


def _load_fact_entity_prompt(
    *,
    prompt_language: str,
    runtime_prompt_config_path: str | Path | None,
) -> str:
    config_path = Path(runtime_prompt_config_path or MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH).resolve()
    config = load_resolved_prompt_config(
        config_path,
        language=normalize_prompt_language(prompt_language),
    )
    prompts = config.get("extract_fact_entities")
    if not isinstance(prompts, dict):
        raise ValueError(
            f"`extract_fact_entities` prompt namespace is required in runtime prompt config: {config_path}"
        )
    template = prompts.get("fact_entity_prompt")
    if not isinstance(template, str) or not template.strip():
        raise ValueError(
            f"`extract_fact_entities.fact_entity_prompt` is required in runtime prompt config: {config_path}"
        )
    return template.strip()

