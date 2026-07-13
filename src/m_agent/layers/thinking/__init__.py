"""Thinking layer for the three-layer agent architecture.

The thinking layer owns the assistant's persona, the per-conversation working
memory and episode-buffer, and decides whether to delegate a turn to the
execution layer. It has NO direct tool-use privileges: any capability that
requires a tool (memory recall, email, schedule, time) is invoked indirectly
by issuing a natural-language instruction to the execution layer.
"""
from __future__ import annotations

from m_agent.layers.perception.contracts import PerceptionInput

from .contracts import (
    TaskProgress,
    TaskProgressUpdate,
    TaskState,
    TaskStateUpdate,
    ThinkingDecision,
    ThinkingSummary,
    TransactionResolution,
)
from .core import ThinkingAgent, ThinkingTurnResult
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
    "ThinkingSummary",
    "ThinkingTurnResult",
    "TransactionResolution",
]
