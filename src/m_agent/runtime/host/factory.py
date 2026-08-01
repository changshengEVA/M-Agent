"""Construct RuntimeHost implementations by engine id."""



from __future__ import annotations



from pathlib import Path

from typing import Callable, Optional



from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent

from m_agent.runtime.routing import (

    LANGGRAPH_RUNTIME_ENGINE,

    RUNTIME_ENGINES,

    resolve_runtime_engine_from_config,

)

from m_agent.runtime.think_life.runtime import ThinkLifeRuntime



from .protocol import RuntimeHost

from .think_life_adapter import ThinkLifeRuntimeHost



ReplyCallback = Callable[[str, str, str, bool], None]





def create_runtime_host(

    *,

    agent: ThreeLayerChatAgent,

    owner_id: str = "anonymous",

    runtime_engine: Optional[str] = None,

    persist_root: Optional[Path | str] = None,

    on_reply: Optional[ReplyCallback] = None,

) -> RuntimeHost:

    """Build the product runtime host for one engine profile.



    ``runtime_engine`` overrides ``agent.config["runtime"].default_engine`` and

    ``M_AGENT_DEFAULT_RUNTIME_ENGINE``.

    """



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

    if engine == LANGGRAPH_RUNTIME_ENGINE:

        from m_agent.runtime.langgraph.runtime import LangGraphRuntime



        return LangGraphRuntime(

            agent,

            owner_id=owner_id,

            persist_root=persist_root,

            on_reply=on_reply,

        )

    if engine not in RUNTIME_ENGINES:

        raise ValueError(f"unsupported runtime engine: {engine!r}")

    runtime = ThinkLifeRuntime(agent, owner_id=owner_id)

    return ThinkLifeRuntimeHost(runtime)





__all__ = ["create_runtime_host"]

