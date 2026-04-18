from .core import (
    DEFAULT_CONFIG_PATH,
    AgentResponse,
    MemoryAgent,
    create_memory_agent,
    main,
)


def create_visual_api_app(*args, **kwargs):
    from .visual_api import create_app

    return create_app(*args, **kwargs)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "AgentResponse",
    "MemoryAgent",
    "create_memory_agent",
    "create_visual_api_app",
    "main",
]
