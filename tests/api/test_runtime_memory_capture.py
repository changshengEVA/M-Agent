"""Runtime async path must buffer rounds for memory flush."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest

from m_agent.api.chat_api_runtime import BufferedRound, ChatServiceRuntime, ThreadSessionState
from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS
from m_agent.runtime.domain.contracts import SceneActor, SceneEntry, SceneEntryType


def _minimal_runtime() -> ChatServiceRuntime:
    rt = ChatServiceRuntime.__new__(ChatServiceRuntime)
    rt._threads_lock = threading.Lock()
    rt._threads = {}
    rt._runtime_pending_users = {}
    rt._operation_lock = threading.Lock()
    rt._stats_lock = threading.Lock()
    rt._flushes_started = 0
    rt._flushes_completed = 0
    rt._flushes_failed = 0
    host = MagicMock()
    rt._engine = host
    rt._runtime_host = host
    rt._runtime_engine_id = "langgraph_v1"
    host.active_user_transaction.return_value = None
    host.scene_pending_flush_metrics.return_value = {}
    host.build_dialogue_flush_payload.return_value = None
    host.load_conversation_seq.return_value = 0
    host.scene_flush_through_seq.return_value = 0
    host.prepare_flush_segment.return_value = {
        "flush_id": "runtime-flush-test",
        "through_seq": 0,
        "dialogue_payload": None,
        "materializations": {},
        "flush_snapshot": {
            "schema_version": 1,
            "flush_id": "runtime-flush-test",
            "runtimes": {"langgraph_v1": {"through_seq": 0}},
        },
    }

    def stage_materialization(
        _flush_id: str,
        *,
        destination: str,
        payload: dict,
    ) -> dict:
        plan = dict(host.prepare_flush_segment.return_value)
        plan["materializations"] = {
            destination: {
                "destination": destination,
                "status": "pending",
                "payload": payload,
                "result": None,
                "delivered_at": None,
            }
        }
        return plan

    host.stage_flush_materialization.side_effect = stage_materialization
    host.on_flush_segment.return_value = {"flush_id": "runtime-flush-test"}
    host.mark_flush_materialization_delivered.return_value = {
        "journal_status": "pending"
    }
    host.complete_flush_segment.return_value = {"status": "completed"}
    rt._thread_event_sink = None
    rt.idle_flush_seconds = 0
    rt.history_max_rounds = 12
    rt._agent = MagicMock(
        user_name="runtime_test",
        assistant_name="Memory Assistant",
        working_memory_config=None,
    )
    rt._agent.describe_episodic_persistence.return_value = {}
    rt._agent.persist_dialogue.return_value = {
        "success": True,
        "dialogue_id": "dialogue-test",
    }
    rt._agent.persist_dialogue_payload.return_value = {
        "success": True,
        "dialogue_id": "dialogue-test",
    }
    rt._agent.on_flush.return_value = []
    return rt


def _minimal_flush_runtime() -> tuple[ChatServiceRuntime, MagicMock]:
    rt = _minimal_runtime()
    host = rt._runtime_host
    host.prepare_flush_segment.return_value = {
            "flush_id": "runtime-flush-test",
            "through_seq": 3,
            "dialogue_payload": None,
            "materializations": {},
            "flush_snapshot": {
                "schema_version": 1,
                "flush_id": "runtime-flush-test",
                "runtimes": {"langgraph_v1": {"through_seq": 3}},
            },
        }
    return rt, host


def test_idle_timer_is_unarmed_until_first_stimulus() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::timer-arm"
    session = rt._get_or_create_thread(tid)
    session.last_activity_at = datetime(2020, 1, 1, tzinfo=timezone.utc)

    assert session.idle_timer_started_at is None

    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )

    assert session.idle_timer_started_at is not None
    assert session.last_activity_at == session.idle_timer_started_at


def test_any_queued_stimulus_arms_idle_timer() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::scheduled-stimulus"
    session = rt._get_or_create_thread(tid)
    rt._wire_runtime_host()
    emitter = rt._runtime_host.set_thread_event_emitter.call_args.args[0]

    emitter(tid, "stimulus_queued", {"kind": "scheduled_plan"})

    assert session.idle_timer_started_at is not None


def test_turn_failed_resolves_pending_user_reply_obligation() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::failed-turn"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )
    rt._wire_runtime_host()
    emitter = rt._runtime_host.set_thread_event_emitter.call_args.args[0]

    emitter(tid, "turn_failed", {"error": "boom"})

    assert tid not in rt._runtime_pending_users
    assert rt._flush_block_reason(tid) is None


def test_finalized_reply_resets_idle_timer() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::timer-reset"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )
    session = rt._threads[tid]
    stimulus_at = session.idle_timer_started_at

    rt._capture_runtime_round(tid, assistant_message="hello back")

    assert stimulus_at is not None
    assert session.idle_timer_started_at is not None
    assert session.idle_timer_started_at >= stimulus_at
    assert session.last_activity_at == session.idle_timer_started_at


def test_idle_flush_skips_active_drainer_even_after_deadline() -> None:
    rt = _minimal_runtime()
    rt.idle_flush_seconds = 1
    tid = "runtime_test::busy-idle-flush"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )
    rt._capture_runtime_round(tid, assistant_message="hello back")
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
    tid = "runtime_test::reply-pending-flush"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )

    result = rt.flush_thread(tid)

    assert result["success"] is False
    assert result["retryable"] is True
    assert result["status"] == "busy"
    assert result["block_reason"] == "reply_pending"
    rt._agent.persist_dialogue.assert_not_called()


def test_flush_cannot_interleave_half_admitted_stimulus() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::submit-flush-race"
    submit_entered = threading.Event()
    release_submit = threading.Event()
    submit_result: dict = {}
    flush_result: dict = {}

    def _submit_stimulus_async(**_: object) -> dict:
        submit_entered.set()
        assert release_submit.wait(timeout=2)
        return {"accepted": True, "stimulus_id": "s-race"}

    rt._engine.submit_stimulus_async.side_effect = _submit_stimulus_async

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
    tid = "runtime_test::flush-disarms"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )
    rt._capture_runtime_round(tid, assistant_message="hello back")
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
            "runtime_segment",
        ),
    ],
)
def test_successful_empty_flush_starts_a_new_conversation_boundary(
    segment_result: dict | None,
    expected_status: str,
) -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::empty-flush-boundary"
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
        flush_id="runtime-flush-test",
        through_seq=0,
        payload={
            "reason": "manual_api",
            "flush_mode": "noop",
            "external_dialogue_written": False,
        },
        flush_snapshot=rt._runtime_host.prepare_flush_segment.return_value[
            "flush_snapshot"
        ],
        defer_completion=True,
    )
    rt._runtime_host.persist_conversation_seq.assert_called_once_with(
        tid,
        1,
    )

    current = rt.get_transactions(tid)
    audit = rt.get_transactions(tid, include_history=True)
    assert current["conversation_id"] == f"{tid}::1"
    assert current["transactions"] == []
    assert audit["transactions"] == [old_transaction]


def test_no_dialogue_flush_advances_internal_scene_snapshot() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::internal-scene-flush"
    session = rt._get_or_create_thread(tid)
    rt._engine.scene_system.reader.entries_since_flush.return_value = [
        SceneEntry(
            seq=7,
            occurred_at="2026-08-02T00:00:01Z",
            entry_type=SceneEntryType.ACTION,
            actor=SceneActor.WORK,
            text="internal tool trace",
            append_id="append-internal-trace",
        )
    ]
    rt._runtime_host.prepare_flush_segment.return_value["through_seq"] = 7
    rt._runtime_host.prepare_flush_segment.return_value["flush_snapshot"][
        "runtimes"
    ]["langgraph_v1"]["through_seq"] = 7

    result = rt.flush_thread(tid)

    assert result["success"] is True
    rt._engine.on_flush_segment.assert_called_once_with(
        tid,
        conversation_id=session.thread_id + "::0",
        flush_id="runtime-flush-test",
        through_seq=7,
        payload={
            "reason": "manual_api",
            "flush_mode": "noop",
            "external_dialogue_written": False,
        },
        flush_snapshot=rt._runtime_host.prepare_flush_segment.return_value[
            "flush_snapshot"
        ],
        defer_completion=True,
    )


def test_runtime_flush_commit_failure_keeps_pending_rounds_for_retry() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::runtime-flush-retry"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )
    rt._capture_runtime_round(tid, assistant_message="hello back")
    session = rt._threads[tid]
    old_conversation_id = session.conversation_id
    rt._engine.on_flush_segment.side_effect = RuntimeError("runtime commit down")

    result = rt.flush_thread(tid)

    assert result["success"] is False
    assert result["retryable"] is True
    assert result["memory_write"]["external_write_success"] is False
    assert "runtime flush commit failed" in result["error"]
    assert session.conversation_id == old_conversation_id
    assert session.conversation_seq == 0
    assert session.flush_count == 0
    assert len(rt._pending_rounds(session)) == 1
    rt._agent.on_flush.assert_not_called()
    rt._agent.persist_dialogue.assert_not_called()
    rt._agent.persist_dialogue_payload.assert_not_called()
    rt._engine.on_flush_segment.assert_called_once_with(
        tid,
        conversation_id=old_conversation_id,
        flush_id="runtime-flush-test",
        through_seq=0,
        payload={
            "flush_mode": "buffered_rounds",
            "rounds_flushed": 1,
            "turns_flushed": 2,
            "external_dialogue_written": False,
        },
        flush_snapshot=rt._runtime_host.prepare_flush_segment.return_value[
            "flush_snapshot"
        ],
        defer_completion=True,
    )


def test_flush_commits_snapshot_before_external_materialization() -> None:
    rt, host = _minimal_flush_runtime()
    tid = "runtime_test::runtime-first"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )
    rt._capture_runtime_round(tid, assistant_message="hello back")
    call_order: list[str] = []

    def commit_runtime(*_args: object, **_kwargs: object) -> dict:
        call_order.append("runtime")
        assert rt._agent.persist_dialogue.call_count == 0
        assert rt._agent.persist_dialogue_payload.call_count == 0
        return {"flush_id": "runtime-flush-test"}

    def persist_external(**_kwargs: object) -> dict:
        call_order.append("external")
        return {"success": True, "dialogue_id": "dialogue-test"}

    def complete_runtime(_flush_id: str) -> dict:
        call_order.append("complete")
        return {"status": "completed"}

    host.on_flush_segment.side_effect = commit_runtime  # type: ignore[attr-defined]
    host.complete_flush_segment.side_effect = complete_runtime  # type: ignore[attr-defined]
    rt._agent.persist_dialogue_payload.side_effect = persist_external

    result = rt.flush_thread(tid)

    assert result["success"] is True
    assert call_order == ["runtime", "external", "complete"]
    assert result["runtime_flush"]["journal_status"] == "completed"
    assert rt._threads[tid].conversation_seq == 1
    host.on_flush_segment.assert_called_once_with(  # type: ignore[attr-defined]
        tid,
        conversation_id=f"{tid}::0",
        flush_id="runtime-flush-test",
        through_seq=3,
        flush_snapshot=host.prepare_flush_segment.return_value[  # type: ignore[attr-defined]
            "flush_snapshot"
        ],
        defer_completion=True,
        payload={
            "flush_mode": "buffered_rounds",
            "rounds_flushed": 1,
            "turns_flushed": 2,
            "external_dialogue_written": False,
        },
    )


def test_runtime_runtime_commit_failure_does_not_call_external_sink() -> None:
    rt, host = _minimal_flush_runtime()
    tid = "runtime_test::runtime-runtime-failure"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )
    rt._capture_runtime_round(tid, assistant_message="hello back")
    host.on_flush_segment.side_effect = RuntimeError(  # type: ignore[attr-defined]
        "second engine unavailable"
    )

    result = rt.flush_thread(tid)

    assert result["success"] is False
    assert result["retryable"] is True
    assert result["memory_write"]["external_write_success"] is False
    assert len(rt._pending_rounds(rt._threads[tid])) == 1
    assert rt._threads[tid].conversation_seq == 0
    rt._agent.persist_dialogue.assert_not_called()
    rt._agent.persist_dialogue_payload.assert_not_called()
    host.complete_flush_segment.assert_not_called()  # type: ignore[attr-defined]


def test_dialogue_materialization_failure_keeps_durable_flush_pending() -> None:
    rt, host = _minimal_flush_runtime()
    tid = "runtime_test::materialization-failure"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )
    rt._capture_runtime_round(tid, assistant_message="hello back")
    rt._agent.persist_dialogue_payload.return_value = {
        "success": False,
        "error": "dialogue sink unavailable",
    }

    result = rt.flush_thread(tid)

    assert result["success"] is False
    assert result["retryable"] is True
    assert "dialogue sink unavailable" in result["error"]
    host.on_flush_segment.assert_called_once()  # type: ignore[attr-defined]
    host.mark_flush_materialization_delivered.assert_not_called()  # type: ignore[attr-defined]
    host.complete_flush_segment.assert_not_called()  # type: ignore[attr-defined]
    assert len(rt._pending_rounds(rt._threads[tid])) == 1
    assert rt._threads[tid].conversation_seq == 0


def test_completion_failure_after_delivery_is_retryable_without_losing_rounds() -> None:
    rt, host = _minimal_flush_runtime()
    tid = "runtime_test::completion-failure"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )
    rt._capture_runtime_round(tid, assistant_message="hello back")
    host.complete_flush_segment.side_effect = RuntimeError(
        "completion journal unavailable"
    )

    result = rt.flush_thread(tid)

    assert result["success"] is False
    assert result["retryable"] is True
    assert result["memory_write"]["external_write_success"] is True
    assert "completion journal unavailable" in result["error"]
    host.mark_flush_materialization_delivered.assert_called_once()  # type: ignore[attr-defined]
    host.complete_flush_segment.assert_called_once_with(  # type: ignore[attr-defined]
        "runtime-flush-test"
    )
    assert len(rt._pending_rounds(rt._threads[tid])) == 1
    assert rt._threads[tid].conversation_seq == 0


def test_conversation_boundary_persistence_failure_is_not_reported_success() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::conversation-boundary-failure"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )
    rt._capture_runtime_round(tid, assistant_message="hello back")
    session = rt._threads[tid]
    rt._runtime_host.persist_conversation_seq.side_effect = (
        OSError("sequence store unavailable")
    )

    result = rt.flush_thread(tid)

    assert result["success"] is False
    assert result["retryable"] is True
    assert "conversation boundary persistence failed" in result["error"]
    assert session.conversation_seq == 0
    assert session.flush_count == 0
    assert len(rt._pending_rounds(session)) == 1
    rt._agent.on_flush.assert_not_called()


def test_runtime_restart_materializes_frozen_buffer_without_session_rounds() -> None:
    rt, host = _minimal_flush_runtime()
    tid = "runtime_test::runtime-frozen-recovery"
    frozen_dialogue = {
        "dialogue_id": "dialogue-frozen-recovery",
        "user_id": "runtime_test",
        "participants": ["runtime_test", "Memory Assistant"],
        "meta": {
            "thread_id": tid,
            "round_count": 1,
            "start_time": "2026-08-02T00:00:00Z",
            "end_time": "2026-08-02T00:00:01Z",
        },
        "turns": [
            {"turn_id": 0, "speaker": "runtime_test", "text": "hello"},
            {"turn_id": 1, "speaker": "Memory Assistant", "text": "hi"},
        ],
    }
    host.prepare_flush_segment.return_value["materializations"] = {  # type: ignore[attr-defined]
        "dialogue": {
            "destination": "dialogue",
            "status": "pending",
            "payload": {
                "dialogue_payload": frozen_dialogue,
                "flush_mode": "buffered_rounds",
                "rounds_flushed": 1,
                "turns_flushed": 2,
            },
            "result": None,
            "delivered_at": None,
        }
    }
    rt._agent.persist_dialogue_payload.return_value = {
        "success": True,
        "dialogue_id": "dialogue-frozen-recovery",
    }

    result = rt.flush_thread(tid)

    assert result["success"] is True
    assert result["flush_mode"] == "buffered_rounds"
    rt._agent.persist_dialogue_payload.assert_called_once()
    assert (
        rt._agent.persist_dialogue_payload.call_args.kwargs[
            "dialogue_payload"
        ]
        == frozen_dialogue
    )
    host.mark_flush_materialization_delivered.assert_called_once()  # type: ignore[attr-defined]
    host.complete_flush_segment.assert_called_once_with(  # type: ignore[attr-defined]
        "runtime-flush-test"
    )


def test_capture_runtime_round_buffers_pending_for_flush() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::demo-thread-1"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="你好",
        user_turn={"speaker": "runtime_test", "text": "你好"},
    )
    rt._capture_runtime_round(tid, assistant_message="你好，有什么可以帮你？")

    session = rt._threads[tid]
    pending = rt._pending_rounds(session)
    assert len(pending) == 1
    assert pending[0].user_message == "你好"
    assert pending[0].assistant_message == "你好，有什么可以帮你？"
    assert pending[0].capture_state == "pending"


def test_rounds_for_history_excludes_flushed_when_runtime() -> None:
    from datetime import datetime, timezone

    rt = _minimal_runtime()
    tid = "runtime_test::hist-thread"
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


def test_capture_runtime_round_uses_real_timestamps_not_fixed_offset() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::demo-thread-2"
    submitted = datetime(2026, 5, 29, 14, 14, 3, tzinfo=timezone.utc)
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="你好",
        user_turn={
            "speaker": "runtime_test",
            "text": "你好",
            "timestamp": submitted.isoformat().replace("+00:00", "Z"),
        },
    )
    with rt._threads_lock:
        rt._runtime_pending_users[tid][0]["submitted_at"] = submitted

    rt._capture_runtime_round(tid, assistant_message="回复")

    session = rt._threads[tid]
    round_item = rt._pending_rounds(session)[0]
    assert round_item.user_at == submitted
    assert round_item.assistant_at >= submitted


def test_scene_payload_missing_assistant_uses_buffer_as_loss_prevention() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::scene-fallback"
    rt._enqueue_runtime_user_turn(
        tid,
        user_message="hello",
        user_turn={"speaker": "runtime_test", "text": "hello"},
    )
    rt._capture_runtime_round(tid, assistant_message="hello back")
    pending = rt._pending_rounds(rt._threads[tid])

    user_only_payload = {
        "meta": {"round_count": 0},
        "turns": [{"speaker": "runtime_test", "text": "hello"}],
    }
    complete_payload = {
        "meta": {"round_count": 1},
        "turns": [
            {"speaker": "runtime_test", "text": "hello"},
            {"speaker": "Memory Assistant", "text": "hello back"},
        ],
    }

    assert rt._scene_payload_covers_pending_rounds(user_only_payload, pending) is False
    assert rt._scene_payload_covers_pending_rounds(complete_payload, pending) is True


def test_thread_state_restores_visible_messages_from_persisted_scene() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::restart-thread"
    entries = [
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
    rt._runtime_host.list_scene.return_value = {
        "entries": [entry.to_dict() for entry in entries]
    }
    rt._engine.scene_pending_flush_metrics.return_value = {
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


def test_thread_session_restores_persisted_conversation_sequence() -> None:
    rt = _minimal_runtime()
    tid = "runtime_test::persistent-thread"
    rt._runtime_host.load_conversation_seq.return_value = 5

    session = rt._get_or_create_thread(tid)

    assert session.conversation_seq == 5
    assert session.conversation_id == f"{tid}::5"
