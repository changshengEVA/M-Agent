#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scene fact extraction module.

Scan scene files under one workflow and write back a `facts` field for each
scene. Each fact item stores one extracted atomic fact from source episode
spans referenced by scene.source.episodes.

Fact contract (brief):
- A fact is one self-contained, retrievable claim grounded in dialogue.
- Completeness is coverage-based: every verifiable span in the current chunk
  should be represented by at least one fact (low-information turns may be
  skipped as a group).
- Episodes must define segment boundaries; there is no fallback mechanical
  chunking across the whole episode when segments are missing.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:
    yaml = None
from tqdm import tqdm

from m_agent.config_paths import FACT_EXTRACTION_PROMPT_CONFIG_PATH
from m_agent.paths import memory_workflow_dir
from m_agent.prompt_utils import load_resolved_prompt_config, normalize_prompt_language

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = FACT_EXTRACTION_PROMPT_CONFIG_PATH

_FACT_CHUNK_SIZE = 8
_FACT_CHUNK_OVERLAP = 0

# Segment-internal sub-chunking for fact extraction (turn counts).
_FACT_SEGMENT_SUBCHUNK_THRESHOLD = 5
_FACT_SUBCHUNK_MAX_TURNS = 4

_FACT_OPTIONAL_KEYS = (
    "fact_type",
    "keywords",
    "entities",
    "time_norm",
    "relation",
    "event_tags",
)

_RELATIVE_TIME_EN_PATTERN = re.compile(
    r"\b("
    r"day before yesterday|day after tomorrow|"
    r"yesterday|today|tomorrow|"
    r"last week|next week|"
    r"last month|this month|next month|"
    r"last year|this year|next year"
    r")\b",
    flags=re.IGNORECASE,
)
_RELATIVE_TIME_ZH_PATTERN = re.compile(
    r"(前天|后天|昨天|今天|明天|上周|下周|上个月|本月|下个月|去年|今年|明年)"
)


def _parse_anchor_datetime(anchor_text: str) -> Optional[datetime]:
    value = str(anchor_text or "").strip()
    if not value:
        return None

    candidates = [value]
    if value.endswith("Z"):
        candidates.append(value[:-1] + "+00:00")

    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except Exception:
            continue

    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            continue
    return None


def _shift_month(anchor_dt: datetime, month_delta: int) -> datetime:
    month_index = anchor_dt.month - 1 + month_delta
    year = anchor_dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(anchor_dt.day, monthrange(year, month)[1])
    return anchor_dt.replace(year=year, month=month, day=day)


def _format_abs_day_en(dt: datetime) -> str:
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _format_abs_month_en(dt: datetime) -> str:
    return f"{dt.strftime('%B')} {dt.year}"


def _format_abs_day_zh(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _format_abs_month_zh(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _has_following_time_annotation(text: str, match_end: int) -> bool:
    tail = str(text[match_end:] if match_end < len(text) else "")
    return bool(re.match(r"^\s*[（(][^）)]*\d{4}[^）)]*[）)]", tail))


def _resolve_relative_time_label(label: str, anchor_dt: datetime, *, zh: bool) -> Optional[str]:
    token = str(label or "").strip().lower()
    if not token:
        return None

    if zh:
        if token == "前天":
            return _format_abs_day_zh(anchor_dt - timedelta(days=2))
        if token == "昨天":
            return _format_abs_day_zh(anchor_dt - timedelta(days=1))
        if token == "今天":
            return _format_abs_day_zh(anchor_dt)
        if token == "明天":
            return _format_abs_day_zh(anchor_dt + timedelta(days=1))
        if token == "后天":
            return _format_abs_day_zh(anchor_dt + timedelta(days=2))
        if token == "上周":
            return _format_abs_day_zh(anchor_dt - timedelta(days=7))
        if token == "下周":
            return _format_abs_day_zh(anchor_dt + timedelta(days=7))
        if token == "上个月":
            return _format_abs_month_zh(_shift_month(anchor_dt, -1))
        if token == "本月":
            return _format_abs_month_zh(anchor_dt)
        if token == "下个月":
            return _format_abs_month_zh(_shift_month(anchor_dt, 1))
        if token == "去年":
            return str(anchor_dt.year - 1)
        if token == "今年":
            return str(anchor_dt.year)
        if token == "明年":
            return str(anchor_dt.year + 1)
        return None

    if token == "day before yesterday":
        return _format_abs_day_en(anchor_dt - timedelta(days=2))
    if token == "yesterday":
        return _format_abs_day_en(anchor_dt - timedelta(days=1))
    if token == "today":
        return _format_abs_day_en(anchor_dt)
    if token == "tomorrow":
        return _format_abs_day_en(anchor_dt + timedelta(days=1))
    if token == "day after tomorrow":
        return _format_abs_day_en(anchor_dt + timedelta(days=2))
    if token == "last week":
        return _format_abs_day_en(anchor_dt - timedelta(days=7))
    if token == "next week":
        return _format_abs_day_en(anchor_dt + timedelta(days=7))
    if token == "last month":
        return _format_abs_month_en(_shift_month(anchor_dt, -1))
    if token == "this month":
        return _format_abs_month_en(anchor_dt)
    if token == "next month":
        return _format_abs_month_en(_shift_month(anchor_dt, 1))
    if token == "last year":
        return str(anchor_dt.year - 1)
    if token == "this year":
        return str(anchor_dt.year)
    if token == "next year":
        return str(anchor_dt.year + 1)
    return None


def _annotate_relative_time(text: str, anchor_text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return raw

    anchor_dt = _parse_anchor_datetime(anchor_text)
    if anchor_dt is None:
        return raw

    def _replace_en(match: re.Match[str]) -> str:
        if _has_following_time_annotation(raw, match.end()):
            return match.group(0)
        resolved = _resolve_relative_time_label(match.group(0), anchor_dt, zh=False)
        if not resolved:
            return match.group(0)
        return f"{match.group(0)} ({resolved})"

    annotated = _RELATIVE_TIME_EN_PATTERN.sub(_replace_en, raw)

    def _replace_zh(match: re.Match[str]) -> str:
        if _has_following_time_annotation(annotated, match.end()):
            return match.group(0)
        resolved = _resolve_relative_time_label(match.group(0), anchor_dt, zh=True)
        if not resolved:
            return match.group(0)
        return f"{match.group(0)}（{resolved}）"

    return _RELATIVE_TIME_ZH_PATTERN.sub(_replace_zh, annotated)


def get_memory_root(workflow_id: str) -> Path:
    return memory_workflow_dir(workflow_id)


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


def load_prompts(prompt_language: str = "zh") -> Dict[str, Any]:
    try:
        return load_resolved_prompt_config(
            CONFIG_PATH,
            language=normalize_prompt_language(prompt_language),
        )
    except Exception:
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
    """Format turns for fact-extraction prompts.

    Appends `blip_caption` (when present) so object/setting cues from images
    enter the textual evidence the LLM sees — otherwise facts like
    "clipboard with a notepad" never appear in atomic facts.
    """
    lines: List[str] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        lines.append(
            normalize_line(str(turn.get("speaker", "Unknown")), str(turn.get("text", "")))
        )
        cap = turn.get("blip_caption")
        if isinstance(cap, str) and cap.strip():
            lines.append(f"  [Image: {cap.strip()}]")
    return "\n".join(lines)


def split_turns_into_chunks(
    turns: List[Dict[str, Any]],
    chunk_size: int = _FACT_CHUNK_SIZE,
    overlap: int = _FACT_CHUNK_OVERLAP,
) -> List[List[Dict[str, Any]]]:
    valid_turns = [t for t in turns if isinstance(t, dict)]
    if not valid_turns:
        return []

    safe_chunk_size = max(1, int(chunk_size))
    safe_overlap = max(0, int(overlap))
    if safe_overlap >= safe_chunk_size:
        safe_overlap = safe_chunk_size - 1

    step = max(1, safe_chunk_size - safe_overlap)
    chunks: List[List[Dict[str, Any]]] = []
    start = 0
    total = len(valid_turns)
    while start < total:
        end = min(total, start + safe_chunk_size)
        chunk = valid_turns[start:end]
        if chunk:
            chunks.append(chunk)
        if end >= total:
            break
        start += step
    return chunks


def segment_turns_into_fact_subchunks(
    seg_turns: List[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    """Split one segment's turns for LLM fact extraction.

    If the segment has more than `_FACT_SEGMENT_SUBCHUNK_THRESHOLD` turns,
    use consecutive windows of at most `_FACT_SUBCHUNK_MAX_TURNS` turns;
    otherwise return a single chunk (the whole segment).
    """
    valid = [t for t in seg_turns if isinstance(t, dict)]
    if not valid:
        return []
    n = len(valid)
    if n <= _FACT_SEGMENT_SUBCHUNK_THRESHOLD:
        return [valid]
    out: List[List[Dict[str, Any]]] = []
    i = 0
    while i < n:
        out.append(valid[i : i + _FACT_SUBCHUNK_MAX_TURNS])
        i += _FACT_SUBCHUNK_MAX_TURNS
    return out


def _normalize_chunk_params(
    chunk_size: Optional[int],
    chunk_overlap: Optional[int],
) -> Tuple[int, int]:
    size = _FACT_CHUNK_SIZE if chunk_size is None else int(chunk_size)
    overlap = _FACT_CHUNK_OVERLAP if chunk_overlap is None else int(chunk_overlap)

    size = max(1, size)
    overlap = max(0, overlap)
    if overlap >= size:
        overlap = size - 1
    return size, overlap


def _normalize_optional_fact_fields(raw_item: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw_item, dict):
        return {}

    normalized: Dict[str, Any] = {}
    for key in _FACT_OPTIONAL_KEYS:
        if key not in raw_item:
            continue
        value = raw_item.get(key)
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
    normalized_turns: List[Dict[str, Any]] = [
        dict(turn) for turn in turns if isinstance(turn, dict)
    ]

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
                if not evidence_sentence:
                    evidence_sentence = atomic_fact
                fact_item: Dict[str, Any] = {
                    "Atomic fact": atomic_fact,
                    "evidence_sentence": evidence_sentence,
                }
                fact_item.update(_normalize_optional_fact_fields(item))
                normalized.append(fact_item)
        elif isinstance(item, str) and item.strip():
            s = item.strip()
            normalized.append({"Atomic fact": s, "evidence_sentence": s})
    return normalized


def call_fact_extraction(
    dialogue_block: str,
    episode_payload_text: str,
    start_time: str,
    prompt_template: str,
    llm_model: Optional[Callable[[str], str]] = None,
    *,
    segment_context_block: str = "",
) -> List[Dict[str, Any]]:
    if llm_model is None:
        from m_agent.load_model.OpenAIcall import get_llm

        llm_model = get_llm(model_temperature=0.1)

    seg_ctx = (segment_context_block or "").strip()
    full_prompt = (
        prompt_template.replace("{dialogue_block}", dialogue_block)
        .replace("{episode}", episode_payload_text)
        .replace("{start_time}", start_time)
        .replace("{segment_context}", seg_ctx if seg_ctx else "(无)")
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


def _normalize_dedupe_text(text: Any) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    # Drop punctuation noise so minor formatting differences do not bypass dedupe.
    raw = re.sub(r"[^\w\u4e00-\u9fff]+", " ", raw, flags=re.UNICODE)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def complete_fact_item(
    raw_item: Dict[str, Any],
    source_ep: Dict[str, Any],
    embed_model: Callable[[Any], Any],
    segment_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    evidence_sentence = str(raw_item.get("evidence_sentence", "")).strip()
    atomic_fact = extract_atomic_fact(raw_item)
    if not atomic_fact:
        atomic_fact = evidence_sentence or "unknown_atomic_fact"
    if not evidence_sentence:
        evidence_sentence = atomic_fact
    anchor_start_time = str(source_ep.get("start_time", "")).strip()
    if anchor_start_time:
        atomic_fact = _annotate_relative_time(atomic_fact, anchor_start_time)

    embedding: List[float] = []
    embedding_input = build_atomic_fact_embedding_text(atomic_fact=atomic_fact)
    try:
        vec = embed_model(embedding_input)
        if isinstance(vec, list):
            embedding = vec
    except Exception as exc:
        logger.warning("Embedding generation failed for atomic_fact '%s': %s", embedding_input[:80], exc)

    evidence: Dict[str, Any] = {
        "episode_id": str(source_ep.get("episode_id", "")),
        "dialogue_id": str(source_ep.get("dialogue_id", "")),
    }
    if isinstance(segment_info, dict):
        seg_id = str(segment_info.get("segment_id", "")).strip()
        seg_span = segment_info.get("turn_span")
        if seg_id:
            evidence["segment_id"] = seg_id
        if isinstance(seg_span, list) and len(seg_span) == 2:
            evidence["segment_turn_span"] = seg_span

    output: Dict[str, Any] = {
        "Atomic fact": atomic_fact,
        "evidence_sentence": evidence_sentence,
        "evidence": evidence,
        "embedding": embedding,
    }

    start_time = str(source_ep.get("start_time", "")).strip()
    end_time = str(source_ep.get("end_time", "")).strip()
    if start_time:
        output["start_time"] = start_time
    if end_time:
        output["end_time"] = end_time

    for key, value in _normalize_optional_fact_fields(raw_item).items():
        if key not in output:
            output[key] = value

    return output


def deduplicate_facts(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: List[Dict[str, Any]] = []
    seen_atomic = set()
    seen_evidence = set()
    for item in facts:
        evidence = item.get("evidence", {})
        episode_id = str(evidence.get("episode_id", "")).strip()
        dialogue_id = str(evidence.get("dialogue_id", "")).strip()

        atomic_norm = _normalize_dedupe_text(extract_atomic_fact(item))
        evidence_norm = _normalize_dedupe_text(item.get("evidence_sentence", ""))

        atomic_key = (episode_id, dialogue_id, atomic_norm)
        evidence_key = (episode_id, dialogue_id, evidence_norm)

        if atomic_norm and atomic_key in seen_atomic:
            continue
        if evidence_norm and evidence_key in seen_evidence:
            continue

        if atomic_norm:
            seen_atomic.add(atomic_key)
        if evidence_norm:
            seen_evidence.add(evidence_key)
        unique.append(item)
    return unique


def _resolve_segments_as_chunks(
    source_ep: Dict[str, Any],
    all_episode_turns: List[Dict[str, Any]],
) -> Optional[List[Tuple[List[Dict[str, Any]], Dict[str, Any]]]]:
    """Split episode turns by segment boundaries from source_ep.

    Returns a list of (segment_turns, segment_info) pairs, or None if
    segments are missing or invalid (caller must skip fact extraction).
    """
    segments = source_ep.get("segments")
    if not isinstance(segments, list) or not segments:
        return None

    turn_by_id: Dict[int, Dict[str, Any]] = {}
    for turn in all_episode_turns:
        tid = turn.get("turn_id")
        if isinstance(tid, int):
            turn_by_id[tid] = turn

    result: List[Tuple[List[Dict[str, Any]], Dict[str, Any]]] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_span = seg.get("turn_span")
        if not (isinstance(seg_span, list) and len(seg_span) == 2):
            continue
        try:
            start_id, end_id = int(seg_span[0]), int(seg_span[1])
        except (TypeError, ValueError):
            continue

        seg_turns = [
            turn_by_id[tid]
            for tid in sorted(turn_by_id)
            if start_id <= tid <= end_id
        ]
        if seg_turns:
            result.append((seg_turns, seg))

    return result if result else None


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
    if not turns:
        return []

    episode_payload = build_episode_payload(
        source_ep=source_ep,
        turns=turns,
        dialogue_data=dialogue_data,
    )

    segment_chunks = _resolve_segments_as_chunks(source_ep, turns)
    if not segment_chunks:
        logger.warning(
            "Skipping fact extraction for dialogue_id=%s: missing or empty segments in source_ep",
            dialogue_id,
        )
        return []

    episode_dialogue_block = turns_to_dialogue_block(turns)

    completed: List[Dict[str, Any]] = []
    episode_context = {
        "episode_id": str(episode_payload.get("episode_id", "")),
        "dialogue_id": str(episode_payload.get("dialogue_id", "")),
        "start_time": str(episode_payload.get("start_time", "")),
        "end_time": str(episode_payload.get("end_time", "")),
        "participants": episode_payload.get("participants", []),
    }

    segment_index = 0
    for seg_turns, segment_info in segment_chunks:
        if not seg_turns:
            continue
        segment_index += 1
        segment_dialogue_block = turns_to_dialogue_block(seg_turns)
        subchunks = segment_turns_into_fact_subchunks(seg_turns)
        total_sub = len(subchunks)

        for sub_idx, chunk_turns in enumerate(subchunks, start=1):
            if not chunk_turns:
                continue

            dialogue_block = turns_to_dialogue_block(chunk_turns)
            if not dialogue_block.strip():
                continue

            chunk_turn_span = [
                chunk_turns[0].get("turn_id"),
                chunk_turns[-1].get("turn_id"),
            ]
            chunk_start_time = str(chunk_turns[0].get("timestamp", "")).strip()
            chunk_end_time = str(chunk_turns[-1].get("timestamp", "")).strip()

            segment_label = ""
            if isinstance(segment_info, dict):
                segment_label = str(segment_info.get("topic", "")).strip()

            chunk_payload: Dict[str, Any] = {
                "episode_context": episode_context,
                "segment_dialogue_block": segment_dialogue_block,
                "episode_dialogue_block": episode_dialogue_block,
                "chunk": {
                    "segment_index": segment_index,
                    "subchunk_index": sub_idx,
                    "subchunk_total": total_sub,
                    "turn_span": chunk_turn_span,
                    "start_time": chunk_start_time,
                    "end_time": chunk_end_time,
                },
                "turns": [
                    dict(t)
                    for t in chunk_turns
                    if isinstance(t, dict)
                ],
            }
            if segment_label:
                chunk_payload["chunk"]["segment_topic"] = segment_label

            chunk_payload_text = json.dumps(chunk_payload, ensure_ascii=False, indent=2)
            start_time = chunk_start_time or str(episode_payload.get("start_time", "")).strip()

            logger.info(
                "Fact extract dialogue_id=%s segment=%s subchunk=%s/%s turns=%s-%s",
                dialogue_id,
                segment_index,
                sub_idx,
                total_sub,
                chunk_turn_span[0],
                chunk_turn_span[1],
            )

            try:
                raw_facts = call_fact_extraction(
                    dialogue_block=dialogue_block,
                    episode_payload_text=chunk_payload_text,
                    start_time=start_time,
                    prompt_template=prompt_template,
                    llm_model=llm_model,
                    segment_context_block=segment_dialogue_block,
                )
            except Exception as exc:
                logger.warning(
                    "LLM fact extraction failed for dialogue_id=%s segment=%s subchunk=%s/%s, fallback: %s",
                    dialogue_id,
                    segment_index,
                    sub_idx,
                    total_sub,
                    exc,
                )
                raw_facts = fallback_extract_facts(chunk_turns)

            for item in raw_facts:
                completed.append(
                    complete_fact_item(
                        raw_item=item,
                        source_ep=source_ep,
                        embed_model=embed_model,
                        segment_info=segment_info,
                    )
                )

    if not completed:
        return []

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
    prompt_language: str = "zh",
    embed_model: Optional[Callable[[Any], Any]] = None,
    llm_model: Optional[Callable[[str], str]] = None,
    max_workers: int = 1,
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

    prompts = load_prompts(prompt_language=prompt_language)
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
            from m_agent.load_model.BGEcall import get_embed_model

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

    workers = max(1, int(max_workers))

    def _one_scene(scene_file: Path) -> Tuple[str, int]:
        status, fact_count = process_scene_file(
            scene_file=scene_file,
            dialogues_root=dialogues_root,
            prompt_template=prompt_template,
            force_update=force_update,
            embed_model=embed_model,
            llm_model=llm_model,
        )
        return status, fact_count

    if workers == 1:
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
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(scene_files))) as pool:
            futures = {
                pool.submit(_one_scene, sf): sf for sf in scene_files
            }
            iterator = as_completed(futures)
            if use_tqdm:
                iterator = tqdm(iterator, total=len(futures), desc="Extract scene facts")
            for fut in iterator:
                status, fact_count = fut.result()
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


