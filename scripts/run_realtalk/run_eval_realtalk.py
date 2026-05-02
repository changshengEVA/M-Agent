#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import shutil
import string
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

try:
    from ._bootstrap import bootstrap_project
except ImportError:
    from _bootstrap import bootstrap_project


bootstrap_project()

from m_agent.paths import LOG_DIR, resolve_project_path
from m_agent.utils.api_error_utils import is_network_api_error, is_network_error_text

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

try:
    from nltk.stem import PorterStemmer

    _stemmer = PorterStemmer()
except Exception:
    _stemmer = None


logger = logging.getLogger("run_eval_realtalk")

DEFAULT_TEST_ID = "default"
PREDICTION_FILE_NAME = "realtalk_agent_qa.json"
STATS_FILE_NAME = "realtalk_agent_qa_stats.json"
RUN_LOG_FILE_NAME = "realtalk_agent_qa_run.log"
TRACE_FILE_NAME = "realtalk_agent_qa_trace.jsonl"
SKIPPED_QA_CATEGORIES = {5}
EVAL_CATEGORY_ORDER = [4, 1, 2, 3]
REALTALK_SOURCE_QA_INDEX_KEY = "_realtalk_source_qa_index"
EVIDENCE_DIALOG_REF_PATTERN = re.compile(r"D\d+:\d+")
EPISODE_REF_PATTERN = re.compile(r"dlg_[A-Za-z0-9._-]+:ep_[A-Za-z0-9._-]+")
SESSION_KEY_PATTERN = re.compile(r"^session_(\d+)$")
CHAT_FILE_PATTERN = re.compile(r"^Chat_(\d+)_")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate MemoryAgent on REALTALK QA.")
    parser.add_argument(
        "--data-file",
        type=str,
        default="data/REALTALK/data",
        help="REALTALK data directory or single Chat_*.json.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/agents/memory/locomo_eval_memory_agent_full_mode_E2.yaml",
        help="MemoryAgent config yaml path.",
    )
    parser.add_argument("--test-id", type=str, default=DEFAULT_TEST_ID)
    parser.add_argument("--model-key", type=str, default="memory_agent")
    parser.add_argument("--prediction-key", type=str, default="memory_agent_prediction")
    parser.add_argument("--thread-id-prefix", type=str, default="realtalk-eval")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--sample-ids",
        type=str,
        default="",
        help="Comma-separated sample_ids, e.g. realtalk-chat-1-s1,realtalk-chat-2-s3",
    )
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--save-every", type=int, default=1)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument(
        "--workflow-id",
        type=str,
        default="",
        help="Override MemoryCore workflow_id (must match import --process-id).",
    )
    parser.add_argument("--recall-dir", type=str, default="recall")
    return parser.parse_args()


def _sanitize_test_id(test_id: str) -> str:
    cleaned = str(test_id).strip()
    if not cleaned:
        return DEFAULT_TEST_ID
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned)
    cleaned = cleaned.strip("._-")
    return cleaned or DEFAULT_TEST_ID


def _sanitize_filename(name: str) -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        return "unknown"
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned.strip("._-") or "unknown"


def _sanitize_recall_dir_segment(name: str) -> str:
    s = str(name or "").strip() or "recall"
    s = s.replace("\\", "/").split("/")[-1]
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._-") or "recall"
    return s[:120]


def _load_yaml(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_sample_ids(raw_value: str) -> List[str]:
    values: List[str] = []
    seen = set()
    for token in str(raw_value or "").split(","):
        value = token.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _extract_dialogue_refs_from_evidence(value: Any) -> List[str]:
    refs: List[str] = []

    def _collect(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, str):
            refs.extend(EVIDENCE_DIALOG_REF_PATTERN.findall(item))
            return
        if isinstance(item, (list, tuple, set)):
            for child in item:
                _collect(child)
            return
        refs.extend(EVIDENCE_DIALOG_REF_PATTERN.findall(str(item)))

    _collect(value)
    seen = set()
    out: List[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
    return out


def _extract_episode_refs(value: Any) -> List[str]:
    refs: List[str] = []

    def _collect(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, str):
            refs.extend(EPISODE_REF_PATTERN.findall(item))
            return
        if isinstance(item, (list, tuple, set)):
            for child in item:
                _collect(child)
            return
        refs.extend(EPISODE_REF_PATTERN.findall(str(item)))

    _collect(value)
    seen = set()
    out: List[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
    return out


def _parse_session_from_dialogue_ref(dialogue_ref: str) -> str | None:
    match = re.fullmatch(r"D(\d+):\d+", str(dialogue_ref or "").strip())
    if not match:
        return None
    return match.group(1)


def _parse_session_from_dialogue_id(dialogue_id: str) -> int | None:
    match = re.search(r"_(\d+)$", str(dialogue_id or "").strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _parse_episode_ref(ref: str) -> Tuple[str | None, str | None]:
    text = str(ref or "").strip()
    if not text or ":" not in text:
        return None, None
    dialogue_id, episode_id = text.rsplit(":", 1)
    dialogue_id = dialogue_id.strip()
    episode_id = episode_id.strip()
    if not dialogue_id or not episode_id:
        return None, None
    return dialogue_id, episode_id


def _extract_chat_no(path: Path) -> str:
    matched = CHAT_FILE_PATTERN.match(path.name)
    return matched.group(1) if matched else "unknown"


def _normalize_session_messages(messages: List[Any]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        payload = {
            "speaker": msg.get("speaker", ""),
            "text": msg.get("clean_text", msg.get("text", "")),
            "img_file": msg.get("img_file", []),
            "img_url": msg.get("img_url", []),
            "dia_id": msg.get("dia_id", ""),
        }
        if "blip_caption" in msg:
            payload["blip_caption"] = msg.get("blip_caption")
        normalized.append(payload)
    return normalized


def load_realtalk_eval_samples(data_source_path: str) -> List[Dict[str, Any]]:
    source = Path(data_source_path)
    if source.is_dir():
        files = sorted(source.glob("Chat_*.json"))
    elif source.is_file():
        files = [source]
    else:
        raise FileNotFoundError(f"REALTALK data source not found: {source}")

    samples: List[Dict[str, Any]] = []
    for file_path in files:
        raw = _load_json(str(file_path))
        if not isinstance(raw, dict):
            continue
        chat_no = _extract_chat_no(file_path)
        conversation: Dict[str, List[Dict[str, Any]]] = {}
        for key, value in raw.items():
            matched = SESSION_KEY_PATTERN.match(str(key))
            if not matched or not isinstance(value, list):
                continue
            session_num = int(matched.group(1))
            conversation[f"session_{session_num}"] = _normalize_session_messages(value)

        if not conversation:
            continue

        qa_list = raw.get("qa", [])
        if not isinstance(qa_list, list):
            qa_list = []

        normalized_qas: List[Dict[str, Any]] = []
        for qa_idx, qa in enumerate(qa_list):
            if not isinstance(qa, dict):
                continue
            qa_copy = dict(qa)
            qa_copy.setdefault(REALTALK_SOURCE_QA_INDEX_KEY, qa_idx)
            normalized_qas.append(qa_copy)

        sample = {
            "sample_id": f"realtalk-chat-{chat_no}",
            "qa": normalized_qas,
            "conversation": conversation,
        }
        samples.append(sample)
    return samples


def _build_output_paths(test_id: str) -> Dict[str, str]:
    out_dir = LOG_DIR / _sanitize_test_id(test_id)
    return {
        "out_file": str(out_dir / PREDICTION_FILE_NAME),
        "stats_file": str(out_dir / STATS_FILE_NAME),
        "log_file": str(out_dir / RUN_LOG_FILE_NAME),
        "trace_file": str(out_dir / TRACE_FILE_NAME),
    }


def setup_logging(log_file: str) -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    stream_h = logging.StreamHandler()
    stream_h.setFormatter(fmt)
    logger.addHandler(stream_h)
    file_h = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_h.setFormatter(fmt)
    logger.addHandler(file_h)


def is_qa_processed(qa: Dict[str, Any], prediction_key: str) -> bool:
    error_text = qa.get(prediction_key + "_error")
    if error_text is not None and is_network_error_text(error_text):
        return False
    if prediction_key in qa:
        return True
    derived_keys = (
        prediction_key + "_error",
        prediction_key + "_answer",
        prediction_key + "_gold_answer",
        prediction_key + "_evidence",
        prediction_key + "_evidence_episode_refs",
    )
    return any(key in qa for key in derived_keys)


def should_evaluate_qa(qa: Dict[str, Any]) -> bool:
    try:
        category = int(qa.get("category", -1))
    except Exception:
        category = -1
    return category not in SKIPPED_QA_CATEGORIES


def append_trace(trace_fp, record: Dict[str, Any]) -> None:
    trace_fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    trace_fp.flush()


def _apply_trace_record_to_qa(qa: Dict[str, Any], record: Dict[str, Any], prediction_key: str) -> bool:
    applied = False
    if "prediction" in record:
        qa[prediction_key] = str(record.get("prediction", "") or "")
        applied = True
    if record.get("prediction_answer") is not None:
        qa[prediction_key + "_answer"] = str(record.get("prediction_answer", "") or "")
        applied = True
    if record.get("prediction_gold_answer") is not None:
        qa[prediction_key + "_gold_answer"] = str(record.get("prediction_gold_answer", "") or "")
        applied = True
    if record.get("prediction_evidence") is not None:
        qa[prediction_key + "_evidence"] = record.get("prediction_evidence")
        applied = True
    episode_refs = record.get("prediction_evidence_episode_refs")
    if episode_refs is not None:
        qa[prediction_key + "_evidence_episode_refs"] = episode_refs
        applied = True
    if record.get("prediction_tool_calls") is not None:
        qa[prediction_key + "_tool_calls"] = record.get("prediction_tool_calls")
        applied = True
    if record.get("prediction_plan") is not None:
        qa[prediction_key + "_plan"] = record.get("prediction_plan")
        applied = True
    error_text = record.get("error")
    if error_text is not None and str(error_text).strip():
        qa[prediction_key + "_error"] = str(error_text)
        applied = True
    return applied


def recover_from_trace(trace_path: str, out_map: Dict[str, Dict[str, Any]], prediction_key: str) -> Tuple[int, int]:
    if not os.path.exists(trace_path):
        return 0, 0
    recovered_qas = set()
    skipped_lines = 0
    with open(trace_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                skipped_lines += 1
                continue
            if not isinstance(record, dict):
                skipped_lines += 1
                continue
            if record.get("prediction_key") and record.get("prediction_key") != prediction_key:
                continue
            sid = str(record.get("sample_id"))
            try:
                q_idx = int(record.get("qa_index"))
            except Exception:
                skipped_lines += 1
                continue
            out_sample = out_map.get(sid)
            if not out_sample:
                continue
            qas = out_sample.get("qa", [])
            if not isinstance(qas, list) or q_idx < 0 or q_idx >= len(qas):
                continue
            qa = qas[q_idx]
            if not isinstance(qa, dict) or is_qa_processed(qa, prediction_key):
                continue
            if _apply_trace_record_to_qa(qa, record, prediction_key):
                recovered_qas.add((sid, q_idx))
    return len(recovered_qas), skipped_lines


def _write_outputs(out_path: str, source_order: List[Dict[str, Any]], out_map: Dict[str, Dict[str, Any]]) -> None:
    ordered = []
    for sample in source_order:
        sid = str(sample.get("sample_id"))
        if sid in out_map:
            ordered.append(out_map[sid])
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)


def _write_recall_artifact(
    recall_root: Path,
    trace_id: str,
    thread_id: str,
    question: str,
    result: Optional[Dict[str, Any]],
    error: Optional[str],
) -> str:
    safe = _sanitize_filename(trace_id)
    recall_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "question_id": trace_id,
        "trace_id": trace_id,
        "thread_id": thread_id,
        "question": question,
        "result": result,
        "error": error,
    }
    path = recall_root / f"{safe}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    return str(path.resolve())


def _resolve_episodes_root_from_agent_config(agent_config_path: str, workflow_id_override: str = "") -> Path | None:
    try:
        agent_cfg = _load_yaml(agent_config_path)
    except Exception as exc:
        logger.warning("Failed to load MemoryAgent config (%s): %s", agent_config_path, exc)
        return None
    if not isinstance(agent_cfg, dict):
        return None
    memory_core_path_raw = str(agent_cfg.get("memory_core_config_path", "") or "").strip()
    if not memory_core_path_raw:
        return None
    memory_core_path = Path(memory_core_path_raw)
    if not memory_core_path.is_absolute():
        memory_core_path = (Path(agent_config_path).resolve().parent / memory_core_path).resolve()
    if not memory_core_path.exists():
        return None
    try:
        memory_core_cfg = _load_yaml(str(memory_core_path))
    except Exception:
        return None
    if not isinstance(memory_core_cfg, dict):
        return None
    workflow_id = str(workflow_id_override or "").strip() or str(memory_core_cfg.get("workflow_id", "") or "").strip()
    if not workflow_id:
        return None
    episodes_root = Path(resolve_project_path(f"data/memory/{workflow_id}/episodes"))
    if not episodes_root.exists():
        return None
    return episodes_root


def normalize_answer(text: str) -> str:
    text = str(text).replace(",", "")

    def remove_articles(x: str) -> str:
        return re.sub(r"\b(a|an|the|and)\b", " ", x, flags=re.IGNORECASE)

    def remove_punc(x: str) -> str:
        exclude = set(string.punctuation)
        return "".join(ch for ch in x if ch not in exclude)

    def white_space_fix(x: str) -> str:
        return " ".join(x.split())

    return white_space_fix(remove_articles(remove_punc(text.lower())))


def _stem(word: str) -> str:
    if _stemmer is None:
        return word
    return _stemmer.stem(word)


def f1_score_single(prediction: str, ground_truth: str) -> float:
    pred_tokens = [_stem(w) for w in normalize_answer(prediction).split()]
    gt_tokens = [_stem(w) for w in normalize_answer(ground_truth).split()]
    if not pred_tokens or not gt_tokens:
        return 0.0
    common = Counter(pred_tokens) & Counter(gt_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gt_tokens)
    return (2 * precision * recall) / (precision + recall)


def f1_score_multi(prediction: str, ground_truth: str) -> float:
    predictions = [p.strip() for p in str(prediction).split(",") if p.strip()]
    ground_truths = [g.strip() for g in str(ground_truth).split(",") if g.strip()]
    if not ground_truths:
        return 0.0
    if not predictions:
        predictions = [""]
    return sum(max(f1_score_single(pred, gt) for pred in predictions) for gt in ground_truths) / len(ground_truths)


def b1_score_single(prediction: str, ground_truth: str) -> float:
    pred_tokens = normalize_answer(prediction).split()
    gt_tokens = normalize_answer(ground_truth).split()
    if not pred_tokens or not gt_tokens:
        return 0.0
    pred_counts = Counter(pred_tokens)
    gt_counts = Counter(gt_tokens)
    clipped = sum(min(count, gt_counts[token]) for token, count in pred_counts.items())
    precision = clipped / len(pred_tokens)
    if precision <= 0:
        return 0.0
    cand_len = len(pred_tokens)
    ref_len = len(gt_tokens)
    bp = 1.0 if cand_len > ref_len else math.exp(1.0 - (ref_len / cand_len))
    return bp * precision


def b1_score_multi(prediction: str, ground_truth: str) -> float:
    predictions = [p.strip() for p in str(prediction).split(",") if p.strip()]
    ground_truths = [g.strip() for g in str(ground_truth).split(",") if g.strip()]
    if not ground_truths:
        return 0.0
    if not predictions:
        predictions = [""]
    return sum(max(b1_score_single(pred, gt) for pred in predictions) for gt in ground_truths) / len(ground_truths)


def _load_dialogue_episode_turn_spans(
    dialogue_id: str,
    episodes_root: Path | None,
    dialogue_episode_spans_cache: Dict[str, Dict[str, Tuple[int, int]]],
) -> Dict[str, Tuple[int, int]]:
    if dialogue_id in dialogue_episode_spans_cache:
        return dialogue_episode_spans_cache[dialogue_id]
    spans: Dict[str, Tuple[int, int]] = {}
    if episodes_root is None:
        dialogue_episode_spans_cache[dialogue_id] = spans
        return spans
    episode_file = episodes_root / "by_dialogue" / dialogue_id / "episodes_v1.json"
    if not episode_file.exists():
        dialogue_episode_spans_cache[dialogue_id] = spans
        return spans
    try:
        payload = _load_json(str(episode_file))
    except Exception:
        dialogue_episode_spans_cache[dialogue_id] = spans
        return spans
    episodes = payload.get("episodes", []) if isinstance(payload, dict) else []
    if isinstance(episodes, list):
        for episode in episodes:
            if not isinstance(episode, dict):
                continue
            episode_id = str(episode.get("episode_id", "") or "").strip()
            turn_span = episode.get("turn_span")
            if not episode_id or not isinstance(turn_span, list) or len(turn_span) != 2:
                continue
            try:
                start = int(turn_span[0])
                end = int(turn_span[1])
            except Exception:
                continue
            if end < start:
                start, end = end, start
            spans[episode_id] = (start, end)
    dialogue_episode_spans_cache[dialogue_id] = spans
    return spans


def _episode_ref_to_dialogue_refs(
    episode_ref: str,
    episodes_root: Path | None,
    episode_ref_cache: Dict[str, List[str]],
    dialogue_episode_spans_cache: Dict[str, Dict[str, Tuple[int, int]]],
) -> List[str]:
    cached = episode_ref_cache.get(episode_ref)
    if cached is not None:
        return cached
    dialogue_id, episode_id = _parse_episode_ref(episode_ref)
    if not dialogue_id or not episode_id:
        episode_ref_cache[episode_ref] = []
        return []
    session_num = _parse_session_from_dialogue_id(dialogue_id)
    spans = _load_dialogue_episode_turn_spans(
        dialogue_id=dialogue_id,
        episodes_root=episodes_root,
        dialogue_episode_spans_cache=dialogue_episode_spans_cache,
    )
    turn_span = spans.get(episode_id)
    resolved: List[str] = []
    if turn_span and session_num is not None:
        start, end = turn_span
        resolved = [f"D{session_num}:{dia_id}" for dia_id in range(start + 1, end + 2)]
    if not resolved and session_num is not None:
        resolved = [f"S{session_num}"]
    episode_ref_cache[episode_ref] = resolved
    return resolved


def _get_prediction_episode_refs(qa: Dict[str, Any], prediction_key: str) -> List[str]:
    refs = _extract_episode_refs(qa.get(prediction_key + "_evidence_episode_refs"))
    if refs:
        return refs
    refs = _extract_episode_refs(qa.get(prediction_key + "_evidence"))
    if refs:
        return refs
    return _extract_episode_refs(qa.get(prediction_key + "_answer"))


def _compute_recall_from_episode_refs(
    qa: Dict[str, Any],
    prediction_key: str,
    evidence_refs: List[str],
    episodes_root: Path | None,
    episode_ref_cache: Dict[str, List[str]],
    dialogue_episode_spans_cache: Dict[str, Dict[str, Tuple[int, int]]],
) -> float | None:
    if not evidence_refs:
        return 1.0
    episode_refs = _get_prediction_episode_refs(qa, prediction_key)
    if not episode_refs:
        return None
    pred_dialogue_refs: set[str] = set()
    pred_session_refs: set[str] = set()
    for episode_ref in episode_refs:
        resolved_refs = _episode_ref_to_dialogue_refs(
            episode_ref=episode_ref,
            episodes_root=episodes_root,
            episode_ref_cache=episode_ref_cache,
            dialogue_episode_spans_cache=dialogue_episode_spans_cache,
        )
        for ref in resolved_refs:
            if ref.startswith("D"):
                pred_dialogue_refs.add(ref)
            elif ref.startswith("S"):
                pred_session_refs.add(ref)

    hit_count = 0
    for gt_ref in evidence_refs:
        if gt_ref in pred_dialogue_refs:
            hit_count += 1
            continue
        session_id = _parse_session_from_dialogue_ref(gt_ref)
        if session_id and f"S{session_id}" in pred_session_refs:
            hit_count += 1
    return float(hit_count) / len(evidence_refs)


def _compute_recall_from_context(qa: Dict[str, Any], prediction_key: str, evidence_refs: List[str]) -> float | None:
    if not evidence_refs:
        return 1.0
    context_key = prediction_key + "_context"
    if context_key not in qa:
        return None
    ctx = qa.get(context_key, [])
    if not isinstance(ctx, list) or not ctx:
        return 0.0
    ctx_values = [str(item).strip() for item in ctx if str(item).strip()]
    if not ctx_values:
        return 0.0
    if ctx_values[0].startswith("S"):
        sessions = {value[1:] for value in ctx_values if value.startswith("S") and len(value) > 1}
        hit_count = 0
        for gt_ref in evidence_refs:
            session_id = _parse_session_from_dialogue_ref(gt_ref)
            if session_id and session_id in sessions:
                hit_count += 1
        return float(hit_count) / len(evidence_refs)
    ctx_set = set(ctx_values)
    return float(sum(gt_ref in ctx_set for gt_ref in evidence_refs)) / len(evidence_refs)


def eval_question_answering_realtalk(
    qas: List[Dict[str, Any]],
    prediction_key: str,
    episodes_root: Path | None = None,
    *,
    episode_ref_cache: Dict[str, List[str]] | None = None,
    dialogue_episode_spans_cache: Dict[str, Dict[str, Tuple[int, int]]] | None = None,
) -> Tuple[List[float], List[float], List[float]]:
    all_scores: List[float] = []
    all_b1_scores: List[float] = []
    all_recall: List[float] = []
    if episode_ref_cache is None:
        episode_ref_cache = {}
    if dialogue_episode_spans_cache is None:
        dialogue_episode_spans_cache = {}

    for qa in qas:
        if not should_evaluate_qa(qa):
            all_scores.append(0.0)
            all_b1_scores.append(0.0)
            all_recall.append(1.0)
            continue
        category = int(qa.get("category", -1))
        output = str(qa.get(prediction_key, "") or "")
        answer = str(qa.get("answer", "") or "")
        if category == 3:
            answer = answer.split(";")[0].strip()
        if category in (2, 3, 4):
            score = f1_score_single(output, answer)
            b1 = b1_score_single(output, answer)
        elif category == 1:
            score = f1_score_multi(output, answer)
            b1 = b1_score_multi(output, answer)
        else:
            score = 0.0
            b1 = 0.0
        all_scores.append(score)
        all_b1_scores.append(b1)

        evidence_refs = _extract_dialogue_refs_from_evidence(qa.get("evidence", []))
        recall_acc = _compute_recall_from_episode_refs(
            qa=qa,
            prediction_key=prediction_key,
            evidence_refs=evidence_refs,
            episodes_root=episodes_root,
            episode_ref_cache=episode_ref_cache,
            dialogue_episode_spans_cache=dialogue_episode_spans_cache,
        )
        if recall_acc is None:
            recall_acc = _compute_recall_from_context(qa=qa, prediction_key=prediction_key, evidence_refs=evidence_refs)
        if recall_acc is None:
            recall_acc = 1.0 if not evidence_refs else 0.0
        all_recall.append(recall_acc)
    return all_scores, all_b1_scores, all_recall


def summarize_metric_by_category(out_samples: List[Dict[str, Any]], metric_key: str) -> Dict[str, Any]:
    total_counts = defaultdict(float)
    metric_sums = defaultdict(float)
    for sample in out_samples:
        for qa in sample.get("qa", []):
            if not isinstance(qa, dict) or not should_evaluate_qa(qa):
                continue
            try:
                category = int(qa.get("category", -1))
            except Exception:
                category = -1
            total_counts[category] += 1
            metric_sums[category] += float(qa.get(metric_key, 0.0))

    summary_by_cat = {}
    total_q = 0.0
    total_score = 0.0
    for cat in EVAL_CATEGORY_ORDER:
        count = total_counts[cat]
        score_sum = metric_sums[cat]
        acc = (score_sum / count) if count else 0.0
        summary_by_cat[str(cat)] = {
            "count": int(count),
            "score_sum": round(score_sum, 6),
            "accuracy": round(acc, 6),
        }
        total_q += count
        total_score += score_sum
    overall = (total_score / total_q) if total_q else 0.0
    return {"summary_by_category": summary_by_cat, "overall": round(overall, 6)}


def main() -> int:
    args = parse_args()
    data_file = str(resolve_project_path(args.data_file))
    config_path = str(resolve_project_path(args.config))
    output_paths = _build_output_paths(args.test_id)
    out_file = output_paths["out_file"]
    stats_file = output_paths["stats_file"]
    log_file = output_paths["log_file"]
    trace_file = output_paths["trace_file"]
    recall_root = LOG_DIR / _sanitize_test_id(args.test_id) / _sanitize_recall_dir_segment(args.recall_dir)
    f1_metric_key = f"{args.model_key}_f1"
    b1_metric_key = f"{args.model_key}_b1"
    recall_metric_key = f"{args.model_key}_recall"

    setup_logging(log_file)
    logger.info("Start REALTALK QA evaluation")
    logger.info("out_file=%s", out_file)
    logger.info("stats_file=%s", stats_file)
    logger.info("trace_file=%s", trace_file)
    logger.info("recall_root=%s", recall_root)
    if args.overwrite and recall_root.exists():
        shutil.rmtree(recall_root, ignore_errors=True)

    episodes_root = _resolve_episodes_root_from_agent_config(config_path, workflow_id_override=str(args.workflow_id or "").strip())
    if episodes_root is not None:
        logger.info("episodes_root=%s", episodes_root)
    else:
        logger.warning("episodes_root unavailable; recall falls back to coarse/session matching.")

    samples = load_realtalk_eval_samples(data_file)
    if not samples:
        raise ValueError(f"No REALTALK samples loaded from {data_file}")

    selected_sample_ids = _parse_sample_ids(args.sample_ids)
    if selected_sample_ids:
        target = set(selected_sample_ids)
        samples = [sample for sample in samples if str(sample.get("sample_id")) in target]
        logger.info("sample-id filter enabled: selected %d sample(s)", len(samples))
        if not samples:
            raise ValueError("No REALTALK samples left after --sample-ids filter.")

    existing_map: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(out_file) and not args.overwrite:
        loaded = _load_json(out_file)
        if isinstance(loaded, list):
            existing_map = {str(s.get("sample_id")): s for s in loaded if isinstance(s, dict)}

    out_map: Dict[str, Dict[str, Any]] = {}
    for sample in samples:
        sid = str(sample.get("sample_id"))
        out_sample = {"sample_id": sid, "qa": json.loads(json.dumps(sample.get("qa", [])))}
        if sid in existing_map and isinstance(existing_map[sid], dict):
            existing_qas = existing_map[sid].get("qa", [])
            if isinstance(existing_qas, list):
                for i, qa in enumerate(out_sample["qa"]):
                    if i < len(existing_qas) and isinstance(qa, dict) and isinstance(existing_qas[i], dict):
                        qa.update(existing_qas[i])
        out_map[sid] = out_sample

    recovered_count, skipped_trace_lines = (0, 0)
    if not args.overwrite:
        recovered_count, skipped_trace_lines = recover_from_trace(trace_file, out_map, args.prediction_key)
        if recovered_count:
            logger.info("Recovered %d QA results from trace file.", recovered_count)
        if skipped_trace_lines:
            logger.warning("Skipped %d malformed trace lines.", skipped_trace_lines)

    pending_questions = 0
    for sample in samples:
        sid = str(sample.get("sample_id"))
        qas = out_map.get(sid, {}).get("qa", [])
        if not isinstance(qas, list):
            continue
        for qa in qas:
            if not isinstance(qa, dict) or not should_evaluate_qa(qa):
                continue
            if (not args.overwrite) and is_qa_processed(qa, args.prediction_key):
                continue
            pending_questions += 1
            if args.max_questions and pending_questions >= args.max_questions:
                pending_questions = args.max_questions
                break
        if args.max_questions and pending_questions >= args.max_questions:
            break
    logger.info("Pending questions to run: %d", pending_questions)

    trace_mode = "w" if args.overwrite else "a"
    Path(trace_file).parent.mkdir(parents=True, exist_ok=True)
    trace_fp = open(trace_file, trace_mode, encoding="utf-8")

    progress = tqdm(total=pending_questions, desc="Evaluating QA", unit="q") if (tqdm is not None) else None

    agent = None
    if pending_questions > 0:
        from m_agent.agents.memory_agent import create_memory_agent

        agent = create_memory_agent(config_path, memory_workflow_id=(str(args.workflow_id or "").strip() or None))

    asked_count = 0
    processed_samples = 0
    changed = False
    fatal_error: Exception | None = None
    stop = False
    try:
        for sample in samples:
            sid = str(sample.get("sample_id"))
            qas = out_map[sid].get("qa", [])
            if not isinstance(qas, list):
                continue

            for q_idx, qa in enumerate(qas):
                if not isinstance(qa, dict) or not should_evaluate_qa(qa):
                    continue
                if args.max_questions and asked_count >= args.max_questions:
                    stop = True
                    break
                if (not args.overwrite) and is_qa_processed(qa, args.prediction_key):
                    continue

                question = str(qa.get("question", "") or "").strip()
                source_q_idx = qa.get(REALTALK_SOURCE_QA_INDEX_KEY, q_idx)
                try:
                    source_q_idx = int(source_q_idx)
                except Exception:
                    source_q_idx = q_idx
                thread_id = f"{args.thread_id_prefix}:{sid}:{source_q_idx}"
                trace_id = f"{sid}__q{source_q_idx}"
                error_text = None
                ask_result: Dict[str, Any] | None = None

                if not question:
                    error_text = "empty_question"
                    qa[args.prediction_key] = ""
                    qa[args.prediction_key + "_error"] = error_text
                    changed = True
                else:
                    try:
                        if agent is None:
                            raise RuntimeError("MemoryAgent is not initialized for pending question.")
                        ask_result = agent.ask(question, thread_id=thread_id)
                        answer = str(ask_result.get("answer", "") or "")
                        pred = str(ask_result.get("gold_answer", "") or "")
                        evidence = ask_result.get("evidence")
                        tool_calls = ask_result.get("tool_calls", [])
                        question_plan = ask_result.get("question_plan")
                        evidence_episode_refs = _extract_episode_refs(ask_result.get("evidence_episode_refs"))
                        qa[args.prediction_key] = pred
                        qa[args.prediction_key + "_answer"] = answer
                        qa[args.prediction_key + "_gold_answer"] = pred
                        qa[args.prediction_key + "_evidence"] = evidence
                        qa[args.prediction_key + "_evidence_episode_refs"] = evidence_episode_refs
                        qa[args.prediction_key + "_tool_calls"] = tool_calls if isinstance(tool_calls, list) else []
                        qa[args.prediction_key + "_plan"] = question_plan if isinstance(question_plan, dict) else None
                        qa.pop(args.prediction_key + "_error", None)
                    except Exception as exc:
                        error_text = str(exc)
                        if is_network_api_error(exc):
                            for suffix in ("", "_answer", "_gold_answer", "_evidence", "_evidence_episode_refs"):
                                qa.pop(args.prediction_key + suffix, None)
                            qa[args.prediction_key + "_error"] = error_text
                            fatal_error = exc
                            stop = True
                            logger.exception(
                                "Detected network/API error at sample_id=%s qa_index=%s thread_id=%s; stopping.",
                                sid,
                                q_idx,
                                thread_id,
                            )
                        else:
                            qa[args.prediction_key] = ""
                            qa[args.prediction_key + "_error"] = error_text
                    changed = True

                recall_json_path = _write_recall_artifact(recall_root, trace_id, thread_id, question, ask_result, error_text)
                trace_record = {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "prediction_key": args.prediction_key,
                    "trace_id": trace_id,
                    "recall_json": recall_json_path,
                    "sample_id": sid,
                    "qa_index": q_idx,
                    "source_qa_index": source_q_idx,
                    "category": qa.get("category"),
                    "thread_id": thread_id,
                    "question": question,
                    "ground_truth_answer": qa.get("answer", ""),
                    "prediction": qa.get(args.prediction_key, ""),
                    "prediction_answer": qa.get(args.prediction_key + "_answer"),
                    "prediction_gold_answer": qa.get(args.prediction_key + "_gold_answer"),
                    "prediction_evidence": qa.get(args.prediction_key + "_evidence"),
                    "prediction_evidence_episode_refs": qa.get(args.prediction_key + "_evidence_episode_refs"),
                    "prediction_tool_calls": qa.get(args.prediction_key + "_tool_calls"),
                    "prediction_plan": qa.get(args.prediction_key + "_plan"),
                    "error": error_text,
                }
                append_trace(trace_fp, trace_record)

                asked_count += 1
                if progress is not None:
                    progress.update(1)
                if args.sleep_seconds > 0:
                    time.sleep(args.sleep_seconds)
                if stop:
                    break

            processed_samples += 1
            if changed and args.save_every > 0 and processed_samples % args.save_every == 0:
                _write_outputs(out_file, samples, out_map)
            if stop:
                break
    finally:
        if progress is not None:
            progress.close()
        trace_fp.close()

    episode_ref_cache: Dict[str, List[str]] = {}
    dialogue_episode_spans_cache: Dict[str, Dict[str, Tuple[int, int]]] = {}
    for sample in out_map.values():
        qas = sample.get("qa", [])
        if not isinstance(qas, list):
            continue
        f1_scores, b1_scores, recalls = eval_question_answering_realtalk(
            qas,
            args.prediction_key,
            episodes_root=episodes_root,
            episode_ref_cache=episode_ref_cache,
            dialogue_episode_spans_cache=dialogue_episode_spans_cache,
        )
        for i, qa in enumerate(qas):
            if not isinstance(qa, dict) or not should_evaluate_qa(qa):
                qa.pop(f1_metric_key, None)
                qa.pop(b1_metric_key, None)
                qa.pop(recall_metric_key, None)
                continue
            qa[f1_metric_key] = round(f1_scores[i], 3)
            qa[b1_metric_key] = round(b1_scores[i], 3)
            qa[recall_metric_key] = round(recalls[i], 3)

    _write_outputs(out_file, samples, out_map)

    out_samples_in_order = [out_map[str(s.get("sample_id"))] for s in samples]
    f1_summary = summarize_metric_by_category(out_samples_in_order, f1_metric_key)
    b1_summary = summarize_metric_by_category(out_samples_in_order, b1_metric_key)
    recall_summary = summarize_metric_by_category(out_samples_in_order, recall_metric_key)
    all_stats = _load_json(stats_file) if os.path.exists(stats_file) else {}
    if not isinstance(all_stats, dict):
        all_stats = {}
    all_stats[args.model_key] = {
        "summary_by_category": f1_summary["summary_by_category"],
        "overall_accuracy": f1_summary["overall"],
        "summary_by_category_b1": b1_summary["summary_by_category"],
        "overall_b1": b1_summary["overall"],
        "summary_by_category_recall": recall_summary["summary_by_category"],
        "overall_recall": recall_summary["overall"],
    }
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(all_stats, f, ensure_ascii=False, indent=2)

    logger.info("Saved predictions: %s", out_file)
    logger.info("Saved stats: %s", stats_file)
    logger.info("Saved trace: %s", trace_file)
    logger.info("Recall artifacts under: %s", recall_root)
    logger.info("Evaluated new questions this run: %d", asked_count)
    logger.info("Overall accuracy (%s): %.3f", args.model_key, f1_summary["overall"])
    logger.info("Overall B1 (%s): %.3f", args.model_key, b1_summary["overall"])
    logger.info("Overall recall (%s): %.3f", args.model_key, recall_summary["overall"])
    if fatal_error is not None:
        logger.error("Evaluation stopped early due to network/API error: %s", fatal_error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
