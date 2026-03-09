#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import json
import logging
from pathlib import Path

# Ensure project root is importable when running this file directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Agents.memory_agent import create_memory_agent
from utils.logging_trace import configure_colored_logging


configure_colored_logging(level=logging.INFO)
logging.getLogger("memory.memory_core").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

memoryagent = create_memory_agent(r"config\prompt\agent_sys.yaml")
print("初始化成功")
result = memoryagent.ask("When is Jon's group performing at a festival?")
print(json.dumps(result, ensure_ascii=False, indent=2))
