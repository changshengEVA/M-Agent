"""Small, fail-closed policy helpers for controller tool execution.

The policy intentionally models only the externally relevant effect class.  It
does not attempt to implement approvals or user interaction; a tool suite opts
capabilities in by name, while this module makes malformed effect metadata
non-executable.
"""
from __future__ import annotations

from typing import Any


SUPPORTED_TOOL_SIDE_EFFECTS = frozenset({"read", "emit", "write", "send"})


class ToolPolicyError(RuntimeError):
    """Raised when a capability cannot safely pass the execution policy."""


def require_supported_side_effect(value: Any, *, tool_name: str) -> str:
    """Return a valid side-effect class or fail closed.

    ``unspecified`` is deliberately not an executable class.  Programmatic and
    third-party registries therefore cannot silently bypass policy merely by
    omitting the metadata that file-backed manifests are required to declare.
    """

    effect = value.strip() if isinstance(value, str) else ""
    if effect not in SUPPORTED_TOOL_SIDE_EFFECTS:
        declared = effect or "unspecified"
        supported = ", ".join(sorted(SUPPORTED_TOOL_SIDE_EFFECTS))
        raise ToolPolicyError(
            f"Tool {tool_name!r} is blocked by policy: side_effect={declared!r} "
            f"is not executable; expected one of: {supported}"
        )
    return effect


__all__ = [
    "SUPPORTED_TOOL_SIDE_EFFECTS",
    "ToolPolicyError",
    "require_supported_side_effect",
]
