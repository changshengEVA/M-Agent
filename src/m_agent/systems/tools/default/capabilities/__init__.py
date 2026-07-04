"""Built-in capability implementations bundled with the tool-suite system.

Built-in capabilities for the ``default`` tool-suite integration.
Register extras via :class:`~m_agent.systems.tools.registry.ControllerCapabilityRegistry`.
"""
from __future__ import annotations

from .email_ops import (
    EMAIL_ASK_CAPABILITY,
    EMAIL_READ_CAPABILITY,
    EMAIL_SEND_CAPABILITY,
)
from .recall import DEEP_RECALL_CAPABILITY, SHALLOW_RECALL_CAPABILITY
from .schedule_ops import (
    SCHEDULE_CREATE_CAPABILITY,
    SCHEDULE_DELETE_CAPABILITY,
    SCHEDULE_QUERY_CAPABILITY,
)
from .reply_to_user import REPLY_TO_USER_CAPABILITY
from .time_context import GET_CURRENT_TIME_CAPABILITY

__all__ = [
    "DEEP_RECALL_CAPABILITY",
    "EMAIL_ASK_CAPABILITY",
    "EMAIL_READ_CAPABILITY",
    "EMAIL_SEND_CAPABILITY",
    "GET_CURRENT_TIME_CAPABILITY",
    "REPLY_TO_USER_CAPABILITY",
    "SCHEDULE_CREATE_CAPABILITY",
    "SCHEDULE_DELETE_CAPABILITY",
    "SCHEDULE_QUERY_CAPABILITY",
    "SHALLOW_RECALL_CAPABILITY",
]
