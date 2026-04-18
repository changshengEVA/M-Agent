#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chain LoCoMo import → warmup → eval with one memory root per conv.

Uses data/memory/<memory_id>/ with memory_id default ``locomo/<conv_id>`` so each conv
has an isolated store (import.process_id == MemoryCore workflow_id).

Examples::

    # Single conv
    python scripts/run_locomo/run_locomo_pipeline.py --env-config config/eval/memory_agent/locomo/test_env.yaml --conv-id conv-30

    # All conv_ids listed in env config
    python scripts/run_locomo/run_locomo_pipeline.py --env-config config/eval/memory_agent/locomo/test_env.yaml --batch
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
    load_env_config,
    log_env_config_summary,
    resolve_project_path,
    resolve_target_conv_ids,
)

logger = logging.getLogger("run_locomo.pipeline")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run import_locomo → warmup_locomo → eval_locomo per conv with isolated memory_id."
    )
    parser.add_argument(
        "--env-config",
        type=str,
        default=DEFAULT_ENV_CONFIG_PATH,
        help="YAML env config (same as import/warmup/eval).",
    )
    parser.add_argument(
        "--conv-id",
        type=str,
        default="",
        help="Single LoCoMo conv id (e.g. conv-30). Required when not using --batch.",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Run every conv_id from env config selection.conv_ids (one isolated pipeline each).",
    )
    parser.add_argument(
        "--memory-id-template",
        type=str,
        default="locomo/{conv_id}",
        help="Template for --process-id / --workflow-id; {conv_id} is substituted.",
    )
    parser.add_argument(
        "--test-id-suffix",
        action="store_true",
        help="Append __<conv_id> to eval test_id for each run so log/<test_id>/ does not overwrite.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned commands only.",
    )
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="Skip import_locomo (warmup + eval only).",
    )
    parser.add_argument(
        "--skip-warmup",
        action="store_true",
        help="Skip warmup_locomo.",
    )
    return parser.parse_args()


def _sanitize_conv_for_test_id(conv_id: str) -> str:
    return conv_id.replace("/", "_").replace("\\", "_")


def _run_step(cmd: list[str], cwd: Path) -> int:
    logger.info("running: %s", " ".join(cmd))
    child_env = os.environ.copy()
    child_env["LOCOMO_SKIP_ENV_CONFIG_LOG"] = "1"
    completed = subprocess.run(cmd, cwd=str(cwd), env=child_env)
    return int(completed.returncode)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    payload, config_path = load_env_config(args.env_config)
    eval_cfg = payload.get("eval", {})
    if not isinstance(eval_cfg, dict):
        eval_cfg = {}
    base_test_id = str(eval_cfg.get("test_id", "locomo_eval_from_env") or "").strip() or "locomo_eval_from_env"

    if args.batch:
        conv_ids = resolve_target_conv_ids(payload)
    else:
        cid = args.conv_id.strip()
        if not cid:
            logger.error("Provide --conv-id or use --batch.")
            return 2
        conv_ids = [cid]

    log_env_config_summary(
        logger,
        payload,
        config_path,
        step="LoCoMo pipeline (batch/single driver)",
        pipeline_cli={
            "batch": args.batch,
            "conv_id": args.conv_id.strip() or None,
            "memory_id_template": args.memory_id_template,
            "test_id_suffix": args.test_id_suffix,
            "dry_run": args.dry_run,
            "skip_import": args.skip_import,
            "skip_warmup": args.skip_warmup,
            "conv_ids_to_run": conv_ids,
        },
    )

    project_root = resolve_project_path(".")
    scripts_dir = project_root / "scripts" / "run_locomo"
    import_py = scripts_dir / "import_locomo.py"
    warmup_py = scripts_dir / "warmup_locomo.py"
    eval_py = scripts_dir / "eval_locomo.py"

    for conv_id in conv_ids:
        memory_id = args.memory_id_template.format(conv_id=conv_id)
        test_id = base_test_id
        if args.test_id_suffix:
            test_id = f"{base_test_id}__{_sanitize_conv_for_test_id(conv_id)}"

        logger.info("=" * 60)
        logger.info("conv_id=%s memory_id=%s test_id=%s", conv_id, memory_id, test_id)
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
                        "--conv-ids",
                        conv_id,
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
                    "--conv-ids",
                    conv_id,
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

    logger.info("Pipeline finished for %d conv(s).", len(conv_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
