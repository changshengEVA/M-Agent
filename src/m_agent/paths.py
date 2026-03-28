from __future__ import annotations

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


def memory_workflow_dir(workflow_id: str) -> Path:
    return DATA_DIR / "memory" / str(workflow_id)


def memory_stage_dir(workflow_id: str, stage_name: str) -> Path:
    return memory_workflow_dir(workflow_id) / str(stage_name)
