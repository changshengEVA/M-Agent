#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import json
import logging
from pathlib import Path

# Ensure project root is importable when running this file directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from m_agent.agents.memory_agent import create_memory_agent
from m_agent.utils.logging_trace import configure_colored_logging


configure_colored_logging(level=logging.INFO)
logging.getLogger("memory.memory_core").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

memoryagent = create_memory_agent(r"config\agents\memory\agent_sys.yaml")
print("Initialized agent successfully.")
result = memoryagent.ask("When is Jon's group performing at a festival?")
print(json.dumps(result, ensure_ascii=False, indent=2))

