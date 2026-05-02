from __future__ import annotations

import copy
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = THIS_FILE.parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

try:
    from ._bootstrap import bootstrap_project
except ImportError:
    from _bootstrap import bootstrap_project


bootstrap_project()


DEFAULT_ENV_CONFIG_PATH = "config/eval/memory_agent/realtalk/test_env.yaml"
CHAT_FILE_PATTERN = re.compile(r"^Chat_(\d+)_")


def resolve_project_path(raw_path: str | Path) -> Path:
    candidate = Path(str(raw_path or "").strip())
    if candidate.is_absolute():
        return candidate.resolve()
    return (PROJECT_ROOT / candidate).resolve()


def load_env_config(env_config_path: str) -> Tuple[Dict[str, Any], Path]:
    path = resolve_project_path(env_config_path)
    if not path.exists():
        raise FileNotFoundError(f"Environment config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Environment config must be a mapping: {path}")
    return payload, path


def get_data_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    data_cfg = payload.get("data", {})
    if not isinstance(data_cfg, dict):
        data_cfg = {}

    file_path = str(data_cfg.get("file", "data/REALTALK/data") or "").strip()
    if not file_path:
        raise ValueError("data.file must not be empty.")

    loader_type = str(data_cfg.get("loader_type", "realtalk") or "realtalk").strip()
    if not loader_type:
        loader_type = "realtalk"

    return {
        "file": file_path,
        "loader_type": loader_type,
    }


def _parse_csv_or_list(raw_value: Any) -> List[str]:
    if isinstance(raw_value, str):
        raw_values = [token.strip() for token in raw_value.split(",")]
    elif isinstance(raw_value, list):
        raw_values = [str(item).strip() for item in raw_value]
    else:
        raw_values = []

    values: List[str] = []
    seen = set()
    for value in raw_values:
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def parse_sample_ids(payload: Dict[str, Any]) -> List[str]:
    selection = payload.get("selection", {})
    if not isinstance(selection, dict):
        selection = {}
    return _parse_csv_or_list(selection.get("sample_ids", []))


def parse_chat_ids(payload: Dict[str, Any]) -> List[str]:
    selection = payload.get("selection", {})
    if not isinstance(selection, dict):
        selection = {}
    return _parse_csv_or_list(selection.get("chat_ids", []))


def _extract_chat_no_from_path(path: Path) -> Optional[str]:
    matched = CHAT_FILE_PATTERN.match(path.name)
    return matched.group(1) if matched else None


def _list_realtalk_files(data_source: Path, chat_ids: List[str]) -> List[Path]:
    target_chat_ids = set(chat_ids)
    files: List[Path] = []
    if data_source.is_file():
        files = [data_source]
    elif data_source.is_dir():
        files = sorted(data_source.glob("Chat_*.json"))
    else:
        raise FileNotFoundError(f"REALTALK data source not found: {data_source}")

    if not target_chat_ids:
        return files

    selected: List[Path] = []
    for file_path in files:
        chat_no = _extract_chat_no_from_path(file_path)
        if chat_no and chat_no in target_chat_ids:
            selected.append(file_path)
    return selected


def derive_sample_ids_from_selection(payload: Dict[str, Any], data_source: Path) -> List[str]:
    chat_ids = parse_chat_ids(payload)

    sample_ids: List[str] = []
    seen = set()

    for file_path in _list_realtalk_files(data_source, chat_ids):
        chat_no = _extract_chat_no_from_path(file_path)
        if not chat_no:
            continue
        sample_id = f"realtalk-chat-{chat_no}"
        if sample_id in seen:
            continue
        seen.add(sample_id)
        sample_ids.append(sample_id)
    return sample_ids


def resolve_target_sample_ids(payload: Dict[str, Any], data_source: Path) -> List[str]:
    explicit = parse_sample_ids(payload)
    derived = derive_sample_ids_from_selection(payload, data_source)

    merged = []
    seen = set()
    for value in explicit + derived:
        if value in seen:
            continue
        seen.add(value)
        merged.append(value)

    if not merged:
        raise ValueError(
            "No target REALTALK sample_ids configured. Provide selection.sample_ids "
            "or selection.chat_ids."
        )
    return merged


def _deep_merge_dict(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            merged = dict(out[key])
            merged.update(value)
            out[key] = merged
        else:
            out[key] = copy.deepcopy(value)
    return out


def build_env_config_snapshot(
    payload: Dict[str, Any],
    overrides: Optional[Dict[str, Any]] = None,
    pipeline_cli: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    keys = ("name", "description", "data", "selection", "import", "warmup", "eval")
    snap: Dict[str, Any] = {k: copy.deepcopy(payload[k]) for k in keys if k in payload}
    if overrides:
        snap = _deep_merge_dict(snap, overrides)
    if pipeline_cli:
        snap["pipeline_cli"] = copy.deepcopy(pipeline_cli)
    return snap


def log_relevant_env_vars(logger: logging.Logger, keys: Optional[Tuple[str, ...]] = None) -> None:
    if keys is None:
        keys = (
            "M_AGENT_EPISODE_MAX_WORKERS",
            "M_AGENT_SCENE_MAX_WORKERS",
            "M_AGENT_SCENE_FACT_MAX_WORKERS",
            "EMBED_PROVIDER",
        )
    lines = []
    for key in keys:
        if key in os.environ:
            lines.append(f"{key}={os.environ[key]}")
    if lines:
        logger.info("relevant env: %s", " | ".join(lines))


def should_dump_full_env_config_log() -> bool:
    return os.environ.get("REALTALK_SKIP_ENV_CONFIG_LOG", "").strip() != "1"


def log_env_config_summary(
    logger: logging.Logger,
    payload: Dict[str, Any],
    config_path: Path,
    *,
    step: str = "",
    overrides: Optional[Dict[str, Any]] = None,
    pipeline_cli: Optional[Dict[str, Any]] = None,
    footer: Optional[Dict[str, Any]] = None,
) -> None:
    title = step.strip() or "env config"
    if not should_dump_full_env_config_log():
        logger.info(
            "=== %s | file=%s (full YAML dump suppressed; set by pipeline parent) ===",
            title,
            config_path,
        )
        if footer:
            for fk, fv in footer.items():
                logger.info("  %s: %s", fk, fv)
        log_relevant_env_vars(logger)
        logger.info("=== end %s ===", title)
        return

    snap = build_env_config_snapshot(payload, overrides=overrides, pipeline_cli=pipeline_cli)
    try:
        text = yaml.safe_dump(
            snap,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
    except Exception:
        text = str(snap)

    logger.info("=== %s | file=%s ===", title, config_path)
    for line in text.rstrip().splitlines():
        logger.info("  %s", line)
    if footer:
        logger.info("  --- effective / extra ---")
        for fk, fv in footer.items():
            logger.info("  %s: %s", fk, fv)
    log_relevant_env_vars(logger)
    logger.info("=== end %s ===", title)
