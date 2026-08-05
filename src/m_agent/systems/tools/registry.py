"""Capability registry machinery (subsystem-neutral)."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .base import ControllerCapabilityContext, ControllerCapabilitySpec
from .policy import require_supported_side_effect


class ControllerCapabilityRegistry:
    """Mutable registry of :class:`ControllerCapabilitySpec` keyed by name."""

    def __init__(self, specs: Optional[Mapping[str, ControllerCapabilitySpec]] = None) -> None:
        self._specs: Dict[str, ControllerCapabilitySpec] = {}
        if specs:
            for spec in specs.values():
                self.register(spec)

    def register(self, spec: ControllerCapabilitySpec, *, replace: bool = False) -> None:
        if not isinstance(spec, ControllerCapabilitySpec):
            raise TypeError(f"expected ControllerCapabilitySpec, got {type(spec).__name__}")
        name = str(spec.name or "").strip()
        if not name:
            raise ValueError("ControllerCapabilitySpec.name must be a non-empty string")
        if name in self._specs and not replace:
            raise ValueError(f"capability already registered: {name}")
        self._specs[name] = spec

    def unregister(self, name: str) -> Optional[ControllerCapabilitySpec]:
        return self._specs.pop(str(name or "").strip(), None)

    def get(self, name: str) -> Optional[ControllerCapabilitySpec]:
        return self._specs.get(str(name or "").strip())

    def __contains__(self, name: str) -> bool:
        return str(name or "").strip() in self._specs

    def names(self) -> List[str]:
        return list(self._specs.keys())

    def copy(self) -> "ControllerCapabilityRegistry":
        return ControllerCapabilityRegistry(self._specs)


def _default_registry() -> ControllerCapabilityRegistry:
    from m_agent.systems.tools.default.registry import get_default_capability_registry

    return get_default_capability_registry()


def _default_capability_order() -> tuple[str, ...]:
    from m_agent.systems.tools.default.registry import DEFAULT_CONTROLLER_CAPABILITY_ORDER

    return DEFAULT_CONTROLLER_CAPABILITY_ORDER


def resolve_enabled_controller_capability_names(
    raw_names: Any,
    *,
    registry: Optional[ControllerCapabilityRegistry] = None,
) -> List[str]:
    active = registry or _default_registry()
    order = _default_capability_order()
    if raw_names is None:
        return [name for name in order if name in active]
    if not isinstance(raw_names, list):
        raise ValueError("`enabled_tools` must be a list when provided in chat controller config")

    enabled_names: List[str] = []
    for item in raw_names:
        name = str(item or "").strip()
        if not name:
            continue
        if name not in active:
            supported = ", ".join(sorted(active.names()))
            raise ValueError(f"Unknown chat controller tool: {name}. Supported tools: {supported}")
        if name not in enabled_names:
            enabled_names.append(name)

    if enabled_names:
        return enabled_names
    return [name for name in order if name in active]


def build_controller_tools(
    *,
    context: ControllerCapabilityContext,
    enabled_tool_names: List[str],
    tool_descriptions: Dict[str, str],
    registry: Optional[ControllerCapabilityRegistry] = None,
) -> List[Any]:
    active = registry or _default_registry()
    tools: List[Any] = []
    for tool_name in enabled_tool_names:
        spec = active.get(tool_name)
        if spec is None:
            supported = ", ".join(sorted(active.names()))
            raise ValueError(f"Unknown chat controller tool: {tool_name}. Supported tools: {supported}")
        # This is the last common point before a capability builder can return
        # an invokable tool.  Unknown or omitted effect metadata must never
        # reach the builder, even for programmatic/third-party registries.
        require_supported_side_effect(spec.side_effect, tool_name=tool_name)
        description = str(tool_descriptions.get(tool_name, "") or "").strip() or f"Top-level tool: {tool_name}"
        tools.append(spec.build_tool(context, description))
    return tools


__all__ = [
    "ControllerCapabilityRegistry",
    "build_controller_tools",
    "resolve_enabled_controller_capability_names",
]
