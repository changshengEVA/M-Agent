#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build dialogues + episodes for a single LongMemEval ``question_id`` (one memory subtree).

Default ``process_id`` is ``longmemeval/<json_stem>/<question_id>`` under ``data/memory/``.
Warmup/eval should use the same path as ``--workflow-id`` / ``--process-id``.

Example (CLI only)::

    python scripts/run_longmemeval/import_longmemeval_one.py \\
        --data-json data/LongMemEval/data/longmemeval_s_cleaned.json \\
        --question-id e47becba \\
        --clean-output

Example (env config, one question_id per run)::

    python scripts/run_longmemeval/import_longmemeval_one.py \\
        --env-config config/eval/memory_agent/longmemeval/test_env.yaml \\
        --question-id e47becba
"""

from __future__ import annotations

import argparse
import logging
import os
import re
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
    parse_question_ids,
    resolve_project_path,
)
from m_agent.pipeline.memory_pre import run_full_pipeline_for_id

logger = logging.getLogger("run_longmemeval.import")


def _safe_segment(name: str) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip())
    return (out[:200] if out else "q").strip("._-")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import one LongMemEval sample into data/memory/<process_id>/.")
    p.add_argument(
        "--env-config",
        type=str,
        default="",
        help="Optional YAML env (default longmemeval test_env when set without path).",
    )
    p.add_argument(
        "--data-json",
        type=str,
        default="",
        help="Path to longmemeval_oracle.json / longmemeval_s_cleaned.json / longmemeval_m_cleaned.json",
    )
    p.add_argument(
        "--question-id",
        type=str,
        default="",
        help="LongMemEval question_id to import (required without env-config unless selection has one id).",
    )
    p.add_argument(
        "--question-ids",
        type=str,
        default="",
        help="Override selection.question_ids: comma-separated (must be exactly one id for this importer).",
    )
    p.add_argument(
        "--process-id",
        type=str,
        default="",
        help="Override memory root id (default: longmemeval/<stem>/<question_id>).",
    )
    p.add_argument(
        "--clean-output",
        action="store_true",
        help="Delete existing dialogues/episodes under this process_id before rebuild.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved settings only, do not run import.",
    )
    return p.parse_args()


def _resolve_single_question_id(args: argparse.Namespace, payload: dict | None) -> str:
    if args.question_ids.strip():
        parts = [x.strip() for x in args.question_ids.split(",") if x.strip()]
        if len(parts) != 1:
            raise ValueError(
                "import_longmemeval_one expects exactly one question_id per run; "
                f"got {len(parts)} from --question-ids. Use run_longmemeval_pipeline.py (multiple targets from YAML) for multiple."
            )
        return parts[0]
    if args.question_id.strip():
        return args.question_id.strip()
    if payload is not None:
        qids = parse_question_ids(payload)
        if len(qids) == 1:
            return qids[0]
        if len(qids) > 1:
            raise ValueError(
                "selection.question_ids has multiple ids; specify --question-id for one run, "
                "or use run_longmemeval_pipeline.py with multiple targets in the env YAML."
            )
    raise ValueError("Provide --question-id, or --env-config with exactly one selection.question_ids.")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    use_env = bool(str(args.env_config or "").strip())
    payload = None
    config_path = None
    if use_env:
        path_arg = str(args.env_config).strip()
        if path_arg in ("", "default"):
            path_arg = DEFAULT_LONGMEMEVAL_ENV_CONFIG_PATH
        payload, config_path = load_env_config(path_arg)
        data_cfg = get_data_config(payload)
        data_path = resolve_project_path(data_cfg["file"])
        loader_type = data_cfg["loader_type"]
        import_cfg = payload.get("import", {})
        if not isinstance(import_cfg, dict):
            import_cfg = {}

        raw_qid = _resolve_single_question_id(args, payload)
        qid_seg = _safe_segment(raw_qid)

        process_id = str(args.process_id or "").strip()
        if not process_id:
            process_id = str(import_cfg.get("process_id", "") or "").strip()
        stem = _safe_segment(data_path.stem)
        if not process_id:
            process_id = f"longmemeval/{stem}/{qid_seg}"

        clean_output = bool(import_cfg.get("clean_output", False))
        if args.clean_output:
            clean_output = True

        ep_workers = import_cfg.get("episode_max_workers", None)
        if ep_workers is not None:
            try:
                ep_workers_i = max(1, int(ep_workers))
                os.environ["M_AGENT_EPISODE_MAX_WORKERS"] = str(ep_workers_i)
            except (TypeError, ValueError):
                logging.warning("Invalid import.episode_max_workers: %r — ignored.", ep_workers)

        if not data_path.is_file():
            logging.error("data file not found: %s", data_path)
            return 2

        ov: dict = {}
        if str(args.process_id or "").strip():
            ov["import"] = {"process_id": process_id}
        log_env_config_summary(
            logger,
            payload,
            config_path,
            step="LongMemEval import",
            overrides=ov or None,
            footer={
                "resolved_data_file": str(data_path),
                "resolved_loader_type": loader_type,
                "question_id": raw_qid,
                "process_id": process_id,
            },
        )

        if args.dry_run:
            logging.info("Dry-run mode, skip import execution.")
            return 0

        ok = run_full_pipeline_for_id(
            process_id=process_id,
            data_source=str(data_path.resolve()),
            loader_type=loader_type,
            include_conv_ids=[raw_qid],
            clean_output=clean_output,
        )
        return 0 if ok else 1

    # --- CLI-only path (no env-config) ---
    data_path = Path(args.data_json)
    if not data_path.is_file():
        logging.error("data-json not found: %s", data_path)
        return 2

    stem = _safe_segment(data_path.stem)
    raw_qid = args.question_id.strip()
    if not raw_qid:
        logging.error("--question-id is required when not using --env-config.")
        return 2
    qid_seg = _safe_segment(raw_qid)
    process_id = str(args.process_id or "").strip()
    if not process_id:
        process_id = f"longmemeval/{stem}/{qid_seg}"

    if args.dry_run:
        logging.info(
            "Dry-run: process_id=%s data=%s question_id=%s",
            process_id,
            data_path,
            raw_qid,
        )
        return 0

    ok = run_full_pipeline_for_id(
        process_id=process_id,
        data_source=str(data_path.resolve()),
        loader_type="longmemeval",
        include_conv_ids=[raw_qid],
        clean_output=bool(args.clean_output),
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
