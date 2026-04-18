#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin wrapper: same warmup as LoCoMo, default env points to longmemeval test_env.yaml.

Without --workflow-id, resolves memory root like import_longmemeval_one (import.process_id or
longmemeval/<data_stem>/<single question_id>).
"""

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
    load_env_config,
    resolve_longmemeval_memory_id,
    resolve_project_path,
)

logger = logging.getLogger("run_longmemeval.warmup")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Warmup scenes/facts for LongMemEval (delegates to warmup_locomo).")
    p.add_argument(
        "--env-config",
        type=str,
        default=DEFAULT_LONGMEMEVAL_ENV_CONFIG_PATH,
        help="YAML env config (default: longmemeval test_env).",
    )
    p.add_argument(
        "--workflow-id",
        type=str,
        default="",
        help="Must match import process_id (optional if derivable from env YAML).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Delete existing scene directory and regenerate.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print command only.",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Forward to warmup_locomo: verbose HTTP and per-chunk fact logs.",
    )
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    warmup_py = (PROJECT_ROOT / "scripts" / "run_locomo" / "warmup_locomo.py").resolve()
    cfg = resolve_project_path(args.env_config)

    payload, _ = load_env_config(str(cfg))
    warmup_cfg = payload.get("warmup", {})
    if not isinstance(warmup_cfg, dict):
        warmup_cfg = {}
    debug = bool(args.debug) or bool(warmup_cfg.get("debug", False))
    try:
        workflow_id = resolve_longmemeval_memory_id(payload, args.workflow_id)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    cmd = [
        sys.executable,
        str(warmup_py),
        "--env-config",
        str(cfg),
        "--workflow-id",
        workflow_id,
    ]
    if args.force:
        cmd.append("--force")
    if debug:
        cmd.append("--debug")

    logger.info("Resolved workflow_id=%s (must match import process_id / data/memory/<id>/)", workflow_id)
    logger.info("Running: %s", " ".join(cmd))
    if args.dry_run:
        return 0

    child_env = os.environ.copy()
    child_env["LOCOMO_SKIP_ENV_CONFIG_LOG"] = "1"
    completed = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=child_env)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
