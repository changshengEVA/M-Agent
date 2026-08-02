"""Callbacks for schedule store updates when HEARTBEAT stimuli run."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol


class ScheduleLifecycleCallback(Protocol):
    def on_schedule_processing_started(
        self,
        *,
        owner_id: str,
        thread_id: str,
        schedule_id: str,
        run_id: str,
        stimulus_id: str,
    ) -> None: ...

    def on_schedule_processing_finished(
        self,
        *,
        owner_id: str,
        thread_id: str,
        schedule_id: str,
        run_id: str,
        success: bool,
        answer: str = "",
        error: str = "",
        memory_capture: Optional[Dict[str, Any]] = None,
    ) -> None: ...


ScheduleLifecycleHook = Optional[ScheduleLifecycleCallback]

__all__ = ["ScheduleLifecycleCallback", "ScheduleLifecycleHook"]
