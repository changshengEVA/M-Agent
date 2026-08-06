"""Injectable clock for deterministic stimulus / schedule timing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

Clock = Callable[[], str]


def wall_clock_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class VirtualClock:
    """Mutable clock for Stimulus Lab and offline replay."""

    def __init__(self, start_at: str) -> None:
        self._now = str(start_at or "").strip() or wall_clock_iso()

    def __call__(self) -> str:
        return self._now

    def set(self, when: str) -> str:
        self._now = str(when or "").strip() or self._now
        return self._now

    def advance_iso(self, when: str) -> str:
        return self.set(when)
