"""Execution layer for the three-layer agent architecture.

The execution layer is a Controller-style agent that owns LLM-with-tools loops
and exposes a capability boundary to the thinking layer above. It is intentionally
persona-less; the persona / planning lives in the thinking layer.
"""
from __future__ import annotations

from .contracts import (
    CapabilityDescriptor,
    ExecutionRequest,
    ExecutionResult,
    ParamFillResult,
)
from .core import ExecutionAgent
from .model_provider import ModelProvider, build_model_provider_from_config

__all__ = [
    "CapabilityDescriptor",
    "ExecutionAgent",
    "ExecutionRequest",
    "ExecutionResult",
    "ModelProvider",
    "ParamFillResult",
    "build_model_provider_from_config",
]
