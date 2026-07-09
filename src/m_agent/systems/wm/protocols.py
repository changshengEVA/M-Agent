"""Plug-in protocols for the working-memory (WM) system.

The thinking layer holds a per-conversation list of WM entries
(``ConversationState.wm_entries``) and depends on two duck-typed protocols
to read / write that list:

* :class:`WMWriter` — projects the execution layer's tool history into
  compact WM entries (the cross-turn "what tools did I just use" log).
* :class:`WMReader` — renders the stored entries into a system-prompt
  block that the thinking layer injects when planning / summarizing.
* :class:`WMDisplay` — optional renderer for execution-layer diagnostics or
  custom runtimes (default chat execution does not consume WM).

Protocols are intentionally narrow so future semantic-retrieval readers
or execution-specific displays can be swapped in via YAML.
"""
from __future__ import annotations

from typing import Any, Dict, List, Protocol, runtime_checkable


@runtime_checkable
class WMReader(Protocol):
    """Render the in-conversation WM entries as a string for prompt injection."""

    def render(self, entries: List[Dict[str, Any]], *, language: str) -> str:
        ...


@runtime_checkable
class WMWriter(Protocol):
    """Project an execution-layer tool history into compact WM entries.

    The writer mutates ``entries`` in-place by appending; it should also
    enforce any ``max_stored_entries`` cap configured on its private state.
    """

    def write(
        self,
        entries: List[Dict[str, Any]],
        tool_history: List[Dict[str, Any]],
    ) -> None:
        ...


@runtime_checkable
class WMDisplay(Protocol):
    """Render recent WM entries for optional execution-layer prompt display."""

    def render(self, entries: List[Dict[str, Any]], *, language: str) -> str:
        ...


__all__ = ["WMReader", "WMWriter", "WMDisplay"]
