"""
Evaluate LoCoMo QA prediction files in-place with an LLM judge.

This script is tailored for this repository's output format:
    log/<test-id>/locomo10_agent_qa.json

It writes judge results back into each QA item as new fields, for example:
    memory_agent_llm_judge_score
    memory_agent_llm_judge

and also updates the sibling stats file:
    log/<test-id>/locomo10_agent_qa_stats.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap_project


bootstrap_project()

from m_agent.config_paths import QA_LLM_JUDGE_PROMPT_CONFIG_PATH
from m_agent.paths import ENV_PATH, resolve_project_path
from m_agent.prompt_utils import load_resolved_prompt_config, normalize_prompt_language


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    try:
        from dotenv import load_dotenv  # type: ignore
    except ModuleNotFoundError:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    else:
        load_dotenv(dotenv_path=path)


_load_env_file(ENV_PATH)


def load_judge_prompts(
    config_path: str | Path,
    prompt_language: str,
) -> tuple[str, str]:
    resolved_path = resolve_project_path(config_path)
    config = load_resolved_prompt_config(
        resolved_path,
        language=normalize_prompt_language(prompt_language),
    )
    prompt_node = config.get("qa_llm_judge")
    if not isinstance(prompt_node, dict):
        raise ValueError(f"`qa_llm_judge` is required in prompt config: {resolved_path}")

    system_prompt = prompt_node.get("system_prompt")
    user_prompt_template = prompt_node.get("user_prompt_template")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ValueError(f"`qa_llm_judge.system_prompt` is required in prompt config: {resolved_path}")
    if not isinstance(user_prompt_template, str) or not user_prompt_template.strip():
        raise ValueError(f"`qa_llm_judge.user_prompt_template` is required in prompt config: {resolved_path}")
    return system_prompt.strip(), user_prompt_template.strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate locomo10_agent_qa.json in-place with an OpenAI-compatible LLM judge."
    )
    parser.add_argument("--input", required=True, help="Path to locomo10_agent_qa.json")
    parser.add_argument(
        "--output",
        help="Optional output path. Default: overwrite --input in place.",
    )
    parser.add_argument(
        "--stats-output",
        help=(
            "Optional stats json path. Default: sibling locomo10_agent_qa_stats.json "
            "or <output_stem>_stats.json."
        ),
    )
    parser.add_argument(
        "--data-file",
        default="data/locomo/data/locomo10.json",
        help=(
            "LoCoMo source annotation file used to compute project-style category/memory "
            "stats. Default: data/locomo/data/locomo10.json"
        ),
    )
    parser.add_argument(
        "--prediction-field",
        default="memory_agent_prediction_answer",
        help="Primary prediction field used as generated answer.",
    )
    parser.add_argument(
        "--fallback-prediction-field",
        default="memory_agent_prediction",
        help="Fallback field used when the primary prediction field is empty.",
    )
    parser.add_argument(
        "--model-key",
        default="",
        help=(
            "Model key prefix for new fields. Default: derive from prediction field, "
            "for example memory_agent_prediction_answer -> memory_agent."
        ),
    )
    parser.add_argument(
        "--judge-field-prefix",
        default="",
        help=(
            "Prefix for judge fields. Default: <model_key>_llm_judge, "
            "for example memory_agent_llm_judge."
        ),
    )
    parser.add_argument(
        "--exclude-categories",
        nargs="*",
        default=[],
        help="Optional categories to skip, e.g. --exclude-categories 5",
    )
    parser.add_argument(
        "--num-runs",
        type=int,
        default=3,
        help="Number of judge runs per QA. Default: 3.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Maximum concurrent judge requests. Default: 10.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="If set, re-run items that already contain judge scores.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Retries per judge call on transient failures. Default: 2.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("LLM_JUDGE_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini",
        help="Judge model. Default: env LLM_JUDGE_MODEL or gpt-4o-mini.",
    )
    parser.add_argument(
        "--api-key",
        default=(
            os.getenv("LLM_API_KEY")
            or os.getenv("API_SECRET_KEY")
            or os.getenv("OPENAI_API_KEY")
        ),
        help="API key. Default: env LLM_API_KEY or OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("LLM_BASE_URL") or os.getenv("BASE_URL") or "https://api.openai.com/v1",
        help="OpenAI-compatible base URL. Default: env LLM_BASE_URL or OpenAI.",
    )
    parser.add_argument(
        "--prompt-language",
        default=os.getenv("PROMPT_LANGUAGE") or "en",
        help="Prompt language for judge instructions. Default: env PROMPT_LANGUAGE or en.",
    )
    parser.add_argument(
        "--prompt-config",
        default=str(QA_LLM_JUDGE_PROMPT_CONFIG_PATH),
        help="Prompt config path for judge instructions.",
    )
    return parser.parse_args()


def _extract_json(content: str) -> str:
    code_block_match = re.search(
        r"```(?:json)?\s*(\{[^`]*\})\s*```", content, re.DOTALL
    )
    if code_block_match:
        return code_block_match.group(1).strip()

    json_match = re.search(r'\{[^{}]*"label"\s*:\s*"[^"]*"[^{}]*\}', content)
    if json_match:
        return json_match.group(0)

    return content.strip()


def _normalize_category(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _pick_gold_answer(qa_item: dict[str, Any]) -> str:
    for field in ("answer", "adversarial_answer"):
        value = qa_item.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _pick_generated_answer(
    qa_item: dict[str, Any], primary_field: str, fallback_field: str
) -> tuple[str, str]:
    for field in (primary_field, fallback_field):
        value = qa_item.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip(), field
    return "", ""


def _derive_model_key(model_key: str, prediction_field: str, fallback_field: str) -> str:
    if model_key.strip():
        return model_key.strip()

    for field in (prediction_field, fallback_field):
        for suffix in ("_prediction_answer", "_prediction", "_answer"):
            if field.endswith(suffix):
                candidate = field[: -len(suffix)].strip("_")
                if candidate:
                    return candidate
    return "model"


def _default_stats_path(output_path: Path) -> Path:
    if output_path.name == "locomo10_agent_qa.json":
        return output_path.with_name("locomo10_agent_qa_stats.json")
    return output_path.with_name(f"{output_path.stem}_stats.json")


def _to_float(value: Any) -> float | None:
    try:
        num = float(value)
    except Exception:
        return None
    return num if num == num else None


def _safe_parse_evidence(ev: str) -> tuple[int | None, int | None]:
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


def get_conversation_lengths(conversation: dict[str, Any]) -> dict[str, int]:
    total_conv_length = 0
    id2length: dict[str, int] = {}

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


def load_samples(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Top-level JSON must be a list")
    return raw


def _ensure_judge_meta(qa_item: dict[str, Any], judge_prefix: str) -> dict[str, Any]:
    meta = qa_item.get(judge_prefix)
    if not isinstance(meta, dict):
        meta = {}
        qa_item[judge_prefix] = meta
    return meta


def _clear_judge_result(qa_item: dict[str, Any], judge_prefix: str) -> None:
    qa_item[judge_prefix + "_score"] = None
    meta = _ensure_judge_meta(qa_item, judge_prefix)
    meta.clear()


def _mark_skipped(
    qa_item: dict[str, Any],
    judge_prefix: str,
    status: str,
    *,
    question: str,
    gold_answer: str,
    generated_answer: str,
    prediction_field_used: str,
) -> None:
    qa_item[judge_prefix + "_score"] = None
    qa_item[judge_prefix] = {
        "status": status,
        "label": None,
        "correct_count": 0,
        "num_runs": 0,
        "judgments": {},
        "question": question,
        "gold_answer": gold_answer,
        "generated_answer": generated_answer,
        "prediction_field_used": prediction_field_used,
    }


def _mark_evaluated(
    qa_item: dict[str, Any],
    judge_prefix: str,
    judgments: list[bool],
    *,
    question: str,
    gold_answer: str,
    generated_answer: str,
    prediction_field_used: str,
) -> float:
    correct_count = sum(1 for item in judgments if item)
    num_runs = len(judgments)
    score = (correct_count / num_runs) if num_runs else 0.0
    label = "CORRECT" if score >= 0.5 else "WRONG"

    qa_item[judge_prefix + "_score"] = round(score, 6)
    qa_item[judge_prefix] = {
        "status": "evaluated",
        "label": label,
        "correct_count": correct_count,
        "num_runs": num_runs,
        "judgments": {
            f"judgment_{idx + 1}": ("CORRECT" if value else "WRONG")
            for idx, value in enumerate(judgments)
        },
        "question": question,
        "gold_answer": gold_answer,
        "generated_answer": generated_answer,
        "prediction_field_used": prediction_field_used,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    return score


def _mark_error(
    qa_item: dict[str, Any],
    judge_prefix: str,
    error_text: str,
    *,
    question: str,
    gold_answer: str,
    generated_answer: str,
    prediction_field_used: str,
) -> None:
    qa_item[judge_prefix + "_score"] = None
    qa_item[judge_prefix] = {
        "status": "error",
        "label": None,
        "correct_count": 0,
        "num_runs": 0,
        "judgments": {},
        "question": question,
        "gold_answer": gold_answer,
        "generated_answer": generated_answer,
        "prediction_field_used": prediction_field_used,
        "error": error_text,
    }


def build_tasks(
    samples: list[dict[str, Any]],
    primary_field: str,
    fallback_field: str,
    excluded_categories: set[str],
    judge_prefix: str,
    overwrite: bool,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    tasks: list[dict[str, Any]] = []
    stats = {
        "samples": len(samples),
        "total_qa": 0,
        "queued_qa": 0,
        "scored_existing": 0,
        "skipped_missing_gold": 0,
        "skipped_missing_prediction": 0,
        "skipped_excluded_category": 0,
    }
    score_key = judge_prefix + "_score"

    for sample_idx, sample in enumerate(samples):
        sample_id = sample.get("sample_id", f"sample_{sample_idx}")
        qa_items = sample.get("qa", [])
        if not isinstance(qa_items, list):
            continue

        for qa_idx, qa_item in enumerate(qa_items):
            if not isinstance(qa_item, dict):
                continue

            stats["total_qa"] += 1
            question = str(qa_item.get("question", "") or "").strip()
            category = _normalize_category(qa_item.get("category"))

            gold_answer = _pick_gold_answer(qa_item)
            generated_answer, prediction_field_used = _pick_generated_answer(
                qa_item,
                primary_field,
                fallback_field,
            )

            if category in excluded_categories:
                stats["skipped_excluded_category"] += 1
                _mark_skipped(
                    qa_item,
                    judge_prefix,
                    "skipped_excluded_category",
                    question=question,
                    gold_answer=gold_answer,
                    generated_answer=generated_answer,
                    prediction_field_used=prediction_field_used,
                )
                continue

            if not gold_answer:
                stats["skipped_missing_gold"] += 1
                _mark_skipped(
                    qa_item,
                    judge_prefix,
                    "skipped_missing_gold",
                    question=question,
                    gold_answer=gold_answer,
                    generated_answer=generated_answer,
                    prediction_field_used=prediction_field_used,
                )
                continue

            if not generated_answer:
                stats["skipped_missing_prediction"] += 1
                _mark_skipped(
                    qa_item,
                    judge_prefix,
                    "skipped_missing_prediction",
                    question=question,
                    gold_answer=gold_answer,
                    generated_answer=generated_answer,
                    prediction_field_used=prediction_field_used,
                )
                continue

            existing_score = _to_float(qa_item.get(score_key))
            existing_meta = qa_item.get(judge_prefix)
            existing_status = (
                existing_meta.get("status")
                if isinstance(existing_meta, dict)
                else None
            )
            if (
                not overwrite
                and existing_score is not None
                and existing_status == "evaluated"
            ):
                stats["scored_existing"] += 1
                continue

            _clear_judge_result(qa_item, judge_prefix)
            tasks.append(
                {
                    "sample_id": str(sample_id),
                    "qa_index": qa_idx,
                    "question": question,
                    "gold_answer": gold_answer,
                    "generated_answer": generated_answer,
                    "prediction_field_used": prediction_field_used,
                    "qa_ref": qa_item,
                }
            )
            stats["queued_qa"] += 1

    return tasks, stats


async def judge_once(
    llm: Any,
    question: str,
    gold_answer: str,
    generated_answer: str,
    max_retries: int,
    user_prompt_template: str,
) -> bool:
    user_prompt = user_prompt_template.format(
        question=question,
        gold_answer=gold_answer,
        generated_answer=generated_answer,
    )

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            content = await asyncio.to_thread(llm, user_prompt)
            payload = json.loads(_extract_json(content))
            return str(payload.get("label", "")).strip().upper() == "CORRECT"
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            await asyncio.sleep(min(2**attempt, 5))

    assert last_error is not None
    raise last_error


async def evaluate_tasks(
    tasks: list[dict[str, Any]],
    llm: Any,
    num_runs: int,
    concurrency: int,
    max_retries: int,
    user_prompt_template: str,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(concurrency)
    completed = 0

    async def evaluate_single(item: dict[str, Any]) -> dict[str, Any]:
        nonlocal completed
        async with semaphore:
            try:
                judgments = await asyncio.gather(
                    *[
                        judge_once(
                            llm=llm,
                            question=item["question"],
                            gold_answer=item["gold_answer"],
                            generated_answer=item["generated_answer"],
                            max_retries=max_retries,
                            user_prompt_template=user_prompt_template,
                        )
                        for _ in range(num_runs)
                    ]
                )
                result: dict[str, Any] = {
                    "sample_id": item["sample_id"],
                    "qa_index": item["qa_index"],
                    "judgments": judgments,
                    "error": None,
                    "task": item,
                }
            except Exception as exc:
                result = {
                    "sample_id": item["sample_id"],
                    "qa_index": item["qa_index"],
                    "judgments": [],
                    "error": str(exc),
                    "task": item,
                }

            completed += 1
            print(f"\rEvaluated {completed}/{len(tasks)}", end="", flush=True)
            return result

    results = await asyncio.gather(*(evaluate_single(item) for item in tasks))
    if tasks:
        print()
    return results


def summarize_metric_by_category(
    out_samples: list[dict[str, Any]], metric_key: str
) -> dict[str, Any]:
    total_counts = defaultdict(float)
    metric_sums = defaultdict(float)

    for sample in out_samples:
        for qa in sample.get("qa", []):
            if not isinstance(qa, dict):
                continue
            score = _to_float(qa.get(metric_key))
            if score is None:
                continue
            category = int(qa.get("category", -1))
            total_counts[category] += 1
            metric_sums[category] += score

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
    return {
        "category_counts": {str(k): float(v) for k, v in total_counts.items()},
        "cum_accuracy_by_category": {str(k): float(v) for k, v in metric_sums.items()},
        "summary_by_category": summary_by_cat,
        "overall_accuracy": round(overall, 6),
    }


def analyze_aggr_acc_locomo(
    ann_samples: list[dict[str, Any]],
    out_samples: list[dict[str, Any]],
    metric_key: str,
) -> dict[str, Any]:
    basic = summarize_metric_by_category(out_samples, metric_key)
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
        id2length = get_conversation_lengths(
            conversation if isinstance(conversation, dict) else {}
        )

        for qa in output.get("qa", []):
            if not isinstance(qa, dict):
                continue
            score = _to_float(qa.get(metric_key))
            if score is None:
                continue

            category = int(qa.get("category", -1))
            total_counts[category] += 1
            acc_counts[category] += score

            evidence_raw = qa.get("evidence", [])
            if not isinstance(evidence_raw, list) or not evidence_raw:
                continue
            evidence = [
                str(x).replace("(", "").replace(")", "")
                for x in evidence_raw
                if str(x).strip()
            ]
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

            memory_bin = int((farthest_length + 999) // 1000)
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
                context_bin = int((context_length + 999) // 1000)
                context_len_og[context_bin] += 1
                context_len_counts[context_bin] += score

    basic["category_counts_by_memory"] = {
        str(cat): {str(bin_key): float(value) for bin_key, value in bins.items()}
        for cat, bins in memory_counts_og.items()
    }
    basic["cum_accuracy_by_category_by_memory"] = {
        str(cat): {str(bin_key): float(value) for bin_key, value in bins.items()}
        for cat, bins in memory_counts.items()
    }
    basic["context_length_counts"] = {
        str(bin_key): float(value) for bin_key, value in context_len_og.items()
    }
    basic["cum_accuracy_by_context_length"] = {
        str(bin_key): float(value) for bin_key, value in context_len_counts.items()
    }
    return basic


def update_stats_file(
    stats_path: Path,
    out_samples: list[dict[str, Any]],
    stats_model_key: str,
    metric_key: str,
    metadata: dict[str, Any],
    data_file: str,
) -> None:
    ann_samples: list[dict[str, Any]] | None = None
    data_path = Path(data_file)
    if data_path.exists():
        try:
            loaded = json.loads(data_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                ann_samples = loaded
        except Exception:
            ann_samples = None

    if ann_samples is not None:
        model_stats = analyze_aggr_acc_locomo(ann_samples, out_samples, metric_key)
    else:
        model_stats = summarize_metric_by_category(out_samples, metric_key)
        model_stats["category_counts_by_memory"] = {}
        model_stats["cum_accuracy_by_category_by_memory"] = {}
        model_stats["context_length_counts"] = {}
        model_stats["cum_accuracy_by_context_length"] = {}

    model_stats["metadata"] = metadata

    if stats_path.exists():
        try:
            stats_obj = json.loads(stats_path.read_text(encoding="utf-8"))
            if not isinstance(stats_obj, dict):
                stats_obj = {}
        except Exception:
            stats_obj = {}
    else:
        stats_obj = {}

    stats_obj[stats_model_key] = model_stats
    stats_path.write_text(
        json.dumps(stats_obj, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


async def main() -> None:
    args = parse_args()
    prompt_language = normalize_prompt_language(args.prompt_language)
    system_prompt, user_prompt_template = load_judge_prompts(
        args.prompt_config,
        prompt_language,
    )

    input_path = resolve_project_path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    output_path = resolve_project_path(args.output) if args.output else input_path
    stats_output_path = (
        resolve_project_path(args.stats_output)
        if args.stats_output
        else _default_stats_path(output_path)
    )

    model_key = _derive_model_key(
        args.model_key,
        args.prediction_field,
        args.fallback_prediction_field,
    )
    judge_prefix = (
        args.judge_field_prefix.strip()
        if args.judge_field_prefix.strip()
        else f"{model_key}_llm_judge"
    )
    metric_key = judge_prefix + "_score"
    excluded_categories = {str(item) for item in args.exclude_categories}

    samples = load_samples(input_path)
    tasks, prep_stats = build_tasks(
        samples=samples,
        primary_field=args.prediction_field,
        fallback_field=args.fallback_prediction_field,
        excluded_categories=excluded_categories,
        judge_prefix=judge_prefix,
        overwrite=args.overwrite,
    )

    print("Input:", input_path)
    print("Output:", output_path)
    print("Stats:", stats_output_path)
    print("Model:", args.model)
    print("Judge field prefix:", judge_prefix)
    print("Prompt language:", prompt_language)
    print("Prompt config:", resolve_project_path(args.prompt_config))
    print("Questions loaded:", prep_stats["total_qa"])
    print("Questions queued:", prep_stats["queued_qa"])
    print("Questions reused:", prep_stats["scored_existing"])
    print("Skipped excluded category:", prep_stats["skipped_excluded_category"])
    print("Skipped missing gold:", prep_stats["skipped_missing_gold"])
    print("Skipped missing prediction:", prep_stats["skipped_missing_prediction"])

    failed_count = 0
    evaluated_new = 0
    if tasks:
        if not args.api_key:
            raise SystemExit(
                "Missing API key. Set --api-key or env LLM_API_KEY / OPENAI_API_KEY."
            )

        try:
            from m_agent.load_model.OpenAIcall import get_chat_llm
        except ModuleNotFoundError as exc:
            raise SystemExit(
                "Missing dependency for load_model.OpenAIcall. Install the project requirements first."
            ) from exc

        llm = get_chat_llm(
            model_temperature=0.0,
            model_name=args.model,
            api_key_override=args.api_key,
            base_url_override=args.base_url,
            system_prompt=system_prompt,
            max_tokens=512,
        )
        results = await evaluate_tasks(
            tasks=tasks,
            llm=llm,
            num_runs=args.num_runs,
            concurrency=args.concurrency,
            max_retries=args.max_retries,
            user_prompt_template=user_prompt_template,
        )
        for result in results:
            qa_item = result["task"]["qa_ref"]
            if result["error"]:
                failed_count += 1
                _mark_error(
                    qa_item,
                    judge_prefix,
                    result["error"],
                    question=result["task"]["question"],
                    gold_answer=result["task"]["gold_answer"],
                    generated_answer=result["task"]["generated_answer"],
                    prediction_field_used=result["task"]["prediction_field_used"],
                )
                continue

            evaluated_new += 1
            _mark_evaluated(
                qa_item,
                judge_prefix,
                result["judgments"],
                question=result["task"]["question"],
                gold_answer=result["task"]["gold_answer"],
                generated_answer=result["task"]["generated_answer"],
                prediction_field_used=result["task"]["prediction_field_used"],
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(samples, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    metadata = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "score_field": metric_key,
        "judge_field_prefix": judge_prefix,
        "model_key": model_key,
        "prediction_field": args.prediction_field,
        "fallback_prediction_field": args.fallback_prediction_field,
        "excluded_categories": sorted(excluded_categories),
        "num_runs": args.num_runs,
        "judge_model": args.model,
        "judge_base_url": args.base_url,
        "prompt_language": prompt_language,
        "prompt_config": str(resolve_project_path(args.prompt_config)),
        "queued_qa": prep_stats["queued_qa"],
        "evaluated_new": evaluated_new,
        "failed_qa": failed_count,
        "scored_existing": prep_stats["scored_existing"],
        "skipped_excluded_category": prep_stats["skipped_excluded_category"],
        "skipped_missing_gold": prep_stats["skipped_missing_gold"],
        "skipped_missing_prediction": prep_stats["skipped_missing_prediction"],
        "total_qa": prep_stats["total_qa"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_file": str(resolve_project_path(args.data_file)),
    }
    update_stats_file(
        stats_path=stats_output_path,
        out_samples=samples,
        stats_model_key=judge_prefix,
        metric_key=metric_key,
        metadata=metadata,
        data_file=str(resolve_project_path(args.data_file)),
    )

    summary = summarize_metric_by_category(samples, metric_key)
    print("Saved QA file:", output_path)
    print("Saved stats file:", stats_output_path)
    print(
        "Overall LLM judge accuracy:",
        f"{summary['overall_accuracy']:.4f}",
        f"({summary['overall_accuracy'] * 100:.2f}%)",
    )
    print("Evaluated new questions:", evaluated_new)
    print("Failed questions:", failed_count)


if __name__ == "__main__":
    asyncio.run(main())

