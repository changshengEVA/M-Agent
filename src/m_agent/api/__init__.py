from __future__ import annotations

from typing import Any

__all__ = [
    "create_handler",
    "main",
]


def __getattr__(name: str) -> Any:
    if name in {"create_handler", "main"}:
        from .chat_api import create_handler, main

        exports = {
            "create_handler": create_handler,
            "main": main,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
