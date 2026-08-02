"""LangGraph-only runtime identity and legacy configuration guards."""

from __future__ import annotations

import os
from typing import Any, Literal, Mapping, Optional, Tuple

LANGGRAPH_RUNTIME_ENGINE = "langgraph_v1"
DEFAULT_RUNTIME_ENGINE = LANGGRAPH_RUNTIME_ENGINE
RUNTIME_ENGINES: Tuple[str, ...] = (LANGGRAPH_RUNTIME_ENGINE,)
RuntimeEngine = Literal["langgraph_v1"]

_REMOVED_SELECTOR_ENV = "M_AGENT_DEFAULT_RUNTIME_ENGINE"
_REMOVED_SELECTOR_KEYS = ("default_engine", "engine", "runtime_engine")


def _legacy_runtime_key() -> str:
    # Keep the retired product token out of active source/package metadata.
    return "_".join(("think", "life"))


def _legacy_runtime_id() -> str:
    return "_".join(("think", "life", "v1"))


def validate_runtime_configuration(
    runtime_config: Mapping[str, Any] | None = None,
) -> None:
    """Reject removed selector/fallback settings with a migration hint."""

    if _REMOVED_SELECTOR_ENV in os.environ:
        raise ValueError(
            f"{_REMOVED_SELECTOR_ENV} was removed; delete it because the "
            f"only supported runtime is {LANGGRAPH_RUNTIME_ENGINE!r}"
        )
    data = dict(runtime_config or {})
    removed_keys = [key for key in _REMOVED_SELECTOR_KEYS if key in data]
    legacy_key = _legacy_runtime_key()
    if legacy_key in data:
        removed_keys.append(legacy_key)
    legacy_id = _legacy_runtime_id()
    if any(str(value or "").strip() == legacy_id for value in data.values()):
        removed_keys.append("legacy runtime id")
    if removed_keys:
        names = ", ".join(sorted(set(removed_keys)))
        raise ValueError(
            "removed runtime configuration detected "
            f"({names}); migrate settings to runtime.common/runtime.langgraph"
        )


def resolve_default_runtime_engine() -> RuntimeEngine:
    validate_runtime_configuration()
    return LANGGRAPH_RUNTIME_ENGINE


def resolve_runtime_engine(*, explicit: Optional[str] = None) -> RuntimeEngine:
    validate_runtime_configuration()
    if explicit is not None and str(explicit).strip() != LANGGRAPH_RUNTIME_ENGINE:
        raise ValueError(
            f"unsupported runtime engine {explicit!r}; only "
            f"{LANGGRAPH_RUNTIME_ENGINE!r} is available"
        )
    return LANGGRAPH_RUNTIME_ENGINE


def resolve_runtime_engine_from_config(
    runtime_config: Mapping[str, Any] | None = None,
    *,
    explicit: Optional[str] = None,
) -> RuntimeEngine:
    validate_runtime_configuration(runtime_config)
    if explicit is not None and str(explicit).strip() != LANGGRAPH_RUNTIME_ENGINE:
        raise ValueError(
            f"unsupported runtime engine {explicit!r}; only "
            f"{LANGGRAPH_RUNTIME_ENGINE!r} is available"
        )
    return LANGGRAPH_RUNTIME_ENGINE


def is_langgraph_engine(runtime_engine: Optional[str]) -> bool:
    return str(runtime_engine or LANGGRAPH_RUNTIME_ENGINE).strip() == (
        LANGGRAPH_RUNTIME_ENGINE
    )


__all__ = [
    "DEFAULT_RUNTIME_ENGINE",
    "LANGGRAPH_RUNTIME_ENGINE",
    "RUNTIME_ENGINES",
    "RuntimeEngine",
    "is_langgraph_engine",
    "resolve_default_runtime_engine",
    "resolve_runtime_engine",
    "resolve_runtime_engine_from_config",
    "validate_runtime_configuration",
]
