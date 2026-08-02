from __future__ import annotations

from pathlib import Path

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
