"""Think-life async path must buffer rounds for memory flush."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from m_agent.api.chat_api_runtime import BufferedRound, ChatServiceRuntime, ThreadSessionState
from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS
from m_agent.runtime.think_life.contracts import SceneActor, SceneEntry, SceneEntryType
from m_agent.systems.scene.default.jsonl_store import SceneLogStore


def _minimal_runtime() -> ChatServiceRuntime:
    rt = ChatServiceRuntime.__new__(ChatServiceRuntime)
    rt._threads_lock = threading.Lock()
    rt._threads = {}
    rt._think_life_pending_users = {}
    rt._operation_lock = threading.Lock()
    rt._stats_lock = threading.Lock()
    rt._flushes_started = 0
    rt._flushes_completed = 0
    rt._flushes_failed = 0
    rt._engine = MagicMock()
    # Compatibility alias used by a few focused assertions in this module.
    rt._think_life = rt._engine
    rt._runtime_host = MagicMock()
    rt._runtime_engine_id = "think_life_v1"
    rt._engine.registry.get_active_user_transaction.return_value = None
    rt._engine.scene_pending_flush_metrics.return_value = {}
    rt._engine.build_dialogue_flush_payload.return_value = None
    rt._engine.scene_system.store.load_conversation_seq.return_value = 0
    rt._thread_event_sink = None
    rt.idle_flush_seconds = 0
    rt.history_max_rounds = 12
    rt._agent = MagicMock(
        user_name="think_life_test",
        assistant_name="Memory Assistant",
        working_memory_config=None,
    )
    rt._agent.describe_episodic_persistence.return_value = {}
    rt._agent.persist_dialogue.return_value = {
        "success": True,
        "dialogue_id": "dialogue-test",
    }
    rt._agent.on_flush.return_value = []
    return rt


def test_idle_timer_is_unarmed_until_first_stimulus() -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::timer-arm"
    session = rt._get_or_create_thread(tid)
    session.last_activity_at = datetime(2020, 1, 1, tzinfo=timezone.utc)

    assert session.idle_timer_started_at is None

    rt._enqueue_think_life_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "think_life_test", "text": "hello"},
    )

    assert session.idle_timer_started_at is not None
    assert session.last_activity_at == session.idle_timer_started_at


def test_any_queued_stimulus_arms_idle_timer() -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::scheduled-stimulus"
    session = rt._get_or_create_thread(tid)
    rt._wire_runtime_host()
    emitter = rt._runtime_host.set_thread_event_emitter.call_args.args[0]

    emitter(tid, "stimulus_queued", {"kind": "scheduled_plan"})

    assert session.idle_timer_started_at is not None


def test_turn_failed_resolves_pending_user_reply_obligation() -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::failed-turn"
    rt._enqueue_think_life_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "think_life_test", "text": "hello"},
    )
    rt._wire_runtime_host()
    emitter = rt._runtime_host.set_thread_event_emitter.call_args.args[0]

    emitter(tid, "turn_failed", {"error": "boom"})

    assert tid not in rt._think_life_pending_users
    assert rt._flush_block_reason(tid) is None


def test_finalized_reply_resets_idle_timer() -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::timer-reset"
    rt._enqueue_think_life_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "think_life_test", "text": "hello"},
    )
    session = rt._threads[tid]
    stimulus_at = session.idle_timer_started_at

    rt._capture_think_life_round(tid, assistant_message="hello back")

    assert stimulus_at is not None
    assert session.idle_timer_started_at is not None
    assert session.idle_timer_started_at >= stimulus_at
    assert session.last_activity_at == session.idle_timer_started_at


def test_idle_flush_skips_active_drainer_even_after_deadline() -> None:
    rt = _minimal_runtime()
    rt.idle_flush_seconds = 1
    tid = "think_life_test::busy-idle-flush"
    rt._enqueue_think_life_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "think_life_test", "text": "hello"},
    )
    rt._capture_think_life_round(tid, assistant_message="hello back")
    session = rt._threads[tid]
    session.idle_timer_started_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    rt._flush_thread_locked = MagicMock()  # type: ignore[method-assign]
    THREAD_RUNTIME_STATUS.set_drainer_active(tid, True)
    try:
        rt.flush_idle_threads()
    finally:
        THREAD_RUNTIME_STATUS.set_drainer_active(tid, False)

    rt._flush_thread_locked.assert_not_called()


def test_manual_flush_is_retryable_while_reply_is_pending() -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::reply-pending-flush"
    rt._enqueue_think_life_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "think_life_test", "text": "hello"},
    )

    result = rt.flush_thread(tid)

    assert result["success"] is False
    assert result["retryable"] is True
    assert result["status"] == "busy"
    assert result["block_reason"] == "reply_pending"
    rt._agent.persist_dialogue.assert_not_called()


def test_flush_cannot_interleave_half_admitted_stimulus() -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::submit-flush-race"
    submit_entered = threading.Event()
    release_submit = threading.Event()
    submit_result: dict = {}
    flush_result: dict = {}

    def _submit_stimulus_async(**_: object) -> dict:
        submit_entered.set()
        assert release_submit.wait(timeout=2)
        return {"accepted": True, "stimulus_id": "s-race"}

    rt._think_life.submit_stimulus_async.side_effect = _submit_stimulus_async

    submit_thread = threading.Thread(
        target=lambda: submit_result.update(
            rt.submit_stimulus(thread_id=tid, message="hello")
        )
    )
    submit_thread.start()
    assert submit_entered.wait(timeout=2)

    flush_thread = threading.Thread(
        target=lambda: flush_result.update(rt.flush_thread(tid))
    )
    flush_thread.start()
    flush_thread.join(timeout=0.1)
    assert flush_thread.is_alive()

    release_submit.set()
    submit_thread.join(timeout=2)
    flush_thread.join(timeout=2)

    assert not submit_thread.is_alive()
    assert not flush_thread.is_alive()
    assert submit_result["accepted"] is True
    assert flush_result["status"] == "busy"
    assert flush_result["block_reason"] == "reply_pending"


def test_successful_flush_disarms_idle_timer() -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::flush-disarms"
    rt._enqueue_think_life_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "think_life_test", "text": "hello"},
    )
    rt._capture_think_life_round(tid, assistant_message="hello back")
    session = rt._threads[tid]
    assert session.idle_timer_started_at is not None

    result = rt.flush_thread(tid)

    assert result["success"] is True
    assert result["status"] == "written"
    assert session.idle_timer_started_at is None
    assert result["thread_state"]["idle_timer_armed"] is False


@pytest.mark.parametrize(
    ("segment_result", "expected_status"),
    [
        (None, "noop"),
        (
            {"completed_transaction_id": "tx-old"},
            "think_life_segment",
        ),
    ],
)
def test_successful_empty_flush_starts_a_new_conversation_boundary(
    segment_result: dict | None,
    expected_status: str,
) -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::empty-flush-boundary"
    session = rt._get_or_create_thread(tid)
    old_conversation_id = session.conversation_id
    old_transaction = {
        "transaction_id": "tx-old",
        "conversation_id": old_conversation_id,
    }
    rt._engine.on_flush_segment.return_value = segment_result

    def _list_transactions(
        thread_id: str,
        *,
        conversation_id: str,
        include_history: bool,
    ) -> dict:
        visible = (
            [old_transaction]
            if include_history or conversation_id == old_conversation_id
            else []
        )
        return {
            "thread_id": thread_id,
            "conversation_id": conversation_id,
            "transactions": visible,
            "include_history": include_history,
        }

    rt._runtime_host.list_transactions.side_effect = _list_transactions

    result = rt.flush_thread(tid)

    assert result["success"] is True
    assert result["status"] == expected_status
    assert result["thread_state"]["conversation_id"] == f"{tid}::1"
    assert result["thread_state"]["conversation_id"] != old_conversation_id
    rt._engine.on_flush_segment.assert_called_once_with(
        tid,
        conversation_id=old_conversation_id,
    )
    rt._engine.scene_system.store.persist_conversation_seq.assert_called_once_with(
        tid,
        1,
    )

    current = rt.get_think_life_transactions(tid)
    audit = rt.get_think_life_transactions(tid, include_history=True)
    assert current["conversation_id"] == f"{tid}::1"
    assert current["transactions"] == []
    assert audit["transactions"] == [old_transaction]


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


def test_scene_payload_missing_assistant_uses_buffer_as_loss_prevention() -> None:
    rt = _minimal_runtime()
    tid = "think_life_test::scene-fallback"
    rt._enqueue_think_life_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "think_life_test", "text": "hello"},
    )
    rt._capture_think_life_round(tid, assistant_message="hello back")
    pending = rt._pending_rounds(rt._threads[tid])

    user_only_payload = {
        "meta": {"round_count": 0},
        "turns": [{"speaker": "think_life_test", "text": "hello"}],
    }
    complete_payload = {
        "meta": {"round_count": 1},
        "turns": [
            {"speaker": "think_life_test", "text": "hello"},
            {"speaker": "Memory Assistant", "text": "hello back"},
        ],
    }

    assert rt._scene_payload_covers_pending_rounds(user_only_payload, pending) is False
    assert rt._scene_payload_covers_pending_rounds(complete_payload, pending) is True


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
