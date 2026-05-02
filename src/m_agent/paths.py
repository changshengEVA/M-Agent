from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
DOCS_DIR = PROJECT_ROOT / "docs"
EXAMPLES_DIR = PROJECT_ROOT / "examples"
EXPERIMENTS_DIR = PROJECT_ROOT / "experiments"
LOG_DIR = PROJECT_ROOT / "log"
MODEL_DIR = PROJECT_ROOT / "model"
CHECKPOINTS_DIR = PROJECT_ROOT / "checkpoints"
TOOLS_DIR = PROJECT_ROOT / "tools"
ENV_PATH = PROJECT_ROOT / ".env"


def resolve_project_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def memory_root_dir() -> Path:
    """
    Resolve the base directory for MemoryCore workflow storage.

    Priority:
    1) M_AGENT_MEMORY_ROOT: full path to the directory that contains workflow subdirs
       (e.g. ".../full_model/GPT-4o-mini" so workflow_id "locomo/conv-48" becomes
       ".../full_model/GPT-4o-mini/locomo/conv-48").
    2) M_AGENT_DATA_DIR: full path to a data dir; memory root becomes "<data_dir>/memory".
    3) default: "<PROJECT_ROOT>/data/memory"
    """
    raw = str(os.getenv("M_AGENT_MEMORY_ROOT", "")).strip()
    if raw:
        return Path(raw).expanduser().resolve()
    raw_data = str(os.getenv("M_AGENT_DATA_DIR", "")).strip()
    if raw_data:
        return (Path(raw_data).expanduser().resolve() / "memory")
    return DATA_DIR / "memory"


def memory_workflow_dir(workflow_id: str) -> Path:
    wid = str(workflow_id or "").strip()
    # Allow hierarchical workflow ids like "locomo/conv-48".
    wid = wid.strip("/\\")
    return memory_root_dir() / Path(wid)


def memory_stage_dir(workflow_id: str, stage_name: str) -> Path:
    return memory_workflow_dir(workflow_id) / str(stage_name)
