from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "migrate_chat_thread.py"
SPEC = importlib.util.spec_from_file_location("migrate_chat_thread", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MIGRATION
SPEC.loader.exec_module(MIGRATION)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_migrates_dialogue_scene_and_episodic_metadata_with_backup(tmp_path: Path) -> None:
    user_root = tmp_path / "data" / "memory" / "chat-api" / "changshengeva"
    old_internal = "changshengeva::demo-thread-1"
    new_internal = "changshengeva::changshengeva-thread"

    dialogue = (
        user_root
        / "dialogues"
        / "2026-07"
        / "chat_changshengeva-demo-thread-1_20260719_121352_115978.json"
    )
    _write_json(
        dialogue,
        {
            "dialogue_id": "chat_changshengeva-demo-thread-1_20260719_121352_115978",
            "user_id": "changshengeva",
            "meta": {"thread_id": old_internal},
            "turns": [{"speaker": "changshengeva", "text": "晚上好"}],
        },
    )

    chunks = user_root / "episodic" / "chunks.jsonl"
    chunks.parent.mkdir(parents=True, exist_ok=True)
    chunks.write_text(
        json.dumps({"chunk_id": "chunk_00001", "thread_id": old_internal, "text": "记忆"}, ensure_ascii=False)
        + "\n"
        + json.dumps({"chunk_id": "chunk_00002", "thread_id": "changshengeva::other", "text": "其他"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    embeddings = chunks.with_name("embeddings.npy")
    np.save(embeddings, np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
    embeddings_before = embeddings.read_bytes()

    scene_root = user_root / "scene"
    scene_root.mkdir(parents=True, exist_ok=True)
    scene = scene_root / "changshengeva__demo-thread-1__0.jsonl"
    scene.write_text('{"seq": 1, "text": "晚上好"}\n', encoding="utf-8")
    scene_meta = scene_root / "changshengeva__demo-thread-1__0.meta.json"
    scene_meta.write_text('{"flush_seq": 1}', encoding="utf-8")
    conversation_state = scene_root / "changshengeva__demo-thread-1.conversation.json"
    conversation_state.write_text('{"conversation_seq": 1}', encoding="utf-8")

    plan = MIGRATION.build_plan(
        project_root=tmp_path,
        username="changshengeva",
        source_thread="demo-thread-1",
        target_thread="changshengeva-thread",
    )
    backup = tmp_path / "backup"
    result = MIGRATION.execute_plan(plan, backup_dir=backup, require_port_free=None)

    assert result["status"] == "complete"
    assert not dialogue.exists()
    migrated_dialogue = dialogue.with_name(
        "chat_changshengeva-changshengeva-thread_20260719_121352_115978.json"
    )
    payload = json.loads(migrated_dialogue.read_text(encoding="utf-8"))
    assert payload["meta"]["thread_id"] == new_internal
    assert "changshengeva-changshengeva-thread" in payload["dialogue_id"]

    records = [json.loads(line) for line in chunks.read_text(encoding="utf-8").splitlines()]
    assert records[0]["thread_id"] == new_internal
    assert records[1]["thread_id"] == "changshengeva::other"
    assert embeddings.read_bytes() == embeddings_before

    assert not scene.exists()
    assert (scene_root / "changshengeva__changshengeva-thread__0.jsonl").is_file()
    assert (scene_root / "changshengeva__changshengeva-thread__0.meta.json").is_file()
    assert (scene_root / "changshengeva__changshengeva-thread.conversation.json").is_file()
    assert (backup / "manifest.json").is_file()
    assert (backup / "original" / dialogue.relative_to(tmp_path)).is_file()

    rollback = MIGRATION.rollback_backup(
        project_root=tmp_path,
        backup_dir=backup,
        require_port_free=None,
    )
    assert rollback["status"] == "rolled_back"
    assert dialogue.is_file()
    assert scene.is_file()
    assert scene_meta.is_file()
    assert conversation_state.is_file()
    assert not migrated_dialogue.exists()
    assert not (scene_root / "changshengeva__changshengeva-thread__0.jsonl").exists()
    restored_records = [json.loads(line) for line in chunks.read_text(encoding="utf-8").splitlines()]
    assert restored_records[0]["thread_id"] == old_internal
    assert embeddings.read_bytes() == embeddings_before


def test_refuses_to_overwrite_existing_scene_target(tmp_path: Path) -> None:
    scene_root = tmp_path / "data" / "memory" / "chat-api" / "changshengeva" / "scene"
    scene_root.mkdir(parents=True)
    (scene_root / "changshengeva__demo-thread-1.jsonl").write_text("{}\n", encoding="utf-8")
    (scene_root / "changshengeva__changshengeva-thread.jsonl").write_text("{}\n", encoding="utf-8")

    with pytest.raises(MIGRATION.MigrationError, match="target already exists"):
        MIGRATION.build_plan(
            project_root=tmp_path,
            username="changshengeva",
            source_thread="demo-thread-1",
            target_thread="changshengeva-thread",
        )


def test_refuses_when_a_planned_source_changes(tmp_path: Path) -> None:
    chunks = (
        tmp_path
        / "data"
        / "memory"
        / "chat-api"
        / "changshengeva"
        / "episodic"
        / "chunks.jsonl"
    )
    chunks.parent.mkdir(parents=True)
    chunks.write_text(
        json.dumps(
            {
                "chunk_id": "chunk_00001",
                "thread_id": "changshengeva::demo-thread-1",
                "text": "before",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    plan = MIGRATION.build_plan(
        project_root=tmp_path,
        username="changshengeva",
        source_thread="demo-thread-1",
        target_thread="changshengeva-thread",
    )
    chunks.write_text(chunks.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(MIGRATION.MigrationError, match="changed after planning"):
        MIGRATION.execute_plan(
            plan,
            backup_dir=tmp_path / "backup",
            require_port_free=None,
        )


def test_rollback_validates_backup_before_deleting_target(tmp_path: Path) -> None:
    scene_root = tmp_path / "data" / "memory" / "chat-api" / "changshengeva" / "scene"
    source = scene_root / "changshengeva__demo-thread-1.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text('{"seq": 1}\n', encoding="utf-8")
    plan = MIGRATION.build_plan(
        project_root=tmp_path,
        username="changshengeva",
        source_thread="demo-thread-1",
        target_thread="changshengeva-thread",
    )
    backup = tmp_path / "backup"
    MIGRATION.execute_plan(plan, backup_dir=backup, require_port_free=None)
    target = scene_root / "changshengeva__changshengeva-thread.jsonl"
    saved_source = backup / "original" / source.relative_to(tmp_path)
    saved_source.write_text("corrupt", encoding="utf-8")

    with pytest.raises(MIGRATION.MigrationError, match="backup verification failed"):
        MIGRATION.rollback_backup(
            project_root=tmp_path,
            backup_dir=backup,
            require_port_free=None,
        )

    assert target.is_file()
    assert not source.exists()


def test_rollback_refuses_post_migration_changes(tmp_path: Path) -> None:
    scene_root = tmp_path / "data" / "memory" / "chat-api" / "changshengeva" / "scene"
    source = scene_root / "changshengeva__demo-thread-1.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text('{"seq": 1}\n', encoding="utf-8")
    plan = MIGRATION.build_plan(
        project_root=tmp_path,
        username="changshengeva",
        source_thread="demo-thread-1",
        target_thread="changshengeva-thread",
    )
    backup = tmp_path / "backup"
    MIGRATION.execute_plan(plan, backup_dir=backup, require_port_free=None)
    target = scene_root / "changshengeva__changshengeva-thread.jsonl"
    target.write_text(target.read_text(encoding="utf-8") + '{"seq": 2}\n', encoding="utf-8")

    with pytest.raises(MIGRATION.MigrationError, match="changed after completion"):
        MIGRATION.rollback_backup(
            project_root=tmp_path,
            backup_dir=backup,
            require_port_free=None,
        )

    assert target.is_file()
    assert not source.exists()
