#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entity statement import workflow.

Builds entity statements from episode payloads and writes one statement per file
under `data/memory/{workflow_id}/entity_statement`.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from tqdm import tqdm

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    yaml = None

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
KG_PROMPT_PATH = PROJECT_ROOT / "config" / "prompt" / "kg_filter.yaml"
ENTITY_STATEMENT_PROMPT_PATH = PROJECT_ROOT / "config" / "memory_core_config" / "make_entity_statement.yaml"


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("load json failed (%s): %s", path, exc)
    return None


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _is_episode_file_payload(data: Dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    return isinstance(data.get("episodes"), list)


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


def _find_dialogue_file(dialogues_root: Path, dialogue_id: str) -> Optional[Path]:
    dialogue_id = str(dialogue_id or "").strip()
    if not dialogue_id:
        return None

    direct = dialogues_root / f"{dialogue_id}.json"
    if direct.exists():
        return direct

    for file_path in dialogues_root.rglob(f"{dialogue_id}.json"):
        if file_path.is_file():
            return file_path
    return None


def _extract_episode_turns(dialogue_data: Dict[str, Any], turn_span: Any) -> List[Dict[str, Any]]:
    turns = dialogue_data.get("turns", [])
    if not isinstance(turns, list):
        return []

    if (
        isinstance(turn_span, list)
        and len(turn_span) == 2
        and all(isinstance(x, int) for x in turn_span)
    ):
        start_id, end_id = turn_span
        by_turn_id: List[Dict[str, Any]] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            tid = turn.get("turn_id")
            if isinstance(tid, int) and start_id <= tid <= end_id:
                by_turn_id.append(turn)
        if by_turn_id:
            return by_turn_id

        start_idx = max(0, start_id)
        end_idx = min(len(turns) - 1, end_id)
        if start_idx <= end_idx:
            return [t for t in turns[start_idx : end_idx + 1] if isinstance(t, dict)]

    return [t for t in turns if isinstance(t, dict)]


def _extract_episode_time_range(episode_meta: Dict[str, Any], dialogue_data: Dict[str, Any]) -> Tuple[str, str]:
    start_time = ""
    end_time = ""

    turn_span = episode_meta.get("turn_span", [])
    if not isinstance(turn_span, list) or len(turn_span) < 2:
        turn_span = [0, 0]

    try:
        start_id = int(turn_span[0])
        end_id = int(turn_span[1])
    except Exception:
        start_id, end_id = 0, 0

    turns = dialogue_data.get("turns", [])
    if not isinstance(turns, list):
        turns = []

    for turn in turns:
        if not isinstance(turn, dict):
            continue
        turn_id = turn.get("turn_id")
        if turn_id == start_id and not start_time:
            start_time = str(turn.get("timestamp", "") or "")
        if turn_id == end_id and not end_time:
            end_time = str(turn.get("timestamp", "") or "")
        if start_time and end_time:
            break

    if not start_time or not end_time:
        span_turns: List[Dict[str, Any]] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            try:
                turn_id = int(turn.get("turn_id", -1))
            except Exception:
                continue
            if start_id <= turn_id <= end_id:
                span_turns.append(turn)
        if span_turns:
            if not start_time:
                start_time = str(span_turns[0].get("timestamp", "") or "")
            if not end_time:
                end_time = str(span_turns[-1].get("timestamp", "") or "")

    meta = dialogue_data.get("meta", {})
    if not isinstance(meta, dict):
        meta = {}
    if not start_time:
        start_time = str(meta.get("start_time", "") or "")
    if not end_time:
        end_time = str(meta.get("end_time", "") or "")

    return start_time, end_time


def _normalize_dialogue_line(speaker: str, text: str) -> str:
    speaker = (speaker or "Unknown").strip()
    text = (text or "").strip()
    if not text:
        return f"{speaker}:"
    for prefix in (f"{speaker}:",):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    return f"{speaker}: {text}"


def _turns_to_dialogue_block(turns: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for turn in turns:
        speaker = str(turn.get("speaker", "Unknown"))
        text = str(turn.get("text", ""))
        lines.append(_normalize_dialogue_line(speaker, text))
    return "\n".join(lines).strip()


def _extract_json_from_text(text: str) -> Any:
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


def _extract_multiline_block(raw_text: str, key: str, parent_key: Optional[str] = None) -> str:
    lines = raw_text.splitlines()
    parent_active = parent_key is None
    parent_indent = -1

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))

        if parent_key is not None:
            if not parent_active:
                if stripped == f"{parent_key}:":
                    parent_active = True
                    parent_indent = indent
                idx += 1
                continue
            if stripped and indent <= parent_indent and stripped != f"{parent_key}:":
                parent_active = False
                continue

        if stripped == f"{key}: |":
            block_indent = indent + 2
            block: List[str] = []
            idx += 1
            while idx < len(lines):
                current = lines[idx]
                current_stripped = current.strip()
                current_indent = len(current) - len(current.lstrip(" "))
                if current_stripped and current_indent < block_indent:
                    break
                if current.startswith(" " * block_indent):
                    block.append(current[block_indent:])
                elif not current_stripped:
                    block.append("")
                else:
                    block.append(current.lstrip())
                idx += 1
            return "\n".join(block).strip()
        idx += 1

    return ""


def _load_entity_extraction_prompt() -> str:
    raw = KG_PROMPT_PATH.read_text(encoding="utf-8")

    if yaml is not None:
        config = yaml.safe_load(raw) or {}
        if isinstance(config, dict):
            node = config.get("kg_strong_filter_v3")
            if isinstance(node, dict):
                prompt = node.get("entity_extraction")
                if isinstance(prompt, str) and prompt.strip():
                    return prompt

    fallback = _extract_multiline_block(raw, key="entity_extraction", parent_key="kg_strong_filter_v3")
    if fallback:
        return fallback
    raise ValueError("entity extraction prompt not found: kg_strong_filter_v3.entity_extraction")


def _load_entity_statement_prompt() -> str:
    raw = ENTITY_STATEMENT_PROMPT_PATH.read_text(encoding="utf-8")

    if yaml is not None:
        config = yaml.safe_load(raw) or {}
        if isinstance(config, dict):
            prompt = config.get("make_entity_statement")
            if isinstance(prompt, str) and prompt.strip():
                return prompt

    fallback = _extract_multiline_block(raw, key="make_entity_statement")
    if fallback:
        return fallback
    raise ValueError("make_entity_statement prompt not found")


def _extract_entity_ids(parsed_payload: Any) -> List[str]:
    entities_raw: List[Any] = []
    if isinstance(parsed_payload, dict):
        raw_entities = parsed_payload.get("entities")
        if isinstance(raw_entities, list):
            entities_raw = raw_entities
        elif isinstance(raw_entities, dict):
            entities_raw = [raw_entities]
    elif isinstance(parsed_payload, list):
        entities_raw = parsed_payload

    results: List[str] = []
    seen: Set[str] = set()
    for item in entities_raw:
        if isinstance(item, dict):
            entity = str(item.get("id") or item.get("name") or "").strip()
        else:
            entity = str(item or "").strip()

        norm = entity.lower()
        if not entity or norm in seen:
            continue
        seen.add(norm)
        results.append(entity)
    return results


def _extract_statement_texts(parsed_payload: Any) -> List[str]:
    raw_items: List[Any] = []
    if isinstance(parsed_payload, list):
        raw_items = parsed_payload
    elif isinstance(parsed_payload, dict):
        for key in ("statements", "entity_statements", "results"):
            value = parsed_payload.get(key)
            if isinstance(value, list):
                raw_items = value
                break
        if not raw_items and isinstance(parsed_payload.get("statement"), str):
            raw_items = [parsed_payload.get("statement")]

    statements: List[str] = []
    seen: Set[str] = set()
    for item in raw_items:
        if isinstance(item, dict):
            statement = str(
                item.get("statement")
                or item.get("text")
                or item.get("value")
                or ""
            ).strip()
        else:
            statement = str(item or "").strip()
        if not statement:
            continue
        norm = statement.lower()
        if norm in seen:
            continue
        seen.add(norm)
        statements.append(statement)
    return statements


def _call_entity_extraction(
    episode_text: str,
    prompt_template: str,
    llm_func: Any,
) -> List[str]:
    full_prompt = prompt_template.replace("<txt_string>", episode_text)
    response = llm_func(full_prompt)
    parsed = _extract_json_from_text(response)
    return _extract_entity_ids(parsed)


def _call_entity_statement_extraction(
    episode_text: str,
    entity_name: str,
    prompt_template: str,
    llm_func: Any,
) -> List[str]:
    full_prompt = prompt_template.replace("{episode_text}", episode_text)
    full_prompt = full_prompt.replace("{entity_name}", entity_name)
    response = llm_func(full_prompt)
    parsed = _extract_json_from_text(response)
    return _extract_statement_texts(parsed)


def _get_next_statement_number(entity_statement_root: Path) -> int:
    entity_statement_root.mkdir(parents=True, exist_ok=True)
    max_number = 0
    for file_path in entity_statement_root.glob("*.json"):
        if not file_path.is_file():
            continue
        try:
            number = int(file_path.stem)
        except Exception:
            continue
        if number > max_number:
            max_number = number
    return max_number + 1


_RELATIVE_TIME_PATTERNS = [
    r"\byesterday\b",
    r"\btoday\b",
    r"\btomorrow\b",
    r"\btonight\b",
    r"\bthis morning\b",
    r"\bthis afternoon\b",
    r"\bthis evening\b",
    r"\blast (week|month|year|night)\b",
    r"\bnext (week|month|year)\b",
    r"\brecently\b",
    r"\bnow\b",
    r"\bthen\b",
    r"\bearlier\b",
    r"\blater\b",
    r"\bsoon\b",
    r"刚刚",
    r"昨天",
    r"今天",
    r"明天",
    r"前天",
    r"后天",
    r"最近",
    r"上周",
    r"下周",
    r"上个月",
    r"下个月",
    r"去年",
    r"明年",
]

_PRONOUN_PATTERNS = [
    r"\bhe\b",
    r"\bshe\b",
    r"\bthey\b",
    r"\bhim\b",
    r"\bher\b",
    r"\bthem\b",
    r"\bhis\b",
    r"\bhers\b",
    r"\btheir\b",
    r"\bit\b",
    r"\bits\b",
    r"\bthis\b",
    r"\bthat\b",
    r"\bthese\b",
    r"\bthose\b",
    r"\bhere\b",
    r"\bthere\b",
    r"他",
    r"她",
    r"它",
    r"他们",
    r"她们",
    r"它们",
    r"这",
    r"那",
    r"这些",
    r"那些",
    r"这里",
    r"那里",
]

_CONTEXT_DEPENDENT_PATTERNS = [
    r"\bthis conversation\b",
    r"\bthat conversation\b",
    r"\bin this conversation\b",
    r"\bthe photo\b",
    r"\bthis photo\b",
    r"\bthat photo\b",
    r"\bthe picture\b",
    r"\bthis picture\b",
    r"\bthat picture\b",
    r"\babove\b",
    r"\bbelow\b",
    r"这次对话",
    r"这段对话",
    r"这张图",
    r"那张图",
    r"上面",
    r"下面",
]


def _is_context_independent_statement(statement: str, entity_name: str) -> bool:
    text = str(statement or "").strip()
    entity = str(entity_name or "").strip()
    if not text or not entity:
        return False

    if not text.lower().startswith(entity.lower()):
        return False

    if entity.lower() not in text.lower():
        return False

    for pattern in _RELATIVE_TIME_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return False
    for pattern in _PRONOUN_PATTERNS:
        # Allow the entity name itself; everything else uses strict block.
        if re.search(pattern, text, flags=re.IGNORECASE):
            return False
    for pattern in _CONTEXT_DEPENDENT_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return False
    return True


def make_entity_statement(
    path: Path,
    memory_core: Any,
    use_tqdm: bool = True,
    force_update: bool = False,
) -> Dict[str, Any]:
    """
    Import from episodes and generate entity statements.

    Args:
        path: episodes root path or one episodes json file.
        memory_core: MemoryCore instance.
        use_tqdm: show progress bar.
        force_update: when False, skip episodes whose
            `entity_statement_generated` is already True in
            `episodes/episode_situation.json`.
    """
    logger.info("Start make_entity_statement on path: %s", path)

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

    dialogues_root = Path(getattr(memory_core, "dialogues_dir"))
    entity_statement_root = Path(getattr(memory_core, "entity_statement_dir"))
    llm_func = getattr(memory_core, "llm_func", None)
    workflow_id = str(getattr(memory_core, "workflow_id", "test"))
    if llm_func is None:
        return {"success": False, "error": "memory_core.llm_func is not available"}

    try:
        from memory.build_memory.episode_status_manager import get_status_manager

        status_manager = get_status_manager(workflow_id=workflow_id)
    except Exception as exc:
        return {"success": False, "error": f"init episode status manager failed: {exc}"}

    try:
        entity_extraction_prompt = _load_entity_extraction_prompt()
        entity_statement_prompt = _load_entity_statement_prompt()
    except Exception as exc:
        return {"success": False, "error": f"load prompt failed: {exc}"}

    results: Dict[str, Any] = {
        "success": True,
        "path": str(path),
        "input_type": "episodes",
        "force_update": bool(force_update),
        "status_source": "episode_situation.json",
        "total_files": len(episode_files),
        "files_processed": 0,
        "files_failed": 0,
        "episodes_processed": 0,
        "episodes_failed": 0,
        "episodes_skipped_existing": 0,
        "episodes_without_entities": 0,
        "episodes_status_updated": 0,
        "total_entities_detected": 0,
        "total_statements_generated": 0,
        "total_duplicates_skipped": 0,
        "total_context_filtered": 0,
        "statement_root": str(entity_statement_root),
        "episode_status_file": str(status_manager.situation_file),
        "file_results": [],
    }

    next_number = _get_next_statement_number(entity_statement_root)
    run_dedup_keys: Set[Tuple[str, str, str, str]] = set()

    file_iter = tqdm(episode_files, desc="Make entity statements") if use_tqdm else episode_files
    for episode_file in file_iter:
        file_result: Dict[str, Any] = {
            "file": episode_file.name,
            "success": True,
            "episodes_processed": 0,
            "episodes_failed": 0,
            "episodes_skipped_existing": 0,
            "entities_detected": 0,
            "statements_generated": 0,
            "duplicates_skipped": 0,
            "context_filtered": 0,
            "errors": [],
        }

        payload = _load_json(episode_file)
        if payload is None:
            file_result["success"] = False
            file_result["errors"].append("json load failed")
            results["files_failed"] += 1
            results["file_results"].append(file_result)
            continue

        if not _is_episode_file_payload(payload):
            file_result["success"] = False
            file_result["errors"].append("not an episodes payload")
            results["files_failed"] += 1
            results["file_results"].append(file_result)
            continue

        payload_dialogue_id = str(payload.get("dialogue_id", "")).strip()
        cached_dialogue_id = ""
        cached_dialogue_data: Optional[Dict[str, Any]] = None

        episodes = payload.get("episodes", [])
        for episode_meta in episodes:
            if not isinstance(episode_meta, dict):
                file_result["episodes_failed"] += 1
                results["episodes_failed"] += 1
                file_result["errors"].append("invalid episode item")
                continue

            episode_id = str(episode_meta.get("episode_id", "")).strip()
            dialogue_id = str(episode_meta.get("dialogue_id") or payload_dialogue_id).strip()
            if not dialogue_id or not episode_id:
                file_result["episodes_failed"] += 1
                results["episodes_failed"] += 1
                file_result["errors"].append(f"episode={episode_id or 'unknown'} missing dialogue_id/episode_id")
                continue

            episode_key = f"{dialogue_id}:{episode_id}"
            existing_episode_status = status_manager.get_episode(episode_key) or {}
            if not force_update and bool(existing_episode_status.get("entity_statement_generated", False)):
                file_result["episodes_skipped_existing"] += 1
                results["episodes_skipped_existing"] += 1
                continue

            if dialogue_id != cached_dialogue_id:
                dialogue_file = _find_dialogue_file(dialogues_root, dialogue_id)
                if dialogue_file is None:
                    file_result["episodes_failed"] += 1
                    results["episodes_failed"] += 1
                    file_result["errors"].append(
                        f"episode={episode_id or 'unknown'} dialogue file not found ({dialogue_id})"
                    )
                    status_manager.update_episode(
                        episode_key,
                        {
                            "entity_statement_generated": False,
                            "entity_statement_generation_error": "dialogue file not found",
                        },
                    )
                    results["episodes_status_updated"] += 1
                    continue

                cached_dialogue_data = _load_json(dialogue_file)
                cached_dialogue_id = dialogue_id
                if cached_dialogue_data is None:
                    file_result["episodes_failed"] += 1
                    results["episodes_failed"] += 1
                    file_result["errors"].append(
                        f"episode={episode_id or 'unknown'} dialogue load failed ({dialogue_file.name})"
                    )
                    status_manager.update_episode(
                        episode_key,
                        {
                            "entity_statement_generated": False,
                            "entity_statement_generation_error": "dialogue load failed",
                        },
                    )
                    results["episodes_status_updated"] += 1
                    continue

            if not isinstance(cached_dialogue_data, dict):
                file_result["episodes_failed"] += 1
                results["episodes_failed"] += 1
                file_result["errors"].append(f"episode={episode_id or 'unknown'} dialogue data unavailable")
                status_manager.update_episode(
                    episode_key,
                    {
                        "entity_statement_generated": False,
                        "entity_statement_generation_error": "dialogue data unavailable",
                    },
                )
                results["episodes_status_updated"] += 1
                continue

            turns = _extract_episode_turns(cached_dialogue_data, episode_meta.get("turn_span"))
            episode_text = _turns_to_dialogue_block(turns)
            if not episode_text:
                file_result["episodes_failed"] += 1
                results["episodes_failed"] += 1
                file_result["errors"].append(f"episode={episode_id or 'unknown'} empty episode_text")
                status_manager.update_episode(
                    episode_key,
                    {
                        "entity_statement_generated": False,
                        "entity_statement_generation_error": "empty episode_text",
                    },
                )
                results["episodes_status_updated"] += 1
                continue

            try:
                entities = _call_entity_extraction(
                    episode_text=episode_text,
                    prompt_template=entity_extraction_prompt,
                    llm_func=llm_func,
                )
            except Exception as exc:
                file_result["episodes_failed"] += 1
                results["episodes_failed"] += 1
                error_text = f"entity extraction failed: {exc}"
                file_result["errors"].append(f"episode={episode_id or 'unknown'} {error_text}")
                status_manager.update_episode(
                    episode_key,
                    {
                        "entity_statement_generated": False,
                        "entity_statement_generation_error": error_text,
                    },
                )
                results["episodes_status_updated"] += 1
                continue

            file_result["episodes_processed"] += 1
            results["episodes_processed"] += 1
            file_result["entities_detected"] += len(entities)
            results["total_entities_detected"] += len(entities)

            start_time, end_time = _extract_episode_time_range(episode_meta, cached_dialogue_data)
            episode_time = {"start_time": start_time, "end_time": end_time}
            created_at = datetime.utcnow().isoformat() + "Z"

            written_files_for_episode: List[str] = []
            episode_context_filtered = 0
            if not entities:
                results["episodes_without_entities"] += 1
            else:
                for entity_name in entities:
                    try:
                        statements = _call_entity_statement_extraction(
                            episode_text=episode_text,
                            entity_name=entity_name,
                            prompt_template=entity_statement_prompt,
                            llm_func=llm_func,
                        )
                    except Exception as exc:
                        file_result["errors"].append(
                            f"episode={episode_id or 'unknown'} entity={entity_name} statement extraction failed: {exc}"
                        )
                        continue

                    for statement in statements:
                        dedup_key = (
                            entity_name.strip().lower(),
                            statement.strip().lower(),
                            episode_id,
                            dialogue_id,
                        )
                        if dedup_key in run_dedup_keys:
                            file_result["duplicates_skipped"] += 1
                            results["total_duplicates_skipped"] += 1
                            continue

                        if not _is_context_independent_statement(statement, entity_name):
                            episode_context_filtered += 1
                            file_result["context_filtered"] += 1
                            results["total_context_filtered"] += 1
                            continue

                        run_dedup_keys.add(dedup_key)
                        statement_id = f"{next_number:08d}"
                        next_number += 1

                        statement_payload: Dict[str, Any] = {
                            "id": statement_id,
                            "entity": entity_name,
                            "statement": statement,
                            "source": {
                                "episode_id": episode_id,
                                "dialogue_id": dialogue_id,
                            },
                            "time": episode_time,
                            "created_at": created_at,
                        }

                        output_path = entity_statement_root / f"{statement_id}.json"
                        _save_json(output_path, statement_payload)
                        written_files_for_episode.append(output_path.name)

                        file_result["statements_generated"] += 1
                        results["total_statements_generated"] += 1

            if hasattr(status_manager, "mark_entity_statement_generated"):
                status_manager.mark_entity_statement_generated(
                    episode_key=episode_key,
                    file_count=len(written_files_for_episode),
                    last_file=written_files_for_episode[-1] if written_files_for_episode else None,
                    context_filtered=episode_context_filtered,
                    created_at=created_at,
                )
            else:
                status_manager.update_episode(
                    episode_key,
                    {
                        "entity_statement_generated": True,
                        "entity_statement_generated_at": created_at,
                        "entity_statement_file_count": len(written_files_for_episode),
                        "entity_statement_last_file": (
                            written_files_for_episode[-1] if written_files_for_episode else None
                        ),
                        "entity_statement_context_filtered": episode_context_filtered,
                        "entity_statement_generation_error": "",
                    },
                )
            results["episodes_status_updated"] += 1

        if file_result["episodes_processed"] == 0 and file_result["episodes_failed"] > 0:
            file_result["success"] = False
            results["files_failed"] += 1
        else:
            results["files_processed"] += 1

        results["file_results"].append(file_result)

    if results["files_processed"] == 0 and results["files_failed"] > 0:
        results["success"] = False
        results["error"] = "all episode files failed during entity statement generation"

    return results
