"""Construct the production LangGraph RuntimeHost."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent
from m_agent.runtime.routing import (
    LANGGRAPH_RUNTIME_ENGINE,
    resolve_runtime_engine_from_config,
)

from .protocol import RuntimeHost

ReplyCallback = Callable[[str, str, str, bool], None]


def create_runtime_host(
    *,
    agent: ThreeLayerChatAgent,
    owner_id: str = "anonymous",
    runtime_engine: Optional[str] = None,
    persist_root: Optional[Path | str] = None,
    on_reply: Optional[ReplyCallback] = None,
) -> RuntimeHost:
    """Build the only supported runtime and fail fast on retired settings."""

    raw_runtime = (
        agent.config.get("runtime")
        if isinstance(getattr(agent, "config", None), dict)
        and isinstance(agent.config.get("runtime"), dict)
        else {}
    )
    engine = resolve_runtime_engine_from_config(
        raw_runtime,
        explicit=runtime_engine,
    )
    if engine != LANGGRAPH_RUNTIME_ENGINE:  # defensive type-narrowing guard
        raise ValueError(f"unsupported runtime engine: {engine!r}")

    from m_agent.runtime.langgraph.runtime import LangGraphRuntime

    return LangGraphRuntime(
        agent,
        owner_id=owner_id,
        persist_root=persist_root,
        on_reply=on_reply,
    )


__all__ = ["create_runtime_host"]
