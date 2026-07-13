"""Perception-layer data contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class StimulusKind(str, Enum):
    USER_MESSAGE = "user_message"
    EXECUTION_FEEDBACK = "execution_feedback"
    SCHEDULED_PLAN = "scheduled_plan"
    OBSERVATION_TRIGGER = "observation_trigger"


@dataclass(frozen=True)
class Stimulus:
    """Readable event content presented to the thinking layer."""

    kind: StimulusKind
    text: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PerceptionInput:
    """Structured input handed to the thinking layer by the perception layer.

    Built from the raw HTTP request (or heartbeat-triggered schedule item) so
    the thinking layer never has to parse the original message format.
    """

    thread_id: str
    conversation_id: str
    transaction_id: Optional[str]
    stimulus: Stimulus
    dialogue_history: List[Dict[str, str]] = field(default_factory=list)
    scene_context: str = ""
