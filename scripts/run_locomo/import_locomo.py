#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import logging

from _shared import (
    DEFAULT_ENV_CONFIG_PATH,
    get_data_config,
    load_env_config,
    parse_question_selection,
    resolve_project_path,
    resolve_target_conv_ids,
)

from m_agent.pipeline.memory_pre import run_full_pipeline_for_id


logger = logging.getLogger("run_locomo.import")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build LoCoMo dialogues/episodes for selected conv_ids using env config."
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
        help="Print resolved settings only, do not run import.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    payload, config_path = load_env_config(args.env_config)
    conv_ids = resolve_target_conv_ids(payload)
    question_selection = parse_question_selection(payload)
    data_cfg = get_data_config(payload)
    import_cfg = payload.get("import", {})
    if not isinstance(import_cfg, dict):
        import_cfg = {}

    process_id = str(import_cfg.get("process_id", "") or "").strip()
    if not process_id:
        raise ValueError("import.process_id must not be empty.")

    clean_output = bool(import_cfg.get("clean_output", True))

    data_file = resolve_project_path(data_cfg["file"])
    if not data_file.exists():
        raise FileNotFoundError(f"LoCoMo data file not found: {data_file}")

    logger.info("Start LoCoMo import")
    logger.info("env_config=%s", config_path)
    logger.info("process_id=%s", process_id)
    logger.info("data_file=%s", data_file)
    logger.info("loader_type=%s", data_cfg["loader_type"])
    logger.info("conv_ids=%s", conv_ids)
    if question_selection:
        logger.info(
            "question_selection enabled for %d convs (%d total questions)",
            len(question_selection),
            sum(len(v) for v in question_selection.values()),
        )
    logger.info("clean_output=%s", clean_output)
    if args.dry_run:
        logger.info("Dry-run mode, skip import execution.")
        return 0

    ok = run_full_pipeline_for_id(
        process_id=process_id,
        data_source=str(data_file),
        loader_type=data_cfg["loader_type"],
        include_conv_ids=conv_ids,
        clean_output=clean_output,
    )
    if not ok:
        logger.error("LoCoMo import failed.")
        return 1

    logger.info("LoCoMo import completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
