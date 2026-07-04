"""Process-wide default capability registry (built-in tool bundle)."""
from __future__ import annotations

from m_agent.systems.tools.default.capabilities import (
    DEEP_RECALL_CAPABILITY,
    EMAIL_ASK_CAPABILITY,
    EMAIL_READ_CAPABILITY,
    EMAIL_SEND_CAPABILITY,
    GET_CURRENT_TIME_CAPABILITY,
    REPLY_TO_USER_CAPABILITY,
    SCHEDULE_CREATE_CAPABILITY,
    SCHEDULE_DELETE_CAPABILITY,
    SCHEDULE_QUERY_CAPABILITY,
    SHALLOW_RECALL_CAPABILITY,
)
from m_agent.systems.tools.base import ControllerCapabilitySpec
from m_agent.systems.tools.registry import ControllerCapabilityRegistry

DEFAULT_CONTROLLER_CAPABILITY_ORDER = (
    SHALLOW_RECALL_CAPABILITY.name,
    DEEP_RECALL_CAPABILITY.name,
    GET_CURRENT_TIME_CAPABILITY.name,
    SCHEDULE_CREATE_CAPABILITY.name,
    SCHEDULE_QUERY_CAPABILITY.name,
    SCHEDULE_DELETE_CAPABILITY.name,
    EMAIL_ASK_CAPABILITY.name,
    EMAIL_READ_CAPABILITY.name,
    EMAIL_SEND_CAPABILITY.name,
)

_DEFAULT_CAPABILITY_SPECS = (
    SHALLOW_RECALL_CAPABILITY,
    DEEP_RECALL_CAPABILITY,
    REPLY_TO_USER_CAPABILITY,
    GET_CURRENT_TIME_CAPABILITY,
    SCHEDULE_CREATE_CAPABILITY,
    SCHEDULE_QUERY_CAPABILITY,
    SCHEDULE_DELETE_CAPABILITY,
    EMAIL_ASK_CAPABILITY,
    EMAIL_READ_CAPABILITY,
    EMAIL_SEND_CAPABILITY,
)

_default_registry = ControllerCapabilityRegistry()
for _spec in _DEFAULT_CAPABILITY_SPECS:
    _default_registry.register(_spec)


def get_default_capability_registry() -> ControllerCapabilityRegistry:
    """Return the process-wide default registry (mutable; callers may register extras)."""
    return _default_registry


def register_capability(spec: ControllerCapabilitySpec, *, replace: bool = False) -> None:
    """Register a spec into the process-global default registry."""
    if not isinstance(spec, ControllerCapabilitySpec):
        raise TypeError(f"expected ControllerCapabilitySpec, got {type(spec).__name__}")
    _default_registry.register(spec, replace=replace)


__all__ = [
    "DEFAULT_CONTROLLER_CAPABILITY_ORDER",
    "get_default_capability_registry",
    "register_capability",
]
