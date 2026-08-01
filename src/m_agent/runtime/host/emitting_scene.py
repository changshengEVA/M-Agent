"""Scene writer wrapper that fans out appends to a thread-event sink."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from m_agent.runtime.think_life.contracts import SceneEntry
from m_agent.systems.scene.protocols import SceneWriter

logger = logging.getLogger(__name__)

SceneAppendHook = Callable[[str, SceneEntry], None]


class EmittingSceneWriter:
    """Wraps SceneWriter to invoke ``on_appended`` after each successful append."""

    def __init__(self, inner: SceneWriter, on_appended: SceneAppendHook) -> None:
        self._inner = inner
        self._on_appended = on_appended

    def append(
        self,
        thread_id: str,
        entry: SceneEntry,
        *,
        append_id: Optional[str] = None,
    ) -> SceneEntry:
        if append_id is not None:
            try:
                stored = self._inner.append(
                    thread_id,
                    entry,
                    append_id=append_id,
                )
            except TypeError:
                stored = self._inner.append(thread_id, entry)
        else:
            stored = self._inner.append(thread_id, entry)
        try:
            self._on_appended(thread_id, stored)
        except Exception:
            logger.exception("scene on_appended hook failed thread_id=%s", thread_id)
        return stored
