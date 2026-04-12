from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml


THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = THIS_FILE.parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from _bootstrap import bootstrap_project


bootstrap_project()


DEFAULT_ENV_CONFIG_PATH = "config/eval/memory_agent/locomo/test_env.yaml"


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


def parse_conv_ids(payload: Dict[str, Any]) -> List[str]:
    selection = payload.get("selection", {})
    if not isinstance(selection, dict):
        selection = {}

    raw_conv_ids = selection.get("conv_ids", [])
    if isinstance(raw_conv_ids, str):
        raw_values = [token.strip() for token in raw_conv_ids.split(",")]
    elif isinstance(raw_conv_ids, list):
        raw_values = [str(item).strip() for item in raw_conv_ids]
    else:
        raw_values = []

    conv_ids: List[str] = []
    seen = set()
    for value in raw_values:
        if not value or value in seen:
            continue
        seen.add(value)
        conv_ids.append(value)

    return conv_ids


def _parse_qa_indices(raw_value: Any, item_label: str) -> List[int]:
    if isinstance(raw_value, int):
        raw_items = [raw_value]
    elif isinstance(raw_value, list):
        raw_items = raw_value
    else:
        raise ValueError(f"{item_label}: qa_indices must be int or list[int].")

    parsed: List[int] = []
    seen = set()
    for raw in raw_items:
        try:
            idx = int(raw)
        except Exception as exc:
            raise ValueError(f"{item_label}: invalid qa index {raw!r}.") from exc
        if idx < 0:
            raise ValueError(f"{item_label}: qa index must be >= 0, got {idx}.")
        if idx in seen:
            continue
        seen.add(idx)
        parsed.append(idx)
    if not parsed:
        raise ValueError(f"{item_label}: qa_indices cannot be empty.")
    return parsed


def parse_question_selection(payload: Dict[str, Any]) -> Dict[str, List[int]]:
    selection = payload.get("selection", {})
    if not isinstance(selection, dict):
        selection = {}

    questions = selection.get("questions", [])
    if questions is None:
        questions = []
    if not isinstance(questions, list):
        raise ValueError("selection.questions must be a list when provided.")

    parsed: Dict[str, List[int]] = {}
    seen_per_sample: Dict[str, set[int]] = {}
    for i, item in enumerate(questions, start=1):
        label = f"selection.questions[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label}: each item must be a mapping.")
        sample_id = str(item.get("sample_id", "") or "").strip()
        if not sample_id:
            raise ValueError(f"{label}: sample_id is required.")

        if "qa_indices" in item:
            qa_indices = _parse_qa_indices(item.get("qa_indices"), label)
        elif "qa_index" in item:
            qa_indices = _parse_qa_indices(item.get("qa_index"), label)
        else:
            raise ValueError(f"{label}: provide qa_indices or qa_index.")

        target = parsed.setdefault(sample_id, [])
        target_seen = seen_per_sample.setdefault(sample_id, set())
        for idx in qa_indices:
            if idx in target_seen:
                continue
            target_seen.add(idx)
            target.append(idx)

    return parsed


def resolve_target_conv_ids(payload: Dict[str, Any]) -> List[str]:
    conv_ids = parse_conv_ids(payload)
    question_selection = parse_question_selection(payload)

    seen = set(conv_ids)
    merged = list(conv_ids)
    for sample_id in question_selection.keys():
        if sample_id in seen:
            continue
        seen.add(sample_id)
        merged.append(sample_id)

    if not merged:
        raise ValueError(
            "No target conversations configured. Provide selection.conv_ids or selection.questions."
        )
    return merged


def get_data_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    data_cfg = payload.get("data", {})
    if not isinstance(data_cfg, dict):
        data_cfg = {}

    file_path = str(data_cfg.get("file", "data/locomo/data/locomo10.json") or "").strip()
    if not file_path:
        raise ValueError("data.file must not be empty.")

    loader_type = str(data_cfg.get("loader_type", "locomo") or "locomo").strip()
    if not loader_type:
        loader_type = "locomo"

    return {
        "file": file_path,
        "loader_type": loader_type,
    }
