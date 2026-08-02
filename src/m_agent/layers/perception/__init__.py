"""Perception layer: normalize chat/runtime payloads for the thinking layer."""
from __future__ import annotations

from m_agent.layers.perception.assemble import (
    build_perception_input,
    normalize_history_messages,
)
from m_agent.layers.perception.contracts import (
    ActivationFrame,
    EventFrame,
    EvidenceFrame,
    ObjectiveFrame,
    PerceptionInput,
    Stimulus,
    StimulusKind,
)

__all__ = [
    "ActivationFrame",
    "EventFrame",
    "EvidenceFrame",
    "ObjectiveFrame",
    "PerceptionInput",
    "Stimulus",
    "StimulusKind",
    "build_perception_input",
    "normalize_history_messages",
]
