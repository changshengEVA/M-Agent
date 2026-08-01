"""Background manager for P1 cross-Runtime scenario runs."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence

from .runner import RunBusyError
from .scenario_catalog import get_scenarios
from .scenario_contract import RUNTIME_IDS, selected_variants
from .scenario_runner import ScenarioAcceptanceRunner


@dataclass
class _ScenarioRunState:
    run_id: str
    scenario_ids: list[str]
    runtime_id: str
    layers: list[str]
    status: str = "queued"
    result: Optional[Dict[str, Any]] = None
    error: str = ""
    thread: Optional[threading.Thread] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario_ids": list(self.scenario_ids),
            "runtime_id": self.runtime_id,
            "layers": list(self.layers),
            "status": self.status,
            "result": self.result,
            "error": self.error,
        }


class ScenarioRunManager:
    """Own at most one queued/running P1 scenario job."""

    def __init__(self, runner: ScenarioAcceptanceRunner) -> None:
        self.runner = runner
        self._lock = threading.RLock()
        self._runs: Dict[str, _ScenarioRunState] = {}
        self._active_run_id: Optional[str] = None

    def start(
        self,
        *,
        scenario_ids: Optional[Sequence[str]],
        runtime_id: str,
        layers: Sequence[str],
        timeout_seconds: float = 300.0,
    ) -> Dict[str, Any]:
        selected = get_scenarios(scenario_ids)
        runtime = str(runtime_id or "").strip().lower()
        if runtime not in RUNTIME_IDS:
            raise ValueError(f"unknown Runtime id: {runtime}")
        normalized_layers = [
            str(item or "").strip().lower()
            for item in layers
            if str(item or "").strip()
        ]
        selected_variants(selected, layers=normalized_layers)
        timeout = float(timeout_seconds)
        if timeout <= 0 or timeout > 3600:
            raise ValueError("timeout_seconds must be between 0 and 3600")

        with self._lock:
            if self._active_run_id is not None:
                active = self._runs.get(self._active_run_id)
                if active is not None and active.status in {
                    "queued",
                    "running",
                }:
                    raise RunBusyError(
                        "another P1 scenario run is already active"
                    )
            run_id = self.runner.store.new_run_id()
            state = _ScenarioRunState(
                run_id=run_id,
                scenario_ids=[
                    item.scenario_id
                    for item in selected
                ],
                runtime_id=runtime,
                layers=normalized_layers,
            )
            self._runs[run_id] = state
            self._active_run_id = run_id
            worker = threading.Thread(
                target=self._execute,
                args=(state, timeout),
                name=f"acceptance-contract-{run_id}",
                daemon=True,
            )
            state.thread = worker
            worker.start()
            return state.to_dict()

    def _execute(
        self,
        state: _ScenarioRunState,
        timeout_seconds: float,
    ) -> None:
        with self._lock:
            state.status = "running"
        try:
            result = self.runner.run_contract(
                scenario_ids=state.scenario_ids,
                runtime_id=state.runtime_id,
                layers=state.layers,
                timeout_seconds=timeout_seconds,
                run_id=state.run_id,
            )
            with self._lock:
                state.status = result.status
                state.result = result.to_dict(include_logs=False)
        except Exception as exc:
            with self._lock:
                state.status = "error"
                state.error = str(exc)
        finally:
            with self._lock:
                if self._active_run_id == state.run_id:
                    self._active_run_id = None

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        safe_id = self.runner.store.validate_run_id(run_id)
        with self._lock:
            state = self._runs.get(safe_id)
            if state is not None:
                return state.to_dict()
        persisted = self.runner.store.load_result(safe_id)
        if persisted is None or persisted.result_kind != "scenario_contract":
            return None
        return {
            "run_id": safe_id,
            "scenario_ids": list(persisted.scenario_ids),
            "runtime_id": persisted.runtime_id,
            "layers": [
                item
                for item in persisted.profile.split(",")
                if item
            ],
            "status": persisted.status,
            "result": persisted.to_dict(include_logs=False),
            "error": persisted.error,
        }

    def latest(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        """Return the newest P1 run for one Runtime, including an active run."""

        runtime = str(runtime_id or "").strip().lower()
        if runtime not in RUNTIME_IDS:
            raise ValueError(f"unknown Runtime id: {runtime}")

        with self._lock:
            for state in reversed(tuple(self._runs.values())):
                if state.runtime_id == runtime:
                    return state.to_dict()

        for run_id in self.runner.store.list_run_ids():
            try:
                persisted = self.runner.store.load_result(run_id)
            except (KeyError, OSError, TypeError, ValueError):
                continue
            if (
                persisted is None
                or persisted.result_kind != "scenario_contract"
                or persisted.runtime_id != runtime
            ):
                continue
            return {
                "run_id": run_id,
                "scenario_ids": list(persisted.scenario_ids),
                "runtime_id": persisted.runtime_id,
                "layers": [
                    item
                    for item in persisted.profile.split(",")
                    if item
                ],
                "status": persisted.status,
                "result": persisted.to_dict(include_logs=False),
                "error": persisted.error,
            }
        return None

    def cancel(self, run_id: str) -> bool:
        safe_id = self.runner.store.validate_run_id(run_id)
        with self._lock:
            state = self._runs.get(safe_id)
            if state is None or state.status not in {"queued", "running"}:
                return False
            previous = state.status
        cancelled = self.runner.cancel(safe_id)
        with self._lock:
            state.status = "cancelling" if cancelled else previous
        return cancelled
