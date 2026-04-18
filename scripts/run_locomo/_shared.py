from __future__ import annotations

import copy
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

from _bootstrap import bootstrap_project


bootstrap_project()


DEFAULT_ENV_CONFIG_PATH = "config/eval/memory_agent/locomo/test_env.yaml"
DEFAULT_LONGMEMEVAL_ENV_CONFIG_PATH = "config/eval/memory_agent/longmemeval/test_env.yaml"


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


def parse_question_ids(payload: Dict[str, Any]) -> List[str]:
    """LongMemEval: selection.question_ids (list or comma-separated string)."""
    selection = payload.get("selection", {})
    if not isinstance(selection, dict):
        selection = {}

    raw = selection.get("question_ids", [])
    if isinstance(raw, str):
        raw_values = [token.strip() for token in raw.split(",") if token.strip()]
    elif isinstance(raw, list):
        raw_values = [str(item).strip() for item in raw]
    else:
        raw_values = []

    qids: List[str] = []
    seen = set()
    for value in raw_values:
        if not value or value in seen:
            continue
        seen.add(value)
        qids.append(value)

    return qids


def resolve_target_question_ids(payload: Dict[str, Any]) -> List[str]:
    qids = parse_question_ids(payload)
    if not qids:
        raise ValueError(
            "No target question_ids configured. Provide selection.question_ids in the env YAML."
        )
    return qids


def _safe_memory_path_segment(name: str) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip())
    return (out[:200] if out else "q").strip("._-")


def resolve_longmemeval_memory_id(payload: Dict[str, Any], cli_workflow_id: str = "") -> str:
    """
    Memory root under data/memory/<id>/ — must match import_longmemeval_one process_id.

    Resolution order: CLI --workflow-id, then import.process_id, then
    ``longmemeval/<data_json_stem>/<question_id_seg>`` when selection.question_ids has exactly one id.
    """
    w = str(cli_workflow_id or "").strip()
    if w:
        return w

    import_cfg = payload.get("import", {})
    if not isinstance(import_cfg, dict):
        import_cfg = {}
    process_id = str(import_cfg.get("process_id", "") or "").strip()
    if process_id:
        return process_id

    qids = parse_question_ids(payload)
    if len(qids) != 1:
        raise ValueError(
            "LongMemEval: pass --workflow-id, or set import.process_id in the env YAML, "
            "or set selection.question_ids to exactly one id so "
            "longmemeval/<data_stem>/<question_id> can be derived (same rule as import)."
        )

    data_cfg = get_data_config(payload)
    data_path = resolve_project_path(data_cfg["file"])
    stem = _safe_memory_path_segment(data_path.stem)
    qid_seg = _safe_memory_path_segment(qids[0])
    return f"longmemeval/{stem}/{qid_seg}"


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


def _deep_merge_dict(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow-merge top-level keys; for dict values, update shallowly."""
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = copy.deepcopy(v)
    return out


def build_env_config_snapshot(
    payload: Dict[str, Any],
    overrides: Optional[Dict[str, Any]] = None,
    pipeline_cli: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Snapshot of test_env-style keys for logging (no prompt/runtime file contents)."""
    keys = ("name", "description", "data", "selection", "import", "warmup", "eval")
    snap: Dict[str, Any] = {k: copy.deepcopy(payload[k]) for k in keys if k in payload}
    if overrides:
        snap = _deep_merge_dict(snap, overrides)
    if pipeline_cli:
        snap["pipeline_cli"] = copy.deepcopy(pipeline_cli)
    return snap


def log_relevant_env_vars(logger: logging.Logger, keys: Optional[Tuple[str, ...]] = None) -> None:
    """Log selected OS env vars that affect import/warmup (not full environ)."""
    if keys is None:
        keys = (
            "M_AGENT_EPISODE_MAX_WORKERS",
            "M_AGENT_SCENE_MAX_WORKERS",
            "M_AGENT_SCENE_FACT_MAX_WORKERS",
            "EMBED_PROVIDER",
        )
    lines = []
    for k in keys:
        if k in os.environ:
            lines.append(f"{k}={os.environ[k]}")
    if lines:
        logger.info("relevant env: %s", " | ".join(lines))


def should_dump_full_env_config_log() -> bool:
    """When subprocess is spawned by run_locomo_pipeline.py, skip duplicate full YAML dump."""
    return os.environ.get("LOCOMO_SKIP_ENV_CONFIG_LOG", "").strip() != "1"


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
    """
    Log data/selection/import/warmup/eval from env YAML (paths and scalars only — no prompt bodies).

    Does not load memory_agent YAML or runtime prompt files; only paths already present in payload.
    """
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
