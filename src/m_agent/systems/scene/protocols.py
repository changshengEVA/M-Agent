"""Scene log protocols (cross-transaction chronological narrative)."""
from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from m_agent.runtime.think_life.contracts import SceneEntry


@runtime_checkable
class SceneWriter(Protocol):
    def append(self, thread_id: str, entry: SceneEntry) -> SceneEntry:
        ...


@runtime_checkable
class SceneReader(Protocol):
    def tail(self, thread_id: str, *, limit: int = 40, before_seq: Optional[int] = None) -> List[SceneEntry]:
        ...

    def entries_since_flush(self, thread_id: str) -> List[SceneEntry]:
        ...
