"""Subprocess runner for the P1 Runtime-independent scenario contract."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .models import CaseResult, RunResult
from .runner import AcceptanceRunner, RunBusyError, _bounded, _now_iso
from .scenario_catalog import (
    P1_CONTRACT_SUITE,
    get_scenarios,
)
from .scenario_contract import (
    RUNTIME_IDS,
    aggregate_scenarios,
    annotate_scenario_cases,
    selected_variants,
)


class ScenarioAcceptanceRunner(AcceptanceRunner):
    """Execute only allowlisted tests declared by P1 scenario bindings."""

    def run_contract(
        self,
        *,
        scenario_ids: Optional[Sequence[str]] = None,
        runtime_id: str = "langgraph_v1",
        layers: Sequence[str] = ("core",),
        timeout_seconds: float = 300.0,
        run_id: Optional[str] = None,
    ) -> RunResult:
        selected = get_scenarios(scenario_ids)
        runtime = str(runtime_id or "").strip().lower()
        if runtime not in RUNTIME_IDS:
            raise ValueError(f"unknown Runtime id: {runtime}")
        selections = selected_variants(selected, layers=layers)
        if not selections:
            raise ValueError("selection contains no variants for the requested layers")
        normalized_layers = tuple(
            dict.fromkeys(
                variant.layer
                for _scenario, variant in selections
            )
        )
        timeout = float(timeout_seconds)
        if timeout <= 0 or timeout > 3600:
            raise ValueError("timeout_seconds must be between 0 and 3600")
        safe_run_id = run_id or self.store.new_run_id()
        self.store.validate_run_id(safe_run_id)

        nodeids: List[str] = []
        seen_nodeids: set[str] = set()
        for _scenario, variant in selections:
            binding = variant.binding_for(runtime)
            if binding.availability != "executable":
                continue
            for ref in binding.tests:
                if ref.nodeid not in seen_nodeids:
                    seen_nodeids.add(ref.nodeid)
                    nodeids.append(ref.nodeid)

        if not self._lock.acquire(blocking=False):
            raise RunBusyError("another acceptance run is already active")

        started_at = _now_iso()
        started = time.monotonic()
        paths: Dict[str, Path] = {}
        stdout = ""
        stderr = ""
        exit_code: Optional[int] = None
        run_status = "passed"
        error = ""
        cases: List[CaseResult] = []
        try:
            paths = self.store.prepare(safe_run_id)
            if nodeids:
                command = self._command(nodeids=nodeids, paths=paths)
                creationflags = 0
                if os.name == "nt":
                    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
                environment = self._environment(
                    paths["temp_dir"],
                    paths["pytest"],
                )
                environment["M_AGENT_ACCEPTANCE_RUNTIME_ID"] = runtime
                process = subprocess.Popen(
                    command,
                    cwd=str(self.project_root),
                    env=environment,
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
        cases = annotate_scenario_cases(
            cases,
            selections,
            runtime_id=runtime,
        )
        scenario_results = aggregate_scenarios(
            selections,
            runtime_id=runtime,
            cases=cases,
            run_status=run_status,
        )
        if run_status == "passed":
            statuses = {item.status for item in scenario_results}
            if statuses & {"failed", "error"}:
                run_status = "failed"
            elif statuses & {"not_covered", "not_implemented", "future"}:
                run_status = "incomplete"
            elif "known_gap" in statuses:
                run_status = "known_gaps"

        artifacts = {
            "summary": "summary",
            "stdout": "stdout",
            "stderr": "stderr",
        }
        if paths and paths["pytest"].is_file():
            artifacts["pytest"] = "pytest"
        if paths and paths["junit"].is_file():
            artifacts["junit"] = "junit"
        result = RunResult(
            run_id=safe_run_id,
            suite=P1_CONTRACT_SUITE,
            profile=",".join(normalized_layers),
            status=run_status,
            invariant_ids=[],
            started_at=started_at,
            finished_at=_now_iso(),
            duration_seconds=max(0.0, time.monotonic() - started),
            pytest_exit_code=exit_code,
            cases=cases,
            invariants=[],
            stdout=stdout,
            stderr=stderr,
            error=error,
            artifacts=artifacts,
            result_kind="scenario_contract",
            runtime_id=runtime,
            scenario_ids=[item.scenario_id for item in selected],
            scenarios=scenario_results,
        )
        if paths:
            self.store.save_result(result, paths)
        return result
