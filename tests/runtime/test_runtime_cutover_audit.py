from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import re
import sqlite3

import pytest

from scripts.audit_runtime_cutover import build_report
from scripts.runtime_retirement_data import (
    ABANDON_REASON,
    ARCHIVE_KIND,
    CURRENT_DATABASE_NAME,
    LEGACY_DATABASE_NAME,
    LEGACY_ENGINE_ID,
    RUNTIME_FLUSH_DATABASE_NAME,
    TRANSACTION_DECISIONS_KIND,
    RetirementDataError,
    build_backup_plan,
    build_scene_merge_plan,
    build_transaction_decision_plan,
    canonical_json_digest,
    execute_backup_plan,
    inspect_sqlite,
    load_and_validate_archive_manifest,
    validate_scene_merge_plan,
    validate_transaction_decision_plan,
)


CURRENT_ENGINE_ID = "langgraph_v1"


def _create_runtime_database(
    path: Path,
    *,
    engine: str,
    transactions: list[dict[str, object]] | None = None,
    scenes: list[dict[str, object]] | None = None,
    effects: list[tuple[str, str]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA user_version = 4")
        connection.executescript(
            """
            CREATE TABLE transactions (
                transaction_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                lifecycle_status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE scene_entries (
                append_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE stimuli (disposition TEXT NOT NULL);
            CREATE TABLE schedule_runs (status TEXT NOT NULL);
            CREATE TABLE effect_intents (
                transaction_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE feedback_ingress_outbox (status TEXT NOT NULL);
            CREATE TABLE flush_outbox (status TEXT NOT NULL);
            """
        )
        for item in transactions or []:
            payload = {
                "runtime_engine": engine,
                "task_state": item.get("task_state", {}),
            }
            connection.execute(
                "INSERT INTO transactions VALUES (?, ?, ?, ?, ?)",
                (
                    item["transaction_id"],
                    item.get("state", "complete"),
                    item.get("lifecycle_status", "terminal"),
                    item.get("revision", 1),
                    json.dumps(payload, sort_keys=True),
                ),
            )
        for item in scenes or []:
            payload = dict(item)
            payload.setdefault("runtime_engine", engine)
            connection.execute(
                "INSERT INTO scene_entries VALUES (?, ?, ?, ?)",
                (
                    payload["append_id"],
                    payload.get("conversation_id", "conversation"),
                    payload.get("seq", 0),
                    json.dumps(payload, sort_keys=True),
                ),
            )
        connection.executemany(
            "INSERT INTO effect_intents VALUES (?, ?)", effects or []
        )
        connection.commit()


def _create_sqlite_marker(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA user_version = 2")
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('ok')")
        connection.commit()


def _create_runtime_flush(path: Path, *, pending: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE runtime_flush_journal (status TEXT NOT NULL);
            CREATE TABLE runtime_flush_runtime_state (status TEXT NOT NULL);
            CREATE TABLE runtime_flush_materialization (status TEXT NOT NULL);
            """
        )
        if pending:
            connection.execute("INSERT INTO runtime_flush_journal VALUES ('pending')")
        connection.commit()


def _create_composite_flush(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE composite_flush_journal (status TEXT NOT NULL);
            CREATE TABLE composite_flush_engine_state (status TEXT NOT NULL);
            CREATE TABLE composite_flush_materialization (status TEXT NOT NULL);
            INSERT INTO composite_flush_journal VALUES ('pending');
            INSERT INTO composite_flush_engine_state VALUES ('pending');
            INSERT INTO composite_flush_materialization VALUES ('pending');
            """
        )
        connection.commit()


def _create_current_inventory(root: Path) -> Path:
    runtime = root / "account" / "runtime"
    _create_runtime_database(
        runtime / CURRENT_DATABASE_NAME,
        engine=CURRENT_ENGINE_ID,
    )
    _create_sqlite_marker(runtime / "langgraph-checkpoints.sqlite3")
    _create_sqlite_marker(runtime / "langgraph-turn-checkpoints.sqlite3")
    _create_runtime_flush(runtime / RUNTIME_FLUSH_DATABASE_NAME)
    return runtime


def _write_archive_manifest(
    tmp_path: Path,
    runtime_root: Path,
    *,
    sources: list[dict[str, object]] | None = None,
    legacy_transaction_ids: list[str] | None = None,
) -> Path:
    index = 0
    archive_dir = tmp_path / "archive-0"
    while archive_dir.exists():
        index += 1
        archive_dir = tmp_path / f"archive-{index}"
    backup = archive_dir / "backup" / "runtime.sqlite3"
    _create_sqlite_marker(backup)
    backup_metadata = inspect_sqlite(backup)
    default_source = {
        **backup_metadata,
        "path": str((runtime_root / "retired" / LEGACY_DATABASE_NAME).resolve()),
    }
    manifest = {
        "schema_version": 1,
        "kind": ARCHIVE_KIND,
        "operator": "test-operator",
        "created_at": "2026-08-02T00:00:00+00:00",
        "legacy_transaction_ids": legacy_transaction_ids or [],
        "sources": sources or [default_source],
        "backups": [backup_metadata],
        "tombstone": {
            "legacy_runtime_quarantined": True,
            "production_scan_excluded": True,
            "restore_only": True,
        },
        "scene_merge": {
            "validated": True,
            "conflict_count": 0,
            "legacy_only_count": 0,
        },
        "transaction_decisions": {
            "validated": True,
            "unresolved_count": 0,
        },
    }
    path = archive_dir / "archive-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _ready_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "runtime-root"
    _create_current_inventory(root)
    schedules = tmp_path / "schedules"
    schedules.mkdir()
    archive = _write_archive_manifest(tmp_path, root)
    return root, schedules, archive


def test_strict_audit_accepts_complete_synthetic_inventory(tmp_path: Path) -> None:
    root, schedules, archive = _ready_inputs(tmp_path)

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    assert report["legacy_retirement_ready"] is True
    assert report["blocker_count"] == 0
    assert report["checkpoint_flush_inventory"]["complete"] is True
    assert report["archive_manifest"]["valid"] is True


@pytest.mark.parametrize("root_name", ["missing", "empty"])
def test_empty_or_wrong_root_can_never_be_ready(
    tmp_path: Path,
    root_name: str,
) -> None:
    root = tmp_path / root_name
    if root_name == "empty":
        root.mkdir()
    schedules = tmp_path / "schedules"
    schedules.mkdir()
    archive = _write_archive_manifest(tmp_path, root)

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    assert report["legacy_retirement_ready"] is False
    assert report["langgraph_database_count"] == 0
    assert report["checkpoint_flush_inventory"]["complete"] is False


def test_missing_checkpoint_or_flush_inventory_blocks_ready(tmp_path: Path) -> None:
    root, schedules, archive = _ready_inputs(tmp_path)
    missing = root / "account" / "runtime" / "langgraph-turn-checkpoints.sqlite3"
    missing.unlink()

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    assert report["legacy_retirement_ready"] is False
    assert report["checkpoint_flush_inventory"]["missing_count"] == 1


def test_pending_runtime_and_composite_flush_work_blocks_ready(tmp_path: Path) -> None:
    root, schedules, archive = _ready_inputs(tmp_path)
    runtime = root / "account" / "runtime"
    with closing(sqlite3.connect(runtime / RUNTIME_FLUSH_DATABASE_NAME)) as connection:
        connection.execute("INSERT INTO runtime_flush_journal VALUES ('pending')")
        connection.commit()
    _create_composite_flush(runtime / "composite-flush.sqlite3")

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    assert report["composite_flush_blocker_count"] == 3
    assert report["database_blocker_count"] == 4
    assert report["legacy_retirement_ready"] is False


def test_current_runtime_active_transaction_is_observed_but_not_a_retirement_blocker(
    tmp_path: Path,
) -> None:
    root, schedules, archive = _ready_inputs(tmp_path)
    current = root / "account" / "runtime" / CURRENT_DATABASE_NAME
    current.unlink()
    _create_runtime_database(
        current,
        engine=CURRENT_ENGINE_ID,
        transactions=[
            {
                "transaction_id": "current-active",
                "state": "continue",
                "lifecycle_status": "active",
            }
        ],
    )

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    database = next(
        item
        for item in report["databases"]
        if item["path"].endswith(CURRENT_DATABASE_NAME)
    )
    assert database["observations"]["non_terminal_transactions"] == 1
    assert database["blockers"]["non_terminal_transactions"] == 0
    assert database["blocker_count"] == 0
    assert report["legacy_retirement_ready"] is True


def test_current_runtime_unresolved_effect_still_blocks_retirement(
    tmp_path: Path,
) -> None:
    root, schedules, archive = _ready_inputs(tmp_path)
    current = root / "account" / "runtime" / CURRENT_DATABASE_NAME
    current.unlink()
    _create_runtime_database(
        current,
        engine=CURRENT_ENGINE_ID,
        effects=[("current-active", "dispatched")],
    )

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    database = next(
        item
        for item in report["databases"]
        if item["path"].endswith(CURRENT_DATABASE_NAME)
    )
    assert database["blockers"]["non_terminal_effects"] == 1
    assert report["legacy_retirement_ready"] is False


def test_unregistered_online_sqlite_database_blocks_ready(tmp_path: Path) -> None:
    root, schedules, archive = _ready_inputs(tmp_path)
    _create_sqlite_marker(root / "account" / "runtime" / "stray.sqlite3")

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    stray = next(
        item
        for item in report["databases"]
        if item["path"].endswith("stray.sqlite3")
    )
    assert stray["database_kind"] == "unregistered_sqlite"
    assert stray["blocker_count"] == 1
    assert report["legacy_retirement_ready"] is False


def test_scene_audit_uses_append_id_and_canonical_digest(tmp_path: Path) -> None:
    root, schedules, _ = _ready_inputs(tmp_path)
    runtime = root / "account" / "runtime"
    current = runtime / CURRENT_DATABASE_NAME
    current.unlink()
    _create_runtime_database(
        current,
        engine=CURRENT_ENGINE_ID,
        scenes=[
            {"append_id": "same", "seq": 9, "value": "stable"},
            {"append_id": "conflict", "seq": 10, "value": "new"},
        ],
    )
    legacy = runtime / LEGACY_DATABASE_NAME
    _create_runtime_database(
        legacy,
        engine=LEGACY_ENGINE_ID,
        scenes=[
            {"append_id": "same", "seq": 1, "value": "stable"},
            {"append_id": "conflict", "seq": 2, "value": "old"},
            {"append_id": "legacy-only", "seq": 3, "value": "old"},
        ],
    )
    archive = _write_archive_manifest(
        tmp_path,
        root,
        sources=[inspect_sqlite(legacy)],
    )

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    assert report["scene"]["identical_count"] == 1
    assert report["scene"]["conflict_count"] == 1
    assert report["scene"]["legacy_only_count"] == 1
    assert report["legacy_retirement_ready"] is False


def test_active_external_schedule_with_legacy_origin_blocks_ready(
    tmp_path: Path,
) -> None:
    root, schedules, _ = _ready_inputs(tmp_path)
    runtime = root / "account" / "runtime"
    legacy = runtime / LEGACY_DATABASE_NAME
    _create_runtime_database(
        legacy,
        engine=LEGACY_ENGINE_ID,
        transactions=[
            {
                "transaction_id": "legacy-transaction",
                "state": "complete",
                "lifecycle_status": "terminal",
            }
        ],
    )
    archive = _write_archive_manifest(
        tmp_path,
        root,
        sources=[inspect_sqlite(legacy)],
    )
    (schedules / "schedules.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "status": "pending",
                        "origin": {"transaction_id": "legacy-transaction"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    assert report["external_schedules"]["legacy_origin_count"] == 1
    assert report["legacy_retirement_ready"] is False


def test_active_external_schedule_without_origin_is_unbound_and_ready(
    tmp_path: Path,
) -> None:
    root, schedules, archive = _ready_inputs(tmp_path)
    (schedules / "schedules.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "schedule_id": "standalone-schedule",
                        "status": "leased",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    schedule_report = report["external_schedules"]
    assert schedule_report["active_count"] == 1
    assert schedule_report["unbound_origin_count"] == 1
    assert schedule_report["invalid_origin_count"] == 0
    assert schedule_report["ready"] is True
    assert report["legacy_retirement_ready"] is True


def test_populated_but_incomplete_schedule_origin_blocks_ready(
    tmp_path: Path,
) -> None:
    root, schedules, archive = _ready_inputs(tmp_path)
    (schedules / "schedules.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "schedule_id": "ambiguous-schedule",
                        "status": "pending",
                        "origin": {"conversation_id": "conversation-only"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    schedule_report = report["external_schedules"]
    assert schedule_report["unbound_origin_count"] == 0
    assert schedule_report["invalid_origin_count"] == 1
    assert schedule_report["ready"] is False
    assert report["legacy_retirement_ready"] is False


def test_explicit_legacy_engine_schedule_origin_blocks_without_transaction_id(
    tmp_path: Path,
) -> None:
    root, schedules, archive = _ready_inputs(tmp_path)
    (schedules / "schedules.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "schedule_id": "legacy-engine-schedule",
                        "status": "running",
                        "origin": {"runtime_engine": LEGACY_ENGINE_ID},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    schedule_report = report["external_schedules"]
    assert schedule_report["legacy_origin_count"] == 1
    assert schedule_report["invalid_origin_count"] == 0
    assert report["legacy_retirement_ready"] is False


def test_archived_transaction_ids_still_block_external_schedule_origin(
    tmp_path: Path,
) -> None:
    root, schedules, _ = _ready_inputs(tmp_path)
    archive = _write_archive_manifest(
        tmp_path,
        root,
        legacy_transaction_ids=["archived-transaction"],
    )
    (schedules / "schedules.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "status": "leased",
                        "origin": {"transaction_id": "archived-transaction"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=archive,
    )

    assert report["external_schedules"]["legacy_origin_count"] == 1
    assert report["legacy_retirement_ready"] is False


def test_online_unregistered_and_post_manifest_writes_are_reported(
    tmp_path: Path,
) -> None:
    root, schedules, _ = _ready_inputs(tmp_path)
    runtime = root / "account" / "runtime"
    legacy = runtime / LEGACY_DATABASE_NAME
    _create_runtime_database(legacy, engine=LEGACY_ENGINE_ID)

    unregistered_archive = _write_archive_manifest(tmp_path, root)
    unregistered = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=unregistered_archive,
    )
    assert unregistered["online_legacy"]["unregistered_legacy_database_count"] == 1

    source_metadata = inspect_sqlite(legacy)
    registered_archive = _write_archive_manifest(
        tmp_path,
        root,
        sources=[source_metadata],
    )
    with closing(sqlite3.connect(legacy)) as connection:
        connection.execute("INSERT INTO stimuli VALUES ('ready')")
        connection.commit()
    changed = build_report(
        root,
        schedules_root=schedules,
        archive_manifest=registered_archive,
    )

    assert changed["online_legacy"]["new_legacy_write_count"] == 1
    assert changed["legacy_retirement_ready"] is False


def test_backup_requires_operator_lock_and_explicit_execute(tmp_path: Path) -> None:
    root = tmp_path / "runtime-root"
    _create_current_inventory(root)
    schedules = tmp_path / "schedules"
    schedules.mkdir()
    (schedules / "schedules.json").write_text(
        json.dumps({"schema_version": 1, "items": []}),
        encoding="utf-8",
    )
    lock = tmp_path / "maintenance-lock.json"
    lock.write_text(
        json.dumps(
            {
                "maintenance": True,
                "services_stopped": True,
                "sqlite_writers": 0,
                "operator": "operator-a",
                "lock_id": "lock-1",
            }
        ),
        encoding="utf-8",
    )
    plan = build_backup_plan(
        runtime_root=root,
        schedules_root=schedules,
        output_dir=tmp_path / "backup-output",
        operator="operator-a",
        maintenance_lock=lock,
    )
    with pytest.raises(RetirementDataError, match="schedules root"):
        build_backup_plan(
            runtime_root=root,
            schedules_root=None,
            output_dir=tmp_path / "incomplete-backup",
            operator="operator-a",
            maintenance_lock=lock,
        )

    with pytest.raises(RetirementDataError, match="--execute"):
        execute_backup_plan(
            plan,
            operator="operator-a",
            maintenance_lock=lock,
            execute=False,
        )
    with pytest.raises(RetirementDataError, match="operator"):
        execute_backup_plan(
            plan,
            operator="operator-b",
            maintenance_lock=lock,
            execute=True,
        )

    manifest = execute_backup_plan(
        plan,
        operator="operator-a",
        maintenance_lock=lock,
        execute=True,
    )

    assert manifest["mode"] == "executed"
    assert manifest["source_mutation"] is False
    assert manifest["entry_count"] >= 5
    for entry in manifest["entries"]:
        for side in ("source", "backup"):
            assert {
                "path",
                "size",
                "row_count",
                "schema_version",
                "sha256",
                "integrity_check",
            }.issubset(entry[side])
    assert Path(manifest["manifest_path"]).is_file()


def test_scene_merge_plan_is_dry_run_conflict_safe_and_stale_safe(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite3"
    target = tmp_path / "target.sqlite3"
    _create_runtime_database(
        source,
        engine=LEGACY_ENGINE_ID,
        scenes=[
            {"append_id": "same", "seq": 1, "value": "stable"},
            {"append_id": "source-only", "seq": 2, "value": "migrate"},
        ],
    )
    _create_runtime_database(
        target,
        engine=CURRENT_ENGINE_ID,
        scenes=[{"append_id": "same", "seq": 40, "value": "stable"}],
    )

    plan = build_scene_merge_plan(source, target)

    assert plan["mode"] == "dry_run"
    assert plan["writes_performed"] is False
    assert plan["merge_count"] == 1
    assert plan["actions"][0]["materialize_dialogue_or_rag"] is False
    assert validate_scene_merge_plan(
        plan,
        source_database=source,
        target_database=target,
    )["valid"] is True

    with closing(sqlite3.connect(target)) as connection:
        payload = {"append_id": "target-only", "seq": 41, "value": "new"}
        connection.execute(
            "INSERT INTO scene_entries VALUES (?, ?, ?, ?)",
            ("target-only", "conversation", 41, json.dumps(payload)),
        )
        connection.commit()
    with pytest.raises(RetirementDataError, match="stale"):
        validate_scene_merge_plan(
            plan,
            source_database=source,
            target_database=target,
        )

    conflict_target = tmp_path / "conflict.sqlite3"
    _create_runtime_database(
        conflict_target,
        engine=CURRENT_ENGINE_ID,
        scenes=[{"append_id": "same", "seq": 3, "value": "different"}],
    )
    with pytest.raises(RetirementDataError, match="conflict"):
        build_scene_merge_plan(source, conflict_target)


def test_transaction_decisions_never_complete_or_replay_automatically(
    tmp_path: Path,
) -> None:
    database = tmp_path / LEGACY_DATABASE_NAME
    task_state = {"goal": "resume safely"}
    _create_runtime_database(
        database,
        engine=LEGACY_ENGINE_ID,
        transactions=[
            {
                "transaction_id": "active-1",
                "state": "continue",
                "lifecycle_status": "active",
                "revision": 7,
                "task_state": task_state,
            }
        ],
    )
    decisions = {
        "kind": TRANSACTION_DECISIONS_KIND,
        "operator": "operator-a",
        "decisions": [
            {
                "transaction_id": "active-1",
                "expected_revision": 7,
                "decision": "natural_completion",
            }
        ],
    }

    plan = build_transaction_decision_plan(database, decisions)

    assert plan["mode"] == "dry_run"
    assert plan["writes_performed"] is False
    assert plan["actions"] == [
        {
            "transaction_id": "active-1",
            "expected_revision": 7,
            "decision": "natural_completion",
            "planned_action": "wait_for_natural_terminal_state",
            "automatic_complete": False,
            "migrate_checkpoint": False,
            "replay_delegate_or_effect": False,
        }
    ]
    assert validate_transaction_decision_plan(
        plan,
        database=database,
        decisions_manifest=decisions,
    ) == {"valid": True, "active_transaction_count": 1}

    invalid = json.loads(json.dumps(decisions))
    invalid["decisions"][0]["decision"] = "complete"
    with pytest.raises(RetirementDataError, match="unsupported"):
        build_transaction_decision_plan(database, invalid)

    abandon = json.loads(json.dumps(decisions))
    abandon["decisions"][0].update(
        {"decision": "abandon", "reason": ABANDON_REASON}
    )
    assert build_transaction_decision_plan(database, abandon)["actions"][0][
        "automatic_complete"
    ] is False

    restore = json.loads(json.dumps(decisions))
    restore["decisions"][0].update(
        {
            "decision": "restore_task",
            "task_state_digest": canonical_json_digest(task_state),
        }
    )
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO effect_intents VALUES (?, ?)",
            ("active-1", "dispatched"),
        )
        connection.commit()
    with pytest.raises(RetirementDataError, match="stale"):
        validate_transaction_decision_plan(
            plan,
            database=database,
            decisions_manifest=decisions,
        )
    with pytest.raises(RetirementDataError, match="unresolved effects"):
        build_transaction_decision_plan(database, restore)


def test_archive_manifest_detects_tampered_backup(tmp_path: Path) -> None:
    root = tmp_path / "runtime-root"
    root.mkdir()
    manifest = _write_archive_manifest(tmp_path, root)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    backup = Path(payload["backups"][0]["path"])
    with closing(sqlite3.connect(backup)) as connection:
        connection.execute("INSERT INTO marker VALUES ('tampered')")
        connection.commit()

    report = load_and_validate_archive_manifest(manifest)

    assert report["valid"] is False
    assert any("backup[0]" in error for error in report["errors"])


def test_active_data_tools_do_not_embed_retired_brand_text() -> None:
    pattern = re.compile("think" + r"[_ -]?" + "life", re.IGNORECASE)
    project_root = Path(__file__).resolve().parents[2]
    active_paths = [
        project_root / "scripts" / "runtime_retirement_data.py",
        project_root / "scripts" / "audit_runtime_cutover.py",
        project_root / "scripts" / "run_runtime_migration_gate.py",
        Path(__file__),
    ]
    assert all(
        pattern.search(path.read_text(encoding="utf-8")) is None
        for path in active_paths
    )
