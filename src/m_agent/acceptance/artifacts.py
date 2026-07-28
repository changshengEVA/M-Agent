"""Safe, repository-local artifact storage for acceptance runs."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .models import RunResult


_RUN_ID_RE = re.compile(r"^run_[0-9a-f]{20}$")
_ARTIFACT_FILES = {
    "summary": "summary.json",
    "pytest": "pytest-report.json",
    "junit": "junit.xml",
    "stdout": "stdout.log",
    "stderr": "stderr.log",
}


def resolve_project_root(explicit: Optional[Path] = None) -> Path:
    """Locate the repository containing pyproject.toml and tests/."""

    starts = []
    if explicit is not None:
        starts.append(Path(explicit).resolve())
    starts.extend([Path.cwd().resolve(), Path(__file__).resolve().parents[3]])
    checked: set[Path] = set()
    for start in starts:
        for candidate in (start, *start.parents):
            if candidate in checked:
                continue
            checked.add(candidate)
            if (candidate / "pyproject.toml").is_file() and (candidate / "tests").is_dir():
                return candidate
    raise RuntimeError("Could not locate the M-Agent project root.")


class ArtifactStore:
    """Persist reports under a fixed root without accepting client paths."""

    def __init__(self, project_root: Path, artifact_root: Optional[Path] = None) -> None:
        self.project_root = Path(project_root).resolve()
        root = artifact_root or (self.project_root / ".acceptance-runs")
        self.root = Path(root).resolve()

    @staticmethod
    def new_run_id() -> str:
        return f"run_{uuid.uuid4().hex[:20]}"

    @staticmethod
    def validate_run_id(run_id: str) -> str:
        value = str(run_id or "").strip()
        if not _RUN_ID_RE.fullmatch(value):
            raise ValueError("invalid acceptance run id")
        return value

    def run_dir(self, run_id: str, *, create: bool = False) -> Path:
        safe_id = self.validate_run_id(run_id)
        path = (self.root / safe_id).resolve()
        if path.parent != self.root:
            raise ValueError("run path escaped artifact root")
        if create:
            path.mkdir(parents=True, exist_ok=False)
        return path

    def prepare(self, run_id: str) -> Dict[str, Path]:
        run_dir = self.run_dir(run_id, create=True)
        temp_dir = run_dir / "tmp"
        temp_dir.mkdir()
        return {
            "run_dir": run_dir,
            "temp_dir": temp_dir,
            "summary": run_dir / _ARTIFACT_FILES["summary"],
            "pytest": run_dir / _ARTIFACT_FILES["pytest"],
            "junit": run_dir / _ARTIFACT_FILES["junit"],
            "stdout": run_dir / _ARTIFACT_FILES["stdout"],
            "stderr": run_dir / _ARTIFACT_FILES["stderr"],
        }

    def save_result(self, result: RunResult, paths: Dict[str, Path]) -> None:
        paths["stdout"].write_text(result.stdout, encoding="utf-8")
        paths["stderr"].write_text(result.stderr, encoding="utf-8")
        paths["summary"].write_text(
            json.dumps(result.to_dict(include_logs=False), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_result(self, run_id: str) -> Optional[RunResult]:
        path = self.run_dir(run_id) / _ARTIFACT_FILES["summary"]
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        result = RunResult.from_dict(data)
        stdout_path = self.run_dir(run_id) / _ARTIFACT_FILES["stdout"]
        stderr_path = self.run_dir(run_id) / _ARTIFACT_FILES["stderr"]
        if stdout_path.is_file():
            result.stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        if stderr_path.is_file():
            result.stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        return result

    def artifact_path(self, run_id: str, artifact_name: str) -> Path:
        safe_name = str(artifact_name or "").strip().lower()
        filename = _ARTIFACT_FILES.get(safe_name)
        if filename is None:
            raise ValueError("unknown artifact")
        path = (self.run_dir(run_id) / filename).resolve()
        if path.parent != self.run_dir(run_id):
            raise ValueError("artifact path escaped run directory")
        return path
