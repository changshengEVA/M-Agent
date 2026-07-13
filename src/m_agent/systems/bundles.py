"""Aggregate container for the three pluggable subsystems.

:class:`SystemsBundle` is the single object that
:class:`~m_agent.chat.three_layer_chat_agent.ThreeLayerChatAgent` consumes
to wire the WM, episodic-memory, and tool-suite subsystems. The bundle
itself is just a dataclass — the loader machinery lives in
:mod:`m_agent.systems.loader`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .episodic import EpisodicMemorySystem
from .tools import ToolSuiteSystem
from .wm import WMSystem


@dataclass
class SystemsBundle:
    """Programmatic override container for the three subsystems.

    Any field left as ``None`` falls back to the YAML ``systems:``
    mapping (or built-in defaults) during ``ThreeLayerChatAgent``
    construction. Use :py:meth:`merge_with` to combine an explicit
    user-supplied bundle with one resolved from YAML — the same
    "explicit > YAML > default" precedence is preserved for translated
    plugin-override configurations.
    """

    wm: Optional[WMSystem] = None
    episodic: Optional[EpisodicMemorySystem] = None
    tools: Optional[ToolSuiteSystem] = None

    def merge_with(self, other: "SystemsBundle") -> "SystemsBundle":
        """Return a new bundle where ``self`` wins per-slot over ``other``."""
        return SystemsBundle(
            wm=self.wm if self.wm is not None else other.wm,
            episodic=self.episodic if self.episodic is not None else other.episodic,
            tools=self.tools if self.tools is not None else other.tools,
        )


__all__ = ["SystemsBundle"]
