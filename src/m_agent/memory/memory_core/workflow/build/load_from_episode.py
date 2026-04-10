#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Episode import workflow.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tqdm import tqdm

logger = logging.getLogger(__name__)


def _emit_progress(
    progress_callback: Optional[Callable[[str, Dict[str, Any]], None]],
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(event_type, payload)
    except Exception:
        logger.exception("Episode import progress callback failed for event_type=%s", event_type)


def _is_episode_file_payload(data: Dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    episodes = data.get("episodes")
    return isinstance(episodes, list)


def _collect_episode_files(path: Path) -> List[Path]:
    if path.is_file() and path.suffix.lower() == ".json":
        return [path]

    if not path.exists() or not path.is_dir():
        return []

    by_dialogue = path / "by_dialogue"
    if by_dialogue.exists() and by_dialogue.is_dir():
        files = sorted(by_dialogue.rglob("episodes_*.json"))
        if files:
            return files

    return sorted(path.rglob("*.json"))


def _infer_episodes_root(path: Path, episode_files: List[Path]) -> Path:
    """
    Infer episodes root for scene/fact builders.
    Builders expect a directory containing `by_dialogue/`.
    """
    if path.is_dir():
        return path

    probe_files = episode_files or ([path] if path.is_file() else [])
    for file_path in probe_files:
        if not file_path.exists():
            continue
        for ancestor in [file_path.parent, *file_path.parents]:
            if ancestor.name == "by_dialogue":
                parent = ancestor.parent
                if parent.exists() and parent.is_dir():
                    return parent
                break

    if path.is_file():
        return path.parent
    return path


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("Load json failed (%s): %s", path, exc)
    return None


def load_from_episode_path(
    path: Path,
    memory_core: Any,
    use_tqdm: bool = True,
    progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    Import from episodes path and trigger Scene/Atomic-facts generation.
    """
    logger.info("Start importing episode path: %s", path)

    if not path.exists():
        return {"success": False, "error": f"path not found: {path}"}

    episode_files = _collect_episode_files(path)
    if not episode_files:
        return {
            "success": False,
            "error": f"no episode json files found under: {path}",
            "files_processed": 0,
            "files_failed": 0,
        }

    results: Dict[str, Any] = {
        "success": True,
        "path": str(path),
        "input_type": "episodes",
        "total_files": len(episode_files),
        "files_processed": 0,
        "files_failed": 0,
        "episodes_processed": 0,
        "total_entities_processed": 0,
        "total_features_processed": 0,
        "total_attributes_processed": 0,
        "total_relations_processed": 0,
        "file_results": [],
        "resolution_applied": False,
    }

    _emit_progress(
        progress_callback,
        "flush_stage",
        {
            "stage": "import_episodes",
            "stage_label": "Import episodes",
            "status": "started",
            "total_files": len(episode_files),
        },
    )
    file_iter = tqdm(episode_files, desc="Import episodes") if use_tqdm else episode_files
    for episode_file in file_iter:
        payload = _load_json(episode_file)
        if payload is None:
            results["files_failed"] += 1
            results["file_results"].append(
                {
                    "file": episode_file.name,
                    "success": False,
                    "error": "json load failed",
                }
            )
            continue

        if not _is_episode_file_payload(payload):
            results["file_results"].append(
                {
                    "file": episode_file.name,
                    "success": False,
                    "error": "not an episodes payload",
                }
            )
            results["files_failed"] += 1
            continue

        episodes_in_file = len(payload.get("episodes", []))
        results["files_processed"] += 1
        results["episodes_processed"] += episodes_in_file
        results["file_results"].append(
            {
                "file": episode_file.name,
                "success": True,
                "episodes_processed": episodes_in_file,
            }
        )

    _emit_progress(
        progress_callback,
        "flush_stage",
        {
            "stage": "import_episodes",
            "stage_label": "Import episodes",
            "status": "completed",
            "total_files": len(episode_files),
            "files_processed": results["files_processed"],
            "files_failed": results["files_failed"],
            "episodes_processed": results["episodes_processed"],
        },
    )

    try:
        episodes_root_for_build = _infer_episodes_root(path, episode_files)
        dialogues_root = memory_core.dialogues_dir
        scene_root = memory_core.scene_dir
        scene_prompt_version = str(getattr(memory_core, "scene_prompt_version", "v2"))
        fact_prompt_version = str(getattr(memory_core, "fact_prompt_version", "v2"))
        memory_owner_name = str(getattr(memory_core, "memory_owner_name", "changshengEVA"))
        prompt_language = str(getattr(memory_core, "prompt_language", "zh"))
        facts_only_mode = bool(getattr(memory_core, "facts_only_mode", False))

        # system-owned behavior: import flow controls force_update internally
        force_update = False

        from m_agent.memory.build_memory.form_scene import scan_and_form_scenes
        from m_agent.memory.build_memory.form_scene_details import scan_and_form_scene_facts

        if not episodes_root_for_build.exists():
            build_result = {
                "success": False,
                "error": f"Episodes path not found: {episodes_root_for_build}",
                "scene_file_count": 0,
                "fact_stats": {},
            }
            _emit_progress(
                progress_callback,
                "flush_stage",
                {
                    "stage": "generate_scenes",
                    "stage_label": "Generate scenes",
                    "status": "failed",
                    "error": build_result["error"],
                },
            )
        else:
            _emit_progress(
                progress_callback,
                "flush_stage",
                {
                    "stage": "generate_scenes",
                    "stage_label": "Generate scenes",
                    "status": "started",
                },
            )
            scan_and_form_scenes(
                use_tqdm=True,
                force_update=force_update,
                prompt_version=scene_prompt_version,
                dialogues_root=dialogues_root,
                episodes_root=episodes_root_for_build,
                scene_root=scene_root,
                memory_owner_name=memory_owner_name,
                prompt_language=prompt_language,
                embed_model=memory_core.embed_func,
                llm_model=memory_core.llm_func,
            )
            scene_file_count = len([p for p in scene_root.glob("*.json") if p.is_file()])
            _emit_progress(
                progress_callback,
                "flush_stage",
                {
                    "stage": "generate_scenes",
                    "stage_label": "Generate scenes",
                    "status": "completed",
                    "scene_file_count": scene_file_count,
                },
            )

            _emit_progress(
                progress_callback,
                "flush_stage",
                {
                    "stage": "extract_scene_facts",
                    "stage_label": "Extract scene facts",
                    "status": "started",
                },
            )
            fact_stats = scan_and_form_scene_facts(
                workflow_id=memory_core.workflow_id,
                prompt_version=fact_prompt_version,
                force_update=force_update,
                use_tqdm=True,
                prompt_language=prompt_language,
                embed_model=memory_core.embed_func,
                llm_model=memory_core.llm_func,
            )
            _emit_progress(
                progress_callback,
                "flush_stage",
                {
                    "stage": "extract_scene_facts",
                    "stage_label": "Extract scene facts",
                    "status": "completed",
                    "result": fact_stats,
                },
            )

            if facts_only_mode:
                _emit_progress(
                    progress_callback,
                    "flush_stage",
                    {
                        "stage": "extract_fact_entities",
                        "stage_label": "Extract fact entities",
                        "status": "skipped",
                        "reason": "facts_only_mode=true",
                    },
                )
                _emit_progress(
                    progress_callback,
                    "flush_stage",
                    {
                        "stage": "import_fact_entities",
                        "stage_label": "Import fact entities",
                        "status": "skipped",
                        "reason": "facts_only_mode=true",
                    },
                )
                build_result = {
                    "success": True,
                    "scene_file_count": scene_file_count,
                    "fact_stats": fact_stats,
                    "fact_entity_stats": {
                        "success": True,
                        "skipped": True,
                        "reason": "facts_only_mode=true",
                    },
                    "fact_import_stats": {
                        "success": True,
                        "skipped": True,
                        "reason": "facts_only_mode=true",
                    },
                    "episodes_root": str(episodes_root_for_build),
                    "scene_root": str(scene_root),
                    "facts_root": str(getattr(memory_core, "facts_dir", memory_core.memory_root / "facts")),
                    "facts_situation_file": str(
                        getattr(memory_core, "facts_situation_file", memory_core.memory_root / "facts_situation.json")
                    ),
                    "scene_prompt_version": scene_prompt_version,
                    "fact_prompt_version": fact_prompt_version,
                    "memory_owner_name": memory_owner_name,
                    "prompt_language": prompt_language,
                    "force_update": force_update,
                    "facts_only_mode": True,
                }
            else:
                _emit_progress(
                    progress_callback,
                    "flush_stage",
                    {
                        "stage": "extract_fact_entities",
                        "stage_label": "Extract fact entities",
                        "status": "started",
                    },
                )
                if hasattr(memory_core, "extract_fact_entities"):
                    fact_entity_stats = memory_core.extract_fact_entities(
                        force_update=force_update,
                        use_tqdm=True,
                    )
                else:
                    from m_agent.memory.memory_core.workflow.build.extract_fact_entities import (
                        extract_fact_entities as workflow_extract_fact_entities,
                    )

                    fact_entity_stats = workflow_extract_fact_entities(
                        memory_core=memory_core,
                        force_update=force_update,
                        use_tqdm=True,
                    )
                _emit_progress(
                    progress_callback,
                    "flush_stage",
                    {
                        "stage": "extract_fact_entities",
                        "stage_label": "Extract fact entities",
                        "status": "completed",
                        "result": fact_entity_stats,
                    },
                )

                _emit_progress(
                    progress_callback,
                    "flush_stage",
                    {
                        "stage": "import_fact_entities",
                        "stage_label": "Import fact entities",
                        "status": "started",
                    },
                )
                if hasattr(memory_core, "import_fact_entities"):
                    fact_import_stats = memory_core.import_fact_entities(
                        force_update=force_update,
                        use_tqdm=True,
                        progress_callback=progress_callback,
                    )
                else:
                    from m_agent.memory.memory_core.workflow.build.import_fact_entities import (
                        import_fact_entities as workflow_import_fact_entities,
                    )

                    fact_import_stats = workflow_import_fact_entities(
                        memory_core=memory_core,
                        force_update=force_update,
                        use_tqdm=True,
                    )
                _emit_progress(
                    progress_callback,
                    "flush_stage",
                    {
                        "stage": "import_fact_entities",
                        "stage_label": "Import fact entities",
                        "status": "completed",
                        "result": fact_import_stats,
                    },
                )

                build_result = {
                    "success": bool(fact_import_stats.get("success", True)),
                    "scene_file_count": scene_file_count,
                    "fact_stats": fact_stats,
                    "fact_entity_stats": fact_entity_stats,
                    "fact_import_stats": fact_import_stats,
                    "episodes_root": str(episodes_root_for_build),
                    "scene_root": str(scene_root),
                    "facts_root": str(getattr(memory_core, "facts_dir", memory_core.memory_root / "facts")),
                    "facts_situation_file": str(
                        getattr(memory_core, "facts_situation_file", memory_core.memory_root / "facts_situation.json")
                    ),
                    "scene_prompt_version": scene_prompt_version,
                    "fact_prompt_version": fact_prompt_version,
                    "memory_owner_name": memory_owner_name,
                    "prompt_language": prompt_language,
                    "force_update": force_update,
                    "facts_only_mode": False,
                }
                if not build_result["success"]:
                    build_result["error"] = "fact entity import failed"

        results["scene_build_result"] = build_result
        if not build_result.get("success", False):
            results["success"] = False
            results["error"] = f"scene/fact generation failed: {build_result}"
    except Exception as exc:
        logger.error("Scene/fact generation failed: %s", exc)
        _emit_progress(
            progress_callback,
            "flush_stage",
            {
                "stage": "memory_pipeline",
                "stage_label": "Memory pipeline",
                "status": "failed",
                "error": str(exc),
            },
        )
        results["success"] = False
        results["error"] = str(exc)

    results["resolution_note"] = (
        "Resolution pass skipped: KG candidate generation is disabled, current mode keeps 0 entities/relations."
    )
    return results

