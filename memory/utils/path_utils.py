from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_output_path(
    process_id: str,
    stage_name: str,
    project_root: str | Path | None = None,
) -> Path:
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    return root / "data" / "memory" / str(process_id) / str(stage_name)
