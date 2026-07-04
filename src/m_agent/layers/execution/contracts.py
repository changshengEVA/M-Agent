"""Data contracts exchanged between the thinking and execution layers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


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
class ExecutionRequest:
    """A natural-language instruction the thinking layer issues to execution.

    ``allowed_tool_names`` restricts which LangChain tools are mounted for this
    invocation (Think-life: exactly one name). When set, the execution layer
    also enforces a single tool call per invoke via controller limits.

    ``capability_hint`` is legacy/telemetry; prefer ``allowed_tool_names``.
    ``correlation_id`` is used for log/event correlation.
    ``thread_id`` is required by some capabilities (e.g. schedule_create) and
    by recall thread namespacing — it does not pollute the NL instruction.
    """

    instruction: str
    thread_id: str
    correlation_id: str = ""
    allowed_tool_names: Optional[List[str]] = None
    capability_hint: Optional[List[str]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """Structured outcome of one execution-layer invocation.

    ``summary`` is the natural-language report the thinking layer reads back.
    ``tool_history`` is the raw controller_tool_history (one entry per tool call)
    that is later projected into working-memory entries by the WMWriter.
    ``insufficient`` and ``limit_reached`` are explicit failure-mode signals
    that the thinking layer can surface to the user in its summarize pass.
    """

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
