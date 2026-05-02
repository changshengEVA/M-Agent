#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from _shared import (
    DEFAULT_ENV_CONFIG_PATH,
    PROJECT_ROOT,
    get_data_config,
    load_env_config,
    log_env_config_summary,
    resolve_project_path,
    resolve_target_sample_ids,
)


logger = logging.getLogger("run_realtalk.eval")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run REALTALK eval for selected sample_ids using env config."
    )
    parser.add_argument(
        "--env-config",
        type=str,
        default=DEFAULT_ENV_CONFIG_PATH,
        help="Config path under config/eval/memory_agent/realtalk.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print resolved eval command only.")
    parser.add_argument(
        "--workflow-id",
        type=str,
        default="",
        help="Override MemoryCore workflow_id for eval (must match import --process-id).",
    )
    parser.add_argument("--test-id", type=str, default="", help="Override eval.test_id from env config.")
    parser.add_argument(
        "--sample-ids",
        type=str,
        default="",
        help="Override selection.sample_ids for this eval run (comma-separated).",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    payload, config_path = load_env_config(args.env_config)
    data_cfg = get_data_config(payload)
    data_source = resolve_project_path(data_cfg["file"])
    if not data_source.exists():
        raise FileNotFoundError(f"REALTALK data source not found: {data_source}")

    if args.sample_ids.strip():
        sample_ids = [x.strip() for x in args.sample_ids.split(",") if x.strip()]
        if not sample_ids:
            raise ValueError("--sample-ids must list at least one sample id.")
    else:
        sample_ids = resolve_target_sample_ids(payload, data_source)

    eval_cfg = payload.get("eval", {})
    if not isinstance(eval_cfg, dict):
        eval_cfg = {}

    memory_agent_config = str(
        eval_cfg.get("memory_agent_config", "config/agents/memory/locomo_eval_memory_agent_full_mode_E2.yaml")
        or "config/agents/memory/locomo_eval_memory_agent_full_mode_E2.yaml"
    ).strip()
    test_id = str(args.test_id or "").strip()
    if not test_id:
        test_id = str(eval_cfg.get("test_id", "realtalk_eval_from_env") or "").strip() or "realtalk_eval_from_env"
    model_key = str(eval_cfg.get("model_key", "memory_agent") or "memory_agent").strip()
    prediction_key = str(eval_cfg.get("prediction_key", "memory_agent_prediction") or "memory_agent_prediction").strip()
    thread_id_prefix = str(eval_cfg.get("thread_id_prefix", "realtalk-eval") or "realtalk-eval").strip()
    overwrite = bool(eval_cfg.get("overwrite", False))
    max_questions = int(eval_cfg.get("max_questions", 0))
    save_every = int(eval_cfg.get("save_every", 1))
    sleep_seconds = float(eval_cfg.get("sleep_seconds", 0.0))
    recall_dir = str(eval_cfg.get("recall_dir", "recall") or "recall").strip() or "recall"

    agent_config_path = resolve_project_path(memory_agent_config)
    run_eval_script = (PROJECT_ROOT / "scripts" / "run_realtalk" / "run_eval_realtalk.py").resolve()
    if not agent_config_path.exists():
        raise FileNotFoundError(f"MemoryAgent config not found: {agent_config_path}")
    if not run_eval_script.exists():
        raise FileNotFoundError(f"Eval script not found: {run_eval_script}")

    cmd = [
        sys.executable,
        str(run_eval_script),
        "--data-file",
        str(data_source),
        "--config",
        str(agent_config_path),
        "--test-id",
        test_id,
        "--model-key",
        model_key,
        "--prediction-key",
        prediction_key,
        "--thread-id-prefix",
        thread_id_prefix,
        "--sample-ids",
        ",".join(sample_ids),
        "--max-questions",
        str(max_questions),
        "--save-every",
        str(save_every),
        "--sleep-seconds",
        str(sleep_seconds),
        "--recall-dir",
        recall_dir,
    ]
    if overwrite:
        cmd.append("--overwrite")
    workflow_id = str(args.workflow_id or "").strip()
    if workflow_id:
        cmd.extend(["--workflow-id", workflow_id])

    ev_over: Dict[str, Any] = {}
    if args.test_id.strip():
        ev_over["eval"] = {"test_id": test_id}
    if args.sample_ids.strip():
        ev_over["selection"] = {"sample_ids": sample_ids}
    log_env_config_summary(
        logger,
        payload,
        config_path,
        step="REALTALK eval (env + resolved paths)",
        overrides=ev_over or None,
        footer={
            "resolved_data_source": str(data_source),
            "resolved_memory_agent_config": str(agent_config_path),
            "resolved_workflow_id": workflow_id or "(from MemoryCore YAML)",
            "sample_ids": sample_ids,
            "test_id": test_id,
            "recall_dir": recall_dir,
            "run_eval_command": " ".join(cmd),
        },
    )

    logger.info("Start REALTALK eval subprocess")
    if args.dry_run:
        logger.info("Dry-run mode, skip eval execution.")
        return 0

    completed = subprocess.run(cmd, cwd=str(Path(PROJECT_ROOT)))
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
