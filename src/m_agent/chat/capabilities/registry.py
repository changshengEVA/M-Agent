from __future__ import annotations

from typing import Any, Dict, List

from .base import ControllerCapabilityContext, ControllerCapabilitySpec
from .email_ops import EMAIL_ASK_CAPABILITY, EMAIL_READ_CAPABILITY, EMAIL_SEND_CAPABILITY
from .recall import DEEP_RECALL_CAPABILITY, SHALLOW_RECALL_CAPABILITY
from .schedule_ops import SCHEDULE_MANAGE_CAPABILITY, SCHEDULE_QUERY_CAPABILITY
from .time_context import GET_CURRENT_TIME_CAPABILITY


DEFAULT_CONTROLLER_CAPABILITY_ORDER = (
    SHALLOW_RECALL_CAPABILITY.name,
    DEEP_RECALL_CAPABILITY.name,
    GET_CURRENT_TIME_CAPABILITY.name,
    SCHEDULE_MANAGE_CAPABILITY.name,
    SCHEDULE_QUERY_CAPABILITY.name,
    EMAIL_ASK_CAPABILITY.name,
    EMAIL_READ_CAPABILITY.name,
    EMAIL_SEND_CAPABILITY.name,
)

_BUILTIN_CONTROLLER_CAPABILITIES: Dict[str, ControllerCapabilitySpec] = {
    spec.name: spec
    for spec in (
        SHALLOW_RECALL_CAPABILITY,
        DEEP_RECALL_CAPABILITY,
        GET_CURRENT_TIME_CAPABILITY,
        SCHEDULE_MANAGE_CAPABILITY,
        SCHEDULE_QUERY_CAPABILITY,
        EMAIL_ASK_CAPABILITY,
        EMAIL_READ_CAPABILITY,
        EMAIL_SEND_CAPABILITY,
    )
}


def resolve_enabled_controller_capability_names(raw_names: Any) -> List[str]:
    if raw_names is None:
        return list(DEFAULT_CONTROLLER_CAPABILITY_ORDER)
    if not isinstance(raw_names, list):
        raise ValueError("`enabled_tools` must be a list when provided in chat controller config")

    enabled_names: List[str] = []
    for item in raw_names:
        name = str(item or "").strip()
        if not name:
            continue
        if name not in _BUILTIN_CONTROLLER_CAPABILITIES:
            supported = ", ".join(sorted(_BUILTIN_CONTROLLER_CAPABILITIES))
            raise ValueError(f"Unknown chat controller tool: {name}. Supported tools: {supported}")
        if name not in enabled_names:
            enabled_names.append(name)

    return enabled_names or list(DEFAULT_CONTROLLER_CAPABILITY_ORDER)


def build_controller_tools(
    *,
    context: ControllerCapabilityContext,
    enabled_tool_names: List[str],
    tool_descriptions: Dict[str, str],
) -> List[Any]:
    tools: List[Any] = []
    for tool_name in enabled_tool_names:
        spec = _BUILTIN_CONTROLLER_CAPABILITIES[tool_name]
        description = str(tool_descriptions.get(tool_name, "") or "").strip() or f"Top-level tool: {tool_name}"
        tools.append(spec.build_tool(context, description))
    return tools
