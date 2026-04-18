#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chain LongMemEval import → warmup → eval with one memory root per question_id.

Default memory id: ``longmemeval/<json_stem>/<question_id>`` (matches import_longmemeval_one).

Target question_ids come from the env YAML: ``selection.question_ids`` (if non-empty) else
``selection.sampling`` (legacy ``eval.sampling`` if ``selection`` has no ``sampling`` key).
Optional ``--question-id`` runs a single id (override).

Examples::

    python scripts/run_longmemeval/run_longmemeval_pipeline.py \\
        --env-config config/eval/memory_agent/longmemeval/test_env.yaml

    python scripts/run_longmemeval/run_longmemeval_pipeline.py \\
        --env-config config/eval/memory_agent/longmemeval/test_env.yaml \\
        --question-id e47becba
"""

from __future__ import annotations

import argparse
import logging
import os
import re
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
    get_data_config,
    load_env_config,
    log_env_config_summary,
    resolve_project_path,
)

from sample_longmemeval import get_batch_question_ids

logger = logging.getLogger("run_longmemeval.pipeline")


def _safe_segment(name: str) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip())
    return (out[:200] if out else "q").strip("._-")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run import_longmemeval_one → warmup_longmemeval → eval_longmemeval per question_id (targets from YAML)."
    )
    p.add_argument(
        "--env-config",
        type=str,
        default=DEFAULT_LONGMEMEVAL_ENV_CONFIG_PATH,
        help="YAML env config.",
    )
    p.add_argument(
        "--question-id",
        type=str,
        default="",
        help="Run only this question_id (optional; default: all targets from selection / sampling).",
    )
    p.add_argument(
        "--memory-id-template",
        type=str,
        default="longmemeval/{json_stem}/{question_id}",
        help="Template for import --process-id / warmup --workflow-id; {json_stem} {question_id} substituted.",
    )
    p.add_argument(
        "--test-id-suffix",
        action="store_true",
        help="Append __<question_id> to eval test_id so each question gets its own log/<test_id>/ dir.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned commands only.",
    )
    p.add_argument(
        "--skip-import",
        action="store_true",
        help="Skip import step.",
    )
    p.add_argument(
        "--skip-warmup",
        action="store_true",
        help="Skip warmup step.",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Verbose warmup logs (HTTP + per-chunk fact lines); or set warmup.debug in YAML.",
    )
    return p.parse_args()


def _sanitize_for_test_id(qid: str) -> str:
    return qid.replace("/", "_").replace("\\", "_")


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
    data_cfg = get_data_config(payload)
    data_path = resolve_project_path(data_cfg["file"])
    json_stem = _safe_segment(data_path.stem)

    eval_cfg = payload.get("eval", {})
    if not isinstance(eval_cfg, dict):
        eval_cfg = {}
    base_test_id = str(eval_cfg.get("test_id", "longmemeval_run") or "").strip() or "longmemeval_run"
    eval_overwrite = bool(eval_cfg.get("overwrite", False))

    warmup_cfg = payload.get("warmup", {})
    if not isinstance(warmup_cfg, dict):
        warmup_cfg = {}
    warmup_debug = bool(args.debug) or bool(warmup_cfg.get("debug", False))

    cli_qid = args.question_id.strip()
    try:
        resolved = get_batch_question_ids(payload, data_path)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    if cli_qid:
        qids = [cli_qid]
    else:
        qids = resolved

    if not qids:
        logger.error(
            "No target question_ids to run (set selection.question_ids or selection.sampling; legacy eval.sampling ok)."
        )
        return 2

    log_env_config_summary(
        logger,
        payload,
        config_path,
        step="LongMemEval pipeline (config-driven targets)",
        pipeline_cli={
            "question_id": cli_qid or None,
            "memory_id_template": args.memory_id_template,
            "test_id_suffix": args.test_id_suffix,
            "dry_run": args.dry_run,
            "skip_import": args.skip_import,
            "skip_warmup": args.skip_warmup,
            "question_ids_to_run": qids,
        },
    )

    project_root = resolve_project_path(".")
    scripts_dir = project_root / "scripts" / "run_longmemeval"
    import_py = scripts_dir / "import_longmemeval_one.py"
    warmup_py = scripts_dir / "warmup_longmemeval.py"
    eval_py = scripts_dir / "eval_longmemeval.py"

    for qid in qids:
        memory_id = args.memory_id_template.format(json_stem=json_stem, question_id=qid)

        logger.info("=" * 60)
        logger.info("question_id=%s memory_id=%s (import + warmup)", qid, memory_id)
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
                        "--question-id",
                        qid,
                        "--process-id",
                        memory_id,
                    ],
                )
            )
        if not args.skip_warmup:
            w_cmd = [
                sys.executable,
                str(warmup_py),
                "--env-config",
                str(config_path),
                "--workflow-id",
                memory_id,
            ]
            if warmup_debug:
                w_cmd.append("--debug")
            steps.append(("warmup", w_cmd))

        for name, cmd in steps:
            if args.dry_run:
                logger.info("[dry-run] %s: %s", name, " ".join(cmd))
                continue
            rc = _run_step(cmd, project_root)
            if rc != 0:
                logger.error("Step %s failed with exit code %s", name, rc)
                return rc

    multi = len(qids) > 1
    one_log = multi and not args.test_id_suffix

    for i, qid in enumerate(qids):
        memory_id = args.memory_id_template.format(json_stem=json_stem, question_id=qid)
        test_id = base_test_id
        if args.test_id_suffix:
            test_id = f"{base_test_id}__{_sanitize_for_test_id(qid)}"

        eval_cmd = [
            sys.executable,
            str(eval_py),
            "--env-config",
            str(config_path),
            "--workflow-id",
            memory_id,
            "--question-ids",
            qid,
            "--test-id",
            test_id,
            "--no-sampling",
        ]
        if one_log:
            if i == 0:
                if eval_overwrite:
                    eval_cmd.append("--overwrite")
            else:
                eval_cmd.append("--append")
        elif eval_overwrite and i == 0 and not multi:
            eval_cmd.append("--overwrite")

        logger.info("=" * 60)
        logger.info(
            "eval question_id=%s test_id=%s%s",
            qid,
            test_id,
            f" ({i + 1}/{len(qids)})" if multi else "",
        )
        logger.info("=" * 60)

        if args.dry_run:
            logger.info("[dry-run] eval: %s", " ".join(eval_cmd))
            continue
        rc = _run_step(eval_cmd, project_root)
        if rc != 0:
            logger.error("Step eval failed with exit code %s", rc)
            return rc

    if one_log:
        logger.info(
            "Hypothesis lines for all %d question(s) are in log/%s/<hypothesis_jsonl> (eval.test_id + eval.hypothesis_jsonl).",
            len(qids),
            base_test_id,
        )
    logger.info("Pipeline finished for %d question(s).", len(qids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
