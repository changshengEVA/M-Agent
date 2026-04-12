#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Grid-search hybrid retrieval parameters for LoCoMo eval.

This script:
1) reads a base MemoryAgent config;
2) generates temporary MemoryCore/Agent configs for each parameter combination;
3) runs `scripts/run_locomo/run_eval_locomo.py` sequentially;
4) writes a sweep summary (jsonl/json/csv) under log/<test-id-prefix>/_sweep/.
"""

from __future__ import annotations

import argparse
import copy
import csv
import itertools
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml

from _bootstrap import bootstrap_project


bootstrap_project()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_project_path(raw: str) -> Path:
    candidate = Path(str(raw).strip())
    if candidate.is_absolute():
        return candidate.resolve()
    return (_project_root() / candidate).resolve()


def _sanitize_name(value: str, default: str = "default") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or default


def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"YAML must be a mapping: {path}")
    return payload


def _write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def _parse_list(raw: str, cast, label: str) -> List[Any]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError(f"{label} cannot be empty.")
    values: List[Any] = []
    for part in text.split(","):
        piece = part.strip()
        if not piece:
            continue
        try:
            values.append(cast(piece))
        except Exception as exc:
            raise ValueError(f"Invalid value in {label}: {piece!r}") from exc
    if not values:
        raise ValueError(f"{label} cannot be empty.")
    return values


def _resolve_related_path(base_cfg_path: Path, raw_value: Any) -> Path:
    raw = str(raw_value or "").strip()
    if not raw:
        raise ValueError(f"Empty related path for base config: {base_cfg_path}")
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_cfg_path.parent / candidate).resolve()


def _parse_list_with_default(
    raw: str,
    cast,
    label: str,
    default_value: Any,
) -> List[Any]:
    text = str(raw or "").strip()
    if text:
        return _parse_list(text, cast, label)
    try:
        return [cast(default_value)]
    except Exception as exc:
        raise ValueError(f"Invalid default value for {label}: {default_value!r}") from exc


def _fmt_num(value: float) -> str:
    text = f"{value:g}"
    return text.replace("-", "m").replace(".", "p")


def _resolve_memory_core_from_agent(
    agent_cfg_path: Path,
    agent_cfg: Dict[str, Any],
    override_core_path: str,
) -> Path:
    if override_core_path:
        return _resolve_project_path(override_core_path)

    raw = str(agent_cfg.get("memory_core_config_path", "") or "").strip()
    if not raw:
        raise ValueError(
            f"`memory_core_config_path` missing in agent config: {agent_cfg_path}"
        )
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.resolve()
    return (agent_cfg_path.parent / candidate).resolve()


def _build_combinations(
    detail_topk: Iterable[int],
    dense_recall_topn: Iterable[int],
    sparse_recall_topn: Iterable[int],
    rrf_k: Iterable[int],
    dense_weight: Iterable[float],
    sparse_weight_raw: List[float],
    bm25_k1: Iterable[float],
    bm25_b: Iterable[float],
) -> List[Dict[str, Any]]:
    combos: List[Dict[str, Any]] = []
    for topk, d_topn, s_topn, k_rrf, w_dense, k1, b in itertools.product(
        detail_topk,
        dense_recall_topn,
        sparse_recall_topn,
        rrf_k,
        dense_weight,
        bm25_k1,
        bm25_b,
    ):
        sparse_candidates = (
            sparse_weight_raw if sparse_weight_raw else [max(0.0, 1.0 - float(w_dense))]
        )
        for w_sparse in sparse_candidates:
            combos.append(
                {
                    "detail_topk": int(topk),
                    "dense_recall_topn": int(d_topn),
                    "sparse_recall_topn": int(s_topn),
                    "rrf_k": int(k_rrf),
                    "dense_weight": float(w_dense),
                    "sparse_weight": float(w_sparse),
                    "bm25_k1": float(k1),
                    "bm25_b": float(b),
                }
            )
    return combos


def _extract_metrics(stats_path: Path, model_key: str) -> Dict[str, Any]:
    if not stats_path.exists():
        return {}
    try:
        payload = json.loads(stats_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}

    model_stats = payload.get(model_key, {})
    judge_stats = payload.get(model_key + "_llm_judge", {})
    if not isinstance(model_stats, dict):
        model_stats = {}
    if not isinstance(judge_stats, dict):
        judge_stats = {}

    return {
        "overall_accuracy": model_stats.get("overall_accuracy"),
        "overall_b1": model_stats.get("overall_b1"),
        "overall_recall": model_stats.get("overall_recall"),
        "judge_overall_accuracy": judge_stats.get("overall_accuracy"),
    }


def _build_test_id(prefix: str, idx: int, params: Dict[str, Any]) -> str:
    return _sanitize_name(
        (
            f"{prefix}_{idx:03d}"
            f"_t{params['detail_topk']}"
            f"_d{params['dense_recall_topn']}"
            f"_s{params['sparse_recall_topn']}"
            f"_k{params['rrf_k']}"
            f"_dw{_fmt_num(params['dense_weight'])}"
            f"_sw{_fmt_num(params['sparse_weight'])}"
            f"_k1{_fmt_num(params['bm25_k1'])}"
            f"_b{_fmt_num(params['bm25_b'])}"
        )
    )


def _run_eval(
    cmd: List[str],
    cwd: Path,
    dry_run: bool,
) -> Tuple[int, float]:
    start = time.time()
    if dry_run:
        print("[DRY-RUN]", " ".join(cmd))
        return 0, 0.0
    completed = subprocess.run(cmd, cwd=str(cwd), check=False)
    return int(completed.returncode), time.time() - start


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sweep hybrid retrieval params and run scripts/run_locomo/run_eval_locomo.py continuously."
    )
    parser.add_argument(
        "--agent-config",
        type=str,
        default="config/agents/memory/locomo_eval_memory_agent.yaml",
        help="Base MemoryAgent yaml.",
    )
    parser.add_argument(
        "--memory-core-config",
        type=str,
        default="",
        help="Optional override for MemoryCore yaml; default reads from --agent-config.",
    )
    parser.add_argument(
        "--data-file",
        type=str,
        default="data/locomo/data/locomo10.json",
        help="LoCoMo data json path.",
    )
    parser.add_argument(
        "--question-config",
        type=str,
        default="",
        help="Optional fixed QA selection yaml.",
    )
    parser.add_argument(
        "--test-id-prefix",
        type=str,
        default="hybrid_sweep",
        help="Prefix used to build per-run --test-id.",
    )
    parser.add_argument(
        "--model-key",
        type=str,
        default="memory_agent",
        help="Metric model key used by run_eval_locomo.",
    )
    parser.add_argument(
        "--thread-id-prefix",
        type=str,
        default="locomo-sweep",
        help="Thread-id prefix passed to run_eval_locomo.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Pass --overwrite to run_eval_locomo.")
    parser.add_argument("--sample-fraction", type=float, default=0.1, help="Pass-through eval arg.")
    parser.add_argument("--sample-seed", type=int, default=42, help="Pass-through eval arg.")
    parser.add_argument("--max-samples", type=int, default=0, help="Pass-through eval arg.")
    parser.add_argument("--max-questions", type=int, default=0, help="Pass-through eval arg.")
    parser.add_argument("--save-every", type=int, default=1, help="Pass-through eval arg.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Pass-through eval arg.")

    parser.add_argument("--dense-recall-topn", type=str, default="30")
    parser.add_argument("--sparse-recall-topn", type=str, default="30")
    parser.add_argument(
        "--rrf-k",
        type=str,
        default="",
        help="Comma-list. Empty means using value from base memory-core config.",
    )
    parser.add_argument("--detail-topk", type=str, default="", help="Comma-list. Empty means using base agent topk.")
    parser.add_argument("--dense-weight", type=str, default="0.5")
    parser.add_argument(
        "--sparse-weight",
        type=str,
        default="",
        help="Optional comma-list. If empty, uses (1 - dense_weight).",
    )
    parser.add_argument(
        "--bm25-k1",
        type=str,
        default="",
        help="Comma-list. Empty means using value from base memory-core config.",
    )
    parser.add_argument(
        "--bm25-b",
        type=str,
        default="",
        help="Comma-list. Empty means using value from base memory-core config.",
    )

    parser.add_argument("--max-runs", type=int, default=0, help="Optional cap on number of combinations.")
    parser.add_argument(
        "--sleep-between-runs",
        type=float,
        default=0.0,
        help="Sleep seconds between finished runs.",
    )
    parser.add_argument("--skip-existing", action="store_true", help="Skip runs whose stats already exist.")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop sweep on first non-zero exit.")
    parser.add_argument("--dry-run", action="store_true", help="Only print commands/configs.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = _project_root()
    log_root = (project_root / "log").resolve()
    safe_prefix = _sanitize_name(args.test_id_prefix, default="hybrid_sweep")
    sweep_root = log_root / safe_prefix / "_sweep"
    cfg_root = sweep_root / "configs"
    sweep_root.mkdir(parents=True, exist_ok=True)
    cfg_root.mkdir(parents=True, exist_ok=True)

    agent_cfg_path = _resolve_project_path(args.agent_config)
    data_file_path = _resolve_project_path(args.data_file)
    question_config_path = _resolve_project_path(args.question_config) if args.question_config else None

    base_agent_cfg = _load_yaml(agent_cfg_path)
    base_core_cfg_path = _resolve_memory_core_from_agent(
        agent_cfg_path=agent_cfg_path,
        agent_cfg=base_agent_cfg,
        override_core_path=args.memory_core_config,
    )
    base_core_cfg = _load_yaml(base_core_cfg_path)
    base_hybrid = base_core_cfg.get("detail_search_hybrid")
    if not isinstance(base_hybrid, dict):
        base_hybrid = {}
    base_detail_defaults = base_agent_cfg.get("detail_search_defaults")
    if not isinstance(base_detail_defaults, dict):
        base_detail_defaults = {}

    dense_recall_topn = _parse_list_with_default(
        args.dense_recall_topn,
        int,
        "--dense-recall-topn",
        base_hybrid.get("dense_recall_topn", 30),
    )
    sparse_recall_topn = _parse_list_with_default(
        args.sparse_recall_topn,
        int,
        "--sparse-recall-topn",
        base_hybrid.get("sparse_recall_topn", 30),
    )
    rrf_k_values = _parse_list_with_default(
        args.rrf_k,
        int,
        "--rrf-k",
        base_hybrid.get("rrf_k", 60),
    )
    detail_topk_values = _parse_list_with_default(
        args.detail_topk,
        int,
        "--detail-topk",
        base_detail_defaults.get("topk", 5),
    )
    dense_weights = _parse_list(args.dense_weight, float, "--dense-weight")
    sparse_weights = _parse_list(args.sparse_weight, float, "--sparse-weight") if args.sparse_weight else []
    bm25_k1_values = _parse_list_with_default(
        args.bm25_k1,
        float,
        "--bm25-k1",
        base_hybrid.get("bm25_k1", 1.5),
    )
    bm25_b_values = _parse_list_with_default(
        args.bm25_b,
        float,
        "--bm25-b",
        base_hybrid.get("bm25_b", 0.75),
    )

    if any(v <= 0 for v in detail_topk_values):
        raise ValueError("--detail-topk values must be > 0.")

    combos = _build_combinations(
        detail_topk=detail_topk_values,
        dense_recall_topn=dense_recall_topn,
        sparse_recall_topn=sparse_recall_topn,
        rrf_k=rrf_k_values,
        dense_weight=dense_weights,
        sparse_weight_raw=sparse_weights,
        bm25_k1=bm25_k1_values,
        bm25_b=bm25_b_values,
    )
    if args.max_runs > 0:
        combos = combos[: args.max_runs]

    if not combos:
        raise ValueError("No parameter combinations generated.")

    print(f"Sweep combos: {len(combos)}")
    print(f"Base agent config: {agent_cfg_path}")
    print(f"Base memory core config: {base_core_cfg_path}")
    print(f"Sweep output dir: {sweep_root}")

    summary_rows: List[Dict[str, Any]] = []
    summary_jsonl = sweep_root / "sweep_results.jsonl"
    summary_json = sweep_root / "sweep_results.json"
    summary_csv = sweep_root / "sweep_results.csv"

    for idx, params in enumerate(combos, start=1):
        test_id = _build_test_id(safe_prefix, idx, params)
        run_log_dir = log_root / test_id
        stats_path = run_log_dir / "locomo10_agent_qa_stats.json"

        run_record: Dict[str, Any] = {
            "index": idx,
            "test_id": test_id,
            "params": params,
            "status": "pending",
            "exit_code": None,
            "elapsed_sec": None,
            "stats_path": str(stats_path),
        }

        if args.skip_existing and stats_path.exists():
            run_record["status"] = "skipped_existing"
            run_record["metrics"] = _extract_metrics(stats_path, args.model_key)
            summary_rows.append(run_record)
            with open(summary_jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(run_record, ensure_ascii=False) + "\n")
            print(f"[{idx}/{len(combos)}] skip existing: {test_id}")
            continue

        run_core_cfg = copy.deepcopy(base_core_cfg)
        hybrid_params = {k: v for k, v in params.items() if k != "detail_topk"}
        run_core_cfg["detail_search_hybrid"] = hybrid_params
        core_runtime_raw = run_core_cfg.get("runtime_prompt_config_path")
        if isinstance(core_runtime_raw, str) and core_runtime_raw.strip():
            run_core_cfg["runtime_prompt_config_path"] = str(
                _resolve_related_path(base_core_cfg_path, core_runtime_raw)
            )
        core_base_raw = run_core_cfg.get("base_config_path")
        if isinstance(core_base_raw, str) and core_base_raw.strip():
            run_core_cfg["base_config_path"] = str(
                _resolve_related_path(base_core_cfg_path, core_base_raw)
            )
        run_core_cfg_path = cfg_root / f"{test_id}.memory_core.yaml"
        _write_yaml(run_core_cfg_path, run_core_cfg)

        run_agent_cfg = copy.deepcopy(base_agent_cfg)
        run_agent_cfg["memory_core_config_path"] = str(run_core_cfg_path)
        agent_runtime_raw = run_agent_cfg.get("runtime_prompt_config_path")
        if isinstance(agent_runtime_raw, str) and agent_runtime_raw.strip():
            run_agent_cfg["runtime_prompt_config_path"] = str(
                _resolve_related_path(agent_cfg_path, agent_runtime_raw)
            )
        agent_base_raw = run_agent_cfg.get("base_config_path")
        if isinstance(agent_base_raw, str) and agent_base_raw.strip():
            run_agent_cfg["base_config_path"] = str(
                _resolve_related_path(agent_cfg_path, agent_base_raw)
            )
        detail_defaults = run_agent_cfg.get("detail_search_defaults")
        if not isinstance(detail_defaults, dict):
            detail_defaults = {}
        detail_defaults["topk"] = int(params["detail_topk"])
        run_agent_cfg["detail_search_defaults"] = detail_defaults
        run_agent_cfg_path = cfg_root / f"{test_id}.agent.yaml"
        _write_yaml(run_agent_cfg_path, run_agent_cfg)

        cmd = [
            sys.executable,
            str((project_root / "scripts" / "run_locomo" / "run_eval_locomo.py").resolve()),
            "--config",
            str(run_agent_cfg_path),
            "--data-file",
            str(data_file_path),
            "--test-id",
            test_id,
            "--model-key",
            args.model_key,
            "--thread-id-prefix",
            f"{args.thread_id_prefix}:{idx:03d}",
            "--sample-fraction",
            str(args.sample_fraction),
            "--sample-seed",
            str(args.sample_seed),
            "--save-every",
            str(args.save_every),
            "--sleep-seconds",
            str(args.sleep_seconds),
        ]
        if question_config_path is not None:
            cmd.extend(["--question-config", str(question_config_path)])
        if args.max_samples > 0:
            cmd.extend(["--max-samples", str(args.max_samples)])
        if args.max_questions > 0:
            cmd.extend(["--max-questions", str(args.max_questions)])
        if args.overwrite:
            cmd.append("--overwrite")

        print(f"[{idx}/{len(combos)}] running: {test_id}")
        exit_code, elapsed_sec = _run_eval(
            cmd=cmd,
            cwd=project_root,
            dry_run=args.dry_run,
        )

        run_record["exit_code"] = exit_code
        run_record["elapsed_sec"] = round(elapsed_sec, 3)
        if exit_code == 0:
            run_record["status"] = "ok"
        else:
            run_record["status"] = "failed"
        run_record["metrics"] = _extract_metrics(stats_path, args.model_key)

        summary_rows.append(run_record)
        with open(summary_jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(run_record, ensure_ascii=False) + "\n")

        if exit_code != 0 and args.stop_on_error:
            print(f"Stop on error at run {idx}: {test_id}")
            break
        if args.sleep_between_runs > 0:
            time.sleep(args.sleep_between_runs)

    with open(summary_json, "w", encoding="utf-8") as f:
        json.dump(summary_rows, f, ensure_ascii=False, indent=2)

    csv_fields = [
        "index",
        "test_id",
        "status",
        "exit_code",
        "elapsed_sec",
        "detail_topk",
        "dense_recall_topn",
        "sparse_recall_topn",
        "rrf_k",
        "dense_weight",
        "sparse_weight",
        "bm25_k1",
        "bm25_b",
        "overall_accuracy",
        "overall_b1",
        "overall_recall",
        "judge_overall_accuracy",
    ]
    with open(summary_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        for row in summary_rows:
            params = row.get("params", {}) if isinstance(row.get("params"), dict) else {}
            metrics = row.get("metrics", {}) if isinstance(row.get("metrics"), dict) else {}
            writer.writerow(
                {
                    "index": row.get("index"),
                    "test_id": row.get("test_id"),
                    "status": row.get("status"),
                    "exit_code": row.get("exit_code"),
                    "elapsed_sec": row.get("elapsed_sec"),
                    "detail_topk": params.get("detail_topk"),
                    "dense_recall_topn": params.get("dense_recall_topn"),
                    "sparse_recall_topn": params.get("sparse_recall_topn"),
                    "rrf_k": params.get("rrf_k"),
                    "dense_weight": params.get("dense_weight"),
                    "sparse_weight": params.get("sparse_weight"),
                    "bm25_k1": params.get("bm25_k1"),
                    "bm25_b": params.get("bm25_b"),
                    "overall_accuracy": metrics.get("overall_accuracy"),
                    "overall_b1": metrics.get("overall_b1"),
                    "overall_recall": metrics.get("overall_recall"),
                    "judge_overall_accuracy": metrics.get("judge_overall_accuracy"),
                }
            )

    print(f"Saved summary: {summary_jsonl}")
    print(f"Saved summary: {summary_json}")
    print(f"Saved summary: {summary_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
