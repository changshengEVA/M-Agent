"""Plan-only thinking layer used by the Think-life scheduler.

The selected transaction owns task state, working memory, and episode notes.
The thinking layer owns persona-guided decisions but has no direct tool-use
privileges; the scheduler delegates each selected capability.
"""
from __future__ import annotations

from m_agent.layers.perception.contracts import PerceptionInput

from .contracts import (
    TaskProgress,
    TaskProgressUpdate,
    TaskState,
    TaskStateUpdate,
    ThinkingDecision,
    TransactionResolution,
)
from .core import ThinkingAgent
from .state import (
    ConversationState,
    ConversationStateRegistry,
)

__all__ = [
    "ConversationState",
    "ConversationStateRegistry",
    "PerceptionInput",
    "TaskProgress",
    "TaskProgressUpdate",
    "TaskState",
    "TaskStateUpdate",
    "ThinkingAgent",
    "ThinkingDecision",
    "TransactionResolution",
]
