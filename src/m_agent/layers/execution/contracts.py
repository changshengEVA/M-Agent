"""Data contracts exchanged between the thinking and execution layers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal


@dataclass(frozen=True)
class CapabilityDescriptor:
    """Capability boundary description shown to the thinking layer.

    The execution layer assembles a list of descriptors via
    :py:meth:`ExecutionAgent.describe_capabilities` so the thinking layer can
    decide what natural-language instructions are reasonable to issue. Disabled
    capabilities (for example episode_query when its switch is off) MUST NOT
    appear in this list — they are transparently hidden.
    """

    name: str
    category: str
    short_description: str

@dataclass
class ExecutionResult:
    """Structured outcome of one direct Think-life capability invocation."""

    summary: str
    tool_history: List[Dict[str, Any]] = field(default_factory=list)
    success: bool = True
    insufficient: bool = False
    limit_reached: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def tool_call_count(self) -> int:
        return sum(
            1
            for item in self.tool_history
            if isinstance(item, dict) and str(item.get("tool_name", "") or "").strip()
        )

    @property
    def tool_names(self) -> List[str]:
        names: List[str] = []
        for item in self.tool_history:
            if not isinstance(item, dict):
                continue
            name = str(item.get("tool_name", "") or "").strip()
            if name and name not in names:
                names.append(name)
        return names


@dataclass
class ParamFillResult:
    """Outcome of Think-life param fill (structured LLM pass before direct invoke)."""

    tool_name: str
    status: Literal["ready", "needs_clarification"]
    args: Dict[str, Any] = field(default_factory=dict)
    missing_fields: List[str] = field(default_factory=list)
    reason: str = ""
    stage: str = "param_fill"

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    @property
    def needs_clarification(self) -> bool:
        return self.status == "needs_clarification"
