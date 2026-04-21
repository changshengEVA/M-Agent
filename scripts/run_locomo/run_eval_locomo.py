#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import re
import shutil
import string
import time
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

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


logger = logging.getLogger("run_eval_locomo")


DEFAULT_TEST_ID = "default"
PREDICTION_FILE_NAME = "locomo10_agent_qa.json"
STATS_FILE_NAME = "locomo10_agent_qa_stats.json"
RUN_LOG_FILE_NAME = "locomo10_agent_qa_run.log"
TRACE_FILE_NAME = "locomo10_agent_qa_qa_trace.jsonl"
SKIPPED_QA_CATEGORIES = {5}
EVAL_CATEGORY_ORDER = [4, 1, 2, 3]
LOCOMO_SOURCE_QA_INDEX_KEY = "_locomo_source_qa_index"
EVIDENCE_DIALOG_REF_PATTERN = re.compile(r"D\d+:\d+")
EPISODE_REF_PATTERN = re.compile(r"dlg_[A-Za-z0-9._-]+:ep_[A-Za-z0-9._-]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate MemoryAgent on LoCoMo QA using LoCoMo-style QA metrics."
    )
    parser.add_argument(
        "--data-file",
        type=str,
        default="data/locomo/data/locomo10.json",
        help="LoCoMo annotation file (must contain sample_id + qa + conversation).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/agents/memory/locomo_eval_memory_agent.yaml",
        help="MemoryAgent config yaml path.",
    )
    parser.add_argument(
        "--test-id",
        type=str,
        default=DEFAULT_TEST_ID,
        help="Output test folder name under log/, e.g. log/<test-id>/.",
    )
    parser.add_argument(
        "--model-key",
        type=str,
        default="memory_agent",
        help="Prefix for metric fields, e.g. memory_agent_f1.",
    )
    parser.add_argument(
        "--prediction-key",
        type=str,
        default="memory_agent_prediction",
        help="Field used to store predicted answer in each QA record.",
    )
    parser.add_argument(
        "--thread-id-prefix",
        type=str,
        default="locomo-eval",
        help="Prefix of thread id passed to MemoryAgent.ask().",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="If set, overwrite existing predictions in the fixed prediction file.",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=0,
        help="Optional cap on number of samples to evaluate (0 means all).",
    )
    parser.add_argument(
        "--sample-fraction",
        type=float,
        default=0.1,
        help=(
            "Uniform random fraction of samples to evaluate (default: 0.1). "
            "Ignored when --question-config is used."
        ),
    )
    parser.add_argument(
        "--sample-seed",
        type=int,
        default=42,
        help="Random seed used for sample selection.",
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=0,
        help="Optional cap on total number of questions to evaluate (0 means all).",
    )
    parser.add_argument(
        "--save-every",
        type=int,
        default=1,
        help="Save intermediate output every N samples.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Optional delay between QA calls.",
    )
    parser.add_argument(
        "--question-config",
        type=str,
        default="",
        help=(
            "Optional yaml path for fixed question selection. "
            "When enabled, --sample-fraction and --max-samples are ignored."
        ),
    )
    parser.add_argument(
        "--conv-ids",
        type=str,
        default="",
        help=(
            "Optional comma-separated LoCoMo conv ids to evaluate, e.g. "
            "'conv-30,conv-48'. Applied before question filtering/sampling."
        ),
    )
    parser.add_argument(
        "--workflow-id",
        type=str,
        default="",
        help=(
            "Override MemoryCore workflow_id so episodes/scene load from "
            "data/memory/<workflow_id>/ (must match import --process-id)."
        ),
    )
    parser.add_argument(
        "--recall-dir",
        type=str,
        default="recall",
        help=(
            "Subfolder under log/<test-id>/ for per-question recall JSON "
            "(same layout idea as LongMemEval run_eval_longmemeval)."
        ),
    )
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


def locomo_trace_id(sample_id: str, source_qa_index: int) -> str:
    """Stable id for recall / recall_trace (matches plan: sample_id__q{index})."""
    return f"{str(sample_id).strip()}__q{int(source_qa_index)}"


def _write_locomo_recall_artifacts(
    recall_root: Path,
    trace_id: str,
    thread_id: str,
    question: str,
    result: Optional[Dict[str, Any]],
    error: Optional[str],
) -> str:
    """Write log/<test_id>/<recall>/<stem>.json and optional Workspace/round_*.json. Returns absolute path."""
    safe = _sanitize_filename(trace_id)
    recall_root.mkdir(parents=True, exist_ok=True)
    per_question: Dict[str, Any] = {
        "question_id": trace_id,
        "trace_id": trace_id,
        "thread_id": thread_id,
        "question": question,
        "result": result,
        "error": error,
    }
    path = recall_root / f"{safe}.json"
    with open(path, "w", encoding="utf-8") as f_one:
        json.dump(per_question, f_one, ensure_ascii=False, indent=2, default=str)

    if isinstance(result, dict):
        rounds = result.get("workspace_rounds")
        if isinstance(rounds, list) and rounds:
            q_folder = recall_root / safe
            ws_dir = q_folder / "Workspace"
            ws_dir.mkdir(parents=True, exist_ok=True)
            for item in rounds:
                if not isinstance(item, dict):
                    continue
                rid_raw = item.get("round_id")
                try:
                    rid = int(rid_raw)
                except Exception:
                    rid = 0
                name = (
                    f"round_{rid:03d}.json"
                    if rid > 0
                    else f"round_{_sanitize_filename(str(rid_raw))}.json"
                )
                with open(ws_dir / name, "w", encoding="utf-8") as f_round:
                    json.dump(item, f_round, ensure_ascii=False, indent=2, default=str)

    return str(path.resolve())


def _load_yaml(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _parse_question_indices(raw_value: Any, *, item_label: str) -> List[int]:
    if isinstance(raw_value, int):
        raw_items = [raw_value]
    elif isinstance(raw_value, list):
        raw_items = raw_value
    else:
        raise ValueError(f"{item_label}: qa_indices must be an int or list[int].")

    parsed: List[int] = []
    seen = set()
    for raw_idx in raw_items:
        try:
            qa_idx = int(raw_idx)
        except Exception as exc:
            raise ValueError(f"{item_label}: invalid qa index {raw_idx!r}.") from exc
        if qa_idx < 0:
            raise ValueError(f"{item_label}: qa index must be >= 0, got {qa_idx}.")
        if qa_idx in seen:
            continue
        seen.add(qa_idx)
        parsed.append(qa_idx)

    if not parsed:
        raise ValueError(f"{item_label}: qa_indices cannot be empty.")
    return parsed


def load_question_selection_config(path: str) -> Dict[str, List[int]]:
    payload = _load_yaml(path)
    if not isinstance(payload, dict):
        raise ValueError("Question config must be a yaml mapping.")

    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("Question config must contain a non-empty top-level 'questions' list.")

    selection: Dict[str, List[int]] = {}
    seen_per_sample: Dict[str, set[int]] = {}

    for item_idx, item in enumerate(questions, start=1):
        item_label = f"questions[{item_idx}]"
        if not isinstance(item, dict):
            raise ValueError(f"{item_label}: each item must be a mapping.")

        sample_id = str(item.get("sample_id", "") or "").strip()
        if not sample_id:
            raise ValueError(f"{item_label}: sample_id is required.")

        if "qa_indices" in item:
            qa_indices = _parse_question_indices(item.get("qa_indices"), item_label=item_label)
        elif "qa_index" in item:
            qa_indices = _parse_question_indices(item.get("qa_index"), item_label=item_label)
        else:
            raise ValueError(f"{item_label}: provide qa_indices or qa_index.")

        target = selection.setdefault(sample_id, [])
        target_seen = seen_per_sample.setdefault(sample_id, set())
        for qa_idx in qa_indices:
            if qa_idx in target_seen:
                continue
            target.append(qa_idx)
            target_seen.add(qa_idx)

    return selection


def _parse_conv_ids(raw_value: str) -> List[str]:
    conv_ids: List[str] = []
    seen = set()
    for token in str(raw_value or "").split(","):
        value = token.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        conv_ids.append(value)
    return conv_ids


def filter_samples_by_conv_ids(
    samples: List[Dict[str, Any]],
    conv_ids: List[str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not conv_ids:
        return samples, []

    target = set(conv_ids)
    filtered: List[Dict[str, Any]] = []
    hit_ids = set()

    for sample in samples:
        if not isinstance(sample, dict):
            continue
        sample_id = str(sample.get("sample_id", "") or "").strip()
        if sample_id in target:
            filtered.append(sample)
            hit_ids.add(sample_id)

    missing = sorted(target - hit_ids)
    return filtered, missing


def filter_samples_by_question_selection(
    samples: List[Dict[str, Any]],
    question_selection: Dict[str, List[int]],
) -> List[Dict[str, Any]]:
    if not question_selection:
        return samples

    sample_lookup = {}
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        sid = str(sample.get("sample_id"))
        if sid not in sample_lookup:
            sample_lookup[sid] = sample

    missing_sample_ids = [sid for sid in question_selection if sid not in sample_lookup]
    if missing_sample_ids:
        missing_preview = ", ".join(missing_sample_ids[:5])
        suffix = " ..." if len(missing_sample_ids) > 5 else ""
        raise ValueError(
            f"Question config references unknown sample_id(s): {missing_preview}{suffix}"
        )

    filtered_samples: List[Dict[str, Any]] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        sid = str(sample.get("sample_id"))
        selected_indices = question_selection.get(sid)
        if not selected_indices:
            continue

        qas = sample.get("qa", [])
        if not isinstance(qas, list):
            raise ValueError(f"Sample {sid} has invalid qa payload: expected list.")

        sample_copy = deepcopy(sample)
        selected_qas: List[Any] = []
        for qa_idx in selected_indices:
            if qa_idx >= len(qas):
                raise ValueError(
                    f"Sample {sid} only has {len(qas)} QA items, but question config "
                    f"requested qa_index={qa_idx}."
                )
            qa_copy = deepcopy(qas[qa_idx])
            if isinstance(qa_copy, dict):
                qa_copy.setdefault(LOCOMO_SOURCE_QA_INDEX_KEY, qa_idx)
            selected_qas.append(qa_copy)

        sample_copy["qa"] = selected_qas
        filtered_samples.append(sample_copy)

    return filtered_samples


def _get_qa_category(qa: Dict[str, Any]) -> int:
    try:
        return int(qa.get("category", -1))
    except Exception:
        return -1


def should_evaluate_qa(qa: Dict[str, Any]) -> bool:
    return _get_qa_category(qa) not in SKIPPED_QA_CATEGORIES


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


def count_pending_questions(
    samples: List[Dict[str, Any]],
    out_map: Dict[str, Dict[str, Any]],
    prediction_key: str,
    overwrite: bool,
    max_questions: int,
) -> int:
    count = 0
    for sample in samples:
        sid = str(sample.get("sample_id"))
        qas = out_map.get(sid, {}).get("qa", [])
        if not isinstance(qas, list):
            continue
        for qa in qas:
            if not isinstance(qa, dict):
                continue
            if not should_evaluate_qa(qa):
                continue
            if (not overwrite) and is_qa_processed(qa, prediction_key):
                continue
            count += 1
            if max_questions and count >= max_questions:
                return max_questions
    return count


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


def append_trace(trace_fp, record: Dict[str, Any]) -> None:
    trace_fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    trace_fp.flush()


def _apply_trace_record_to_qa(
    qa: Dict[str, Any], record: Dict[str, Any], prediction_key: str
) -> bool:
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
    if episode_refs is None:
        episode_refs = record.get("evidence_episode_refs")
    if episode_refs is not None:
        qa[prediction_key + "_evidence_episode_refs"] = episode_refs
        applied = True

    episode_ref_count = record.get("prediction_evidence_episode_ref_count")
    if episode_ref_count is None:
        episode_ref_count = record.get("evidence_episode_ref_count")
    if episode_ref_count is not None:
        qa[prediction_key + "_evidence_episode_ref_count"] = episode_ref_count
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


def recover_from_trace(
    trace_path: str, out_map: Dict[str, Dict[str, Any]], prediction_key: str
) -> Tuple[int, int]:
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

            record_prediction_key = record.get("prediction_key")
            if record_prediction_key and record_prediction_key != prediction_key:
                continue

            sid = str(record.get("sample_id"))
            q_idx_raw = record.get("qa_index")
            try:
                q_idx = int(q_idx_raw)
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
            if not isinstance(qa, dict):
                continue

            if is_qa_processed(qa, prediction_key):
                continue

            if _apply_trace_record_to_qa(qa, record, prediction_key):
                recovered_qas.add((sid, q_idx))

    return len(recovered_qas), skipped_lines


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

    scores = []
    for gt in ground_truths:
        scores.append(max(f1_score_single(pred, gt) for pred in predictions))
    return sum(scores) / len(scores)


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

    scores = []
    for gt in ground_truths:
        scores.append(max(b1_score_single(pred, gt) for pred in predictions))
    return sum(scores) / len(scores)


def _dedupe_keep_order(values: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        norm = str(value or "").strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        ordered.append(norm)
    return ordered


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
    return _dedupe_keep_order(refs)


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
    return _dedupe_keep_order(refs)


def _is_successful_tool_call_for_eval(call: Dict[str, Any]) -> bool:
    status = str(call.get("status", "") or "").strip().lower()
    if status and status != "completed":
        return False

    result = call.get("result")
    if isinstance(result, dict):
        if bool(result.get("blocked", False)):
            return False
        if "hit" in result and not bool(result.get("hit")):
            return False
        if "success" in result and not bool(result.get("success")):
            return False
    return True


def _collect_episode_refs_from_value(value: Any, refs: set[str]) -> None:
    if isinstance(value, dict):
        dialogue_id = str(value.get("dialogue_id", "") or "").strip()
        episode_id = str(value.get("episode_id", "") or "").strip()
        if dialogue_id and episode_id:
            refs.add(f"{dialogue_id}:{episode_id}")
        for child in value.values():
            _collect_episode_refs_from_value(child, refs)
        return

    if isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_episode_refs_from_value(item, refs)


def _extract_episode_refs_from_tool_calls(tool_calls: Any) -> List[str]:
    if not isinstance(tool_calls, list):
        return []

    refs: set[str] = set()
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        if not _is_successful_tool_call_for_eval(call):
            continue
        _collect_episode_refs_from_value(call.get("result"), refs)
        _collect_episode_refs_from_value(call.get("params"), refs)

    return sorted(refs)


def _get_prediction_episode_refs(qa: Dict[str, Any], prediction_key: str) -> List[str]:
    refs = _extract_episode_refs(qa.get(prediction_key + "_evidence_episode_refs"))
    if refs:
        return refs

    refs = _extract_episode_refs_from_tool_calls(qa.get(prediction_key + "_tool_calls"))
    if refs:
        return refs

    refs = _extract_episode_refs(qa.get(prediction_key + "_evidence"))
    if refs:
        return refs

    return _extract_episode_refs(qa.get(prediction_key + "_answer"))


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


def _parse_session_from_dialogue_id(dialogue_id: str) -> int | None:
    match = re.search(r"_(\d+)$", str(dialogue_id or "").strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _parse_session_from_dialogue_ref(dialogue_ref: str) -> str | None:
    match = re.fullmatch(r"D(\d+):\d+", str(dialogue_ref or "").strip())
    if not match:
        return None
    return match.group(1)


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
    except Exception as exc:
        logger.debug("Failed to load episode file for %s: %s", dialogue_id, exc)
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
    resolved: List[str] = []

    spans = _load_dialogue_episode_turn_spans(
        dialogue_id=dialogue_id,
        episodes_root=episodes_root,
        dialogue_episode_spans_cache=dialogue_episode_spans_cache,
    )
    turn_span = spans.get(episode_id)
    if turn_span and session_num is not None:
        start, end = turn_span
        resolved = [f"D{session_num}:{dia_id}" for dia_id in range(start + 1, end + 2)]

    if not resolved and session_num is not None:
        resolved = [f"S{session_num}"]

    resolved = _dedupe_keep_order(resolved)
    episode_ref_cache[episode_ref] = resolved
    return resolved


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


def _compute_recall_from_context(
    qa: Dict[str, Any], prediction_key: str, evidence_refs: List[str]
) -> float | None:
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


def eval_question_answering_locomo(
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
            recall_acc = _compute_recall_from_context(
                qa=qa,
                prediction_key=prediction_key,
                evidence_refs=evidence_refs,
            )
        if recall_acc is None:
            recall_acc = 1.0 if not evidence_refs else 0.0

        all_recall.append(recall_acc)

    return all_scores, all_b1_scores, all_recall


def get_conversation_lengths(conversation: Dict[str, Any]) -> Dict[str, int]:
    total_conv_length = 0
    id2length: Dict[str, int] = {}

    for sess_num in range(1, 50):
        key = f"session_{sess_num}"
        if key not in conversation:
            continue
        dialogs = conversation.get(key, [])
        if not isinstance(dialogs, list) or not dialogs:
            continue

        for dialog in dialogs:
            if not isinstance(dialog, dict):
                continue
            dialog_tokens = f"{dialog.get('speaker', '')}: {dialog.get('text', '')}\n"
            if dialog.get("img_file"):
                dialog_tokens += f"[shares {dialog.get('blip_caption', '')}]\n"
            dialog_length = len(dialog_tokens)
            dia_id = dialog.get("dia_id")
            if dia_id:
                id2length[str(dia_id)] = total_conv_length + dialog_length
            total_conv_length += dialog_length
    return id2length


def _safe_parse_evidence(ev: str) -> Tuple[int | None, int | None]:
    try:
        parts = str(ev).split(":")
        if len(parts) < 2:
            return None, None
        sess_str = parts[0]
        if not sess_str.startswith("D"):
            return None, None
        sess = int(sess_str[1:])
        dia = int(parts[-1])
        return sess, dia
    except Exception:
        return None, None


def _to_plain_dict(obj: Any) -> Any:
    if isinstance(obj, defaultdict):
        return {k: _to_plain_dict(v) for k, v in obj.items()}
    if isinstance(obj, dict):
        return {k: _to_plain_dict(v) for k, v in obj.items()}
    return obj


def analyze_aggr_acc_locomo(
    ann_samples: List[Dict[str, Any]],
    out_samples: List[Dict[str, Any]],
    out_file: str,
    model_key: str,
    metric_key: str,
) -> Dict[str, Any]:
    total_counts = defaultdict(float)
    acc_counts = defaultdict(float)
    memory_counts = defaultdict(lambda: defaultdict(float))
    memory_counts_og = defaultdict(lambda: defaultdict(float))
    context_len_counts = defaultdict(float)
    context_len_og = defaultdict(float)

    ann_by_id = {str(s.get("sample_id")): s for s in ann_samples}
    out_by_id = {str(s.get("sample_id")): s for s in out_samples}

    for sample_id, output in out_by_id.items():
        ann = ann_by_id.get(sample_id, {})
        conversation = ann.get("conversation", {})
        id2length = get_conversation_lengths(conversation if isinstance(conversation, dict) else {})

        for qa in output.get("qa", []):
            if not isinstance(qa, dict) or not should_evaluate_qa(qa):
                continue
            category = _get_qa_category(qa)
            total_counts[category] += 1
            if metric_key not in qa:
                continue

            score = float(qa.get(metric_key, 0.0))
            acc_counts[category] += score

            evidence_raw = qa.get("evidence", [])
            if not isinstance(evidence_raw, list) or not evidence_raw:
                continue
            evidence = [str(x).replace("(", "").replace(")", "") for x in evidence_raw if str(x).strip()]
            parsed = [_safe_parse_evidence(ev) for ev in evidence]
            parsed = [(s, d) for s, d in parsed if s is not None and d is not None]
            if not parsed:
                continue

            farthest_session = min(s for s, _ in parsed)
            farthest_dialog = min(d for s, d in parsed if s == farthest_session)
            farthest_key = f"D{farthest_session}:{farthest_dialog}"
            farthest_length = id2length.get(farthest_key)
            if farthest_length is None:
                continue

            memory_bin = int(math.ceil(farthest_length / 1000.0))
            memory_counts_og[category][memory_bin] += 1
            memory_counts[category][memory_bin] += score

            if category == 1:
                latest_session = max(s for s, _ in parsed)
                latest_dialog = max(d for s, d in parsed if s == latest_session)
                latest_key = f"D{latest_session}:{latest_dialog}"
                latest_length = id2length.get(latest_key)
                if latest_length is None:
                    continue
                context_length = latest_length - farthest_length
                context_bin = int(math.ceil(context_length / 1000.0))
                context_len_og[context_bin] += 1
                context_len_counts[context_bin] += score

    keys = EVAL_CATEGORY_ORDER
    summary_by_cat = {}
    total_q = 0.0
    total_score = 0.0
    for cat in keys:
        count = total_counts[cat]
        score_sum = acc_counts[cat]
        acc = (score_sum / count) if count else 0.0
        summary_by_cat[str(cat)] = {
            "count": int(count),
            "score_sum": round(score_sum, 6),
            "accuracy": round(acc, 6),
        }
        total_q += count
        total_score += score_sum

    overall_acc = (total_score / total_q) if total_q else 0.0

    if os.path.exists(out_file):
        stats = json.load(open(out_file, "r", encoding="utf-8"))
        if not isinstance(stats, dict):
            stats = {}
    else:
        stats = {}

    stats[model_key] = {
        "category_counts": _to_plain_dict(total_counts),
        "cum_accuracy_by_category": _to_plain_dict(acc_counts),
        "category_counts_by_memory": _to_plain_dict(memory_counts_og),
        "cum_accuracy_by_category_by_memory": _to_plain_dict(memory_counts),
        "context_length_counts": _to_plain_dict(context_len_og),
        "cum_accuracy_by_context_length": _to_plain_dict(context_len_counts),
        "summary_by_category": summary_by_cat,
        "overall_accuracy": round(overall_acc, 6),
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return stats[model_key]


def summarize_metric_by_category(
    out_samples: List[Dict[str, Any]], metric_key: str
) -> Dict[str, Any]:
    total_counts = defaultdict(float)
    metric_sums = defaultdict(float)

    for sample in out_samples:
        for qa in sample.get("qa", []):
            if not isinstance(qa, dict) or not should_evaluate_qa(qa):
                continue
            category = _get_qa_category(qa)
            total_counts[category] += 1
            metric_sums[category] += float(qa.get(metric_key, 0.0))

    keys = EVAL_CATEGORY_ORDER
    summary_by_cat = {}
    total_q = 0.0
    total_score = 0.0
    for cat in keys:
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


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _prepare_out_sample(
    sample: Dict[str, Any], existing: Dict[str, Any] | None
) -> Dict[str, Any]:
    out_sample = {"sample_id": sample.get("sample_id"), "qa": deepcopy(sample.get("qa", []))}
    if not existing:
        return out_sample

    existing_qas = existing.get("qa", [])
    if not isinstance(existing_qas, list):
        return out_sample

    def _pick_existing_qa(out_qa: Any, out_idx: int) -> Dict[str, Any] | None:
        if not isinstance(out_qa, dict):
            candidate = existing_qas[out_idx] if out_idx < len(existing_qas) else None
            return candidate if isinstance(candidate, dict) else None

        source_idx = out_qa.get(LOCOMO_SOURCE_QA_INDEX_KEY)
        try:
            source_idx = int(source_idx) if source_idx is not None else None
        except Exception:
            source_idx = None

        if source_idx is not None:
            for existing_qa in existing_qas:
                if not isinstance(existing_qa, dict):
                    continue
                existing_source_idx = existing_qa.get(LOCOMO_SOURCE_QA_INDEX_KEY)
                try:
                    existing_source_idx = (
                        int(existing_source_idx) if existing_source_idx is not None else None
                    )
                except Exception:
                    existing_source_idx = None
                if existing_source_idx == source_idx:
                    return existing_qa

        if out_idx < len(existing_qas) and isinstance(existing_qas[out_idx], dict):
            candidate = existing_qas[out_idx]
            if str(candidate.get("question", "") or "") == str(out_qa.get("question", "") or ""):
                return candidate

        out_question = str(out_qa.get("question", "") or "")
        if out_question:
            for existing_qa in existing_qas:
                if not isinstance(existing_qa, dict):
                    continue
                if str(existing_qa.get("question", "") or "") == out_question:
                    return existing_qa

        return None

    for out_idx, out_qa in enumerate(out_sample["qa"]):
        existing_qa = _pick_existing_qa(out_qa, out_idx)
        if isinstance(out_qa, dict) and isinstance(existing_qa, dict):
            out_qa.update(existing_qa)
    return out_sample


def _write_outputs(
    out_path: str,
    source_order: List[Dict[str, Any]],
    out_map: Dict[str, Dict[str, Any]],
) -> None:
    ordered = []
    for sample in source_order:
        sid = str(sample.get("sample_id"))
        if sid in out_map:
            ordered.append(out_map[sid])
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)


def _uniform_sample_by_fraction(
    samples: List[Dict[str, Any]], fraction: float, seed: int
) -> List[Dict[str, Any]]:
    if fraction <= 0:
        raise ValueError(f"--sample-fraction must be > 0, got {fraction}")
    if fraction >= 1 or not samples:
        return samples

    sample_size = max(1, int(len(samples) * fraction))
    if sample_size >= len(samples):
        return samples

    rng = random.Random(seed)
    picked_indices = sorted(rng.sample(range(len(samples)), sample_size))
    return [samples[i] for i in picked_indices]


def _resolve_relative_path(path_value: str, base_path: str) -> Path:
    candidate = Path(path_value)
    if candidate.is_absolute():
        return candidate
    return (Path(base_path).resolve().parent / candidate).resolve()


def _resolve_locomo_episodes_root_from_agent_config(
    agent_config_path: str,
    workflow_id_override: str = "",
) -> Path | None:
    try:
        agent_cfg = _load_yaml(agent_config_path)
    except Exception as exc:
        logger.warning("Failed to load MemoryAgent config (%s): %s", agent_config_path, exc)
        return None

    if not isinstance(agent_cfg, dict):
        logger.warning("MemoryAgent config is not a mapping: %s", agent_config_path)
        return None

    memory_core_path_raw = str(agent_cfg.get("memory_core_config_path", "") or "").strip()
    if not memory_core_path_raw:
        logger.warning("MemoryAgent config missing memory_core_config_path: %s", agent_config_path)
        return None

    memory_core_path = _resolve_relative_path(memory_core_path_raw, agent_config_path)
    if not memory_core_path.exists():
        logger.warning("MemoryCore config not found: %s", memory_core_path)
        return None

    try:
        memory_core_cfg = _load_yaml(str(memory_core_path))
    except Exception as exc:
        logger.warning("Failed to load MemoryCore config (%s): %s", memory_core_path, exc)
        return None

    if not isinstance(memory_core_cfg, dict):
        logger.warning("MemoryCore config is not a mapping: %s", memory_core_path)
        return None

    workflow_id = str(workflow_id_override or "").strip()
    if not workflow_id:
        workflow_id = str(memory_core_cfg.get("workflow_id", "") or "").strip()
    if not workflow_id:
        logger.warning("workflow_id missing in MemoryCore config: %s", memory_core_path)
        return None

    episodes_root = Path(resolve_project_path(f"data/memory/{workflow_id}/episodes"))
    if not episodes_root.exists():
        logger.warning("episodes root not found: %s", episodes_root)
        return None
    return episodes_root


def main() -> int:
    args = parse_args()

    data_file = str(resolve_project_path(args.data_file))
    config_path = str(resolve_project_path(args.config))
    output_paths = _build_output_paths(args.test_id)
    out_file = output_paths["out_file"]
    stats_file = output_paths["stats_file"]
    log_file = output_paths["log_file"]
    trace_file = output_paths["trace_file"]
    recall_root = LOG_DIR / _sanitize_test_id(args.test_id) / _sanitize_recall_dir_segment(
        args.recall_dir
    )
    f1_metric_key = f"{args.model_key}_f1"
    b1_metric_key = f"{args.model_key}_b1"

    setup_logging(log_file)
    logger.info("Start LoCoMo QA evaluation")
    logger.info("out_file=%s", out_file)
    logger.info("stats_file=%s", stats_file)
    logger.info("log_file=%s", log_file)
    logger.info("trace_file=%s", trace_file)
    logger.info("recall_root=%s", recall_root)
    if args.overwrite and recall_root.exists():
        shutil.rmtree(recall_root, ignore_errors=True)
    logger.info("Skipped QA categories: %s", sorted(SKIPPED_QA_CATEGORIES))
    wf_override = str(args.workflow_id or "").strip()
    episodes_root = _resolve_locomo_episodes_root_from_agent_config(
        config_path,
        workflow_id_override=wf_override,
    )
    if episodes_root is not None:
        logger.info("episodes_root=%s", episodes_root)
    else:
        logger.warning(
            "episodes_root unavailable; recall will fallback to coarse/session or context matching."
        )
    if wf_override:
        logger.info("workflow_id override=%s", wf_override)

    run_params: Dict[str, Any] = {
        "data_file": data_file,
        "memory_agent_config": config_path,
        "test_id": _sanitize_test_id(args.test_id),
        "workflow_id_override": wf_override or None,
        "episodes_root_resolved": str(episodes_root) if episodes_root is not None else None,
        "model_key": args.model_key,
        "prediction_key": args.prediction_key,
        "thread_id_prefix": args.thread_id_prefix,
        "overwrite": bool(args.overwrite),
        "sample_fraction": args.sample_fraction,
        "sample_seed": args.sample_seed,
        "max_samples": args.max_samples,
        "max_questions": args.max_questions,
        "save_every": args.save_every,
        "sleep_seconds": args.sleep_seconds,
        "recall_dir": str(args.recall_dir or "").strip() or "recall",
        "conv_ids_cli": args.conv_ids or "",
        "question_config_cli": str(args.question_config or "").strip(),
    }
    try:
        params_txt = yaml.safe_dump(
            run_params,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
    except Exception:
        params_txt = str(run_params)
    logger.info("=== run_eval_locomo parameters (paths/scalars only; no prompt file contents) ===")
    for line in params_txt.rstrip().splitlines():
        logger.info("  %s", line)
    logger.info("=== end run_eval_locomo parameters ===")

    samples = _load_json(data_file)
    if not isinstance(samples, list):
        raise ValueError(f"Expected list data in {data_file}")

    original_sample_count = len(samples)
    conv_ids = _parse_conv_ids(args.conv_ids)
    if conv_ids:
        samples, missing_conv_ids = filter_samples_by_conv_ids(samples, conv_ids)
        logger.info(
            "Conv-id filter enabled: selected %d/%d samples (conv_ids=%s)",
            len(samples),
            original_sample_count,
            conv_ids,
        )
        if missing_conv_ids:
            logger.warning("Requested conv_ids not found: %s", missing_conv_ids)
        original_sample_count = len(samples)

    question_config_path = ""
    if args.question_config:
        question_config_path = str(resolve_project_path(args.question_config))
        question_selection = load_question_selection_config(question_config_path)
        samples = filter_samples_by_question_selection(samples, question_selection)
        selected_question_count = sum(len(sample.get("qa", [])) for sample in samples)
        logger.info("Question config enabled: %s", question_config_path)
        logger.info(
            "Question config selected %d/%d samples and %d questions.",
            len(samples),
            original_sample_count,
            selected_question_count,
        )
        logger.info(
            "Question config mode ignores sample sampling flags: sample_fraction=%.4f, max_samples=%d",
            args.sample_fraction,
            args.max_samples,
        )
    else:
        samples = _uniform_sample_by_fraction(samples, args.sample_fraction, args.sample_seed)
        logger.info(
            "Uniform sample enabled: kept %d/%d samples (fraction=%.4f, seed=%d)",
            len(samples),
            original_sample_count,
            args.sample_fraction,
            args.sample_seed,
        )

        if args.max_samples and args.max_samples > 0:
            samples = samples[: args.max_samples]

    existing_map: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(out_file) and not args.overwrite:
        loaded = _load_json(out_file)
        if isinstance(loaded, list):
            existing_map = {str(s.get("sample_id")): s for s in loaded if isinstance(s, dict)}

    out_map: Dict[str, Dict[str, Any]] = {}
    for sample in samples:
        sid = str(sample.get("sample_id"))
        out_map[sid] = _prepare_out_sample(sample, existing_map.get(sid))

    recovered_count = 0
    skipped_trace_lines = 0
    if not args.overwrite:
        recovered_count, skipped_trace_lines = recover_from_trace(
            trace_path=trace_file,
            out_map=out_map,
            prediction_key=args.prediction_key,
        )
        if recovered_count > 0:
            logger.info("Recovered %d QA results from trace file.", recovered_count)
        if skipped_trace_lines > 0:
            logger.warning(
                "Skipped %d malformed trace lines while recovering.", skipped_trace_lines
            )

    pending_questions = count_pending_questions(
        samples=samples,
        out_map=out_map,
        prediction_key=args.prediction_key,
        overwrite=args.overwrite,
        max_questions=args.max_questions,
    )
    logger.info("Pending questions to run this time: %d", pending_questions)

    trace_mode = "w" if args.overwrite else "a"
    Path(trace_file).parent.mkdir(parents=True, exist_ok=True)
    trace_fp = open(trace_file, trace_mode, encoding="utf-8")

    progress = None
    if tqdm is not None:
        progress = tqdm(total=pending_questions, desc="Evaluating QA", unit="q")

    agent = None
    if pending_questions > 0:
        from m_agent.agents.memory_agent import create_memory_agent

        agent = create_memory_agent(
            config_path,
            memory_workflow_id=wf_override or None,
        )
    else:
        logger.info("No pending questions. Skip model inference and recompute metrics only.")

    asked_count = 0
    processed_samples = 0
    changed = False
    fatal_error: Exception | None = None

    stop = False
    try:
        for sample in samples:
            sid = str(sample.get("sample_id"))
            out_sample = out_map[sid]
            qas = out_sample.get("qa", [])
            if not isinstance(qas, list):
                continue

            for q_idx, qa in enumerate(qas):
                if not isinstance(qa, dict):
                    continue
                if not should_evaluate_qa(qa):
                    continue
                if args.max_questions and asked_count >= args.max_questions:
                    stop = True
                    break

                if (not args.overwrite) and is_qa_processed(qa, args.prediction_key):
                    continue

                question = str(qa.get("question", "") or "").strip()
                source_q_idx = qa.get(LOCOMO_SOURCE_QA_INDEX_KEY, q_idx)
                try:
                    source_q_idx = int(source_q_idx)
                except Exception:
                    source_q_idx = q_idx
                thread_id = f"{args.thread_id_prefix}:{sid}:{source_q_idx}"
                trace_id = locomo_trace_id(sid, source_q_idx)
                error_text = None
                ask_result: Dict[str, Any] | None = None
                pred = ""
                answer = ""
                evidence = None
                tool_calls: Any = None
                question_plan: Any = None
                evidence_episode_refs: List[str] = []
                evidence_episode_ref_count = 0

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
                        evidence_episode_refs = _extract_episode_refs(
                            ask_result.get("evidence_episode_refs")
                        )
                        raw_ref_count = ask_result.get("evidence_episode_ref_count")
                        try:
                            evidence_episode_ref_count = int(raw_ref_count)
                        except Exception:
                            evidence_episode_ref_count = len(evidence_episode_refs)
                        evidence_episode_ref_count = max(
                            evidence_episode_ref_count, len(evidence_episode_refs)
                        )
                        qa[args.prediction_key] = pred
                        qa[args.prediction_key + "_answer"] = answer
                        qa[args.prediction_key + "_gold_answer"] = pred
                        qa[args.prediction_key + "_evidence"] = evidence
                        qa[args.prediction_key + "_evidence_episode_refs"] = evidence_episode_refs
                        qa[args.prediction_key + "_evidence_episode_ref_count"] = (
                            evidence_episode_ref_count
                        )
                        qa[args.prediction_key + "_tool_calls"] = (
                            tool_calls if isinstance(tool_calls, list) else []
                        )
                        qa[args.prediction_key + "_plan"] = (
                            question_plan if isinstance(question_plan, dict) else None
                        )
                        qa.pop(args.prediction_key + "_error", None)
                    except Exception as exc:
                        error_text = str(exc)
                        if hasattr(agent, "get_last_question_plan"):
                            try:
                                qa[args.prediction_key + "_plan"] = agent.get_last_question_plan()
                            except Exception:
                                pass
                        if hasattr(agent, "get_last_tool_calls"):
                            try:
                                qa[args.prediction_key + "_tool_calls"] = agent.get_last_tool_calls()
                            except Exception:
                                pass
                        if is_network_api_error(exc):
                            for suffix in (
                                "",
                                "_answer",
                                "_gold_answer",
                                "_evidence",
                                "_evidence_episode_refs",
                                "_evidence_episode_ref_count",
                            ):
                                qa.pop(args.prediction_key + suffix, None)
                            qa[args.prediction_key + "_error"] = error_text
                            fatal_error = exc
                            stop = True
                            logger.exception(
                                "Detected network/API error at sample_id=%s qa_index=%s thread_id=%s; stopping evaluation.",
                                sid,
                                q_idx,
                                thread_id,
                            )
                        else:
                            qa[args.prediction_key] = ""
                            qa[args.prediction_key + "_error"] = error_text

                    changed = True

                recall_json_path = _write_locomo_recall_artifacts(
                    recall_root,
                    trace_id,
                    thread_id,
                    question,
                    ask_result,
                    error_text,
                )
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
                    "prediction_evidence_episode_refs": qa.get(
                        args.prediction_key + "_evidence_episode_refs"
                    ),
                    "prediction_evidence_episode_ref_count": qa.get(
                        args.prediction_key + "_evidence_episode_ref_count"
                    ),
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
                logger.info(
                    "Intermediate save: processed_samples=%d, asked_count=%d",
                    processed_samples,
                    asked_count,
                )

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
        f1_scores, b1_scores, recalls = eval_question_answering_locomo(
            qas,
            args.prediction_key,
            episodes_root=episodes_root,
            episode_ref_cache=episode_ref_cache,
            dialogue_episode_spans_cache=dialogue_episode_spans_cache,
        )
        for i, qa in enumerate(qas):
            if not isinstance(qa, dict):
                continue
            if not should_evaluate_qa(qa):
                qa.pop(f1_metric_key, None)
                qa.pop(b1_metric_key, None)
                qa.pop(args.model_key + "_recall", None)
                continue
            qa[f1_metric_key] = round(f1_scores[i], 3)
            qa[b1_metric_key] = round(b1_scores[i], 3)
            qa[args.model_key + "_recall"] = round(recalls[i], 3)

    _write_outputs(out_file, samples, out_map)

    out_samples_in_order = [out_map[str(s.get("sample_id"))] for s in samples]
    stats = analyze_aggr_acc_locomo(
        ann_samples=samples,
        out_samples=out_samples_in_order,
        out_file=stats_file,
        model_key=args.model_key,
        metric_key=f1_metric_key,
    )
    b1_summary = summarize_metric_by_category(out_samples_in_order, b1_metric_key)
    stats["summary_by_category_b1"] = b1_summary["summary_by_category"]
    stats["overall_b1"] = b1_summary["overall"]
    recall_metric_key = args.model_key + "_recall"
    recall_summary = summarize_metric_by_category(out_samples_in_order, recall_metric_key)
    stats["summary_by_category_recall"] = recall_summary["summary_by_category"]
    stats["overall_recall"] = recall_summary["overall"]
    all_stats = _load_json(stats_file) if os.path.exists(stats_file) else {}
    if not isinstance(all_stats, dict):
        all_stats = {}
    all_stats[args.model_key] = stats
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(all_stats, f, ensure_ascii=False, indent=2)

    logger.info("Saved predictions: %s", out_file)
    logger.info("Saved stats: %s", stats_file)
    logger.info("Saved trace: %s", trace_file)
    logger.info("Recall artifacts under: %s", recall_root)
    logger.info("Evaluated new questions this run: %d", asked_count)
    logger.info("Overall accuracy (%s): %.3f", args.model_key, stats["overall_accuracy"])
    logger.info("Overall B1 (%s): %.3f", args.model_key, stats["overall_b1"])
    logger.info("Overall recall (%s): %.3f", args.model_key, stats["overall_recall"])
    logger.info("Category accuracy: %s", json.dumps(stats["summary_by_category"], ensure_ascii=False))
    if fatal_error is not None:
        logger.error("Evaluation stopped early due to network/API error: %s", fatal_error)
        return 1
    return 0


if __name__ == "__main__":
    # Example:
    # python scripts/run_locomo/run_eval_locomo.py --test-id locomo_subset_test --question-config path/to/questions.yaml
    # python scripts/run_locomo/run_eval_locomo.py --test-id locomo_before_hybrid_test1 --conv-ids conv-30
    raise SystemExit(main())
