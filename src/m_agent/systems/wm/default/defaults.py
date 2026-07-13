"""Default WMReader / WMWriter implementations.

These are thin wrappers around the existing
:mod:`m_agent.chat.working_memory` helpers, so behavior is identical to
the legacy chat-controller path.

Pluggable callers may swap in custom implementations by satisfying the
:class:`~m_agent.systems.wm.protocols.WMReader` /
:class:`~m_agent.systems.wm.protocols.WMWriter` /
:class:`~m_agent.systems.wm.protocols.WMDisplay` protocols.
"""
from __future__ import annotations

from typing import Any, Dict, List

from m_agent.chat.working_memory import (
    WorkingMemoryConfig,
    append_tool_history_to_working_memory,
    format_working_memory_prompt,
)


class DefaultWMReader:
    """Default :class:`WMReader` that uses the legacy tail-N renderer.

    NOTE: this is intentionally NOT a semantic retriever; it formats the
    stored entries directly. The plug-in slot is reserved so a future
    retrieval-based reader can be wired in via YAML without touching the
    thinking layer.
    """

    def __init__(self, config: WorkingMemoryConfig) -> None:
        self.config = config

    def render(
        self,
        entries: List[Dict[str, Any]],
        *,
        language: str,
        task_progress: Any = None,
    ) -> str:
        if not self.config.enable:
            return ""
        return format_working_memory_prompt(
            entries,
            self.config,
            prompt_language=language,
            task_progress=task_progress,
        )


class DefaultWMWriter:
    """Default :class:`WMWriter` that reuses the legacy projection helper."""

    def __init__(self, config: WorkingMemoryConfig) -> None:
        self.config = config

    def write(
        self,
        entries: List[Dict[str, Any]],
        tool_history: List[Dict[str, Any]],
    ) -> None:
        if not self.config.enable:
            return
        append_tool_history_to_working_memory(entries, tool_history, self.config)


class DefaultWMDisplay:
    """Default :class:`WMDisplay` — recent tail via ``inject_max_entries``.

    Uses the same renderer as :class:`DefaultWMReader`. The default chat path
    leaves execution WM display disabled, but custom runtimes may opt in.
    """

    def __init__(self, config: WorkingMemoryConfig) -> None:
        self.config = config

    def render(
        self,
        entries: List[Dict[str, Any]],
        *,
        language: str,
        task_progress: Any = None,
    ) -> str:
        if not self.config.enable:
            return ""
        return format_working_memory_prompt(
            entries,
            self.config,
            prompt_language=language,
            task_progress=task_progress,
        )


__all__ = ["DefaultWMReader", "DefaultWMWriter", "DefaultWMDisplay"]
