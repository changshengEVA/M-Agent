from __future__ import annotations

from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

import pytest

from scripts.runtime_retirement_data import (
    ABANDON_REASON,
    CURRENT_DATABASE_NAME,
    LEGACY_DATABASE_NAME,
    LEGACY_ENGINE_ID,
    RETIREMENT_REQUEST_KIND,
    TRANSACTION_DECISIONS_KIND,
    RetirementDataError,
    _print_stdout_json,
    build_backup_plan,
    build_retirement_execution_plan,
    build_scene_merge_plan,
    build_transaction_decision_plan,
    execute_backup_plan,
    execute_retirement_execution_plan,
    load_and_validate_archive_manifest,
)


CURRENT_ENGINE_ID = "langgraph_v1"


def _create_database(
    path: Path,
    *,
    engine: str,
    scenes: list[dict[str, object]] | None = None,
    active_transaction: bool = False,
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
            CREATE TABLE conversation_state (
                conversation_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                scene_seq INTEGER NOT NULL,
                flush_watermark INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE scene_entries (
                append_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(conversation_id, seq)
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
        if active_transaction:
            connection.execute(
                "INSERT INTO transactions VALUES (?, ?, ?, ?, ?)",
                (
                    "active-1",
                    "continue",
                    "active",
                    7,
                    json.dumps(
                        {"runtime_engine": engine, "task_state": {"goal": "keep"}}
                    ),
                ),
            )
        grouped: dict[str, list[dict[str, object]]] = {}
        for raw in scenes or []:
            grouped.setdefault(str(raw["conversation_id"]), []).append(raw)
        for conversation_id, entries in grouped.items():
            max_seq = max(int(item["seq"]) for item in entries)
            connection.execute(
                "INSERT INTO conversation_state VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, "thread-a", max_seq, max_seq, max_seq + 1, "now"),
            )
            for raw in entries:
                payload = {
                    "append_id": raw["append_id"],
                    "seq": raw["seq"],
                    "occurred_at": f"2026-08-02T00:00:0{raw['seq']}+00:00",
                    "entry_type": "outcome",
                    "actor": "work",
                    "text": raw["text"],
                    "runtime_engine": engine,
                }
                connection.execute(
                    "INSERT INTO scene_entries VALUES (?, ?, ?, ?)",
                    (
                        raw["append_id"],
                        conversation_id,
                        raw["seq"],
                        json.dumps(payload, sort_keys=True),
                    ),
                )
        connection.commit()


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _prepare_batch(tmp_path: Path) -> tuple[dict[str, object], Path, Path, Path]:
    runtime_root = tmp_path / "online-runtime"
    primary = runtime_root / "account-a" / "runtime"
    legacy = primary / LEGACY_DATABASE_NAME
    target = primary / CURRENT_DATABASE_NAME
    conversation_id = "account-a::thread-a::21"
    _create_database(
        legacy,
        engine=LEGACY_ENGINE_ID,
        active_transaction=True,
        scenes=[
            {
                "append_id": "z-first-by-time",
                "conversation_id": conversation_id,
                "seq": 1,
                "text": "first",
            },
            {
                "append_id": "a-second-by-time",
                "conversation_id": conversation_id,
                "seq": 2,
                "text": "second",
            },
        ],
    )
    _create_database(target, engine=CURRENT_ENGINE_ID)
    empty_legacy = runtime_root / "account-b" / "runtime" / LEGACY_DATABASE_NAME
    _create_database(empty_legacy, engine=LEGACY_ENGINE_ID)

    schedules_root = tmp_path / "schedules"
    schedule_path = schedules_root / "by_user" / "account-a" / "schedules.json"
    _write_json(
        schedule_path,
        {
            "thread_id": "thread-a",
            "items": [
                {
                    "schedule_id": "stale-lease",
                    "thread_id": "thread-a",
                    "due_at_utc": "2026-08-02T00:00:00+00:00",
                    "timezone_name": "Asia/Shanghai",
                    "text": "continue the deferred objective",
                    "status": "leased",
                    "created_at": "2026-08-01T00:00:00+00:00",
                }
            ],
        },
    )
    lock = _write_json(
        tmp_path / "control" / "maintenance-lock.json",
        {
            "maintenance": True,
            "services_stopped": True,
            "sqlite_writers": 0,
            "operator": "operator-a",
            "lock_id": "lock-a",
        },
    )
    backup_dir = tmp_path / "validated-backup"
    backup_plan = build_backup_plan(
        runtime_root=runtime_root,
        schedules_root=schedules_root,
        output_dir=backup_dir,
        operator="operator-a",
        maintenance_lock=lock,
    )
    backup = execute_backup_plan(
        backup_plan,
        operator="operator-a",
        maintenance_lock=lock,
        execute=True,
    )

    control = tmp_path / "control"
    scene_plan = _write_json(
        control / "scene-plan.json",
        build_scene_merge_plan(legacy, target),
    )
    decisions = _write_json(
        control / "decisions-a.json",
        {
            "kind": TRANSACTION_DECISIONS_KIND,
            "operator": "operator-a",
            "decisions": [
                {
                    "transaction_id": "active-1",
                    "expected_revision": 7,
                    "decision": "abandon",
                    "reason": ABANDON_REASON,
                    "idempotency_key": "retire-active-1",
                    "transition_id": "transition-active-1",
                    "created_at": "2026-08-02T01:00:00+00:00",
                }
            ],
        },
    )
    transaction_plan = _write_json(
        control / "transaction-plan-a.json",
        build_transaction_decision_plan(
            legacy,
            json.loads(decisions.read_text(encoding="utf-8")),
        ),
    )
    empty_decisions = _write_json(
        control / "decisions-b.json",
        {
            "kind": TRANSACTION_DECISIONS_KIND,
            "operator": "operator-a",
            "decisions": [],
        },
    )
    empty_transaction_plan = _write_json(
        control / "transaction-plan-b.json",
        build_transaction_decision_plan(
            empty_legacy,
            json.loads(empty_decisions.read_text(encoding="utf-8")),
        ),
    )
    archive_dir = tmp_path / "retirement-archive"
    request: dict[str, object] = {
        "schema_version": 1,
        "kind": RETIREMENT_REQUEST_KIND,
        "batch_id": "batch-a",
        "operator": "operator-a",
        "runtime_root": str(runtime_root),
        "archive_dir": str(archive_dir),
        "backup_manifest": str(backup["manifest_path"]),
        "databases": [
            {
                "legacy_database": str(legacy),
                "target_database": str(target),
                "scene_plan": str(scene_plan),
                "transaction_plan": str(transaction_plan),
                "transaction_decisions": str(decisions),
            },
            {
                "legacy_database": str(empty_legacy),
                "transaction_plan": str(empty_transaction_plan),
                "transaction_decisions": str(empty_decisions),
            },
        ],
        "schedules": [
            {"path": str(schedule_path), "schedule_ids": ["stale-lease"]}
        ],
    }
    return request, lock, archive_dir, schedule_path


def _restore_permissions(root: Path) -> None:
    if not root.exists():
        return
    for path in root.rglob("*"):
        try:
            path.chmod(0o777 if path.is_dir() else 0o666)
        except OSError:
            pass


def test_retirement_is_dry_run_by_default_and_executes_auditable_batch(
    tmp_path: Path,
) -> None:
    request, lock, archive_dir, schedule_path = _prepare_batch(tmp_path)
    plan = build_retirement_execution_plan(request, maintenance_lock=lock)

    assert plan["mode"] == "dry_run"
    assert plan["writes_performed"] is False
    assert plan["database_count"] == 2
    assert plan["scene_merge_count"] == 2
    assert plan["schedule_migration_count"] == 1
    assert not archive_dir.exists()
    with pytest.raises(RetirementDataError, match="--execute"):
        execute_retirement_execution_plan(
            plan,
            maintenance_lock=lock,
            execute=False,
        )

    tampered = json.loads(json.dumps(plan))
    tampered["items"][0]["target_database"] = str(tmp_path / "wrong.sqlite3")
    with pytest.raises(RetirementDataError, match="changed after planning"):
        execute_retirement_execution_plan(
            tampered,
            maintenance_lock=lock,
            execute=True,
        )
    assert not archive_dir.exists()

    report = execute_retirement_execution_plan(
        plan,
        maintenance_lock=lock,
        execute=True,
    )
    try:
        assert report["mode"] == "executed"
        assert report["transaction_decisions"]["source_rows_terminalized"] is False
        assert report["transaction_decisions"]["unresolved_count"] == 0
        assert report["schedule_migrations"]["origin_fabricated"] is False
        assert load_and_validate_archive_manifest(report["manifest_path"])["valid"]

        target = Path(request["databases"][0]["target_database"])
        with closing(sqlite3.connect(target)) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM scene_entries ORDER BY seq"
            ).fetchall()
            payloads = [json.loads(row[0]) for row in rows]
            state = connection.execute(
                "SELECT scene_seq, flush_watermark FROM conversation_state"
            ).fetchone()
        assert [item["append_id"] for item in payloads] == [
            "z-first-by-time",
            "a-second-by-time",
        ]
        assert [item["source_seq"] for item in payloads] == [1, 2]
        assert state == (2, 2)

        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))["items"][0]
        assert schedule["schema_version"] == 2
        assert schedule["status"] == "pending"
        assert schedule["origin"] == {}
        assert schedule["schedule_id"] == "stale-lease"
        assert schedule["thread_id"] == "thread-a"
        assert schedule["due_at_utc"] == "2026-08-02T00:00:00+00:00"
        assert schedule["deferred_objective"]["description"] == (
            "continue the deferred objective"
        )

        for item in request["databases"]:
            online = Path(item["legacy_database"])
            assert not online.exists()
            quarantined = (
                archive_dir / "restore-only" / online.relative_to(request["runtime_root"])
            )
            assert quarantined.is_file()
            assert not quarantined.stat().st_mode & (
                stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
            )
    finally:
        _restore_permissions(archive_dir)


def test_retirement_blocks_pending_obligations_and_path_escape(tmp_path: Path) -> None:
    request, lock, _, _ = _prepare_batch(tmp_path)
    escaped = json.loads(json.dumps(request))
    escaped["databases"][0]["legacy_database"] = str(tmp_path / LEGACY_DATABASE_NAME)
    with pytest.raises(RetirementDataError, match="escapes"):
        build_retirement_execution_plan(escaped, maintenance_lock=lock)

    legacy = Path(request["databases"][0]["legacy_database"])
    with closing(sqlite3.connect(legacy)) as connection:
        connection.execute(
            "INSERT INTO effect_intents VALUES (?, ?)",
            ("active-1", "dispatched"),
        )
        connection.commit()
    with pytest.raises(RetirementDataError, match="backup manifest is not valid"):
        build_retirement_execution_plan(request, maintenance_lock=lock)


def test_cli_json_stdout_is_safe_on_gbk_console(monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.BytesIO()
    console = io.TextIOWrapper(output, encoding="gbk", errors="strict")
    monkeypatch.setattr(sys, "stdout", console)

    _print_stdout_json({"objective": "deferred \u263a"})
    console.flush()

    assert b"\\u263a" in output.getvalue()


def test_plan_identity_ignores_shm_mtime_but_rejects_content_change(
    tmp_path: Path,
) -> None:
    request, lock, _, _ = _prepare_batch(tmp_path)
    legacy = Path(request["databases"][0]["legacy_database"])
    shm = Path(str(legacy) + "-shm")
    shm.write_bytes(b"stable-sidecar-content")

    first = build_retirement_execution_plan(request, maintenance_lock=lock)
    second = build_retirement_execution_plan(request, maintenance_lock=lock)
    assert first["items"] == second["items"]
    assert first["validation_digest"] == second["validation_digest"]
    assert any(
        entry["source"] == str(shm)
        for entry in first["items"][0]["quarantine_files"]
    )

    before = shm.stat().st_mtime_ns
    os.utime(shm, ns=(before + 10_000_000, before + 10_000_000))
    touched = build_retirement_execution_plan(request, maintenance_lock=lock)
    assert first["items"] == touched["items"]
    assert first["validation_digest"] == touched["validation_digest"]

    shm.write_bytes(b"changed-sidecar-content")
    with pytest.raises(RetirementDataError, match="changed after planning"):
        execute_retirement_execution_plan(
            first,
            maintenance_lock=lock,
            execute=True,
        )
