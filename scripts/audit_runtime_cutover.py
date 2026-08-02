"""Strict, read-only audit for the durable Runtime retirement cutover.

No persistence root is selected implicitly.  Operators must provide the
runtime tree, the external schedules tree, and the archive manifest.  Every
SQLite connection uses ``mode=ro`` and this module never creates a database.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterable, Mapping

try:  # Package import in tests, local import when invoked as a script.
    from scripts.runtime_retirement_data import (
        CHECKPOINT_DATABASE_NAMES,
        COMPOSITE_FLUSH_DATABASE_NAME,
        CURRENT_DATABASE_NAME,
        LEGACY_DATABASE_NAME,
        LEGACY_ENGINE_ID,
        RUNTIME_FLUSH_DATABASE_NAME,
        RetirementDataError,
        inspect_sqlite,
        load_and_validate_archive_manifest,
        load_scene_entries,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised by CLI invocation
    from runtime_retirement_data import (  # type: ignore[no-redef]
        CHECKPOINT_DATABASE_NAMES,
        COMPOSITE_FLUSH_DATABASE_NAME,
        CURRENT_DATABASE_NAME,
        LEGACY_DATABASE_NAME,
        LEGACY_ENGINE_ID,
        RUNTIME_FLUSH_DATABASE_NAME,
        RetirementDataError,
        inspect_sqlite,
        load_and_validate_archive_manifest,
        load_scene_entries,
    )


CURRENT_ENGINE_ID = "langgraph_v1"
NON_TERMINAL_TRANSACTION_STATES = frozenset({"continue", "pause"})
NON_TERMINAL_STIMULUS_DISPOSITIONS = frozenset({"ready", "claimed"})
NON_TERMINAL_SCHEDULE_STATUSES = frozenset(
    {"scheduled", "due", "blocked_on_activation", "claimed"}
)
NON_TERMINAL_EFFECT_STATUSES = frozenset(
    {"intent_recorded", "dispatched", "result_committed"}
)
NON_TERMINAL_OUTBOX_STATUSES = frozenset({"pending", "ready", "claimed"})
ACTIVE_EXTERNAL_SCHEDULE_STATUSES = frozenset({"pending", "leased", "running"})


def _resolved(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})")
    }


def _column_counts(
    connection: sqlite3.Connection,
    *,
    table: str,
    column: str,
) -> Dict[str, int]:
    if column not in _columns(connection, table):
        return {}
    rows = connection.execute(
        f'SELECT "{column}" AS value, COUNT(*) AS count '
        f'FROM "{table}" GROUP BY "{column}" ORDER BY "{column}"'
    ).fetchall()
    return {
        str(row["value"] if row["value"] is not None else "<null>"): int(
            row["count"]
        )
        for row in rows
    }


def _runtime_engine_counts(connection: sqlite3.Connection) -> Dict[str, int]:
    if "payload_json" not in _columns(connection, "transactions"):
        return {}
    counter: Counter[str] = Counter()
    for row in connection.execute("SELECT payload_json FROM transactions"):
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            counter["<invalid-payload>"] += 1
            continue
        if not isinstance(payload, Mapping):
            counter["<invalid-payload>"] += 1
            continue
        counter[str(payload.get("runtime_engine", "") or "<missing>")] += 1
    return dict(sorted(counter.items()))


def _sum_matching(counts: Mapping[str, int], statuses: Iterable[str]) -> int:
    wanted = set(statuses)
    return sum(int(count) for status, count in counts.items() if status in wanted)


def _audit_flush_database(path: Path) -> Dict[str, Any]:
    if path.name == RUNTIME_FLUSH_DATABASE_NAME:
        tables = {
            "journal_statuses": "runtime_flush_journal",
            "runtime_statuses": "runtime_flush_runtime_state",
            "materialization_statuses": "runtime_flush_materialization",
        }
        database_kind = "runtime_flush"
    else:
        tables = {
            "journal_statuses": "composite_flush_journal",
            "runtime_statuses": "composite_flush_engine_state",
            "materialization_statuses": "composite_flush_materialization",
        }
        database_kind = "composite_flush"
    with closing(_connect_read_only(path)) as connection:
        counts = {
            label: _column_counts(connection, table=table, column="status")
            for label, table in tables.items()
        }
    blockers = {
        "pending_flush_journals": int(counts["journal_statuses"].get("pending", 0)),
        "pending_flush_runtimes": int(counts["runtime_statuses"].get("pending", 0)),
        "pending_flush_materializations": int(
            counts["materialization_statuses"].get("pending", 0)
        ),
    }
    return {
        "path": str(path.resolve()),
        "database_kind": database_kind,
        **counts,
        "metadata": inspect_sqlite(path),
        "blockers": blockers,
        "blocker_count": sum(blockers.values()),
    }


def audit_database(path: Path) -> Dict[str, Any]:
    """Audit one runtime/flush database without opening it for writes."""

    if path.name in {RUNTIME_FLUSH_DATABASE_NAME, COMPOSITE_FLUSH_DATABASE_NAME}:
        return _audit_flush_database(path)
    if path.name in CHECKPOINT_DATABASE_NAMES:
        return {
            "path": str(path.resolve()),
            "database_kind": "checkpoint",
            "metadata": inspect_sqlite(path),
            "blockers": {},
            "blocker_count": 0,
        }
    expected_engine = (
        LEGACY_ENGINE_ID
        if path.name == LEGACY_DATABASE_NAME
        else CURRENT_ENGINE_ID
        if path.name == CURRENT_DATABASE_NAME
        else ""
    )
    with closing(_connect_read_only(path)) as connection:
        transaction_states = _column_counts(
            connection, table="transactions", column="state"
        )
        transaction_lifecycles = _column_counts(
            connection, table="transactions", column="lifecycle_status"
        )
        stimulus_dispositions = _column_counts(
            connection, table="stimuli", column="disposition"
        )
        schedule_statuses = _column_counts(
            connection, table="schedule_runs", column="status"
        )
        effect_statuses = _column_counts(
            connection, table="effect_intents", column="status"
        )
        feedback_outbox_statuses = _column_counts(
            connection, table="feedback_ingress_outbox", column="status"
        )
        flush_outbox_statuses = _column_counts(
            connection, table="flush_outbox", column="status"
        )
        runtime_engines = _runtime_engine_counts(connection)

    database_kind = (
        "legacy"
        if LEGACY_ENGINE_ID in runtime_engines or path.name == LEGACY_DATABASE_NAME
        else "langgraph"
        if path.name == CURRENT_DATABASE_NAME
        else "unregistered_sqlite"
    )
    non_terminal_transactions = _sum_matching(
        transaction_states, NON_TERMINAL_TRANSACTION_STATES
    )
    blockers = {
        # Current-runtime transactions are ordinary product work, not legacy
        # retirement work.  They may remain active across this cutover.  The
        # same states in a legacy store are recoverable legacy work and must
        # still block retirement.
        "non_terminal_transactions": (
            non_terminal_transactions
            if database_kind == "legacy"
            else 0
        ),
        "non_terminal_stimuli": _sum_matching(
            stimulus_dispositions, NON_TERMINAL_STIMULUS_DISPOSITIONS
        ),
        "non_terminal_schedules": _sum_matching(
            schedule_statuses, NON_TERMINAL_SCHEDULE_STATUSES
        ),
        "non_terminal_effects": _sum_matching(
            effect_statuses, NON_TERMINAL_EFFECT_STATUSES
        ),
        "pending_feedback_outbox": _sum_matching(
            feedback_outbox_statuses, NON_TERMINAL_OUTBOX_STATUSES
        ),
        "pending_flush_outbox": _sum_matching(
            flush_outbox_statuses, NON_TERMINAL_OUTBOX_STATUSES
        ),
        "engine_store_mismatches": sum(
            int(count)
            for engine, count in runtime_engines.items()
            if expected_engine and engine != expected_engine
        ),
        "unregistered_sqlite_database": int(
            not expected_engine and LEGACY_ENGINE_ID not in runtime_engines
        ),
    }
    return {
        "path": str(path.resolve()),
        "database_kind": database_kind,
        "expected_runtime_engine": expected_engine,
        "transaction_states": transaction_states,
        "transaction_lifecycles": transaction_lifecycles,
        "runtime_engines": runtime_engines,
        "stimulus_dispositions": stimulus_dispositions,
        "schedule_statuses": schedule_statuses,
        "effect_statuses": effect_statuses,
        "feedback_outbox_statuses": feedback_outbox_statuses,
        "flush_outbox_statuses": flush_outbox_statuses,
        "observations": {
            "non_terminal_transactions": non_terminal_transactions,
        },
        "metadata": inspect_sqlite(path),
        "blockers": blockers,
        "blocker_count": sum(blockers.values()),
    }


def discover_runtime_databases(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return []
    return sorted(path.resolve() for path in root.rglob("*.sqlite3") if path.is_file())


def _safe_audit_database(path: Path) -> Dict[str, Any]:
    try:
        return audit_database(path)
    except (OSError, sqlite3.Error, RetirementDataError, ValueError) as exc:
        return {
            "path": str(path.resolve()),
            "database_kind": "invalid",
            "error": str(exc),
            "blockers": {"unreadable_or_invalid_database": 1},
            "blocker_count": 1,
        }


def _checkpoint_flush_inventory(
    current_paths: list[Path],
    all_paths: set[Path],
) -> Dict[str, Any]:
    runtime_directories: list[Dict[str, Any]] = []
    missing: list[str] = []
    inventory_paths: set[Path] = set()
    required_names = sorted((*CHECKPOINT_DATABASE_NAMES, RUNTIME_FLUSH_DATABASE_NAME))
    for directory in sorted({path.parent for path in current_paths}):
        entries: list[Dict[str, Any]] = []
        directory_missing: list[str] = []
        for name in required_names:
            path = (directory / name).resolve()
            if path not in all_paths:
                directory_missing.append(name)
                missing.append(str(path))
                continue
            inventory_paths.add(path)
            try:
                entries.append(inspect_sqlite(path))
            except (OSError, sqlite3.Error, RetirementDataError) as exc:
                directory_missing.append(name)
                missing.append(str(path))
                entries.append({"path": str(path), "error": str(exc)})
        runtime_directories.append(
            {
                "path": str(directory),
                "required": required_names,
                "missing": directory_missing,
                "entries": entries,
                "complete": not directory_missing,
            }
        )
    return {
        "runtime_directory_count": len(runtime_directories),
        "required_per_directory": required_names,
        "missing_count": len(missing),
        "missing": missing,
        "complete": bool(current_paths) and not missing,
        "directories": runtime_directories,
        "inventory_paths": sorted(str(path) for path in inventory_paths),
    }


def _audit_scene(
    legacy_paths: list[Path],
    current_paths: list[Path],
) -> Dict[str, Any]:
    current_by_directory = {path.parent: path for path in current_paths}
    identical: list[str] = []
    conflicts: list[str] = []
    legacy_only: list[str] = []
    invalid: list[Dict[str, str]] = []
    legacy_entry_count = 0
    current_entry_count = 0
    seen_current: set[Path] = set()
    for source in legacy_paths:
        target = current_by_directory.get(source.parent)
        try:
            source_entries = load_scene_entries(source)
        except (OSError, sqlite3.Error, RetirementDataError, ValueError) as exc:
            invalid.append({"path": str(source), "error": str(exc)})
            continue
        legacy_entry_count += len(source_entries)
        if target is None:
            legacy_only.extend(f"{source}:{append_id}" for append_id in source_entries)
            continue
        seen_current.add(target)
        try:
            target_entries = load_scene_entries(target)
        except (OSError, sqlite3.Error, RetirementDataError, ValueError) as exc:
            invalid.append({"path": str(target), "error": str(exc)})
            legacy_only.extend(f"{source}:{append_id}" for append_id in source_entries)
            continue
        current_entry_count += len(target_entries)
        for append_id in sorted(source_entries):
            qualified = f"{source.parent}:{append_id}"
            target_entry = target_entries.get(append_id)
            if target_entry is None:
                legacy_only.append(qualified)
            elif target_entry["digest"] != source_entries[append_id]["digest"]:
                conflicts.append(qualified)
            else:
                identical.append(qualified)
    for target in current_paths:
        if target in seen_current:
            continue
        try:
            current_entry_count += len(load_scene_entries(target))
        except (OSError, sqlite3.Error, RetirementDataError, ValueError) as exc:
            invalid.append({"path": str(target), "error": str(exc)})
    return {
        "canonical_digest_ignores_local_fields": True,
        "legacy_entry_count": legacy_entry_count,
        "current_entry_count": current_entry_count,
        "identical_count": len(identical),
        "conflict_count": len(conflicts),
        "legacy_only_count": len(legacy_only),
        "invalid_database_count": len(invalid),
        "conflicts": conflicts,
        "legacy_only": legacy_only,
        "invalid_databases": invalid,
        "ready": not conflicts and not legacy_only and not invalid,
    }


def _legacy_transaction_ids(paths: Iterable[Path]) -> set[str]:
    transaction_ids: set[str] = set()
    for path in paths:
        try:
            with closing(_connect_read_only(path)) as connection:
                if "transaction_id" not in _columns(connection, "transactions"):
                    continue
                transaction_ids.update(
                    str(row[0])
                    for row in connection.execute("SELECT transaction_id FROM transactions")
                    if str(row[0] or "").strip()
                )
        except sqlite3.Error:
            continue
    return transaction_ids


def _audit_external_schedules(
    schedules_root: Path | None,
    *,
    legacy_transaction_ids: set[str],
) -> Dict[str, Any]:
    if schedules_root is None:
        return {
            "provided": False,
            "valid_root": False,
            "file_count": 0,
            "active_count": 0,
            "unbound_origin_count": 0,
            "legacy_origin_count": 0,
            "invalid_origin_count": 0,
            "unbound_origins": [],
            "legacy_origins": [],
            "invalid_origins": [],
            "invalid_files": ["schedules root was not provided"],
            "ready": False,
        }
    root = _resolved(schedules_root)
    if not root.is_dir():
        return {
            "provided": True,
            "root": str(root),
            "valid_root": False,
            "file_count": 0,
            "active_count": 0,
            "unbound_origin_count": 0,
            "legacy_origin_count": 0,
            "invalid_origin_count": 0,
            "unbound_origins": [],
            "legacy_origins": [],
            "invalid_origins": [],
            "invalid_files": [f"not a directory: {root}"],
            "ready": False,
        }
    files = sorted(path for path in root.rglob("schedules.json") if path.is_file())
    invalid_files: list[str] = []
    unbound_origins: list[Dict[str, str]] = []
    legacy_origins: list[Dict[str, str]] = []
    invalid_origins: list[Dict[str, str]] = []
    active_count = 0
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            invalid_files.append(f"{path}: {exc}")
            continue
        items = payload.get("items") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            invalid_files.append(f"{path}: items must be a list")
            continue
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                invalid_files.append(f"{path}: items[{index}] must be an object")
                continue
            status = str(item.get("status", "") or "")
            if status not in ACTIVE_EXTERNAL_SCHEDULE_STATUSES:
                continue
            active_count += 1
            origin = item.get("origin")
            if origin is None:
                unbound_origins.append(
                    {"path": str(path), "index": str(index)}
                )
                continue
            if not isinstance(origin, Mapping):
                invalid_origins.append({"path": str(path), "index": str(index)})
                continue
            transaction_id = str(origin.get("transaction_id", "") or "").strip()
            engine = str(origin.get("runtime_engine", "") or "").strip()
            has_origin_data = any(
                value is not None
                and (not isinstance(value, str) or bool(value.strip()))
                for value in origin.values()
            )
            if not has_origin_data:
                unbound_origins.append(
                    {"path": str(path), "index": str(index)}
                )
                continue
            if transaction_id in legacy_transaction_ids or engine == LEGACY_ENGINE_ID:
                legacy_origins.append(
                    {
                        "path": str(path),
                        "index": str(index),
                        "transaction_id": transaction_id,
                    }
                )
                continue
            # A populated origin is a transaction binding.  It is valid only
            # when both the transaction and its current engine are explicit;
            # otherwise the cutover cannot prove that it is non-legacy.
            if not transaction_id or engine != CURRENT_ENGINE_ID:
                invalid_origins.append({"path": str(path), "index": str(index)})
                continue
    ready = not invalid_files and not invalid_origins and not legacy_origins
    return {
        "provided": True,
        "root": str(root),
        "valid_root": True,
        "file_count": len(files),
        "active_count": active_count,
        "unbound_origin_count": len(unbound_origins),
        "legacy_origin_count": len(legacy_origins),
        "invalid_origin_count": len(invalid_origins),
        "unbound_origins": unbound_origins,
        "legacy_origins": legacy_origins,
        "invalid_origins": invalid_origins,
        "invalid_files": invalid_files,
        "ready": ready,
    }


def _archive_report(path: Path | None, runtime_root: Path) -> Dict[str, Any]:
    if path is None:
        return {
            "provided": False,
            "valid": False,
            "outside_runtime_root": False,
            "errors": ["archive manifest was not provided"],
            "sources": [],
            "backups": [],
        }
    resolved = _resolved(path)
    report = load_and_validate_archive_manifest(resolved)
    outside = not _inside(resolved, runtime_root)
    errors = list(report.get("errors", []))
    if not outside:
        errors.append("archive manifest is inside the production runtime root")
    backup_paths_inside = []
    for raw in report.get("backups", []):
        if not isinstance(raw, Mapping):
            continue
        backup_path = _resolved(str(raw.get("path", "") or ""))
        if _inside(backup_path, runtime_root):
            backup_paths_inside.append(str(backup_path))
    if backup_paths_inside:
        errors.append("archive backups are inside the production runtime root")
    return {
        **report,
        "outside_runtime_root": outside,
        "backup_paths_inside_runtime_root": backup_paths_inside,
        "valid": bool(report.get("valid")) and outside and not backup_paths_inside,
        "errors": errors,
    }


def _audit_online_legacy(
    legacy_paths: list[Path],
    archive: Mapping[str, Any],
) -> Dict[str, Any]:
    registered: Dict[Path, Mapping[str, Any]] = {}
    for raw in archive.get("sources", []):
        if not isinstance(raw, Mapping) or not str(raw.get("path", "") or "").strip():
            continue
        registered[_resolved(str(raw["path"]))] = raw
    unregistered: list[str] = []
    new_writes: list[Dict[str, Any]] = []
    inspect_errors: list[Dict[str, str]] = []
    for path in legacy_paths:
        expected = registered.get(path.resolve())
        if expected is None:
            unregistered.append(str(path.resolve()))
            continue
        try:
            current = inspect_sqlite(path)
        except (OSError, sqlite3.Error, RetirementDataError) as exc:
            inspect_errors.append({"path": str(path), "error": str(exc)})
            continue
        changed_fields = [
            field
            for field in ("size", "row_count", "schema_version", "sha256", "integrity_check")
            if current.get(field) != expected.get(field)
        ]
        if changed_fields:
            new_writes.append({"path": str(path), "changed_fields": changed_fields})
    return {
        "online_legacy_database_count": len(legacy_paths),
        "unregistered_legacy_database_count": len(unregistered),
        "new_legacy_write_count": len(new_writes),
        "inspection_error_count": len(inspect_errors),
        "online": [str(path) for path in legacy_paths],
        "unregistered": unregistered,
        "new_writes": new_writes,
        "inspection_errors": inspect_errors,
        "ready": not legacy_paths and not unregistered and not new_writes and not inspect_errors,
    }


def build_report(
    root: Path,
    *,
    schedules_root: Path | None = None,
    archive_manifest: Path | None = None,
) -> Dict[str, Any]:
    """Build a strict retirement report using only explicitly supplied roots."""

    runtime_root = _resolved(root)
    root_valid = runtime_root.is_dir()
    paths = list(discover_runtime_databases(runtime_root)) if root_valid else []
    path_set = set(paths)
    databases = [_safe_audit_database(path) for path in paths]
    legacy_paths = sorted(
        {
            path
            for path in paths
            if path.name == LEGACY_DATABASE_NAME
        }
        | {
            Path(item["path"])
            for item in databases
            if item.get("database_kind") == "legacy"
        }
    )
    current_paths = [
        path for path in paths if path.name == CURRENT_DATABASE_NAME
    ]
    inventory = _checkpoint_flush_inventory(current_paths, path_set)
    scene = _audit_scene(legacy_paths, current_paths)
    archive = _archive_report(archive_manifest, runtime_root)
    online_legacy = _audit_online_legacy(legacy_paths, archive)
    schedule_ids = _legacy_transaction_ids(legacy_paths)
    for value in archive.get("legacy_transaction_ids", []):
        if str(value or "").strip():
            schedule_ids.add(str(value))
    schedules = _audit_external_schedules(
        schedules_root,
        legacy_transaction_ids=schedule_ids,
    )
    legacy_databases = [
        item for item in databases if item.get("database_kind") == "legacy"
    ]
    current_databases = [
        item for item in databases if item.get("database_kind") == "langgraph"
    ]
    composite_databases = [
        item for item in databases if item.get("database_kind") == "composite_flush"
    ]
    runtime_flush_databases = [
        item for item in databases if item.get("database_kind") == "runtime_flush"
    ]
    database_blockers = sum(int(item.get("blocker_count", 0)) for item in databases)
    structural_blockers = (
        (0 if root_valid else 1)
        + (0 if current_paths else 1)
        + int(inventory["missing_count"])
        + int(scene["conflict_count"])
        + int(scene["legacy_only_count"])
        + int(scene["invalid_database_count"])
        + int(schedules["legacy_origin_count"])
        + int(schedules["invalid_origin_count"])
        + len(schedules["invalid_files"])
        + (0 if schedules["valid_root"] else 1)
        + (0 if archive["valid"] else max(1, len(archive.get("errors", []))))
        + int(online_legacy["online_legacy_database_count"])
        + int(online_legacy["unregistered_legacy_database_count"])
        + int(online_legacy["new_legacy_write_count"])
        + int(online_legacy["inspection_error_count"])
    )
    ready = bool(
        root_valid
        and current_paths
        and inventory["complete"]
        and scene["ready"]
        and schedules["ready"]
        and archive["valid"]
        and online_legacy["ready"]
        and database_blockers == 0
    )
    return {
        "schema_version": 2,
        "root": str(runtime_root),
        "root_valid": root_valid,
        "database_count": len(databases),
        "legacy_database_count": len(legacy_databases),
        "langgraph_database_count": len(current_databases),
        "composite_flush_database_count": len(composite_databases),
        "runtime_flush_database_count": len(runtime_flush_databases),
        "legacy_blocker_count": sum(
            int(item.get("blocker_count", 0)) for item in legacy_databases
        ),
        "composite_flush_blocker_count": sum(
            int(item.get("blocker_count", 0)) for item in composite_databases
        ),
        "database_blocker_count": database_blockers,
        "structural_blocker_count": structural_blockers,
        "blocker_count": database_blockers + structural_blockers,
        "legacy_retirement_ready": ready,
        "checkpoint_flush_inventory": inventory,
        "scene": scene,
        "external_schedules": schedules,
        "archive_manifest": archive,
        "online_legacy": online_legacy,
        "databases": databases,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only audit of durable Runtime retirement state."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--schedules-root", type=Path)
    parser.add_argument("--archive-manifest", type=Path)
    parser.add_argument(
        "--require-legacy-retired",
        action="store_true",
        help="Exit 2 unless every strict retirement invariant passes.",
    )
    parser.add_argument(
        "--fail-on-any-blocker",
        action="store_true",
        help="Exit 2 when the report contains any blocker.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = build_report(
        args.root,
        schedules_root=args.schedules_root,
        archive_manifest=args.archive_manifest,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    blocked = bool(
        (args.require_legacy_retired and not report["legacy_retirement_ready"])
        or (args.fail_on_any_blocker and int(report["blocker_count"]) > 0)
    )
    return 2 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
