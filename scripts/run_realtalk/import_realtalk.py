#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import logging
import os

from _shared import (
    DEFAULT_ENV_CONFIG_PATH,
    get_data_config,
    load_env_config,
    log_env_config_summary,
    resolve_project_path,
    resolve_target_sample_ids,
)
from m_agent.pipeline.memory_pre import run_full_pipeline_for_id


logger = logging.getLogger("run_realtalk.import")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build REALTALK dialogues/episodes for selected sample_ids using env config."
    )
    parser.add_argument(
        "--env-config",
        type=str,
        default=DEFAULT_ENV_CONFIG_PATH,
        help="Config path under config/eval/memory_agent/realtalk.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved settings only, do not run import.",
    )
    parser.add_argument(
        "--process-id",
        type=str,
        default="",
        help="Override import.process_id from env config.",
    )
    parser.add_argument(
        "--sample-ids",
        type=str,
        default="",
        help="Override selection.sample_ids: comma-separated sample ids.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    payload, config_path = load_env_config(args.env_config)
    data_cfg = get_data_config(payload)
    import_cfg = payload.get("import", {})
    if not isinstance(import_cfg, dict):
        import_cfg = {}

    data_source = resolve_project_path(data_cfg["file"])
    if not data_source.exists():
        raise FileNotFoundError(f"REALTALK data source not found: {data_source}")

    sample_ids = resolve_target_sample_ids(payload, data_source)
    if args.sample_ids.strip():
        sample_ids = [x.strip() for x in args.sample_ids.split(",") if x.strip()]
        if not sample_ids:
            raise ValueError("--sample-ids must list at least one sample id.")

    process_id = str(args.process_id or "").strip()
    if not process_id:
        process_id = str(import_cfg.get("process_id", "") or "").strip()
    if not process_id:
        raise ValueError("import.process_id must not be empty (or pass --process-id).")

    clean_output = bool(import_cfg.get("clean_output", True))

    ep_workers = import_cfg.get("episode_max_workers", None)
    if ep_workers is not None:
        try:
            ep_workers_i = max(1, int(ep_workers))
            os.environ["M_AGENT_EPISODE_MAX_WORKERS"] = str(ep_workers_i)
        except (TypeError, ValueError):
            logger.warning("Invalid import.episode_max_workers: %r — ignored.", ep_workers)

    logger.info("Start REALTALK import")
    logger.info("env_config=%s", config_path)
    logger.info("process_id=%s", process_id)
    logger.info("data_source=%s", data_source)
    logger.info("loader_type=%s", data_cfg["loader_type"])
    logger.info("sample_ids=%s", sample_ids)
    logger.info("clean_output=%s", clean_output)
    logger.info(
        "episode_max_workers env: M_AGENT_EPISODE_MAX_WORKERS=%s",
        os.environ.get("M_AGENT_EPISODE_MAX_WORKERS", "(unset -> memory_pre defaults to 1)"),
    )

    ov: dict = {}
    if args.process_id.strip():
        ov["import"] = {"process_id": process_id}
    if args.sample_ids.strip():
        ov["selection"] = {"sample_ids": sample_ids}
    log_env_config_summary(
        logger,
        payload,
        config_path,
        step="REALTALK import",
        overrides=ov or None,
        footer={
            "resolved_data_source": str(data_source),
            "resolved_loader_type": data_cfg["loader_type"],
        },
    )

    if args.dry_run:
        logger.info("Dry-run mode, skip import execution.")
        return 0

    ok = run_full_pipeline_for_id(
        process_id=process_id,
        data_source=str(data_source),
        loader_type=data_cfg["loader_type"],
        include_conv_ids=sample_ids,
        clean_output=clean_output,
    )
    if not ok:
        logger.error("REALTALK import failed.")
        return 1

    logger.info("REALTALK import completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
