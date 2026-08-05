"""Tool-suite subsystem (registry machinery + loader; built-ins under ``default/``)."""

from __future__ import annotations

from .base import (
    ControllerCapabilityContext,
    ControllerCapabilitySpec,
    ControllerToolBuilder,
)
from .default import (
    DEEP_RECALL_CAPABILITY,
    EMAIL_ASK_CAPABILITY,
    EMAIL_READ_CAPABILITY,
    EMAIL_SEND_CAPABILITY,
    GET_CURRENT_TIME_CAPABILITY,
    SCHEDULE_CREATE_CAPABILITY,
    SCHEDULE_DELETE_CAPABILITY,
    SCHEDULE_QUERY_CAPABILITY,
    SHALLOW_RECALL_CAPABILITY,
    DEFAULT_CONTROLLER_CAPABILITY_ORDER,
    get_default_capability_registry,
    register_capability,
)
from .registry import (
    ControllerCapabilityRegistry,
    build_controller_tools,
    resolve_enabled_controller_capability_names,
)
from .manifest import ToolCapabilityManifest, load_tool_capability_manifest
from .policy import (
    SUPPORTED_TOOL_SIDE_EFFECTS,
    ToolPolicyError,
    require_supported_side_effect,
)
from .system import (
    ToolSuiteSystem,
    build_default_tool_suite_system,
    load_tool_suite_system,
)

__all__ = [
    "ControllerCapabilityContext",
    "ControllerCapabilityRegistry",
    "ControllerCapabilitySpec",
    "ControllerToolBuilder",
    "DEEP_RECALL_CAPABILITY",
    "DEFAULT_CONTROLLER_CAPABILITY_ORDER",
    "EMAIL_ASK_CAPABILITY",
    "EMAIL_READ_CAPABILITY",
    "EMAIL_SEND_CAPABILITY",
    "GET_CURRENT_TIME_CAPABILITY",
    "SCHEDULE_CREATE_CAPABILITY",
    "SCHEDULE_DELETE_CAPABILITY",
    "SCHEDULE_QUERY_CAPABILITY",
    "SHALLOW_RECALL_CAPABILITY",
    "SUPPORTED_TOOL_SIDE_EFFECTS",
    "ToolSuiteSystem",
    "ToolCapabilityManifest",
    "ToolPolicyError",
    "build_controller_tools",
    "build_default_tool_suite_system",
    "get_default_capability_registry",
    "load_tool_suite_system",
    "load_tool_capability_manifest",
    "register_capability",
    "require_supported_side_effect",
    "resolve_enabled_controller_capability_names",
]
