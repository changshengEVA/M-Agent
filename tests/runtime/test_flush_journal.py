from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from m_agent.runtime.host.flush_journal import (
    RUNTIME_COMMITTED,
    RUNTIME_PENDING,
    JOURNAL_COMPLETED,
    FlushJournal,
    FlushIdempotencyConflict,
    FlushJournalStateError,
)


def _snapshot() -> dict:
    return {
        "runtimes": ["runtime_alpha", "langgraph_v1"],
        "payload": {
            "dialogue_id": "dialogue-1",
            "through_seq": 7,
        },
    }


def test_create_is_idempotent_and_rejects_flush_identity_conflicts() -> None:
    journal = FlushJournal(None)
    try:
        created = journal.create_or_get_pending(
            "conversation-1",
            "thread-1",
            "flush-1",
            _snapshot(),
        )
        replayed = journal.create_or_get_pending(
            "conversation-1",
            "thread-1",
            "flush-1",
            {
                "payload": {"through_seq": 7, "dialogue_id": "dialogue-1"},
                "runtimes": ["runtime_alpha", "langgraph_v1"],
            },
        )

        assert replayed == created
        assert created.pending_runtimes == ["langgraph_v1", "runtime_alpha"]
        assert all(
            state.status == RUNTIME_PENDING for state in created.runtimes.values()
        )
        assert len(created.snapshot_digest) == 64

        with pytest.raises(FlushIdempotencyConflict):
            journal.create_or_get_pending(
                "conversation-1",
                "thread-1",
                "flush-1",
                {
                    "runtimes": ["runtime_alpha", "langgraph_v1"],
                    "payload": {
                        "dialogue_id": "dialogue-1",
                        "through_seq": 8,
                    },
                },
            )
        with pytest.raises(FlushIdempotencyConflict):
            journal.create_or_get_pending(
                "another-conversation",
                "thread-1",
                "flush-1",
                _snapshot(),
            )
    finally:
        journal.close()


def test_pending_runtime_state_survives_restart_and_resumes_after_failure(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-flush.sqlite3"
    first = FlushJournal(database)
    first.create_or_get_pending(
        "conversation-1",
        "thread-1",
        "flush-recovery",
        _snapshot(),
    )
    after_first_runtime = first.mark_runtime_committed(
        "flush-recovery",
        "runtime_alpha",
        {"archived_transaction_ids": ["tx-a"]},
    )
    assert after_first_runtime.runtimes["runtime_alpha"].status == RUNTIME_COMMITTED
    assert after_first_runtime.runtimes["langgraph_v1"].status == RUNTIME_PENDING
    with pytest.raises(FlushJournalStateError):
        first.mark_completed("flush-recovery")
    first.close()

    restarted = FlushJournal(database)
    try:
        pending = restarted.get_pending("flush-recovery")
        assert pending is not None
        assert pending.committed_runtimes == ["runtime_alpha"]
        assert pending.pending_runtimes == ["langgraph_v1"]
        assert pending.runtimes["runtime_alpha"].result == {
            "archived_transaction_ids": ["tx-a"]
        }

        after_second_runtime = restarted.mark_runtime_committed(
            "flush-recovery",
            "langgraph_v1",
            {"archived_transaction_ids": ["tx-b"]},
        )
        assert after_second_runtime.pending_runtimes == []
        completed = restarted.mark_completed("flush-recovery")

        assert completed.status == JOURNAL_COMPLETED
        assert completed.completed_at
        assert restarted.get_pending("flush-recovery") is None
        assert restarted.get("flush-recovery") == completed
        assert restarted.list_pending(conversation_id="conversation-1") == []
    finally:
        restarted.close()


def test_runtime_commit_replay_requires_the_same_result() -> None:
    journal = FlushJournal(None)
    try:
        journal.create_or_get_pending(
            "conversation-1", "thread-1", "flush-1", _snapshot()
        )
        first = journal.mark_runtime_committed(
            "flush-1", "runtime_alpha", {"value": 1}
        )
        replay = journal.mark_runtime_committed(
            "flush-1", "runtime_alpha", {"value": 1}
        )
        assert replay == first

        with pytest.raises(FlushIdempotencyConflict):
            journal.mark_runtime_committed(
                "flush-1", "runtime_alpha", {"value": 2}
            )
        with pytest.raises(KeyError):
            journal.mark_runtime_committed("flush-1", "unknown", {})
    finally:
        journal.close()


def test_materialization_payload_survives_restart_until_delivered(
    tmp_path: Path,
) -> None:
    database = tmp_path / "materialization-outbox.sqlite3"
    first = FlushJournal(database)
    first.create_or_get_pending(
        "conversation-1", "thread-1", "flush-outbox", _snapshot()
    )
    staged = first.stage_materialization(
        "flush-outbox",
        "dialogue",
        {"dialogue_id": "dialogue-1", "turns": [{"text": "hello"}]},
    )
    assert staged.pending_materializations == ["dialogue"]
    first.mark_runtime_committed("flush-outbox", "runtime_alpha", {})
    first.mark_runtime_committed("flush-outbox", "langgraph_v1", {})
    with pytest.raises(FlushJournalStateError):
        first.mark_completed("flush-outbox")
    first.close()

    restarted = FlushJournal(database)
    try:
        pending = restarted.get_pending("flush-outbox")
        assert pending is not None
        assert pending.materializations["dialogue"].payload == {
            "dialogue_id": "dialogue-1",
            "turns": [{"text": "hello"}],
        }
        delivered = restarted.mark_materialization_delivered(
            "flush-outbox",
            "dialogue",
            {"success": True, "dialogue_id": "dialogue-1"},
        )
        assert delivered.pending_materializations == []
        assert restarted.mark_completed("flush-outbox").status == JOURNAL_COMPLETED
    finally:
        restarted.close()


def test_concurrent_create_from_two_connections_collapses_to_one_record(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent.sqlite3"
    journals = [FlushJournal(database), FlushJournal(database)]
    barrier = Barrier(2)

    def create(journal: FlushJournal) -> str:
        barrier.wait(timeout=5)
        return journal.create_or_get_pending(
            "conversation-1", "thread-1", "flush-1", _snapshot()
        ).snapshot_digest

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            digests = list(pool.map(create, journals))
        assert digests[0] == digests[1]
        pending = journals[0].list_pending()
        assert [record.flush_id for record in pending] == ["flush-1"]
    finally:
        for journal in journals:
            journal.close()
