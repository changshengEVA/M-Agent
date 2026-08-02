"""LangGraph production runtime configuration (R2 turn loop)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple

TURN_LOOP_ENV = "M_AGENT_LANGGRAPH_TURN_LOOP"

FAKE_DELEGATE_EXECUTOR = "fake"
EXECUTION_AGENT_DELEGATE_EXECUTOR = "execution_agent"
DELEGATE_EXECUTORS: Tuple[str, ...] = (
    FAKE_DELEGATE_EXECUTOR,
    EXECUTION_AGENT_DELEGATE_EXECUTOR,
)

DEFAULT_FAKE_CAPABILITIES: Tuple[str, ...] = ("fake_capability",)
DEFAULT_DELIVERY_GUARANTEE = "idempotent"
DEFAULT_MAX_DELEGATE_CHAIN = 32

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off", "disabled"})


def parse_bool(value: Any) -> Optional[bool]:
    """Parse a tri-state boolean; ``None`` means "not configured"."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if not token:
        return None
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return None


def resolve_turn_loop_enabled(
    *,
    explicit: Any = None,
    configured: Any = None,
) -> bool:
    """Resolve the R2 rollback switch for the in-graph think/delegate loop.

    Precedence mirrors runtime engine routing: a direct programmatic argument
    wins, then ``M_AGENT_LANGGRAPH_TURN_LOOP``, then YAML, then the built-in
    default. Turning it off falls back to the R1 MVP drain.
    """

    chosen = parse_bool(explicit)
    if chosen is not None:
        return chosen
    from_env = parse_bool(os.getenv(TURN_LOOP_ENV, ""))
    if from_env is not None:
        return from_env
    from_config = parse_bool(configured)
    if from_config is not None:
        return from_config
    return True


@dataclass
class LangGraphRuntimeConfig:
    """Engine-local knobs; shared semantics stay in :class:`RuntimeConfig`."""

    turn_loop_enabled: bool = True
    delegate_executor: str = FAKE_DELEGATE_EXECUTOR
    delivery_guarantee: str = DEFAULT_DELIVERY_GUARANTEE
    max_delegate_chain: int = DEFAULT_MAX_DELEGATE_CHAIN
    fake_capabilities: Tuple[str, ...] = field(
        default_factory=lambda: DEFAULT_FAKE_CAPABILITIES,
    )

    @property
    def uses_execution_agent(self) -> bool:
        return self.delegate_executor == EXECUTION_AGENT_DELEGATE_EXECUTOR


def load_langgraph_config(
    raw: Mapping[str, Any] | None,
) -> LangGraphRuntimeConfig:
    data = dict(raw or {})

    executor = str(
        data.get("delegate_executor", FAKE_DELEGATE_EXECUTOR) or ""
    ).strip()
    if executor not in DELEGATE_EXECUTORS:
        raise ValueError(f"unsupported delegate executor: {executor!r}")

    capabilities_raw = data.get("fake_capabilities")
    if isinstance(capabilities_raw, (list, tuple)):
        capabilities = tuple(
            str(item or "").strip()
            for item in capabilities_raw
            if str(item or "").strip()
        )
    else:
        capabilities = DEFAULT_FAKE_CAPABILITIES
    if not capabilities:
        capabilities = DEFAULT_FAKE_CAPABILITIES

    chain_raw = data.get("max_delegate_chain")
    chain = DEFAULT_MAX_DELEGATE_CHAIN
    if chain_raw not in (None, ""):
        parsed_chain = int(chain_raw)
        if parsed_chain > 0:
            chain = parsed_chain

    return LangGraphRuntimeConfig(
        turn_loop_enabled=resolve_turn_loop_enabled(
            configured=data.get("turn_loop"),
        ),
        delegate_executor=executor,
        delivery_guarantee=str(
            data.get("delivery_guarantee", DEFAULT_DELIVERY_GUARANTEE) or ""
        ).strip()
        or DEFAULT_DELIVERY_GUARANTEE,
        max_delegate_chain=chain,
        fake_capabilities=capabilities,
    )


__all__ = [
    "DEFAULT_DELIVERY_GUARANTEE",
    "DEFAULT_FAKE_CAPABILITIES",
    "DEFAULT_MAX_DELEGATE_CHAIN",
    "DELEGATE_EXECUTORS",
    "EXECUTION_AGENT_DELEGATE_EXECUTOR",
    "FAKE_DELEGATE_EXECUTOR",
    "LangGraphRuntimeConfig",
    "TURN_LOOP_ENV",
    "load_langgraph_config",
    "parse_bool",
    "resolve_turn_loop_enabled",
]
