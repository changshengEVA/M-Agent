from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture(autouse=True)
def reset_chat_api_globals() -> None:
    from m_agent.api import chat_api_records as records
    from m_agent.api import chat_api_shared as shared

    records._RUNS._runs.clear()
    records._THREAD_EVENTS._records.clear()
    with shared._THREAD_LOCKS_GUARD:
        shared._THREAD_LOCKS.clear()
    yield
    records._RUNS._runs.clear()
    records._THREAD_EVENTS._records.clear()
    with shared._THREAD_LOCKS_GUARD:
        shared._THREAD_LOCKS.clear()
