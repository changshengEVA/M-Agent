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
import string
import time
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Tuple

from Agents.memory_agent import create_memory_agent

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
        default="config/prompt/agent_sys.yaml",
        help="MemoryAgent config yaml path.",
    )
    parser.add_argument(
        "--out-file",
        type=str,
        default="locomo10_agent_qa.json",
        help="Prediction output file name (always written under log/).",
    )
    parser.add_argument(
        "--stats-file",
        type=str,
        default="",
        help="Stats output file name. Default: <out-file>_stats.json (under log/).",
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
        help="If set, overwrite existing predictions in out-file.",
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
        help="Uniform random fraction of samples to evaluate (default: 0.1).",
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
        "--log-file",
        type=str,
        default="",
        help="Run log file name. Default: <out-file>_run.log (under log/).",
    )
    parser.add_argument(
        "--trace-file",
        type=str,
        default="",
        help="JSONL trace file for per-question runtime records (GT + prediction). "
        "Default: <out-file>_qa_trace.jsonl (under log/).",
    )
    return parser.parse_args()


def _replace_or_append_suffix(path: str, old_suffix: str, new_suffix: str) -> str:
    if path.endswith(old_suffix):
        return path[: -len(old_suffix)] + new_suffix
    return path + new_suffix


def _force_log_dir(path: str) -> str:
    name = Path(path).name if str(path).strip() else "output.json"
    return str(Path("log") / name)


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
            if (not overwrite) and is_qa_processed(qa, prediction_key):
                continue
            count += 1
            if max_questions and count >= max_questions:
                return max_questions
    return count


def is_qa_processed(qa: Dict[str, Any], prediction_key: str) -> bool:
    if prediction_key in qa:
        return True

    derived_keys = (
        prediction_key + "_error",
        prediction_key + "_answer",
        prediction_key + "_gold_answer",
        prediction_key + "_evidence",
    )
    return any(key in qa for key in derived_keys)


def append_trace(trace_fp, record: Dict[str, Any]) -> None:
    trace_fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    trace_fp.flush()


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


def eval_question_answering_locomo(
    qas: List[Dict[str, Any]], prediction_key: str
) -> Tuple[List[float], List[float], List[float]]:
    all_scores: List[float] = []
    all_b1_scores: List[float] = []
    all_recall: List[float] = []

    for qa in qas:
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
        elif category == 5:
            lowered = output.lower()
            both_empty = (normalize_answer(output) == "") and (normalize_answer(answer) == "")
            score = (
                1.0
                if both_empty or ("no information available" in lowered or "not mentioned" in lowered)
                else 0.0
            )
            b1 = score
        else:
            score = 0.0
            b1 = 0.0
        all_scores.append(score)
        all_b1_scores.append(b1)

        context_key = prediction_key + "_context"
        evidence = qa.get("evidence", []) if isinstance(qa.get("evidence", []), list) else []
        if context_key in qa and evidence:
            ctx = qa.get(context_key, [])
            if isinstance(ctx, list) and ctx:
                if str(ctx[0]).startswith("S"):
                    sessions = [str(e)[1:] for e in ctx]
                    recall_acc = float(
                        sum(str(ev).split(":")[0][1:] in sessions for ev in evidence)
                    ) / len(evidence)
                else:
                    recall_acc = float(sum(str(ev) in ctx for ev in evidence)) / len(evidence)
                all_recall.append(recall_acc)
                continue
        all_recall.append(1.0)

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
            category = int(qa.get("category", -1))
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

    keys = [4, 1, 2, 3, 5]
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
            if not isinstance(qa, dict):
                continue
            category = int(qa.get("category", -1))
            total_counts[category] += 1
            metric_sums[category] += float(qa.get(metric_key, 0.0))

    keys = [4, 1, 2, 3, 5]
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

    for i, qa in enumerate(existing_qas):
        if i >= len(out_sample["qa"]) or not isinstance(qa, dict):
            continue
        out_sample["qa"][i].update(qa)
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


def main() -> None:
    args = parse_args()

    data_file = args.data_file
    out_file = _force_log_dir(args.out_file)
    stats_file = _force_log_dir(args.stats_file or out_file.replace(".json", "_stats.json"))
    log_file = _force_log_dir(args.log_file or _replace_or_append_suffix(out_file, ".json", "_run.log"))
    trace_file = _force_log_dir(
        args.trace_file or _replace_or_append_suffix(out_file, ".json", "_qa_trace.jsonl")
    )
    f1_metric_key = f"{args.model_key}_f1"
    b1_metric_key = f"{args.model_key}_b1"

    setup_logging(log_file)
    logger.info("Start LoCoMo QA evaluation")
    logger.info("data_file=%s", data_file)
    logger.info("config=%s", args.config)
    logger.info("out_file=%s", out_file)
    logger.info("stats_file=%s", stats_file)
    logger.info("log_file=%s", log_file)
    logger.info("trace_file=%s", trace_file)

    samples = _load_json(data_file)
    if not isinstance(samples, list):
        raise ValueError(f"Expected list data in {data_file}")

    original_sample_count = len(samples)
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
        agent = create_memory_agent(args.config)
    else:
        logger.info("No pending questions. Skip model inference and recompute metrics only.")

    asked_count = 0
    processed_samples = 0
    changed = False

    stop = False
    try:
        for sample in samples:
            sid = str(sample.get("sample_id"))
            out_sample = out_map[sid]
            qas = out_sample.get("qa", [])
            if not isinstance(qas, list):
                continue

            for q_idx, qa in enumerate(qas):
                if args.max_questions and asked_count >= args.max_questions:
                    stop = True
                    break
                if not isinstance(qa, dict):
                    continue

                if (not args.overwrite) and is_qa_processed(qa, args.prediction_key):
                    continue

                question = str(qa.get("question", "") or "").strip()
                thread_id = f"{args.thread_id_prefix}:{sid}:{q_idx}"
                error_text = None
                pred = ""
                answer = ""
                evidence = None

                if not question:
                    error_text = "empty_question"
                    qa[args.prediction_key] = ""
                    qa[args.prediction_key + "_error"] = error_text
                    changed = True
                else:
                    try:
                        if agent is None:
                            raise RuntimeError("MemoryAgent is not initialized for pending question.")
                        result = agent.ask(question, thread_id=thread_id)
                        answer = str(result.get("answer", "") or "")
                        pred = str(result.get("gold_answer", "") or "")
                        evidence = result.get("evidence")
                        qa[args.prediction_key] = pred
                        qa[args.prediction_key + "_answer"] = answer
                        qa[args.prediction_key + "_gold_answer"] = pred
                        qa[args.prediction_key + "_evidence"] = evidence
                    except Exception as exc:
                        error_text = str(exc)
                        qa[args.prediction_key] = ""
                        qa[args.prediction_key + "_error"] = error_text

                    changed = True

                trace_record = {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "sample_id": sid,
                    "qa_index": q_idx,
                    "category": qa.get("category"),
                    "thread_id": thread_id,
                    "question": question,
                    "ground_truth_answer": qa.get("answer", ""),
                    "prediction": qa.get(args.prediction_key, ""),
                    "prediction_answer": qa.get(args.prediction_key + "_answer"),
                    "prediction_gold_answer": qa.get(args.prediction_key + "_gold_answer"),
                    "prediction_evidence": qa.get(args.prediction_key + "_evidence"),
                    "error": error_text,
                }
                append_trace(trace_fp, trace_record)

                asked_count += 1
                if progress is not None:
                    progress.update(1)

                if args.sleep_seconds > 0:
                    time.sleep(args.sleep_seconds)

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

    for sample in out_map.values():
        qas = sample.get("qa", [])
        if not isinstance(qas, list):
            continue
        f1_scores, b1_scores, recalls = eval_question_answering_locomo(qas, args.prediction_key)
        for i, qa in enumerate(qas):
            if not isinstance(qa, dict):
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
    all_stats = _load_json(stats_file) if os.path.exists(stats_file) else {}
    if not isinstance(all_stats, dict):
        all_stats = {}
    all_stats[args.model_key] = stats
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(all_stats, f, ensure_ascii=False, indent=2)

    logger.info("Saved predictions: %s", out_file)
    logger.info("Saved stats: %s", stats_file)
    logger.info("Saved trace: %s", trace_file)
    logger.info("Evaluated new questions this run: %d", asked_count)
    logger.info("Overall accuracy (%s): %.3f", args.model_key, stats["overall_accuracy"])
    logger.info("Overall B1 (%s): %.3f", args.model_key, stats["overall_b1"])
    logger.info("Category accuracy: %s", json.dumps(stats["summary_by_category"], ensure_ascii=False))


if __name__ == "__main__":
    main()
