#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run LongMemEval eval via env config (delegates to run_eval_longmemeval.py)."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

THIS = Path(__file__).resolve()
PROJECT_ROOT = THIS.parents[2]
RUN_LOCOMO = PROJECT_ROOT / "scripts" / "run_locomo"
if str(RUN_LOCOMO) not in sys.path:
    sys.path.insert(0, str(RUN_LOCOMO))

from _bootstrap import bootstrap_project

bootstrap_project()

from _shared import (
    DEFAULT_LONGMEMEVAL_ENV_CONFIG_PATH,
    _safe_memory_path_segment,
    get_data_config,
    load_env_config,
    log_env_config_summary,
    parse_question_ids,
    resolve_longmemeval_memory_id,
    resolve_project_path,
)
from m_agent.paths import LOG_DIR

from sample_longmemeval import parse_sampling_config, sample_question_ids

logger = logging.getLogger("run_longmemeval.eval")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LongMemEval eval: write hypothesis jsonl for official evaluate_qa.py.")
    p.add_argument(
        "--env-config",
        type=str,
        default=DEFAULT_LONGMEMEVAL_ENV_CONFIG_PATH,
        help="YAML env config.",
    )
    p.add_argument(
        "--workflow-id",
        type=str,
        default="",
        help="Override MemoryCore workflow_id (must match import process_id).",
    )
    p.add_argument(
        "--test-id",
        type=str,
        default="",
        help="Override eval.test_id.",
    )
    p.add_argument(
        "--question-ids",
        type=str,
        default="",
        help="Override selection.question_ids (comma-separated).",
    )
    p.add_argument(
        "--no-sampling",
        action="store_true",
        help="Ignore selection.sampling (and legacy eval.sampling); use --question-ids / selection.question_ids only (pipeline per question).",
    )
    p.add_argument(
        "--append",
        action="store_true",
        help="Append to hypothesis jsonl (same test_id as a prior run_eval_longmemeval).",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Pass --overwrite to run_eval_longmemeval (truncate/create; overrides YAML eval.overwrite when set).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print subprocess command only.",
    )
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    payload, config_path = load_env_config(args.env_config)
    data_cfg = get_data_config(payload)
    eval_cfg = payload.get("eval", {})
    if not isinstance(eval_cfg, dict):
        eval_cfg = {}

    data_file = resolve_project_path(data_cfg["file"])
    memory_agent_config = str(
        eval_cfg.get("memory_agent_config", "config/agents/memory/longmemeval_eval_memory_agent.yaml")
        or "config/agents/memory/longmemeval_eval_memory_agent.yaml"
    ).strip()
    agent_config_path = resolve_project_path(memory_agent_config)

    test_id = str(args.test_id or "").strip()
    if not test_id:
        test_id = str(eval_cfg.get("test_id", "longmemeval_run") or "").strip() or "longmemeval_run"

    thread_id_prefix = str(eval_cfg.get("thread_id_prefix", "longmemeval-eval") or "longmemeval-eval").strip()
    overwrite = bool(eval_cfg.get("overwrite", False))
    max_questions = int(eval_cfg.get("max_questions", 0))
    sleep_seconds = float(eval_cfg.get("sleep_seconds", 0.0))
    hypothesis_jsonl = str(eval_cfg.get("hypothesis_jsonl", "longmemeval_hypothesis.jsonl") or "").strip() or (
        "longmemeval_hypothesis.jsonl"
    )

    qids_arg = str(args.question_ids or "").strip()
    selection_ids = (
        [x.strip() for x in qids_arg.split(",") if x.strip()]
        if qids_arg
        else parse_question_ids(payload)
    )

    sampled_ids: list[str] = []
    sampling_cfg = None
    if args.no_sampling:
        if not selection_ids:
            logger.error(
                "With --no-sampling, provide --question-ids or selection.question_ids in the env YAML."
            )
            return 2
    elif selection_ids:
        pass
    else:
        sampling_cfg = parse_sampling_config(payload)
        if sampling_cfg is None:
            logger.error(
                "No selection.question_ids and no selection.sampling (per_question_type). "
                "Legacy eval.sampling is used only when selection has no sampling key."
            )
            return 2
        sampled_ids = sample_question_ids(
            data_path=data_file,
            selection_question_ids=[],
            sampling_cfg=sampling_cfg,
        )
        if not sampled_ids:
            logger.error(
                "selection.sampling produced no question_ids (check data file and per_question_type counts)."
            )
            return 2

    run_eval_script = (PROJECT_ROOT / "scripts" / "run_longmemeval" / "run_eval_longmemeval.py").resolve()

    if not data_file.exists():
        raise FileNotFoundError(f"data file not found: {data_file}")
    if not agent_config_path.exists():
        raise FileNotFoundError(f"MemoryAgent config not found: {agent_config_path}")
    if not run_eval_script.exists():
        raise FileNotFoundError(f"Script not found: {run_eval_script}")

    json_stem = _safe_memory_path_segment(data_file.stem)
    cli_wf = str(args.workflow_id or "").strip()
    append_cli = bool(args.append)
    overwrite_cli = bool(args.overwrite)
    overwrite_effective = overwrite or overwrite_cli

    def build_cmd(*, question_ids_csv: str, workflow_id: str, extra_flags: list[str]) -> list[str]:
        cmd = [
            sys.executable,
            str(run_eval_script),
            "--data-file",
            str(data_file),
            "--config",
            str(agent_config_path),
            "--test-id",
            test_id,
            "--thread-id-prefix",
            thread_id_prefix,
            "--hypothesis-jsonl",
            hypothesis_jsonl,
            "--max-questions",
            str(max_questions),
            "--sleep-seconds",
            str(sleep_seconds),
        ]
        if question_ids_csv:
            cmd.extend(["--question-ids", question_ids_csv])
        cmd.extend(["--workflow-id", workflow_id])
        cmd.extend(extra_flags)
        return cmd

    runs: list[tuple[str, str, list[str]]] = []

    if sampling_cfg is not None and len(sampled_ids) > 1:
        if cli_wf:
            logger.warning(
                "Ignoring --workflow-id while evaluating %d sampled question_ids; "
                "using longmemeval/<data_stem>/<question_id> per question.",
                len(sampled_ids),
            )
        for i, qid in enumerate(sampled_ids):
            wf = f"longmemeval/{json_stem}/{_safe_memory_path_segment(qid)}"
            flags: list[str] = []
            if i == 0:
                flags.append("--overwrite")
            else:
                flags.append("--append")
            cmd = build_cmd(question_ids_csv=qid, workflow_id=wf, extra_flags=flags)
            runs.append((f"sampled {i + 1}/{len(sampled_ids)} qid={qid}", wf, cmd))
    elif sampling_cfg is not None and len(sampled_ids) == 1:
        qid = sampled_ids[0]
        workflow_id = cli_wf if cli_wf else f"longmemeval/{json_stem}/{_safe_memory_path_segment(qid)}"
        flags: list[str] = []
        if append_cli:
            flags.append("--append")
        elif overwrite_effective:
            flags.append("--overwrite")
        cmd = build_cmd(question_ids_csv=qid, workflow_id=workflow_id, extra_flags=flags)
        runs.append(("single sampled question", workflow_id, cmd))
    else:
        qids_csv = qids_arg if qids_arg else ",".join(selection_ids)
        try:
            workflow_id = resolve_longmemeval_memory_id(payload, args.workflow_id)
        except ValueError as exc:
            logger.error("%s", exc)
            return 2
        flags = []
        if append_cli:
            flags.append("--append")
        elif overwrite_effective:
            flags.append("--overwrite")
        cmd = build_cmd(question_ids_csv=qids_csv, workflow_id=workflow_id, extra_flags=flags)
        runs.append(("env selection", workflow_id, cmd))

    ev_over: dict = {}
    if args.test_id.strip():
        ev_over["eval"] = {"test_id": test_id}

    first_cmd = runs[0][2]
    footer_wf = (
        f"longmemeval/{json_stem}/<question_id> (×{len(sampled_ids)})"
        if sampling_cfg is not None and len(sampled_ids) > 1
        else runs[0][1]
    )

    log_env_config_summary(
        logger,
        payload,
        config_path,
        step="LongMemEval eval (env + resolved paths)",
        overrides=ev_over or None,
        footer={
            "resolved_data_file": str(data_file),
            "resolved_memory_agent_config": str(agent_config_path),
            "resolved_workflow_id": footer_wf,
            "question_ids": ",".join(sampled_ids) if sampled_ids else (qids_arg or ",".join(selection_ids)),
            "sampling": bool(sampling_cfg),
            "test_id": test_id,
            "hypothesis_output": str(LOG_DIR / test_id / hypothesis_jsonl),
            "run_eval_commands": len(runs),
            "run_eval_command": " ".join(first_cmd),
        },
    )

    if args.dry_run:
        logger.info("Dry-run, skip subprocess.")
        for label, _wf, cmd in runs:
            logger.info("[%s] %s", label, " ".join(cmd))
        return 0

    child_env = os.environ.copy()
    child_env["LOCOMO_SKIP_ENV_CONFIG_LOG"] = "1"
    for label, _wf, cmd in runs:
        logger.info("Starting: %s", label)
        completed = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=child_env)
        if completed.returncode != 0:
            logger.error("run_eval_longmemeval failed (%s) exit=%s", label, completed.returncode)
            return int(completed.returncode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
