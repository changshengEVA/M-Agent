"""Plan-only thinking layer used by the Runtime scheduler.

The selected transaction owns task state, working memory, and episode notes.
The thinking layer owns persona-guided decisions but has no direct tool-use
privileges; the scheduler delegates each selected capability.
"""
from __future__ import annotations

from m_agent.layers.perception.contracts import PerceptionInput

from .contracts import (
    DecisionOutput,
    TASK_COMPLETION_AWAITING_USER,
    TASK_COMPLETION_COMPLETED,
    TASK_COMPLETION_PROCESSING,
    TaskProgress,
    TaskProgressUpdate,
    TaskCompletionStatus,
    TaskState,
    TaskStateOutput,
    TaskStateUpdate,
    ThinkingDecision,
    ThinkingTurnOutput,
    TransactionResolution,
    normalize_task_completion_status,
)
from .core import ThinkingAgent
from .state import (
    ConversationState,
    ConversationStateRegistry,
)

__all__ = [
    "ConversationState",
    "ConversationStateRegistry",
    "DecisionOutput",
    "PerceptionInput",
    "TASK_COMPLETION_AWAITING_USER",
    "TASK_COMPLETION_COMPLETED",
    "TASK_COMPLETION_PROCESSING",
    "TaskCompletionStatus",
    "TaskProgress",
    "TaskProgressUpdate",
    "TaskState",
    "TaskStateOutput",
    "TaskStateUpdate",
    "ThinkingAgent",
    "ThinkingDecision",
    "ThinkingTurnOutput",
    "TransactionResolution",
    "normalize_task_completion_status",
]
