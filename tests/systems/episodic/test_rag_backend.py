"""SimpleRagEpisodicBackend behavior tests."""
from __future__ import annotations

from pathlib import Path

from m_agent.systems.episodic.default.rag_backend import SimpleRagEpisodicBackend


def test_rag_persist_and_recall(tmp_path: Path) -> None:
    backend = SimpleRagEpisodicBackend(
        storage_dir=str(tmp_path / "rag"),
        workflow_id="test",
        top_k=3,
        embed_model="hash",
    )
    backend.persist_round(
        thread_id="t1",
        user_message="I visited Paris last summer.",
        assistant_message="Paris is beautiful in summer.",
    )
    result = backend.shallow_recall("Paris summer", thread_id="t1")
    assert result.get("hit") is True
    assert "Paris" in str(result.get("answer", ""))
    assert isinstance(result.get("evidence"), list)
    deep = backend.deep_recall("Paris summer", thread_id="t1")
    assert deep.get("answer") == result.get("answer")


def test_rag_on_flush_merges_notes(tmp_path: Path) -> None:
    backend = SimpleRagEpisodicBackend(
        storage_dir=str(tmp_path / "rag2"),
        workflow_id="flush",
        embed_model="hash",
    )
    backend.persist_round(
        thread_id="t2",
        user_message="hello",
        assistant_message="hi",
    )
    backend.on_flush(
        thread_id="t2",
        conversation_id="c1",
        episode_notes=[{"note": "remember this"}],
    )
    chunks = backend.store._chunks
    assert chunks
    notes = chunks[-1].get("meta", {}).get("trace_summary", {}).get("episode_notes")
    assert notes == [{"note": "remember this"}]


def test_rag_dialogue_materialization_is_idempotent_by_dialogue_id(
    tmp_path: Path,
) -> None:
    backend = SimpleRagEpisodicBackend(
        storage_dir=str(tmp_path / "rag-idempotent"),
        workflow_id="flush-idempotent",
        embed_model="hash",
    )
    rounds = [
        {
            "user_message": "hello",
            "assistant_message": "hi",
            "dialogue_id": "dialogue-stable-1",
        }
    ]

    first = backend.persist_dialogue(
        thread_id="t-idempotent",
        rounds=rounds,
        reason="flush",
        source="test",
    )
    replay = backend.persist_dialogue(
        thread_id="t-idempotent",
        rounds=rounds,
        reason="flush",
        source="test",
    )

    assert first["success"] is True
    assert first["replayed"] is False
    assert replay["success"] is True
    assert replay["replayed"] is True
    assert replay["chunk_ids"] == first["chunk_ids"]
    assert len(backend.store._chunks) == 1
