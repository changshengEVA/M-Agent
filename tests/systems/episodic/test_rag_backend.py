"""SimpleRagEpisodicBackend correctness and recovery tests."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from m_agent.systems.episodic.default.rag_backend import (
    SimpleRagEpisodicBackend,
)
from m_agent.systems.episodic.default.rag_store import (
    INDEX_FORMAT_VERSION,
    OFFLINE_EMBED_DIM,
    OFFLINE_EMBED_VERSION,
)


def _backend(tmp_path: Path, *, workflow_id: str = "test", **kwargs: object):
    return SimpleRagEpisodicBackend(
        storage_dir=str(tmp_path / "rag"),
        workflow_id=workflow_id,
        embed_model="hash",
        **kwargs,
    )


def test_rag_persist_and_shallow_recall_has_explicit_mode(tmp_path: Path) -> None:
    backend = _backend(tmp_path, top_k=3)
    backend.persist_round(
        thread_id="t1",
        user_message="I visited Paris last summer.",
        assistant_message="Paris is beautiful in summer.",
    )

    # The capability adapter appends this legacy suffix.  It must still map to
    # the exact persisted thread rather than falling back to a global search.
    result = backend.shallow_recall("Paris summer", thread_id="t1:shallow")

    assert result["hit"] is True
    assert result["mode"] == "shallow_recall"
    assert result["supported"] is True
    assert result["scope_thread_id"] == "t1"
    assert "Paris" in result["answer"]
    assert result["evidence"]
    assert {item["thread_id"] for item in result["evidence"]} == {"t1"}


def test_deep_recall_is_compatible_but_explicitly_unsupported(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.persist_round(
        thread_id="t1",
        user_message="I visited Paris last summer.",
        assistant_message="Paris is beautiful in summer.",
    )

    shallow = backend.shallow_recall("Paris summer", thread_id="t1")
    deep = backend.deep_recall("Paris summer", thread_id="t1")

    assert shallow["hit"] is True
    assert deep == {
        "answer": "",
        "evidence": [],
        "mode": "deep_recall",
        "backend": "rag",
        "thread_id": "t1",
        "scope_thread_id": "t1",
        "hit": False,
        "supported": False,
        "reason": "deep_recall_not_supported",
        "deprecated": True,
        "replacement": "shallow_recall",
    }


def test_rag_recall_is_strictly_isolated_by_thread(tmp_path: Path) -> None:
    backend = _backend(tmp_path, top_k=5)
    backend.persist_round(
        thread_id="thread-a",
        user_message="The orchid access code is amber.",
        assistant_message="I will remember amber for this thread.",
    )
    backend.persist_round(
        thread_id="thread-b",
        user_message="The orchid access code is cobalt.",
        assistant_message="I will remember cobalt for this thread.",
    )
    backend.persist_round(
        thread_id="thread-a:shallow",
        user_message="The orchid access code is magenta.",
        assistant_message="This is a literal suffixed thread id.",
    )

    first = backend.shallow_recall("orchid access code", thread_id="thread-a")
    second = backend.shallow_recall("orchid access code", thread_id="thread-b")
    literal_suffix = backend.shallow_recall(
        "orchid access code",
        thread_id="thread-a:shallow",
    )

    assert first["hit"] is True
    assert "amber" in first["answer"]
    assert "cobalt" not in first["answer"]
    assert second["hit"] is True
    assert "cobalt" in second["answer"]
    assert "amber" not in second["answer"]
    assert "magenta" in literal_suffix["answer"]
    assert "amber" not in literal_suffix["answer"]
    assert literal_suffix["scope_thread_id"] == "thread-a:shallow"
    assert backend.shallow_recall(
        "orchid access code",
        thread_id="missing-thread",
    )["hit"] is False


def test_rag_zero_relevance_returns_an_explicit_no_hit(tmp_path: Path) -> None:
    backend = _backend(tmp_path, min_score=0.2)
    backend.persist_round(
        thread_id="t1",
        user_message="We toured Paris museums during summer.",
        assistant_message="The travel memory is saved.",
    )

    result = backend.shallow_recall(
        "quantum entanglement particle spin",
        thread_id="t1",
    )

    assert result["hit"] is False
    assert result["answer"] == ""
    assert result["evidence"] == []
    assert result["reason"] == "no_relevant_memory"
    assert result["min_score"] == pytest.approx(0.2)


def test_offline_embedding_matches_cjk_subphrases(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.persist_round(
        thread_id="中文线程",
        user_message="我去年夏天去了巴黎旅行。",
        assistant_message="这段旅行已经记录。",
    )

    result = backend.shallow_recall("巴黎旅行", thread_id="中文线程")

    assert result["hit"] is True
    assert "巴黎" in result["answer"]


def test_offline_embedding_is_stable_across_process_hash_seeds() -> None:
    project_root = Path(__file__).resolve().parents[3]
    source_root = project_root / "src"
    script = (
        "import json; "
        "from m_agent.systems.episodic.default.rag_store import _default_embed; "
        "print(json.dumps(_default_embed('Stable Paris 巴黎 token')))"
    )

    vectors = []
    for seed in ("1", "987654"):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(
            value for value in (str(source_root), existing_pythonpath) if value
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=project_root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        vectors.append(json.loads(completed.stdout))

    assert len(vectors[0]) == OFFLINE_EMBED_DIM
    assert vectors[0] == vectors[1]
    assert any(value != 0 for value in vectors[0])


@pytest.mark.parametrize(
    ("damage", "expected_reason"),
    [
        ("incompatible_metadata", "incompatible_metadata"),
        ("corrupt_metadata", "missing_or_corrupt_metadata"),
        ("corrupt_embeddings", "missing_or_corrupt_embeddings"),
    ],
)
def test_index_damage_is_rebuilt_from_chunks(
    tmp_path: Path,
    damage: str,
    expected_reason: str,
) -> None:
    backend = _backend(tmp_path, workflow_id=damage)
    backend.persist_round(
        thread_id="t-rebuild",
        user_message="The lighthouse key is silver.",
        assistant_message="Silver is recorded.",
    )
    metadata_path = backend.store.metadata_path
    embeddings_path = backend.store.embeddings_path

    if damage == "incompatible_metadata":
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["embedding"]["version"] = "python-hash-v0"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    elif damage == "corrupt_metadata":
        metadata_path.write_text("{not-json", encoding="utf-8")
    else:
        embeddings_path.write_bytes(b"not-a-numpy-index")

    recovered = _backend(tmp_path, workflow_id=damage)
    result = recovered.shallow_recall("lighthouse silver", thread_id="t-rebuild")

    assert recovered.store.last_rebuild_reason == expected_reason
    assert result["hit"] is True
    assert "silver" in result["answer"]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["format_version"] == INDEX_FORMAT_VERSION
    assert metadata["embedding"]["version"] == OFFLINE_EMBED_VERSION


def test_corrupt_chunk_lines_are_salvaged_without_poisoning_the_index(
    tmp_path: Path,
) -> None:
    backend = _backend(tmp_path, workflow_id="chunk-salvage")
    backend.persist_round(
        thread_id="t-salvage",
        user_message="The observatory opens at dawn.",
        assistant_message="Dawn is recorded.",
    )
    with backend.store.chunks_path.open("ab") as handle:
        handle.write(b"{broken-json\n")

    recovered = _backend(tmp_path, workflow_id="chunk-salvage")

    assert recovered.store.last_rebuild_reason == "corrupt_chunks"
    assert recovered.store.chunk_count == 1
    assert recovered.shallow_recall(
        "observatory dawn",
        thread_id="t-salvage",
    )["hit"] is True


def test_persistence_publishes_checksummed_artifacts_without_temp_files(
    tmp_path: Path,
) -> None:
    backend = _backend(tmp_path, workflow_id="atomic")
    backend.persist_round(
        thread_id="t1",
        user_message="hello",
        assistant_message="hi",
    )

    metadata = json.loads(backend.store.metadata_path.read_text(encoding="utf-8"))
    chunks_bytes = backend.store.chunks_path.read_bytes()
    embeddings_bytes = backend.store.embeddings_path.read_bytes()
    assert metadata["chunks"]["sha256"] == hashlib.sha256(chunks_bytes).hexdigest()
    assert metadata["embeddings"]["sha256"] == hashlib.sha256(
        embeddings_bytes
    ).hexdigest()
    assert list(backend.persistence_root.glob(".*.tmp")) == []


def test_rag_dialogue_materializes_round_notes_and_scopes_legacy_merge(
    tmp_path: Path,
) -> None:
    backend = _backend(tmp_path, workflow_id="flush")
    backend.persist_dialogue(
        thread_id="thread-a",
        rounds=[
            {
                "user_message": "hello",
                "assistant_message": "hi",
                "dialogue_id": "dialogue-a",
                "episode_notes": [{"note": "snapshot note"}],
            }
        ],
        reason="flush",
        source="test",
    )
    backend.persist_dialogue(
        thread_id="thread-b",
        rounds=[
            {
                "user_message": "other",
                "assistant_message": "thread",
                "dialogue_id": "dialogue-b",
            }
        ],
        reason="flush",
        source="test",
    )

    first, second = backend.store._chunks
    assert first["meta"]["trace_summary"]["episode_notes"] == [
        {"note": "snapshot note"}
    ]
    assert "trace_summary" not in second["meta"]

    backend.on_flush(
        thread_id="thread-a",
        conversation_id="dialogue-a",
        episode_notes=[{"note": "legacy targeted note"}],
    )
    assert backend.store._chunks[0]["meta"]["trace_summary"]["episode_notes"] == [
        {"note": "legacy targeted note"}
    ]
    assert "trace_summary" not in backend.store._chunks[1]["meta"]

    # A mismatched dialogue id is a no-op, never a write to the global tail.
    backend.on_flush(
        thread_id="thread-a",
        conversation_id="missing-dialogue",
        episode_notes=[{"note": "must not leak"}],
    )
    assert "trace_summary" not in backend.store._chunks[1]["meta"]


def test_rag_dialogue_materialization_is_idempotent_by_dialogue_id(
    tmp_path: Path,
) -> None:
    backend = _backend(tmp_path, workflow_id="flush-idempotent")
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
