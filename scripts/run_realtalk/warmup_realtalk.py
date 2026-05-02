#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Warmup REALTALK scenes/facts.

This reuses the generic warmup implementation while keeping a dataset-specific
entrypoint and env-config namespace.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from _shared import DEFAULT_ENV_CONFIG_PATH, PROJECT_ROOT


logger = logging.getLogger("run_realtalk.warmup")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate scenes and atomic facts for REALTALK.")
    parser.add_argument(
        "--env-config",
        type=str,
        default=DEFAULT_ENV_CONFIG_PATH,
        help="Config path under config/eval/memory_agent/realtalk.",
    )
    parser.add_argument("--force", action="store_true", help="Force full rebuild.")
    parser.add_argument("--force-scene", action="store_true", help="Force rebuild scene outputs.")
    parser.add_argument("--force-facts", action="store_true", help="Force rebuild atomic facts outputs.")
    parser.add_argument("--force-kg", action="store_true", help="Force rebuild KG/segment entity pipeline.")
    parser.add_argument("--dry-run", action="store_true", help="Print command only.")
    parser.add_argument("--memory-root", type=str, default="", help="Override memory root directory.")
    parser.add_argument("--workflow-id", type=str, default="", help="Override workflow_id in MemoryCore.")
    parser.add_argument("--debug", action="store_true", help="Enable verbose warmup logs.")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    warmup_script = (PROJECT_ROOT / "scripts" / "run_locomo" / "warmup_locomo.py").resolve()
    cmd = [sys.executable, str(warmup_script), "--env-config", str(args.env_config)]
    if args.force:
        cmd.append("--force")
    if args.force_scene:
        cmd.append("--force-scene")
    if args.force_facts:
        cmd.append("--force-facts")
    if args.force_kg:
        cmd.append("--force-kg")
    if args.dry_run:
        cmd.append("--dry-run")
    if args.memory_root.strip():
        cmd.extend(["--memory-root", args.memory_root.strip()])
    if args.workflow_id.strip():
        cmd.extend(["--workflow-id", args.workflow_id.strip()])
    if args.debug:
        cmd.append("--debug")

    logger.info("Running REALTALK warmup command: %s", " ".join(cmd))
    completed = subprocess.run(cmd, cwd=str(Path(PROJECT_ROOT)))
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
