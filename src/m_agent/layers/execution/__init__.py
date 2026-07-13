"""Single-capability execution primitives used by the Think-life scheduler.

The layer exposes capability metadata, structured argument filling, and direct
tool invocation. Persona and planning remain in the thinking layer.
"""
from __future__ import annotations

from .contracts import (
    CapabilityDescriptor,
    ExecutionResult,
    ParamFillResult,
)
from .core import ExecutionAgent
from .model_provider import ModelProvider, build_model_provider_from_config

__all__ = [
    "CapabilityDescriptor",
    "ExecutionAgent",
    "ExecutionResult",
    "ModelProvider",
    "ParamFillResult",
    "build_model_provider_from_config",
]
