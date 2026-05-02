#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chain REALTALK import -> warmup -> eval with one memory root per sample_id.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from _shared import (
    DEFAULT_ENV_CONFIG_PATH,
    get_data_config,
    load_env_config,
    log_env_config_summary,
    resolve_project_path,
    resolve_target_sample_ids,
)


logger = logging.getLogger("run_realtalk.pipeline")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run import_realtalk -> warmup_realtalk -> eval_realtalk per sample_id."
    )
    parser.add_argument(
        "--env-config",
        type=str,
        default=DEFAULT_ENV_CONFIG_PATH,
        help="YAML env config (same as import/warmup/eval).",
    )
    parser.add_argument("--sample-id", type=str, default="", help="Single sample_id to run.")
    parser.add_argument("--batch", action="store_true", help="Run every selected sample_id from env config.")
    parser.add_argument(
        "--memory-id-template",
        type=str,
        default="realtalk/{sample_id}",
        help="Template for --process-id / --workflow-id; {sample_id} is substituted.",
    )
    parser.add_argument(
        "--test-id-suffix",
        action="store_true",
        help="Append __<sample_id> to eval test_id for each run.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands only.")
    parser.add_argument("--skip-import", action="store_true", help="Skip import_realtalk.")
    parser.add_argument("--skip-warmup", action="store_true", help="Skip warmup_realtalk.")
    return parser.parse_args()


def _sanitize_sample_for_test_id(sample_id: str) -> str:
    return sample_id.replace("/", "_").replace("\\", "_")


def _run_step(cmd: list[str], cwd: Path) -> int:
    logger.info("running: %s", " ".join(cmd))
    child_env = os.environ.copy()
    child_env["REALTALK_SKIP_ENV_CONFIG_LOG"] = "1"
    completed = subprocess.run(cmd, cwd=str(cwd), env=child_env)
    return int(completed.returncode)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    payload, config_path = load_env_config(args.env_config)
    data_cfg = get_data_config(payload)
    data_source = resolve_project_path(data_cfg["file"])
    eval_cfg = payload.get("eval", {})
    if not isinstance(eval_cfg, dict):
        eval_cfg = {}
    base_test_id = str(eval_cfg.get("test_id", "realtalk_eval_from_env") or "").strip() or "realtalk_eval_from_env"

    if args.batch:
        sample_ids = resolve_target_sample_ids(payload, data_source)
    else:
        sid = args.sample_id.strip()
        if not sid:
            logger.error("Provide --sample-id or use --batch.")
            return 2
        sample_ids = [sid]

    log_env_config_summary(
        logger,
        payload,
        config_path,
        step="REALTALK pipeline (batch/single driver)",
        pipeline_cli={
            "batch": args.batch,
            "sample_id": args.sample_id.strip() or None,
            "memory_id_template": args.memory_id_template,
            "test_id_suffix": args.test_id_suffix,
            "dry_run": args.dry_run,
            "skip_import": args.skip_import,
            "skip_warmup": args.skip_warmup,
            "sample_ids_to_run": sample_ids,
        },
    )

    project_root = resolve_project_path(".")
    scripts_dir = project_root / "scripts" / "run_realtalk"
    import_py = scripts_dir / "import_realtalk.py"
    warmup_py = scripts_dir / "warmup_realtalk.py"
    eval_py = scripts_dir / "eval_realtalk.py"

    for sample_id in sample_ids:
        memory_id = args.memory_id_template.format(sample_id=sample_id)
        test_id = base_test_id
        if args.test_id_suffix:
            test_id = f"{base_test_id}__{_sanitize_sample_for_test_id(sample_id)}"

        logger.info("=" * 60)
        logger.info("sample_id=%s memory_id=%s test_id=%s", sample_id, memory_id, test_id)
        logger.info("=" * 60)

        steps: list[tuple[str, list[str]]] = []
        if not args.skip_import:
            steps.append(
                (
                    "import",
                    [
                        sys.executable,
                        str(import_py),
                        "--env-config",
                        str(config_path),
                        "--process-id",
                        memory_id,
                        "--sample-ids",
                        sample_id,
                    ],
                )
            )
        if not args.skip_warmup:
            steps.append(
                (
                    "warmup",
                    [
                        sys.executable,
                        str(warmup_py),
                        "--env-config",
                        str(config_path),
                        "--workflow-id",
                        memory_id,
                    ],
                )
            )
        steps.append(
            (
                "eval",
                [
                    sys.executable,
                    str(eval_py),
                    "--env-config",
                    str(config_path),
                    "--workflow-id",
                    memory_id,
                    "--sample-ids",
                    sample_id,
                    "--test-id",
                    test_id,
                ],
            )
        )

        for name, cmd in steps:
            if args.dry_run:
                logger.info("[dry-run] %s: %s", name, " ".join(cmd))
                continue
            rc = _run_step(cmd, project_root)
            if rc != 0:
                logger.error("Step %s failed with exit code %s", name, rc)
                return rc

    logger.info("Pipeline finished for %d sample(s).", len(sample_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
