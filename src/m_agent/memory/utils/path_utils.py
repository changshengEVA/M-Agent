from __future__ import annotations

from pathlib import Path

from m_agent.paths import PROJECT_ROOT, memory_stage_dir


def get_output_path(
    process_id: str,
    stage_name: str,
    project_root: str | Path | None = None,
) -> Path:
    if project_root is not None:
        root = Path(project_root)
        return root / "data" / "memory" / str(process_id) / str(stage_name)
    return memory_stage_dir(str(process_id), str(stage_name))
