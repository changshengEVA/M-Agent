#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fact entity import workflow.

Responsibilities:
1. Scan `data/memory/{workflow_id}/facts/*.json`.
2. Ensure each fact has `entity_UID` (bound to `main_entity`).
3. Import minimal entity info into KG (`UID`, `name`).
4. Write `facts_situation.json` under workflow root to track import status.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tqdm import tqdm

from m_agent.paths import memory_workflow_dir

logger = logging.getLogger(__name__)


def get_memory_root(workflow_id: str) -> Path:
    return memory_workflow_dir(workflow_id)


def get_facts_root(memory_root: Path) -> Path:
    return memory_root / "facts"


def get_scene_root(memory_root: Path) -> Path:
    return memory_root / "scene"


def get_facts_situation_file(memory_root: Path) -> Path:
    return memory_root / "facts_situation.json"


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be a dict: {path}")
    return data


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_name_key(name: str) -> str:
    return _normalize_text(name).lower()


def _sorted_fact_files(facts_root: Path) -> List[Path]:
    if not facts_root.exists():
        return []

    def _sort_key(path: Path) -> Tuple[Any, ...]:
        stem = path.stem
        left, right = stem, "0"
        if "_" in stem:
            left, right = stem.rsplit("_", 1)
        try:
            return (0, int(left), int(right))
        except Exception:
            return (1, stem)

    return sorted([p for p in facts_root.glob("*.json") if p.is_file()], key=_sort_key)


def _load_facts_situation(path: Path, workflow_id: str) -> Dict[str, Any]:
    if path.exists():
        try:
            loaded = load_json(path)
            if isinstance(loaded, dict):
                loaded.setdefault("workflow_id", workflow_id)
                if not isinstance(loaded.get("summary"), dict):
                    loaded["summary"] = {}
                if not isinstance(loaded.get("entities"), list):
                    loaded["entities"] = []
                if not isinstance(loaded.get("facts"), dict):
                    loaded["facts"] = {}
                if not isinstance(loaded.get("metadata"), dict):
                    loaded["metadata"] = {}
                return loaded
        except Exception as exc:
            logger.warning("Load facts_situation failed (%s): %s", path, exc)

    return {
        "workflow_id": workflow_id,
        "summary": {},
        "entities": [],
        "facts": {},
        "metadata": {},
    }


def _seed_entity_uid_map(existing_situation: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    name_to_uid: Dict[str, str] = {}
    uid_to_name: Dict[str, str] = {}

    entities = existing_situation.get("entities", [])
    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            uid = _normalize_text(entity.get("UID"))
            name = _normalize_text(entity.get("name"))
            if not uid or not name:
                continue
            key = _normalize_name_key(name)
            if key and key not in name_to_uid:
                name_to_uid[key] = uid
            uid_to_name.setdefault(uid, name)

    facts = existing_situation.get("facts", {})
    if isinstance(facts, dict):
        for fact_node in facts.values():
            if not isinstance(fact_node, dict):
                continue
            uid = _normalize_text(fact_node.get("entity_UID"))
            name = _normalize_text(fact_node.get("main_entity"))
            if not uid or not name:
                continue
            key = _normalize_name_key(name)
            if key and key not in name_to_uid:
                name_to_uid[key] = uid
            uid_to_name.setdefault(uid, name)

    return name_to_uid, uid_to_name


def _resolve_scene_fact_slot(fact_file: Path, payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    # Preferred mapping from fact_id: "{scene_stem}_{index:04d}"
    fact_id = _normalize_text(payload.get("fact_id"))
    if "_" in fact_id:
        scene_stem, idx_text = fact_id.rsplit("_", 1)
        if idx_text.isdigit():
            return scene_stem, int(idx_text) - 1

    # Fallback mapping from file stem.
    stem = fact_file.stem
    if "_" in stem:
        scene_stem, idx_text = stem.rsplit("_", 1)
        if idx_text.isdigit():
            return scene_stem, int(idx_text) - 1

    return None, None


def scan_and_import_fact_entities(
    workflow_id: str,
    memory_core: Optional[Any] = None,
    force_update: bool = False,
    use_tqdm: bool = True,
) -> Dict[str, Any]:
    memory_root = get_memory_root(workflow_id)
    facts_root = get_facts_root(memory_root)
    scene_root = get_scene_root(memory_root)
    facts_situation_file = get_facts_situation_file(memory_root)

    stats: Dict[str, Any] = {
        "workflow_id": workflow_id,
        "success": False,
        "facts_root": str(facts_root),
        "scene_root": str(scene_root),
        "facts_situation_file": str(facts_situation_file),
        "facts_scanned": 0,
        "facts_updated": 0,
        "facts_failed": 0,
        "facts_with_main_entity": 0,
        "facts_without_main_entity": 0,
        "facts_entity_imported": 0,
        "scene_files_updated": 0,
        "scene_files_failed": 0,
        "entities_total": 0,
        "kg_available": False,
        "kg_entities_created": 0,
        "kg_entities_updated": 0,
        "kg_entities_failed": 0,
    }

    if not memory_root.exists():
        stats["error"] = f"memory root not found: {memory_root}"
        return stats
    if not facts_root.exists():
        stats["error"] = f"facts root not found: {facts_root}"
        return stats

    existing_situation = _load_facts_situation(facts_situation_file, workflow_id)
    name_to_uid, uid_to_name = _seed_entity_uid_map(existing_situation)

    fact_status: Dict[str, Dict[str, Any]] = {}
    scene_cache: Dict[str, Dict[str, Any]] = {}
    dirty_scene_files: Dict[str, Path] = {}

    fact_files = _sorted_fact_files(facts_root)
    if not fact_files:
        created_at = datetime.utcnow().isoformat() + "Z"
        empty_payload = {
            "workflow_id": workflow_id,
            "summary": {
                "facts_scanned": 0,
                "facts_entity_imported": 0,
                "entities_total": 0,
            },
            "entities": [],
            "facts": {},
            "metadata": {
                "last_updated": created_at,
                "source": "import_fact_entities",
            },
        }
        save_json(facts_situation_file, empty_payload)
        stats["success"] = True
        return stats

    file_iter = tqdm(fact_files, desc="Import fact entities") if use_tqdm else fact_files
    for fact_file in file_iter:
        stats["facts_scanned"] += 1
        fact_id = fact_file.stem
        try:
            payload = load_json(fact_file)
        except Exception as exc:
            stats["facts_failed"] += 1
            fact_status[fact_id] = {
                "fact_file": fact_file.name,
                "main_entity": "",
                "entity_UID": "",
                "entity_imported": False,
                "kg_imported": False,
                "error": str(exc),
            }
            continue

        main_entity = _normalize_text(payload.get("main_entity"))
        previous_uid = _normalize_text(payload.get("entity_UID"))
        assigned_uid = ""
        entity_imported = False

        if main_entity:
            stats["facts_with_main_entity"] += 1
            name_key = _normalize_name_key(main_entity)

            if not force_update and previous_uid:
                if name_key not in name_to_uid:
                    name_to_uid[name_key] = previous_uid
                assigned_uid = name_to_uid.get(name_key, previous_uid)
            else:
                assigned_uid = name_to_uid.get(name_key, "")
                if not assigned_uid:
                    assigned_uid = previous_uid or str(uuid.uuid4())
                    name_to_uid[name_key] = assigned_uid

            uid_to_name[assigned_uid] = main_entity
            entity_imported = bool(assigned_uid)
            if entity_imported:
                stats["facts_entity_imported"] += 1
        else:
            stats["facts_without_main_entity"] += 1

        payload_changed = ("entity_UID" not in payload) or (_normalize_text(payload.get("entity_UID")) != assigned_uid)
        if payload_changed:
            payload["entity_UID"] = assigned_uid
            try:
                save_json(fact_file, payload)
                stats["facts_updated"] += 1
            except Exception as exc:
                stats["facts_failed"] += 1
                fact_status[fact_id] = {
                    "fact_file": fact_file.name,
                    "main_entity": main_entity,
                    "entity_UID": assigned_uid,
                    "entity_imported": False,
                    "kg_imported": False,
                    "error": str(exc),
                }
                continue

        scene_stem, fact_index = _resolve_scene_fact_slot(fact_file, payload)
        if scene_stem is not None and fact_index is not None:
            scene_file = scene_root / f"{scene_stem}.json"
            scene_key = str(scene_file)
            if scene_file.exists():
                if scene_key not in scene_cache:
                    try:
                        scene_cache[scene_key] = load_json(scene_file)
                    except Exception as exc:
                        stats["scene_files_failed"] += 1
                        logger.warning("Load scene file failed (%s): %s", scene_file, exc)
                        scene_cache[scene_key] = {}

                scene_data = scene_cache.get(scene_key, {})
                facts = scene_data.get("facts", []) if isinstance(scene_data, dict) else []
                if isinstance(facts, list) and 0 <= fact_index < len(facts):
                    slot = facts[fact_index]
                    if isinstance(slot, dict):
                        current_uid = _normalize_text(slot.get("entity_UID"))
                        if current_uid != assigned_uid:
                            slot["entity_UID"] = assigned_uid
                            dirty_scene_files[scene_key] = scene_file

        fact_status[fact_id] = {
            "fact_file": fact_file.name,
            "main_entity": main_entity,
            "entity_UID": assigned_uid,
            "entity_imported": entity_imported,
            "kg_imported": False,
            "error": "",
        }

    for scene_key, scene_file in dirty_scene_files.items():
        scene_payload = scene_cache.get(scene_key)
        if not isinstance(scene_payload, dict):
            stats["scene_files_failed"] += 1
            continue
        try:
            save_json(scene_file, scene_payload)
            stats["scene_files_updated"] += 1
        except Exception as exc:
            stats["scene_files_failed"] += 1
            logger.warning("Save scene file failed (%s): %s", scene_file, exc)

    kg_base = getattr(memory_core, "kg_base", None) if memory_core is not None else None
    stats["kg_available"] = bool(getattr(kg_base, "store", None) and getattr(kg_base.store, "available", False))
    kg_uid_status: Dict[str, bool] = {}

    for uid, name in uid_to_name.items():
        uid = _normalize_text(uid)
        name = _normalize_text(name)
        if not uid or not name:
            continue

        if kg_base is None:
            kg_uid_status[uid] = False
            continue

        source_info = {
            "workflow_id": workflow_id,
            "source": "fact_entity_import",
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

        try:
            if hasattr(kg_base, "upsert_entity"):
                result = kg_base.upsert_entity(
                    entity_id=uid,
                    entity_name=name,
                    entity_uid=uid,
                    entity_type="fact_entity",
                    source_info=source_info,
                )
            else:
                result = kg_base.add_entity(
                    entity_id=uid,
                    entity_type="fact_entity",
                    source_info=source_info,
                )

            success = bool(result.get("success"))
            changed = bool(result.get("changed"))
            op = str(result.get("details", {}).get("operation", ""))
            kg_uid_status[uid] = success

            if not success:
                stats["kg_entities_failed"] += 1
                continue
            if changed and op == "upsert_entity":
                action = str(result.get("details", {}).get("action", ""))
                if action == "created":
                    stats["kg_entities_created"] += 1
                else:
                    stats["kg_entities_updated"] += 1
            elif changed:
                stats["kg_entities_created"] += 1
        except Exception as exc:
            logger.warning("KG import entity failed (uid=%s, name=%s): %s", uid, name, exc)
            stats["kg_entities_failed"] += 1
            kg_uid_status[uid] = False

    for fact_id, node in fact_status.items():
        uid = _normalize_text(node.get("entity_UID"))
        node["kg_imported"] = bool(uid and kg_uid_status.get(uid, False))
        fact_status[fact_id] = node

    created_at = datetime.utcnow().isoformat() + "Z"
    entities_payload = [
        {"UID": uid, "name": name}
        for uid, name in sorted(uid_to_name.items(), key=lambda x: (_normalize_name_key(x[1]), x[0]))
        if _normalize_text(uid) and _normalize_text(name)
    ]

    stats["entities_total"] = len(entities_payload)
    facts_situation_payload = {
        "workflow_id": workflow_id,
        "summary": {
            "facts_scanned": stats["facts_scanned"],
            "facts_updated": stats["facts_updated"],
            "facts_failed": stats["facts_failed"],
            "facts_with_main_entity": stats["facts_with_main_entity"],
            "facts_without_main_entity": stats["facts_without_main_entity"],
            "facts_entity_imported": stats["facts_entity_imported"],
            "entities_total": stats["entities_total"],
            "kg_available": stats["kg_available"],
            "kg_entities_created": stats["kg_entities_created"],
            "kg_entities_updated": stats["kg_entities_updated"],
            "kg_entities_failed": stats["kg_entities_failed"],
            "scene_files_updated": stats["scene_files_updated"],
            "scene_files_failed": stats["scene_files_failed"],
        },
        "entities": entities_payload,
        "facts": fact_status,
        "metadata": {
            "last_updated": created_at,
            "source": "import_fact_entities",
        },
    }
    save_json(facts_situation_file, facts_situation_payload)

    # If KG is disabled, local import is still considered successful.
    # If KG is enabled, require kg import to have no failures.
    kg_ok = (not stats["kg_available"]) or (stats["kg_entities_failed"] == 0)
    stats["success"] = stats["facts_failed"] == 0 and stats["scene_files_failed"] == 0 and kg_ok
    return stats


def import_fact_entities(
    memory_core: Any,
    force_update: bool = False,
    use_tqdm: bool = True,
) -> Dict[str, Any]:
    """
    MemoryCore-facing interface for fact-entity import.
    """
    workflow_id = str(getattr(memory_core, "workflow_id", "default"))
    return scan_and_import_fact_entities(
        workflow_id=workflow_id,
        memory_core=memory_core,
        force_update=force_update,
        use_tqdm=use_tqdm,
    )
