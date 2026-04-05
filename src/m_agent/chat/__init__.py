from __future__ import annotations

__all__ = [
    "DEFAULT_CHAT_CONFIG_PATH",
    "ChatMemoryPersistence",
    "SimpleMemoryChatAgent",
    "create_simple_memory_chat_agent",
]


def __getattr__(name: str):
    if name in __all__:
        from .simple_chat_agent import (
            DEFAULT_CHAT_CONFIG_PATH,
            ChatMemoryPersistence,
            SimpleMemoryChatAgent,
            create_simple_memory_chat_agent,
        )

        exported = {
            "DEFAULT_CHAT_CONFIG_PATH": DEFAULT_CHAT_CONFIG_PATH,
            "ChatMemoryPersistence": ChatMemoryPersistence,
            "SimpleMemoryChatAgent": SimpleMemoryChatAgent,
            "create_simple_memory_chat_agent": create_simple_memory_chat_agent,
        }
        return exported[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
