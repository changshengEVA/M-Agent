"""Runtime engine routing for P8 gray rollout and rollback."""

from __future__ import annotations

import os
from typing import Any, Literal, Mapping, Optional, Tuple

RUNTIME_ENGINES: Tuple[str, ...] = ("think_life_v1", "langgraph_v1")
RuntimeEngine = Literal["think_life_v1", "langgraph_v1"]

DEFAULT_RUNTIME_ENGINE: RuntimeEngine = "think_life_v1"
LANGGRAPH_RUNTIME_ENGINE: RuntimeEngine = "langgraph_v1"

_CONFIG_ENGINE_KEYS: Tuple[str, ...] = (
    "default_engine",
    "engine",
    "runtime_engine",
)


def _normalized_engine(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    if normalized in RUNTIME_ENGINES:
        return normalized
    return None


def resolve_default_runtime_engine() -> RuntimeEngine:
    """Resolve the default runtime for newly created transactions.

    Controlled by ``M_AGENT_DEFAULT_RUNTIME_ENGINE``. Rollback is a config
    change only: existing transactions keep their persisted ``runtime_engine``.
    """

    override = _normalized_engine(
        os.getenv("M_AGENT_DEFAULT_RUNTIME_ENGINE", "")
    )
    if override is not None:
        return override  # type: ignore[return-value]
    return DEFAULT_RUNTIME_ENGINE


def resolve_runtime_engine(*, explicit: Optional[str] = None) -> RuntimeEngine:
    """Resolve runtime engine for a new transaction."""

    chosen = _normalized_engine(explicit)
    if chosen is not None:
        return chosen  # type: ignore[return-value]
    return resolve_default_runtime_engine()


def resolve_runtime_engine_from_config(
    runtime_config: Mapping[str, Any] | None = None,
    *,
    explicit: Optional[str] = None,
) -> RuntimeEngine:
    """Resolve engine for Chat API / host construction.

    Precedence: explicit argument → ``runtime.default_engine`` (yaml) →
    ``M_AGENT_DEFAULT_RUNTIME_ENGINE`` → ``think_life_v1``.
    """

    chosen = _normalized_engine(explicit)
    if chosen is not None:
        return chosen  # type: ignore[return-value]
    data = dict(runtime_config or {})
    for key in _CONFIG_ENGINE_KEYS:
        from_config = _normalized_engine(
            str(data.get(key, "") or "") if data.get(key) is not None else None
        )
        if from_config is not None:
            return from_config  # type: ignore[return-value]
    return resolve_default_runtime_engine()


def is_langgraph_engine(runtime_engine: Optional[str]) -> bool:
    return _normalized_engine(runtime_engine) == LANGGRAPH_RUNTIME_ENGINE


__all__ = [
    "DEFAULT_RUNTIME_ENGINE",
    "LANGGRAPH_RUNTIME_ENGINE",
    "RUNTIME_ENGINES",
    "RuntimeEngine",
    "is_langgraph_engine",
    "resolve_default_runtime_engine",
    "resolve_runtime_engine",
    "resolve_runtime_engine_from_config",
]
