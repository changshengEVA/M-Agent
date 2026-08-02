"""Scene log protocols (cross-transaction chronological narrative)."""
from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from m_agent.runtime.domain.contracts import SceneEntry


@runtime_checkable
class SceneWriter(Protocol):
    def append(
        self,
        conversation_id: str,
        entry: SceneEntry,
        *,
        append_id: Optional[str] = None,
    ) -> SceneEntry:
        ...


@runtime_checkable
class SceneReader(Protocol):
    def tail(self, conversation_id: str, *, limit: int = 40, before_seq: Optional[int] = None) -> List[SceneEntry]:
        ...

    def entries_since_flush(self, conversation_id: str) -> List[SceneEntry]:
        ...
