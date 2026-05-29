"""Think-life async path must buffer rounds for memory flush."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock

from m_agent.api.chat_api_runtime import ChatServiceRuntime, ThreadSessionState


def _minimal_runtime() -> ChatServiceRuntime:
    rt = ChatServiceRuntime.__new__(ChatServiceRuntime)
    rt._threads_lock = threading.Lock()
    rt._threads = {}
    rt._think_life_pending_users = {}
    rt._think_life = None
    rt._thread_event_sink = None
    rt.idle_flush_seconds = 0
    rt.history_max_rounds = 12
    rt._agent = MagicMock(
        user_name="think_life_test",
        assistant_name="Memory Assistant",
    )
    return rt


def test_capture_think_life_round_buffers_pending_for_flush() -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::demo-thread-1"
    rt._enqueue_think_life_user_turn(
        tid,
        user_message="你好",
        user_turn={"speaker": "think_life_test", "text": "你好"},
    )
    rt._capture_think_life_round(tid, assistant_message="你好，有什么可以帮你？")

    session = rt._threads[tid]
    pending = rt._pending_rounds(session)
    assert len(pending) == 1
    assert pending[0].user_message == "你好"
    assert pending[0].assistant_message == "你好，有什么可以帮你？"
    assert pending[0].capture_state == "pending"


def test_capture_think_life_round_uses_real_timestamps_not_fixed_offset() -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::demo-thread-2"
    submitted = datetime(2026, 5, 29, 14, 14, 3, tzinfo=timezone.utc)
    rt._enqueue_think_life_user_turn(
        tid,
        user_message="你好",
        user_turn={
            "speaker": "think_life_test",
            "text": "你好",
            "timestamp": submitted.isoformat().replace("+00:00", "Z"),
        },
    )
    with rt._threads_lock:
        rt._think_life_pending_users[tid][0]["submitted_at"] = submitted

    rt._capture_think_life_round(tid, assistant_message="回复")

    session = rt._threads[tid]
    round_item = rt._pending_rounds(session)[0]
    assert round_item.user_at == submitted
    assert round_item.assistant_at >= submitted
