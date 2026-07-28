"""Subprocess-only executor for the curated semantic acceptance suite."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .artifacts import ArtifactStore, resolve_project_root
from .catalog import PHASE0_SUITE, get_invariants
from .models import (
    CaseResult,
    RunResult,
    aggregate_invariant,
    annotate_cases,
    unique_nodeids,
)


class RunBusyError(RuntimeError):
    """Raised when a second acceptance subprocess is requested."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded(value: str, limit: int = 200_000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + f"\n\n[acceptance log truncated; {omitted} characters omitted]\n"


class AcceptanceRunner:
    """Run only catalog node IDs in one isolated pytest subprocess at a time."""

    def __init__(
        self,
        *,
        project_root: Optional[Path] = None,
        artifact_root: Optional[Path] = None,
        python_executable: Optional[str] = None,
    ) -> None:
        self.project_root = resolve_project_root(project_root)
        self.store = ArtifactStore(self.project_root, artifact_root)
        self.python_executable = str(python_executable or sys.executable)
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_process: Optional[subprocess.Popen[str]] = None
        self._active_run_id: Optional[str] = None
        self._cancel_requested: set[str] = set()

    def _environment(self, temp_dir: Path, report_path: Path) -> Dict[str, str]:
        allowed = (
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "APPDATA",
            "LOCALAPPDATA",
            "USERPROFILE",
            "HOMEDRIVE",
            "HOMEPATH",
            "PROGRAMDATA",
            "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE",
            "LANG",
            "LC_ALL",
        )
        env = {
            key: value
            for key in allowed
            if (value := os.environ.get(key)) is not None
        }
        env.update(
            {
                "PYTHONPATH": str(self.project_root / "src"),
                "PYTHONIOENCODING": "utf-8",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "M_AGENT_ACCEPTANCE_MODE": "1",
                "M_AGENT_ACCEPTANCE_REPORT": str(report_path),
                "TEMP": str(temp_dir),
                "TMP": str(temp_dir),
            }
        )
        return env

    def _command(
        self,
        *,
        nodeids: Sequence[str],
        paths: Dict[str, Path],
    ) -> List[str]:
        return [
            self.python_executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "--strict-markers",
            "--strict-config",
            "--maxfail=0",
            "--color=no",
            "-q",
            "-ra",
            "-p",
            "no:cacheprovider",
            "-p",
            "m_agent.acceptance.pytest_plugin",
            f"--basetemp={paths['temp_dir'] / 'pytest'}",
            f"--junitxml={paths['junit']}",
            *nodeids,
        ]

    def run(
        self,
        *,
        invariant_ids: Optional[Sequence[str]] = None,
        profile: str = "gate",
        timeout_seconds: float = 300.0,
        run_id: Optional[str] = None,
    ) -> RunResult:
        """Execute a selected allowlist and persist its structured artifacts."""

        selected = get_invariants(invariant_ids)
        if profile not in {"gate", "full"}:
            raise ValueError("profile must be 'gate' or 'full'")
        timeout = float(timeout_seconds)
        if timeout <= 0 or timeout > 3600:
            raise ValueError("timeout_seconds must be between 0 and 3600")
        safe_run_id = run_id or self.store.new_run_id()
        self.store.validate_run_id(safe_run_id)
        if not self._lock.acquire(blocking=False):
            raise RunBusyError("another acceptance run is already active")

        started_at = _now_iso()
        started = time.monotonic()
        paths: Dict[str, Path] = {}
        stdout = ""
        stderr = ""
        exit_code: Optional[int] = None
        run_status = "error"
        error = ""
        cases: List[CaseResult] = []

        try:
            paths = self.store.prepare(safe_run_id)
            nodeids = unique_nodeids(selected, profile=profile)
            command = self._command(nodeids=nodeids, paths=paths)
            creationflags = 0
            if os.name == "nt":
                creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            process = subprocess.Popen(
                command,
                cwd=str(self.project_root),
                env=self._environment(paths["temp_dir"], paths["pytest"]),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                creationflags=creationflags,
            )
            with self._state_lock:
                self._active_process = process
                self._active_run_id = safe_run_id
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                exit_code = int(process.returncode)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                exit_code = int(process.returncode)
                run_status = "timeout"
                error = f"acceptance run exceeded {timeout:g} seconds"

            with self._state_lock:
                cancelled = safe_run_id in self._cancel_requested
            if cancelled:
                run_status = "cancelled"
                error = "acceptance run cancelled"
            elif run_status != "timeout":
                run_status = "passed" if exit_code == 0 else "failed"

            if paths["pytest"].is_file():
                raw = json.loads(paths["pytest"].read_text(encoding="utf-8"))
                cases = [
                    CaseResult.from_dict(item)
                    for item in raw.get("cases", [])
                    if isinstance(item, dict)
                ]
            elif run_status not in {"cancelled", "timeout"}:
                run_status = "error"
                error = "pytest did not produce the structured report"
        except Exception as exc:
            error = str(exc)
            run_status = "error"
        finally:
            with self._state_lock:
                self._active_process = None
                self._active_run_id = None
                self._cancel_requested.discard(safe_run_id)
            self._lock.release()

        stdout = _bounded(stdout)
        stderr = _bounded(stderr)
        cases = annotate_cases(cases, selected, profile=profile)
        case_by_nodeid = {item.nodeid: item for item in cases}
        invariant_results = [
            aggregate_invariant(
                spec,
                case_by_nodeid,
                profile=profile,
                run_status=run_status,
            )
            for spec in selected
        ]
        if run_status == "passed":
            statuses = {item.status for item in invariant_results}
            if "failed" in statuses:
                run_status = "failed"
            elif "known_gap" in statuses:
                run_status = "known_gaps"
            elif "skipped" in statuses:
                run_status = "incomplete"

        result = RunResult(
            run_id=safe_run_id,
            suite=PHASE0_SUITE,
            profile=profile,
            status=run_status,
            invariant_ids=[item.invariant_id for item in selected],
            started_at=started_at,
            finished_at=_now_iso(),
            duration_seconds=max(0.0, time.monotonic() - started),
            pytest_exit_code=exit_code,
            cases=cases,
            invariants=invariant_results,
            stdout=stdout,
            stderr=stderr,
            error=error,
            artifacts={
                "summary": "summary",
                "pytest": "pytest",
                "junit": "junit",
                "stdout": "stdout",
                "stderr": "stderr",
            },
        )
        if paths:
            self.store.save_result(result, paths)
        return result

    def cancel(self, run_id: str) -> bool:
        """Cancel the currently active run; arbitrary process IDs are never accepted."""

        safe_id = self.store.validate_run_id(run_id)
        with self._state_lock:
            if self._active_run_id != safe_id or self._active_process is None:
                return False
            self._cancel_requested.add(safe_id)
            process = self._active_process
        if process.poll() is None:
            process.terminate()
        return True
