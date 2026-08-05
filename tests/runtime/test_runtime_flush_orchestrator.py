from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from m_agent.runtime.host.flush_orchestrator import RuntimeFlushOrchestrator
from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE


class _Store:
    def capture_flush_snapshot(self, conversation_id: str) -> dict:
        return {
            "conversation_id": conversation_id,
            "flush_watermark": 0,
            "through_seq": 3,
            "scene_entries": [],
            "eligible_revisions": {"tx-1": 4},
        }


class _Runtime:
    runtime_engine_id = LANGGRAPH_RUNTIME_ENGINE
    runtime_store = _Store()
    agent = None

    def __init__(self, *, fail_commit: bool = False) -> None:
        self.fail_commit = fail_commit
        self.commit_calls = 0

    def ensure_scene_thread_loaded(self, conversation_id: str) -> None:
        assert conversation_id == "conversation-1"

    def _commit_flush_segment(self, thread_id: str, **kwargs) -> dict:
        self.commit_calls += 1
        if self.fail_commit:
            raise RuntimeError("injected runtime commit failure")
        return {
            "thread_id": thread_id,
            "archived_transaction_ids": ["tx-1"],
            "flush_id": kwargs["flush_id"],
        }


class _RecoveryStore(_Store):
    def capture_flush_snapshot(self, conversation_id: str) -> dict:
        return {
            "conversation_id": conversation_id,
            "flush_watermark": 0,
            "through_seq": 2,
            "scene_entries": [
                {
                    "seq": 1,
                    "occurred_at": "2026-08-01T09:25:30Z",
                    "entry_type": "utterance",
                    "actor": "user",
                    "text": f"hello from {conversation_id}",
                    "append_id": f"{conversation_id}:user",
                    "transaction_id": f"{conversation_id}:tx",
                    "delegate_id": None,
                    "tool_name": None,
                    "payload_ref": None,
                },
                {
                    "seq": 2,
                    "occurred_at": "2026-08-01T09:25:31Z",
                    "entry_type": "reply",
                    "actor": "assistant",
                    "text": "hello back",
                    "append_id": f"{conversation_id}:reply",
                    "transaction_id": f"{conversation_id}:tx",
                    "delegate_id": None,
                    "tool_name": "reply_to_user",
                    "payload_ref": None,
                },
            ],
            "eligible_revisions": {},
        }


class _RecoveryAgent:
    user_name = "User"
    assistant_name = "Assistant"

    def __init__(
        self,
        *,
        write_calls: list[dict[str, Any]] | None = None,
        fail_threads: set[str] | None = None,
    ) -> None:
        self.write_calls = write_calls if write_calls is not None else []
        self.fail_threads = set(fail_threads or set())
        self.on_flush_calls = 0

    def persist_dialogue_payload(self, **kwargs: Any) -> dict[str, Any]:
        call = deepcopy(dict(kwargs))
        self.write_calls.append(call)
        thread_id = str(kwargs.get("thread_id", "") or "")
        dialogue_payload = dict(kwargs.get("dialogue_payload") or {})
        dialogue_id = str(dialogue_payload.get("dialogue_id", "") or "")
        if thread_id in self.fail_threads:
            return {
                "success": False,
                "dialogue_id": dialogue_id,
                "error": f"injected materialization failure for {thread_id}",
            }
        return {
            "success": True,
            "dialogue_id": dialogue_id,
            "round_count": int(
                dict(dialogue_payload.get("meta") or {}).get("round_count", 0)
                or 0
            ),
            "turn_count": len(dialogue_payload.get("turns") or []),
            "import_result": None,
        }

    def on_flush(self, **_kwargs: Any) -> list[dict[str, Any]]:
        self.on_flush_calls += 1
        return []


class _RecoveryRuntime(_Runtime):
    def __init__(
        self,
        *,
        sequence_state: dict[str, int] | None = None,
        write_calls: list[dict[str, Any]] | None = None,
        fail_threads: set[str] | None = None,
        fail_sequence: bool = False,
        committed_flushes: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self.runtime_store = _RecoveryStore()
        self.agent = _RecoveryAgent(
            write_calls=write_calls,
            fail_threads=fail_threads,
        )
        self.sequence_state = (
            sequence_state if sequence_state is not None else {}
        )
        self.fail_sequence = bool(fail_sequence)
        self.committed_flushes = (
            committed_flushes if committed_flushes is not None else {}
        )

    def ensure_scene_thread_loaded(self, conversation_id: str) -> None:
        assert str(conversation_id or "").strip()

    def load_conversation_seq(self, thread_id: str) -> int:
        return int(self.sequence_state.get(thread_id, 0))

    def persist_conversation_seq(self, thread_id: str, sequence: int) -> None:
        if self.fail_sequence:
            raise RuntimeError("injected conversation sequence failure")
        self.sequence_state[thread_id] = max(
            int(self.sequence_state.get(thread_id, 0)),
            int(sequence),
        )

    def _commit_flush_segment(self, thread_id: str, **kwargs: Any) -> dict:
        result = super()._commit_flush_segment(thread_id, **kwargs)
        self.committed_flushes[str(kwargs["flush_id"])] = deepcopy(result)
        return result

    def load_committed_flush_segment(
        self,
        thread_id: str,
        *,
        conversation_id: str,
        flush_id: str,
    ) -> dict[str, Any] | None:
        del thread_id, conversation_id
        result = self.committed_flushes.get(flush_id)
        return deepcopy(result) if result is not None else None


def _prepare(
    orchestrator: RuntimeFlushOrchestrator,
) -> dict:
    return orchestrator.prepare(
        "thread-1",
        conversation_id="conversation-1",
    )


def _stage(
    orchestrator: RuntimeFlushOrchestrator,
    plan: dict,
) -> dict:
    return orchestrator.stage_materialization(
        plan["flush_id"],
        destination="dialogue",
        payload={
            "dialogue_payload": {
                "dialogue_id": "dialogue-1",
                "turns": [{"speaker": "User", "text": "hello"}],
            }
        },
    )


def _prepare_recovery(
    orchestrator: RuntimeFlushOrchestrator,
    *,
    thread_id: str = "thread-1",
    sequence: int = 4,
) -> dict:
    return orchestrator.prepare(
        thread_id,
        conversation_id=f"{thread_id}::{sequence}",
    )


def _stage_recovery(
    orchestrator: RuntimeFlushOrchestrator,
    plan: dict,
) -> dict:
    dialogue = deepcopy(plan["dialogue_payload"])
    return orchestrator.stage_materialization(
        plan["flush_id"],
        destination="dialogue",
        payload={
            "dialogue_payload": dialogue,
            "flush_mode": "scene",
            "rounds_flushed": int(
                dict(dialogue.get("meta") or {}).get("round_count", 0) or 0
            ),
            "turns_flushed": len(dialogue.get("turns") or []),
        },
    )


def test_commit_failure_keeps_snapshot_and_materialization_for_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-flush.sqlite3"
    failing_runtime = _Runtime(fail_commit=True)
    first = RuntimeFlushOrchestrator(
        failing_runtime,
        journal_path=database,
    )
    plan = _prepare(first)
    staged = _stage(first, plan)

    with pytest.raises(RuntimeError, match="injected runtime commit failure"):
        first.commit_runtime(
            "thread-1",
            conversation_id="conversation-1",
            flush_snapshot=staged["flush_snapshot"],
            defer_completion=True,
        )
    first.close()

    recovered_runtime = _Runtime()
    recovered = RuntimeFlushOrchestrator(
        recovered_runtime,
        journal_path=database,
    )
    resumed = _prepare(recovered)
    assert resumed["flush_id"] == plan["flush_id"]
    assert resumed["materializations"]["dialogue"]["payload"] == {
        "dialogue_payload": {
            "dialogue_id": "dialogue-1",
            "turns": [{"speaker": "User", "text": "hello"}],
        }
    }
    recovered.commit_runtime(
        "thread-1",
        conversation_id="conversation-1",
        flush_snapshot=resumed["flush_snapshot"],
        defer_completion=True,
    )
    assert recovered_runtime.commit_calls == 1
    recovered.close()


def test_runtime_commit_is_not_replayed_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "runtime-flush.sqlite3"
    runtime = _Runtime()
    first = RuntimeFlushOrchestrator(runtime, journal_path=database)
    plan = _stage(first, _prepare(first))
    first.commit_runtime(
        "thread-1",
        conversation_id="conversation-1",
        flush_snapshot=plan["flush_snapshot"],
        defer_completion=True,
    )
    assert runtime.commit_calls == 1
    first.close()

    restarted_runtime = _Runtime()
    restarted = RuntimeFlushOrchestrator(
        restarted_runtime,
        journal_path=database,
    )
    resumed = _prepare(restarted)
    restarted.commit_runtime(
        "thread-1",
        conversation_id="conversation-1",
        flush_snapshot=resumed["flush_snapshot"],
        defer_completion=True,
    )
    assert restarted_runtime.commit_calls == 0
    restarted.mark_materialization_delivered(
        resumed["flush_id"],
        destination="dialogue",
        result={"success": True, "dialogue_id": "dialogue-1"},
    )
    assert restarted.complete(resumed["flush_id"])["status"] == "completed"
    restarted.close()


def test_delivered_materialization_can_complete_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-flush.sqlite3"
    first = RuntimeFlushOrchestrator(_Runtime(), journal_path=database)
    plan = _stage(first, _prepare(first))
    first.commit_runtime(
        "thread-1",
        conversation_id="conversation-1",
        flush_snapshot=plan["flush_snapshot"],
        defer_completion=True,
    )
    first.mark_materialization_delivered(
        plan["flush_id"],
        destination="dialogue",
        result={"success": True, "dialogue_id": "dialogue-1"},
    )
    first.close()

    restarted = RuntimeFlushOrchestrator(_Runtime(), journal_path=database)
    resumed = _prepare(restarted)
    dialogue = resumed["materializations"]["dialogue"]
    assert dialogue["status"] == "delivered"
    assert dialogue["result"]["dialogue_id"] == "dialogue-1"
    assert restarted.complete(plan["flush_id"])["status"] == "completed"
    restarted.close()


def test_prepare_freezes_committed_episode_notes_into_dialogue_payload(
    tmp_path: Path,
) -> None:
    class _NoteStore(_Store):
        def capture_flush_snapshot(self, conversation_id: str) -> dict:
            snapshot = super().capture_flush_snapshot(conversation_id)
            snapshot["scene_entries"] = [
                {
                    "seq": 1,
                    "occurred_at": "2026-08-01T09:25:30Z",
                    "entry_type": "utterance",
                    "actor": "user",
                    "text": "I prefer tea",
                    "append_id": "stim-1",
                    "transaction_id": "tx-1",
                    "delegate_id": None,
                    "tool_name": None,
                    "payload_ref": None,
                },
                {
                    "seq": 2,
                    "occurred_at": "2026-08-01T09:25:31Z",
                    "entry_type": "thought",
                    "actor": "think",
                    "text": "The user prefers tea.",
                    "append_id": "note-1",
                    "transaction_id": "tx-1",
                    "delegate_id": None,
                    "tool_name": None,
                    "payload_ref": "episode_note:v1",
                },
                {
                    "seq": 3,
                    "occurred_at": "2026-08-01T09:25:32Z",
                    "entry_type": "reply",
                    "actor": "assistant",
                    "text": "Got it.",
                    "append_id": "reply-1",
                    "transaction_id": "tx-1",
                    "delegate_id": None,
                    "tool_name": "reply_to_user",
                    "payload_ref": None,
                },
            ]
            return snapshot

    runtime = _Runtime()
    runtime.runtime_store = _NoteStore()
    orchestrator = RuntimeFlushOrchestrator(
        runtime,
        journal_path=tmp_path / "runtime-flush.sqlite3",
    )

    plan = _prepare(orchestrator)

    notes = plan["dialogue_payload"]["meta"]["trace_summary"]["episode_notes"]
    assert notes == [
        {
            "note_id": "note-1",
            "note": "The user prefers tea.",
            "turn_meta": {
                "source": "committed_scene",
                "scene_seq": 2,
                "occurred_at": "2026-08-01T09:25:31Z",
                "transaction_id": "tx-1",
            },
        }
    ]
    orchestrator.close()


def test_startup_recovery_stages_snapshot_then_completes_oldest_flush(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-flush.sqlite3"
    first = RuntimeFlushOrchestrator(
        _RecoveryRuntime(),
        journal_path=database,
    )
    plan = _prepare_recovery(first)
    expected_dialogue_id = plan["dialogue_payload"]["dialogue_id"]
    first.close()

    sequence_state: dict[str, int] = {}
    restarted_runtime = _RecoveryRuntime(sequence_state=sequence_state)
    restarted = RuntimeFlushOrchestrator(
        restarted_runtime,
        journal_path=database,
    )
    recovery = restarted.recover_pending()

    assert recovery["pending_before"] == 1
    assert recovery["pending_after"] == 0
    assert recovery["failed"] == []
    assert recovery["blocked"] == []
    assert [item["flush_id"] for item in recovery["recovered"]] == [
        plan["flush_id"]
    ]
    assert restarted_runtime.commit_calls == 1
    assert sequence_state == {"thread-1": 5}
    assert [
        call["dialogue_payload"]["dialogue_id"]
        for call in restarted_runtime.agent.write_calls
    ] == [expected_dialogue_id]
    assert restarted_runtime.agent.on_flush_calls == 0
    assert restarted.journal.get(plan["flush_id"]).status == "completed"
    health = restarted.health()
    assert health["startup_recovery"]["pending_after"] == 0
    restarted.close()


def test_startup_recovery_reconciles_commit_before_journal_ack(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-flush.sqlite3"
    committed_flushes: dict[str, dict[str, Any]] = {}
    first_runtime = _RecoveryRuntime(committed_flushes=committed_flushes)
    first = RuntimeFlushOrchestrator(first_runtime, journal_path=database)
    plan = _stage_recovery(first, _prepare_recovery(first))
    boundary = plan["flush_snapshot"]["runtimes"][LANGGRAPH_RUNTIME_ENGINE]
    first_runtime._commit_flush_segment(
        "thread-1",
        conversation_id="thread-1::4",
        flush_id=f"{plan['flush_id']}:{LANGGRAPH_RUNTIME_ENGINE}",
        through_seq=boundary["through_seq"],
        eligible_revisions=boundary["eligible_revisions"],
        payload={"flush_mode": "scene", "external_dialogue_written": False},
    )
    assert first_runtime.commit_calls == 1
    assert first.journal.get(plan["flush_id"]).pending_runtimes == [
        LANGGRAPH_RUNTIME_ENGINE
    ]
    first.close()

    restarted_runtime = _RecoveryRuntime(
        committed_flushes=committed_flushes,
    )
    restarted = RuntimeFlushOrchestrator(
        restarted_runtime,
        journal_path=database,
    )
    recovery = restarted.recover_pending()

    assert recovery["pending_after"] == 0
    assert restarted_runtime.commit_calls == 0
    assert len(restarted_runtime.agent.write_calls) == 1
    assert restarted_runtime.sequence_state["thread-1"] == 5
    assert recovery["recovered"][0]["runtime_commit_reconciled"] is True
    restarted.close()


def test_startup_recovery_replays_same_dialogue_id_after_write_before_ack(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-flush.sqlite3"
    write_calls: list[dict[str, Any]] = []
    first_runtime = _RecoveryRuntime(write_calls=write_calls)
    first = RuntimeFlushOrchestrator(first_runtime, journal_path=database)
    plan = _stage_recovery(first, _prepare_recovery(first))
    first.commit_runtime(
        "thread-1",
        conversation_id="thread-1::4",
        flush_snapshot=plan["flush_snapshot"],
        defer_completion=True,
    )
    staged_dialogue = plan["materializations"]["dialogue"]["payload"][
        "dialogue_payload"
    ]
    first_write = first_runtime.agent.persist_dialogue_payload(
        dialogue_payload=deepcopy(staged_dialogue),
        thread_id="thread-1",
        reason="chat_thread_manual",
        source="chat_api_thread_flush",
    )
    assert first_write["success"] is True
    first.close()

    restarted_runtime = _RecoveryRuntime(write_calls=write_calls)
    restarted = RuntimeFlushOrchestrator(
        restarted_runtime,
        journal_path=database,
    )
    recovery = restarted.recover_pending()

    assert recovery["pending_after"] == 0
    assert len(write_calls) == 2
    dialogue_ids = [
        call["dialogue_payload"]["dialogue_id"] for call in write_calls
    ]
    assert dialogue_ids == [dialogue_ids[0], dialogue_ids[0]]
    assert recovery["recovered"][0]["materialization_replayed"] is True
    restarted.close()


def test_delivered_flush_waits_for_sequence_then_retries_without_rewrite(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-flush.sqlite3"
    first = RuntimeFlushOrchestrator(
        _RecoveryRuntime(),
        journal_path=database,
    )
    plan = _stage_recovery(first, _prepare_recovery(first))
    first.commit_runtime(
        "thread-1",
        conversation_id="thread-1::4",
        flush_snapshot=plan["flush_snapshot"],
        defer_completion=True,
    )
    first.mark_materialization_delivered(
        plan["flush_id"],
        destination="dialogue",
        result={
            "success": True,
            "dialogue_id": plan["dialogue_payload"]["dialogue_id"],
        },
    )
    first.close()

    sequence_state: dict[str, int] = {}
    restarted_runtime = _RecoveryRuntime(
        sequence_state=sequence_state,
        fail_sequence=True,
    )
    restarted = RuntimeFlushOrchestrator(
        restarted_runtime,
        journal_path=database,
    )
    failed = restarted.recover_pending()

    assert failed["recovered"] == []
    assert failed["pending_after"] == 1
    assert failed["blocked_threads"] == ["thread-1"]
    assert "conversation sequence" in failed["failed"][0]["error"]
    assert restarted_runtime.commit_calls == 0
    assert restarted_runtime.agent.write_calls == []
    assert restarted.journal.get(plan["flush_id"]).status == "pending"

    restarted_runtime.fail_sequence = False
    recovered = restarted.recover_pending()
    assert recovered["pending_after"] == 0
    assert sequence_state == {"thread-1": 5}
    assert restarted_runtime.agent.write_calls == []
    assert restarted.journal.get(plan["flush_id"]).status == "completed"
    restarted.close()


def test_recovery_failure_blocks_newer_same_thread_but_not_other_threads(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-flush.sqlite3"
    first = RuntimeFlushOrchestrator(
        _RecoveryRuntime(),
        journal_path=database,
    )
    thread_a_plans = [
        _prepare_recovery(first, thread_id="thread-a", sequence=sequence)
        for sequence in (0, 1)
    ]
    thread_b_plan = _prepare_recovery(
        first,
        thread_id="thread-b",
        sequence=0,
    )
    first.close()

    restarted_runtime = _RecoveryRuntime(fail_threads={"thread-a"})
    restarted = RuntimeFlushOrchestrator(
        restarted_runtime,
        journal_path=database,
    )
    recovery = restarted.recover_pending()

    assert len(recovery["failed"]) == 1
    assert recovery["failed"][0]["thread_id"] == "thread-a"
    assert len(recovery["blocked"]) == 1
    assert recovery["blocked"][0]["thread_id"] == "thread-a"
    assert recovery["blocked"][0]["blocked_by_flush_id"] == (
        recovery["failed"][0]["flush_id"]
    )
    assert recovery["blocked_threads"] == ["thread-a"]
    assert [item["flush_id"] for item in recovery["recovered"]] == [
        thread_b_plan["flush_id"]
    ]
    assert recovery["pending_after"] == 2
    assert restarted_runtime.sequence_state == {"thread-b": 1}
    pending_ids = {
        record.flush_id for record in restarted.journal.list_pending()
    }
    assert pending_ids == {plan["flush_id"] for plan in thread_a_plans}
    restarted.close()


def test_sequence_advanced_before_completion_is_monotonic_and_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-flush.sqlite3"
    first = RuntimeFlushOrchestrator(
        _RecoveryRuntime(),
        journal_path=database,
    )
    plan = _stage_recovery(first, _prepare_recovery(first))
    first.commit_runtime(
        "thread-1",
        conversation_id="thread-1::4",
        flush_snapshot=plan["flush_snapshot"],
        defer_completion=True,
    )
    first.mark_materialization_delivered(
        plan["flush_id"],
        destination="dialogue",
        result={
            "success": True,
            "dialogue_id": plan["dialogue_payload"]["dialogue_id"],
        },
    )
    first.close()

    sequence_state = {"thread-1": 8}
    restarted_runtime = _RecoveryRuntime(sequence_state=sequence_state)
    restarted = RuntimeFlushOrchestrator(
        restarted_runtime,
        journal_path=database,
    )
    recovery = restarted.recover_pending()

    assert recovery["pending_after"] == 0
    assert restarted_runtime.commit_calls == 0
    assert restarted_runtime.agent.write_calls == []
    assert sequence_state == {"thread-1": 8}
    assert recovery["recovered"][0]["conversation_sequence"] == {
        "before": 8,
        "required": 5,
        "after": 8,
    }
    restarted.close()
