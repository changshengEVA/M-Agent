"""Think-life async path must buffer rounds for memory flush."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock

from m_agent.api.chat_api_runtime import BufferedRound, ChatServiceRuntime, ThreadSessionState
from m_agent.runtime.think_life.contracts import SceneActor, SceneEntry, SceneEntryType
from m_agent.systems.scene.default.jsonl_store import SceneLogStore


def _minimal_runtime() -> ChatServiceRuntime:
    rt = ChatServiceRuntime.__new__(ChatServiceRuntime)
    rt._threads_lock = threading.Lock()
    rt._threads = {}
    rt._think_life_pending_users = {}
    rt._think_life = MagicMock()
    rt._think_life.registry.get_active_user_transaction.return_value = None
    rt._think_life.scene_pending_flush_metrics.return_value = {}
    rt._thread_event_sink = None
    rt.idle_flush_seconds = 0
    rt.history_max_rounds = 12
    rt._agent = MagicMock(
        user_name="think_life_test",
        assistant_name="Memory Assistant",
        working_memory_config=None,
    )
    rt._agent.describe_episodic_persistence.return_value = {}
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


def test_rounds_for_history_excludes_flushed_when_think_life() -> None:
    from datetime import datetime, timezone

    rt = _minimal_runtime()
    tid = "think_life_test::hist-thread"
    session = ThreadSessionState(thread_id=tid)
    flushed_at = datetime(2026, 5, 29, 10, 0, 0, tzinfo=timezone.utc)
    session.rounds.append(
        BufferedRound(
            round_id="old",
            user_message="old user",
            assistant_message="old assistant",
            user_turn={"speaker": "u", "text": "old user"},
            assistant_turn={"speaker": "a", "text": "old assistant"},
            user_at=flushed_at,
            assistant_at=flushed_at,
            capture_state="flushed",
        )
    )
    session.rounds.append(
        BufferedRound(
            round_id="new",
            user_message="new user",
            assistant_message="new assistant",
            user_turn={"speaker": "u", "text": "new user"},
            assistant_turn={"speaker": "a", "text": "new assistant"},
            user_at=datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc),
            assistant_at=datetime(2026, 5, 31, 12, 0, 1, tzinfo=timezone.utc),
            capture_state="pending",
        )
    )
    rt._threads[tid] = session
    history = rt._build_history_messages(session)
    assert len(history) == 2
    assert history[0]["content"] == "new user"
    assert history[1]["content"] == "new assistant"


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


def test_thread_state_restores_visible_messages_from_persisted_scene() -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::restart-thread"
    rt._think_life.scene_system.reader.entries_since_flush.return_value = [
        SceneEntry(
            seq=1,
            occurred_at="2026-07-13T10:00:00Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="message before restart",
        ),
        SceneEntry(
            seq=2,
            occurred_at="2026-07-13T10:00:01Z",
            entry_type=SceneEntryType.THOUGHT,
            actor=SceneActor.THINK,
            text="internal thought",
        ),
        SceneEntry(
            seq=3,
            occurred_at="2026-07-13T10:00:02Z",
            entry_type=SceneEntryType.REPLY,
            actor=SceneActor.ASSISTANT,
            text="reply before restart",
        ),
    ]
    rt._think_life.scene_pending_flush_metrics.return_value = {
        "scene_pending_entries": 3,
        "scene_pending_turns": 2,
        "can_flush": True,
    }

    state = rt.get_thread_state(tid)

    assert state["has_pending_data"] is True
    assert state["conversation_messages"] == [
        {
            "message_id": "scene-1",
            "role": "user",
            "content": "message before restart",
            "timestamp": "2026-07-13T10:00:00Z",
        },
        {
            "message_id": "scene-3",
            "role": "assistant",
            "content": "reply before restart",
            "timestamp": "2026-07-13T10:00:02Z",
        },
    ]


def test_thread_session_restores_persisted_conversation_sequence(tmp_path) -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::persistent-thread"
    store = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    store.persist_conversation_seq(tid, 5)
    rt._think_life.scene_system.store = store

    session = rt._get_or_create_thread(tid)

    assert session.conversation_seq == 5
    assert session.conversation_id == f"{tid}::5"
