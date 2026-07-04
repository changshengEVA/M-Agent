"""Default tool-suite integration (registry + built-in capabilities)."""
from __future__ import annotations

from m_agent.systems.tools.default.capabilities import (
    DEEP_RECALL_CAPABILITY,
    EMAIL_ASK_CAPABILITY,
    EMAIL_READ_CAPABILITY,
    EMAIL_SEND_CAPABILITY,
    GET_CURRENT_TIME_CAPABILITY,
    SCHEDULE_CREATE_CAPABILITY,
    SCHEDULE_DELETE_CAPABILITY,
    SCHEDULE_QUERY_CAPABILITY,
    SHALLOW_RECALL_CAPABILITY,
)
from m_agent.systems.tools.default.registry import (
    DEFAULT_CONTROLLER_CAPABILITY_ORDER,
    get_default_capability_registry,
    register_capability,
)

__all__ = [
    "DEFAULT_CONTROLLER_CAPABILITY_ORDER",
    "DEEP_RECALL_CAPABILITY",
    "EMAIL_ASK_CAPABILITY",
    "EMAIL_READ_CAPABILITY",
    "EMAIL_SEND_CAPABILITY",
    "GET_CURRENT_TIME_CAPABILITY",
    "SCHEDULE_CREATE_CAPABILITY",
    "SCHEDULE_DELETE_CAPABILITY",
    "SCHEDULE_QUERY_CAPABILITY",
    "SHALLOW_RECALL_CAPABILITY",
    "get_default_capability_registry",
    "register_capability",
]
