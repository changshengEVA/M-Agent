#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

from _shared import (
    DEFAULT_ENV_CONFIG_PATH,
    PROJECT_ROOT,
    get_data_config,
    load_env_config,
    parse_question_selection,
    resolve_project_path,
    resolve_target_conv_ids,
)


logger = logging.getLogger("run_locomo.eval")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LoCoMo eval for selected conv_ids using env config."
    )
    parser.add_argument(
        "--env-config",
        type=str,
        default=DEFAULT_ENV_CONFIG_PATH,
        help="Config path under config/eval/memory_agent/locomo.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved eval command only, do not execute.",
    )
    parser.add_argument(
        "--question-config",
        type=str,
        default="",
        help="Optional override for question subset yaml (sample_id + qa_indices).",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    payload, config_path = load_env_config(args.env_config)
    conv_ids = resolve_target_conv_ids(payload)
    question_selection = parse_question_selection(payload)
    data_cfg = get_data_config(payload)

    eval_cfg = payload.get("eval", {})
    if not isinstance(eval_cfg, dict):
        eval_cfg = {}

    memory_agent_config = str(
        eval_cfg.get("memory_agent_config", "config/agents/memory/locomo_eval_memory_agent.yaml")
        or "config/agents/memory/locomo_eval_memory_agent.yaml"
    ).strip()
    test_id = str(eval_cfg.get("test_id", "locomo_eval_from_env") or "").strip() or "locomo_eval_from_env"
    model_key = str(eval_cfg.get("model_key", "memory_agent") or "memory_agent").strip()
    prediction_key = str(
        eval_cfg.get("prediction_key", "memory_agent_prediction") or "memory_agent_prediction"
    ).strip()
    thread_id_prefix = str(eval_cfg.get("thread_id_prefix", "locomo-eval") or "locomo-eval").strip()
    overwrite = bool(eval_cfg.get("overwrite", False))
    sample_fraction = float(eval_cfg.get("sample_fraction", 1.0))
    sample_seed = int(eval_cfg.get("sample_seed", 42))
    max_samples = int(eval_cfg.get("max_samples", 0))
    max_questions = int(eval_cfg.get("max_questions", 0))
    save_every = int(eval_cfg.get("save_every", 1))
    sleep_seconds = float(eval_cfg.get("sleep_seconds", 0.0))
    question_config_raw = str(eval_cfg.get("question_config", "") or "").strip()
    if args.question_config:
        question_config_raw = str(args.question_config).strip()

    data_file = resolve_project_path(data_cfg["file"])
    agent_config_path = resolve_project_path(memory_agent_config)
    run_eval_script = (PROJECT_ROOT / "scripts" / "run_locomo" / "run_eval_locomo.py").resolve()
    question_config_path = resolve_project_path(question_config_raw) if question_config_raw else None

    if not data_file.exists():
        raise FileNotFoundError(f"LoCoMo data file not found: {data_file}")
    if not agent_config_path.exists():
        raise FileNotFoundError(f"MemoryAgent config not found: {agent_config_path}")
    if not run_eval_script.exists():
        raise FileNotFoundError(f"Eval script not found: {run_eval_script}")
    if question_config_path is not None and (not question_config_path.exists()):
        raise FileNotFoundError(f"Question config not found: {question_config_path}")

    generated_question_config_path: Path | None = None
    if question_config_path is None and question_selection:
        generated_question_config_path = (
            PROJECT_ROOT / "log" / test_id / "_env_question_selection.yaml"
        ).resolve()
        generated_question_config_path.parent.mkdir(parents=True, exist_ok=True)
        generated_payload: Dict[str, Any] = {
            "name": "env_question_selection",
            "description": "Auto-generated from env-config selection.questions.",
            "questions": [
                {"sample_id": sample_id, "qa_indices": indices}
                for sample_id, indices in question_selection.items()
            ],
        }
        with open(generated_question_config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(generated_payload, f, allow_unicode=True, sort_keys=False)
        question_config_path = generated_question_config_path

    cmd = [
        sys.executable,
        str(run_eval_script),
        "--data-file",
        str(data_file),
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
        "--conv-ids",
        ",".join(conv_ids),
        "--sample-fraction",
        str(sample_fraction),
        "--sample-seed",
        str(sample_seed),
        "--max-samples",
        str(max_samples),
        "--max-questions",
        str(max_questions),
        "--save-every",
        str(save_every),
        "--sleep-seconds",
        str(sleep_seconds),
    ]
    if overwrite:
        cmd.append("--overwrite")
    if question_config_path is not None:
        cmd.extend(["--question-config", str(question_config_path)])

    logger.info("Start LoCoMo eval")
    logger.info("env_config=%s", config_path)
    logger.info("data_file=%s", data_file)
    logger.info("memory_agent_config=%s", agent_config_path)
    logger.info("conv_ids=%s", conv_ids)
    if question_selection:
        logger.info(
            "question_selection enabled for %d convs (%d total questions)",
            len(question_selection),
            sum(len(v) for v in question_selection.values()),
        )
    if question_config_path is not None:
        logger.info("question_config=%s", question_config_path)
    logger.info("test_id=%s", test_id)
    logger.info("run command: %s", " ".join(cmd))
    if args.dry_run:
        logger.info("Dry-run mode, skip eval execution.")
        return 0

    completed = subprocess.run(cmd, cwd=str(Path(PROJECT_ROOT)))
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
