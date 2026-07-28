"""Background run manager used by the local acceptance dashboard."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence

from .runner import AcceptanceRunner, RunBusyError


@dataclass
class _RunState:
    run_id: str
    profile: str
    invariant_ids: list[str]
    status: str = "queued"
    result: Optional[Dict[str, Any]] = None
    error: str = ""
    thread: Optional[threading.Thread] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "profile": self.profile,
            "invariant_ids": list(self.invariant_ids),
            "status": self.status,
            "result": self.result,
            "error": self.error,
        }


class RunManager:
    """Own at most one queued/running acceptance job."""

    def __init__(self, runner: AcceptanceRunner) -> None:
        self.runner = runner
        self._lock = threading.RLock()
        self._runs: Dict[str, _RunState] = {}
        self._active_run_id: Optional[str] = None

    def start(
        self,
        *,
        invariant_ids: Optional[Sequence[str]],
        profile: str,
        timeout_seconds: float = 300.0,
    ) -> Dict[str, Any]:
        # Validate before allocating a visible job.
        from .catalog import get_invariants

        selected = get_invariants(invariant_ids)
        if profile not in {"gate", "full"}:
            raise ValueError("profile must be 'gate' or 'full'")
        timeout = float(timeout_seconds)
        if timeout <= 0 or timeout > 3600:
            raise ValueError("timeout_seconds must be between 0 and 3600")
        with self._lock:
            if self._active_run_id is not None:
                active = self._runs.get(self._active_run_id)
                if active is not None and active.status in {"queued", "running"}:
                    raise RunBusyError("another acceptance run is already active")
            run_id = self.runner.store.new_run_id()
            state = _RunState(
                run_id=run_id,
                profile=profile,
                invariant_ids=[item.invariant_id for item in selected],
            )
            self._runs[run_id] = state
            self._active_run_id = run_id
            worker = threading.Thread(
                target=self._execute,
                args=(state, timeout),
                name=f"acceptance-{run_id}",
                daemon=True,
            )
            state.thread = worker
            worker.start()
            return state.to_dict()

    def _execute(self, state: _RunState, timeout_seconds: float) -> None:
        with self._lock:
            state.status = "running"
        try:
            result = self.runner.run(
                invariant_ids=state.invariant_ids,
                profile=state.profile,
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
        if persisted is None:
            return None
        return {
            "run_id": safe_id,
            "profile": persisted.profile,
            "invariant_ids": persisted.invariant_ids,
            "status": persisted.status,
            "result": persisted.to_dict(include_logs=False),
            "error": persisted.error,
        }

    def cancel(self, run_id: str) -> bool:
        safe_id = self.runner.store.validate_run_id(run_id)
        with self._lock:
            state = self._runs.get(safe_id)
            if state is None or state.status not in {"queued", "running"}:
                return False
            previous_status = state.status
        cancelled = self.runner.cancel(safe_id)
        with self._lock:
            state.status = "cancelling" if cancelled else previous_status
        return cancelled
