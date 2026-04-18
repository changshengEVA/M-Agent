"""Compatibility shim for legacy file-based imports.

Primary implementation moved to package:
`m_agent.agents.memory_agent` (directory) / `core.py`.
"""

from m_agent.agents.memory_agent.core import (
    DEFAULT_CONFIG_PATH,
    AgentResponse,
    MemoryAgent,
    create_memory_agent,
    main,
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "AgentResponse",
    "MemoryAgent",
    "create_memory_agent",
    "main",
]

if __name__ == "__main__":
    main()
