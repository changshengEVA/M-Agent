"""Guarded backup, migration, and quarantine utilities for Runtime retirement data.

The default behavior is read-only.  Every mutating operation requires an
explicit ``--execute`` flag, an operator-bound maintenance lock, and validated
backup/decision/Scene documents.  Retirement migrates only flushed Scene
history, never checkpoints or side effects, and quarantines legacy databases
instead of deleting them.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import stat
from typing import Any, Dict, Iterable, Mapping, Sequence

from m_agent.schedule.models import (
    SCHEDULE_SCHEMA_VERSION,
    SCHEDULE_STATUS_LEASED,
    SCHEDULE_STATUS_PENDING,
    ScheduleItem,
)


LEGACY_DATABASE_NAME = "".join(("think", "_", "life", ".sqlite3"))
LEGACY_ENGINE_ID = "".join(("think", "_", "life", "_v1"))
CURRENT_DATABASE_NAME = "langgraph.sqlite3"
CHECKPOINT_DATABASE_NAMES = frozenset(
    {
        "langgraph-checkpoints.sqlite3",
        "langgraph-turn-checkpoints.sqlite3",
    }
)
RUNTIME_FLUSH_DATABASE_NAME = "runtime-flush.sqlite3"
COMPOSITE_FLUSH_DATABASE_NAME = "composite-flush.sqlite3"
ARCHIVE_KIND = "runtime_retirement_archive"
BACKUP_KIND = "runtime_retirement_backup"
SCENE_PLAN_KIND = "scene_merge_plan"
TRANSACTION_PLAN_KIND = "transaction_decision_plan"
TRANSACTION_DECISIONS_KIND = "transaction_retirement_decisions"
RETIREMENT_REQUEST_KIND = "runtime_retirement_request"
RETIREMENT_PLAN_KIND = "runtime_retirement_execution_plan"
RETIREMENT_TOMBSTONE_KIND = "runtime_retirement_tombstone"
ABANDON_REASON = "legacy_engine_retirement"
CURRENT_ENGINE_ID = "langgraph_v1"
NON_TERMINAL_TRANSACTION_STATES = frozenset({"continue", "pause"})
ACTIVE_LIFECYCLES = frozenset({"active", "<missing>", ""})
SAFE_EFFECT_STATUSES = frozenset(
    {"completed", "failed", "discarded", "expected_discard", "invalidated"}
)
_LOCAL_SCENE_FIELDS = frozenset(
    {"seq", "runtime_engine", "source_engine", "source_seq"}
)
_UNSAFE_LEGACY_STATUSES = {
    "stimuli": ("disposition", frozenset({"ready", "claimed"})),
    "schedule_runs": (
        "status",
        frozenset({"scheduled", "due", "blocked_on_activation", "claimed"}),
    ),
    "effect_intents": (
        "status",
        frozenset({"intent_recorded", "dispatched", "result_committed"}),
    ),
    "feedback_ingress_outbox": (
        "status",
        frozenset({"pending", "ready", "claimed"}),
    ),
    "flush_outbox": (
        "status",
        frozenset({"pending", "ready", "claimed"}),
    ),
}


class RetirementDataError(RuntimeError):
    """A retirement plan or manifest failed a safety invariant."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolved(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def _connect_read_only(path: Path | str) -> sqlite3.Connection:
    resolved = _resolved(path)
    connection = sqlite3.connect(
        f"file:{resolved.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})")
    }


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with _resolved(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_scene_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        str(key): value
        for key, value in dict(payload).items()
        if str(key) not in _LOCAL_SCENE_FIELDS
    }


def canonical_scene_digest(payload: Mapping[str, Any]) -> str:
    return canonical_json_digest(canonical_scene_payload(payload))


def inspect_sqlite(path: Path | str) -> Dict[str, Any]:
    resolved = _resolved(path)
    if not resolved.is_file():
        raise RetirementDataError(f"SQLite source does not exist: {resolved}")
    with closing(_connect_read_only(resolved)) as connection:
        integrity_rows = [
            str(row[0])
            for row in connection.execute("PRAGMA integrity_check").fetchall()
        ]
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
        ]
        table_row_counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
        }
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "kind": "sqlite",
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "row_count": sum(table_row_counts.values()),
        "table_row_counts": table_row_counts,
        "schema_version": schema_version,
        "sha256": sha256_file(resolved),
        "integrity_check": (
            "ok" if integrity_rows == ["ok"] else "; ".join(integrity_rows)
        ),
    }


def _schema_versions(value: Any) -> set[int]:
    versions: set[int] = set()
    if isinstance(value, Mapping):
        raw = value.get("schema_version")
        if isinstance(raw, int):
            versions.add(raw)
        for nested in value.values():
            versions.update(_schema_versions(nested))
    elif isinstance(value, list):
        for nested in value:
            versions.update(_schema_versions(nested))
    return versions


def inspect_json_file(path: Path | str) -> Dict[str, Any]:
    resolved = _resolved(path)
    if not resolved.is_file():
        raise RetirementDataError(f"JSON source does not exist: {resolved}")
    row_count = 0
    versions: set[int] = set()
    try:
        if resolved.suffix.lower() == ".jsonl":
            with resolved.open("r", encoding="utf-8") as handle:
                for line_number, raw in enumerate(handle, start=1):
                    text = raw.strip()
                    if not text:
                        continue
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise RetirementDataError(
                            f"invalid JSONL at {resolved}:{line_number}: {exc.msg}"
                        ) from exc
                    row_count += 1
                    versions.update(_schema_versions(payload))
        else:
            with resolved.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
                row_count = len(payload["items"])
            elif isinstance(payload, list):
                row_count = len(payload)
            else:
                row_count = 1
            versions.update(_schema_versions(payload))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetirementDataError(f"invalid JSON source {resolved}: {exc}") from exc
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "kind": "jsonl" if resolved.suffix.lower() == ".jsonl" else "json",
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "row_count": row_count,
        "table_row_counts": {},
        "schema_version": max(versions, default=0),
        "sha256": sha256_file(resolved),
        "integrity_check": "ok",
    }


def inspect_file(path: Path | str) -> Dict[str, Any]:
    resolved = _resolved(path)
    if resolved.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        return inspect_sqlite(resolved)
    if resolved.suffix.lower() in {".json", ".jsonl"}:
        return inspect_json_file(resolved)
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "kind": "file",
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "row_count": 0,
        "table_row_counts": {},
        "schema_version": 0,
        "sha256": sha256_file(resolved),
        "integrity_check": "not_applicable",
    }


def _stable_metadata(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    """Drop filesystem timestamps while retaining every content identity field."""

    return {
        str(key): value
        for key, value in metadata.items()
        if str(key) != "mtime_ns"
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _require_inside(path: Path, root: Path, *, label: str) -> None:
    if not _inside(path, root):
        raise RetirementDataError(f"{label} escapes its declared root: {path}")


def _require_disjoint_roots(first: Path, second: Path, *, label: str) -> None:
    if _inside(first, second) or _inside(second, first):
        raise RetirementDataError(f"{label} roots must be disjoint")


def _metadata_matches(
    expected: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    sqlite_logical_copy: bool = False,
) -> bool:
    fields = (
        ("row_count", "table_row_counts", "schema_version", "integrity_check")
        if sqlite_logical_copy
        else ("size", "row_count", "schema_version", "sha256", "integrity_check")
    )
    return all(expected.get(field) == current.get(field) for field in fields)


def discover_retirement_sources(
    runtime_root: Path | str,
    schedules_root: Path | str | None = None,
) -> list[tuple[str, Path, Path]]:
    runtime = _resolved(runtime_root)
    if not runtime.is_dir():
        raise RetirementDataError(f"runtime root is not a directory: {runtime}")
    discovered: dict[Path, tuple[str, Path, Path]] = {}
    for path in runtime.rglob("*.sqlite3"):
        if path.is_file():
            discovered[path.resolve()] = ("runtime", runtime, path.resolve())
    for path in runtime.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
            continue
        if "scene" in {part.lower() for part in path.parts}:
            discovered[path.resolve()] = ("runtime", runtime, path.resolve())
    if schedules_root is not None:
        schedules = _resolved(schedules_root)
        if not schedules.is_dir():
            raise RetirementDataError(
                f"schedule root is not a directory: {schedules}"
            )
        for path in schedules.rglob("schedules.json"):
            if path.is_file():
                discovered[path.resolve()] = (
                    "schedules",
                    schedules,
                    path.resolve(),
                )
    return [discovered[path] for path in sorted(discovered)]


def build_backup_plan(
    *,
    runtime_root: Path | str,
    schedules_root: Path | str | None,
    output_dir: Path | str,
    operator: str,
    maintenance_lock: Path | str,
) -> Dict[str, Any]:
    actor = str(operator or "").strip()
    if not actor:
        raise RetirementDataError("operator is required")
    runtime = _resolved(runtime_root)
    if schedules_root is None:
        raise RetirementDataError("an explicit schedules root is required")
    schedules = _resolved(schedules_root)
    output = _resolved(output_dir)
    if _inside(output, runtime) or _inside(output, schedules):
        raise RetirementDataError("backup output must be outside every source root")
    sources = discover_retirement_sources(runtime, schedules)
    if not sources:
        raise RetirementDataError("no retirement data sources were discovered")
    entries: list[Dict[str, Any]] = []
    for label, root, source in sources:
        relative = source.relative_to(root)
        entries.append(
            {
                "source": inspect_file(source),
                "destination": str(output / label / relative),
                "backup_method": (
                    "sqlite_backup_api"
                    if source.suffix.lower() in {".sqlite", ".sqlite3", ".db"}
                    else "verified_file_copy"
                ),
            }
        )
    return {
        "schema_version": 1,
        "kind": BACKUP_KIND,
        "mode": "plan",
        "created_at": _utc_now(),
        "operator": actor,
        "maintenance_lock": str(_resolved(maintenance_lock)),
        "runtime_root": str(runtime),
        "schedules_root": str(schedules),
        "output_dir": str(output),
        "entry_count": len(entries),
        "entries": entries,
        "source_mutation": False,
    }


def validate_maintenance_lock(
    lock_path: Path | str,
    *,
    operator: str,
) -> Dict[str, Any]:
    path = _resolved(lock_path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RetirementDataError(f"maintenance lock is unreadable: {path}") from exc
    if not isinstance(payload, Mapping):
        raise RetirementDataError("maintenance lock must be a JSON object")
    actor = str(operator or "").strip()
    required = {
        "maintenance": payload.get("maintenance") is True,
        "services_stopped": payload.get("services_stopped") is True,
        "sqlite_writers": payload.get("sqlite_writers") == 0,
        "operator": str(payload.get("operator", "") or "").strip() == actor,
        "lock_id": bool(str(payload.get("lock_id", "") or "").strip()),
    }
    failed = sorted(key for key, passed in required.items() if not passed)
    if failed:
        raise RetirementDataError(
            "maintenance lock is not safe: " + ", ".join(failed)
        )
    return dict(payload)


def _verify_planned_sources(plan: Mapping[str, Any]) -> None:
    entries = plan.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RetirementDataError("backup plan has no entries")
    for entry in entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("source"), Mapping):
            raise RetirementDataError("backup plan entry is invalid")
        expected = dict(entry["source"])
        current = inspect_file(str(expected.get("path", "")))
        for field in ("size", "row_count", "schema_version", "sha256", "integrity_check"):
            if current.get(field) != expected.get(field):
                raise RetirementDataError(
                    f"source changed after planning: {current['path']} ({field})"
                )
        if current["integrity_check"] != "ok" and current["kind"] != "file":
            raise RetirementDataError(
                f"source integrity check failed: {current['path']}"
            )


def execute_backup_plan(
    plan: Mapping[str, Any],
    *,
    operator: str,
    maintenance_lock: Path | str,
    execute: bool,
) -> Dict[str, Any]:
    if not execute:
        raise RetirementDataError("backup execution requires --execute")
    if str(plan.get("kind", "")) != BACKUP_KIND:
        raise RetirementDataError("unsupported backup plan kind")
    actor = str(operator or "").strip()
    if actor != str(plan.get("operator", "") or "").strip():
        raise RetirementDataError("operator does not match the backup plan")
    expected_lock = _resolved(str(plan.get("maintenance_lock", "")))
    supplied_lock = _resolved(maintenance_lock)
    if supplied_lock != expected_lock:
        raise RetirementDataError("maintenance lock does not match the backup plan")
    lock = validate_maintenance_lock(supplied_lock, operator=actor)
    _verify_planned_sources(plan)
    output = _resolved(str(plan.get("output_dir", "")))
    if output.exists() and any(output.iterdir()):
        raise RetirementDataError("backup output directory must be empty")
    entries = list(plan["entries"])
    destinations = [_resolved(str(entry["destination"])) for entry in entries]
    if len(set(destinations)) != len(destinations):
        raise RetirementDataError("backup plan contains duplicate destinations")
    output.mkdir(parents=True, exist_ok=True)
    completed: list[Dict[str, Any]] = []
    for entry in entries:
        source_meta = dict(entry["source"])
        source = _resolved(str(source_meta["path"]))
        destination = _resolved(str(entry["destination"]))
        if not _inside(destination, output):
            raise RetirementDataError("backup destination escapes output directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise RetirementDataError(f"backup destination already exists: {destination}")
        if str(entry.get("backup_method")) == "sqlite_backup_api":
            with closing(_connect_read_only(source)) as source_connection:
                destination_connection = sqlite3.connect(destination)
                try:
                    source_connection.backup(destination_connection)
                    destination_connection.commit()
                finally:
                    destination_connection.close()
        else:
            shutil.copy2(source, destination)
        backup_meta = inspect_file(destination)
        if str(entry.get("backup_method")) == "sqlite_backup_api":
            # SQLite's Backup API guarantees a transactionally consistent
            # database image, not byte-for-byte file identity.  Page layout,
            # freelist contents, and header metadata may legitimately differ.
            for field in (
                "row_count",
                "table_row_counts",
                "schema_version",
                "integrity_check",
            ):
                if backup_meta.get(field) != source_meta.get(field):
                    raise RetirementDataError(
                        f"SQLite backup verification mismatch: "
                        f"{source} -> {destination} ({field})"
                    )
        else:
            if backup_meta["sha256"] != source_meta["sha256"]:
                raise RetirementDataError(
                    f"backup digest mismatch: {source} -> {destination}"
                )
            if backup_meta["integrity_check"] != source_meta["integrity_check"]:
                raise RetirementDataError(
                    f"backup integrity mismatch: {source} -> {destination}"
                )
        completed.append(
            {
                "source": source_meta,
                "backup": backup_meta,
                "backup_method": entry["backup_method"],
            }
        )
    manifest = {
        "schema_version": 1,
        "kind": BACKUP_KIND,
        "mode": "executed",
        "created_at": _utc_now(),
        "operator": actor,
        "maintenance_lock": {
            "path": str(supplied_lock),
            "lock_id": str(lock["lock_id"]),
            "services_stopped": True,
            "sqlite_writers": 0,
        },
        "runtime_root": plan.get("runtime_root"),
        "schedules_root": plan.get("schedules_root"),
        "output_dir": str(output),
        "entry_count": len(completed),
        "entries": completed,
        "source_mutation": False,
    }
    manifest_path = output / "retirement-backup-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {**manifest, "manifest_path": str(manifest_path)}


def validate_backup_manifest_payload(
    payload: Mapping[str, Any],
    *,
    operator: str,
    maintenance_lock: Mapping[str, Any],
    runtime_root: Path | str,
    verify_sources: bool = True,
) -> Dict[str, Any]:
    """Validate an executed backup before any retirement mutation is allowed."""

    errors: list[str] = []
    actor = str(operator or "").strip()
    runtime = _resolved(runtime_root)
    output_dir = _resolved(str(payload.get("output_dir", "") or ""))
    if payload.get("schema_version") != 1:
        errors.append("schema_version")
    if str(payload.get("kind", "")) != BACKUP_KIND:
        errors.append("kind")
    if payload.get("mode") != "executed":
        errors.append("mode")
    if str(payload.get("operator", "") or "").strip() != actor:
        errors.append("operator")
    if payload.get("source_mutation") is not False:
        errors.append("source_mutation")
    if _resolved(str(payload.get("runtime_root", "") or "")) != runtime:
        errors.append("runtime_root")
    if _inside(output_dir, runtime):
        errors.append("output_dir")
    lock = payload.get("maintenance_lock")
    if not isinstance(lock, Mapping):
        errors.append("maintenance_lock")
    else:
        if str(lock.get("lock_id", "") or "") != str(
            maintenance_lock.get("lock_id", "") or ""
        ):
            errors.append("maintenance_lock.lock_id")
        if lock.get("services_stopped") is not True:
            errors.append("maintenance_lock.services_stopped")
        if lock.get("sqlite_writers") != 0:
            errors.append("maintenance_lock.sqlite_writers")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        errors.append("entries")
        raw_entries = []
    if payload.get("entry_count") != len(raw_entries):
        errors.append("entry_count")
    entries: list[Dict[str, Any]] = []
    seen_sources: set[Path] = set()
    seen_backups: set[Path] = set()
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, Mapping):
            errors.append(f"entry[{index}]")
            continue
        source = raw.get("source")
        backup = raw.get("backup")
        method = str(raw.get("backup_method", "") or "")
        if not isinstance(source, Mapping) or not isinstance(backup, Mapping):
            errors.append(f"entry[{index}].metadata")
            continue
        try:
            source_path = _resolved(str(source.get("path", "") or ""))
            backup_path = _resolved(str(backup.get("path", "") or ""))
        except (OSError, RuntimeError):
            errors.append(f"entry[{index}].path")
            continue
        if source_path in seen_sources:
            errors.append(f"entry[{index}].duplicate_source")
        if backup_path in seen_backups:
            errors.append(f"entry[{index}].duplicate_backup")
        seen_sources.add(source_path)
        seen_backups.add(backup_path)
        if not _inside(source_path, runtime) and str(
            payload.get("schedules_root", "") or ""
        ):
            schedules_root = _resolved(str(payload["schedules_root"]))
            if not _inside(source_path, schedules_root):
                errors.append(f"entry[{index}].source_root")
        if _inside(backup_path, runtime) or source_path == backup_path:
            errors.append(f"entry[{index}].backup_root")
        if not _inside(backup_path, output_dir):
            errors.append(f"entry[{index}].backup_output_containment")
        if method not in {"sqlite_backup_api", "verified_file_copy"}:
            errors.append(f"entry[{index}].backup_method")
        try:
            current_backup = inspect_file(backup_path)
        except (OSError, sqlite3.Error, RetirementDataError):
            errors.append(f"entry[{index}].backup_unreadable")
        else:
            if not _metadata_matches(backup, current_backup):
                errors.append(f"entry[{index}].backup_changed")
        if verify_sources:
            try:
                current_source = inspect_file(source_path)
            except (OSError, sqlite3.Error, RetirementDataError):
                errors.append(f"entry[{index}].source_unreadable")
            else:
                if not _metadata_matches(source, current_source):
                    errors.append(f"entry[{index}].source_changed")
        if method == "sqlite_backup_api":
            if not _metadata_matches(source, backup, sqlite_logical_copy=True):
                errors.append(f"entry[{index}].logical_copy")
        elif not _metadata_matches(source, backup):
            errors.append(f"entry[{index}].verified_copy")
        entries.append(
            {
                "source": dict(source),
                "backup": dict(backup),
                "backup_method": method,
            }
        )
    return {
        "valid": not errors,
        "errors": errors,
        "entries": entries,
        "entry_count": len(entries),
    }


def load_and_validate_backup_manifest(
    path: Path | str,
    *,
    operator: str,
    maintenance_lock: Mapping[str, Any],
    runtime_root: Path | str,
    verify_sources: bool = True,
) -> Dict[str, Any]:
    resolved = _resolved(path)
    payload = _load_json_object(resolved)
    report = validate_backup_manifest_payload(
            payload,
            operator=operator,
            maintenance_lock=maintenance_lock,
            runtime_root=runtime_root,
            verify_sources=verify_sources,
        )
    output_dir = _resolved(str(payload.get("output_dir", "") or ""))
    if not _inside(resolved, output_dir):
        report["errors"].append("manifest_output_containment")
        report["valid"] = False
    return {
        **report,
        "path": str(resolved),
        "payload": payload,
    }


def load_scene_entries(path: Path | str) -> Dict[str, Dict[str, Any]]:
    resolved = _resolved(path)
    entries: Dict[str, Dict[str, Any]] = {}
    with closing(_connect_read_only(resolved)) as connection:
        if not _table_exists(connection, "scene_entries"):
            return entries
        rows = connection.execute(
            "SELECT append_id, payload_json FROM scene_entries ORDER BY append_id"
        ).fetchall()
    for row in rows:
        append_id = str(row["append_id"] or "").strip()
        if not append_id:
            raise RetirementDataError(f"Scene entry without append_id: {resolved}")
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError as exc:
            raise RetirementDataError(
                f"invalid Scene payload for {append_id} in {resolved}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise RetirementDataError(
                f"Scene payload is not an object for {append_id} in {resolved}"
            )
        payload_append_id = str(payload.get("append_id", "") or "").strip()
        if payload_append_id and payload_append_id != append_id:
            raise RetirementDataError(
                f"Scene append_id mismatch for {append_id} in {resolved}"
            )
        entries[append_id] = {
            "append_id": append_id,
            "digest": canonical_scene_digest(payload),
            "seq": int(payload.get("seq", 0) or 0),
            "payload": dict(payload),
        }
    return entries


def build_scene_merge_plan(
    source_database: Path | str,
    target_database: Path | str,
) -> Dict[str, Any]:
    source = _resolved(source_database)
    target = _resolved(target_database)
    if source == target:
        raise RetirementDataError("Scene source and target must differ")
    source_entries = load_scene_entries(source)
    target_entries = load_scene_entries(target)
    shared = sorted(set(source_entries) & set(target_entries))
    conflicts = [
        append_id
        for append_id in shared
        if source_entries[append_id]["digest"]
        != target_entries[append_id]["digest"]
    ]
    if conflicts:
        raise RetirementDataError(
            "Scene digest conflict for append_id: " + ", ".join(conflicts)
        )
    source_only = sorted(set(source_entries) - set(target_entries))
    return {
        "schema_version": 1,
        "kind": SCENE_PLAN_KIND,
        "mode": "dry_run",
        "created_at": _utc_now(),
        "source_database": str(source),
        "target_database": str(target),
        "source_sha256": sha256_file(source),
        "target_sha256": sha256_file(target),
        "source_entry_count": len(source_entries),
        "target_entry_count": len(target_entries),
        "identical_count": len(shared),
        "conflict_count": 0,
        "merge_count": len(source_only),
        "actions": [
            {
                "action": "append_preserving_flushed_semantics",
                "append_id": append_id,
                "canonical_digest": source_entries[append_id]["digest"],
                "source_seq": source_entries[append_id]["seq"],
                "materialize_dialogue_or_rag": False,
            }
            for append_id in source_only
        ],
        "writes_performed": False,
    }


def validate_scene_merge_plan(
    plan: Mapping[str, Any],
    *,
    source_database: Path | str,
    target_database: Path | str,
) -> Dict[str, Any]:
    if str(plan.get("kind", "")) != SCENE_PLAN_KIND:
        raise RetirementDataError("invalid Scene merge plan kind")
    current = build_scene_merge_plan(source_database, target_database)
    for field in (
        "source_database",
        "target_database",
        "source_sha256",
        "target_sha256",
        "merge_count",
        "actions",
    ):
        if plan.get(field) != current.get(field):
            raise RetirementDataError(f"stale Scene merge plan: {field}")
    return {"valid": True, "merge_count": current["merge_count"]}


def _load_non_terminal_transactions(path: Path | str) -> Dict[str, Dict[str, Any]]:
    resolved = _resolved(path)
    with closing(_connect_read_only(resolved)) as connection:
        columns = _columns(connection, "transactions")
        required = {"transaction_id", "state", "lifecycle_status", "revision", "payload_json"}
        if not required.issubset(columns):
            missing = sorted(required - columns)
            raise RetirementDataError(
                "transaction table is missing columns: " + ", ".join(missing)
            )
        rows = connection.execute(
            "SELECT transaction_id, state, lifecycle_status, revision, payload_json "
            "FROM transactions ORDER BY transaction_id"
        ).fetchall()
        effects: Dict[str, list[str]] = {}
        effect_columns = _columns(connection, "effect_intents")
        if {"transaction_id", "status"}.issubset(effect_columns):
            for row in connection.execute(
                "SELECT transaction_id, status FROM effect_intents ORDER BY transaction_id"
            ).fetchall():
                effects.setdefault(str(row["transaction_id"]), []).append(
                    str(row["status"] or "")
                )
    transactions: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        state = str(row["state"] or "")
        lifecycle = str(row["lifecycle_status"] or "<missing>")
        if state not in NON_TERMINAL_TRANSACTION_STATES or lifecycle not in ACTIVE_LIFECYCLES:
            continue
        transaction_id = str(row["transaction_id"] or "").strip()
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError as exc:
            raise RetirementDataError(
                f"invalid transaction payload: {transaction_id}"
            ) from exc
        task_state = payload.get("task_state") if isinstance(payload, Mapping) else None
        transactions[transaction_id] = {
            "transaction_id": transaction_id,
            "state": state,
            "lifecycle_status": lifecycle,
            "revision": int(row["revision"]),
            "task_state_digest": canonical_json_digest(
                task_state if isinstance(task_state, Mapping) else {}
            ),
            "effect_statuses": effects.get(transaction_id, []),
        }
    return transactions


def build_transaction_decision_plan(
    database: Path | str,
    decisions_manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    resolved = _resolved(database)
    if str(decisions_manifest.get("kind", "")) != TRANSACTION_DECISIONS_KIND:
        raise RetirementDataError("invalid transaction decisions manifest kind")
    operator = str(decisions_manifest.get("operator", "") or "").strip()
    if not operator:
        raise RetirementDataError("transaction decisions require an operator")
    raw_decisions = decisions_manifest.get("decisions")
    if not isinstance(raw_decisions, list):
        raise RetirementDataError("transaction decisions must be a list")
    decisions: Dict[str, Mapping[str, Any]] = {}
    for raw in raw_decisions:
        if not isinstance(raw, Mapping):
            raise RetirementDataError("transaction decision must be an object")
        transaction_id = str(raw.get("transaction_id", "") or "").strip()
        if not transaction_id or transaction_id in decisions:
            raise RetirementDataError("transaction decisions contain a missing or duplicate id")
        decisions[transaction_id] = raw
    active = _load_non_terminal_transactions(resolved)
    if set(decisions) != set(active):
        missing = sorted(set(active) - set(decisions))
        extra = sorted(set(decisions) - set(active))
        raise RetirementDataError(
            f"transaction decisions do not match active work; missing={missing}, extra={extra}"
        )
    actions: list[Dict[str, Any]] = []
    for transaction_id in sorted(active):
        transaction = active[transaction_id]
        decision = decisions[transaction_id]
        expected_revision = int(decision.get("expected_revision", -1))
        if expected_revision != transaction["revision"]:
            raise RetirementDataError(
                f"revision mismatch for transaction {transaction_id}"
            )
        choice = str(decision.get("decision", "") or "").strip()
        if choice == "natural_completion":
            action = "wait_for_natural_terminal_state"
        elif choice == "abandon":
            if str(decision.get("reason", "") or "").strip() != ABANDON_REASON:
                raise RetirementDataError(
                    f"abandon decision requires reason {ABANDON_REASON}: {transaction_id}"
                )
            action = "archive_under_maintenance_lock"
        elif choice == "restore_task":
            if decision.get("task_state_digest") != transaction["task_state_digest"]:
                raise RetirementDataError(
                    f"task state digest mismatch for transaction {transaction_id}"
                )
            unsafe_effects = sorted(
                status
                for status in transaction["effect_statuses"]
                if status not in SAFE_EFFECT_STATUSES
            )
            if unsafe_effects:
                raise RetirementDataError(
                    f"transaction {transaction_id} has unresolved effects: {unsafe_effects}"
                )
            action = "invalidate_activation_and_open_restore_task"
        else:
            raise RetirementDataError(
                f"unsupported transaction decision {choice!r}: {transaction_id}"
            )
        actions.append(
            {
                "transaction_id": transaction_id,
                "expected_revision": expected_revision,
                "decision": choice,
                "planned_action": action,
                "automatic_complete": False,
                "migrate_checkpoint": False,
                "replay_delegate_or_effect": False,
            }
        )
    return {
        "schema_version": 1,
        "kind": TRANSACTION_PLAN_KIND,
        "mode": "dry_run",
        "created_at": _utc_now(),
        "operator": operator,
        "database": str(resolved),
        "database_sha256": sha256_file(resolved),
        "active_transaction_count": len(active),
        "actions": actions,
        "writes_performed": False,
    }


def validate_transaction_decision_plan(
    plan: Mapping[str, Any],
    *,
    database: Path | str,
    decisions_manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    if str(plan.get("kind", "")) != TRANSACTION_PLAN_KIND:
        raise RetirementDataError("invalid transaction decision plan kind")
    current = build_transaction_decision_plan(database, decisions_manifest)
    for field in (
        "operator",
        "database",
        "database_sha256",
        "active_transaction_count",
        "actions",
    ):
        if plan.get(field) != current.get(field):
            raise RetirementDataError(f"stale transaction decision plan: {field}")
    return {
        "valid": True,
        "active_transaction_count": current["active_transaction_count"],
    }


def _load_scene_migration_rows(path: Path | str) -> Dict[str, Dict[str, Any]]:
    resolved = _resolved(path)
    with closing(_connect_read_only(resolved)) as connection:
        scene_columns = _columns(connection, "scene_entries")
        state_columns = _columns(connection, "conversation_state")
        required_scene = {"append_id", "conversation_id", "seq", "payload_json"}
        required_state = {
            "conversation_id",
            "thread_id",
            "scene_seq",
            "flush_watermark",
        }
        if not required_scene.issubset(scene_columns):
            raise RetirementDataError(
                "Scene table is missing migration columns: "
                + ", ".join(sorted(required_scene - scene_columns))
            )
        if not required_state.issubset(state_columns):
            raise RetirementDataError(
                "conversation state is missing migration columns: "
                + ", ".join(sorted(required_state - state_columns))
            )
        states = {
            str(row["conversation_id"]): dict(row)
            for row in connection.execute(
                "SELECT conversation_id, thread_id, scene_seq, flush_watermark "
                "FROM conversation_state ORDER BY conversation_id"
            ).fetchall()
        }
        rows = connection.execute(
            "SELECT append_id, conversation_id, seq, payload_json "
            "FROM scene_entries ORDER BY append_id"
        ).fetchall()
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        append_id = str(row["append_id"] or "").strip()
        conversation_id = str(row["conversation_id"] or "").strip()
        if not append_id or not conversation_id:
            raise RetirementDataError(
                f"Scene migration identity is missing in {resolved}"
            )
        state = states.get(conversation_id)
        if state is None:
            raise RetirementDataError(
                f"Scene conversation state is missing for {conversation_id} in {resolved}"
            )
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError as exc:
            raise RetirementDataError(
                f"invalid Scene payload for {append_id} in {resolved}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise RetirementDataError(
                f"Scene payload is not an object for {append_id} in {resolved}"
            )
        seq = int(row["seq"])
        if int(payload.get("seq", seq) or 0) != seq:
            raise RetirementDataError(
                f"Scene seq mismatch for {append_id} in {resolved}"
            )
        result[append_id] = {
            "append_id": append_id,
            "conversation_id": conversation_id,
            "thread_id": str(state["thread_id"] or "").strip(),
            "seq": seq,
            "digest": canonical_scene_digest(payload),
            "payload": dict(payload),
            "conversation_scene_seq": int(state["scene_seq"]),
            "flush_watermark": int(state["flush_watermark"]),
        }
    return result


def _validate_scene_execution(
    plan: Mapping[str, Any],
    *,
    source_database: Path,
    target_database: Path,
) -> list[Dict[str, Any]]:
    validation = validate_scene_merge_plan(
        plan,
        source_database=source_database,
        target_database=target_database,
    )
    if validation.get("valid") is not True:
        raise RetirementDataError("Scene merge plan is not valid")
    source_rows = _load_scene_migration_rows(source_database)
    target_rows = _load_scene_migration_rows(target_database)
    raw_actions = plan.get("actions")
    if not isinstance(raw_actions, list):
        raise RetirementDataError("Scene merge plan actions must be a list")
    actions: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_actions:
        if not isinstance(raw, Mapping):
            raise RetirementDataError("Scene merge action must be an object")
        append_id = str(raw.get("append_id", "") or "").strip()
        if not append_id or append_id in seen:
            raise RetirementDataError("Scene merge action id is missing or duplicated")
        seen.add(append_id)
        if raw.get("action") != "append_preserving_flushed_semantics":
            raise RetirementDataError(f"unsupported Scene merge action: {append_id}")
        if raw.get("materialize_dialogue_or_rag") is not False:
            raise RetirementDataError(
                f"Scene merge would replay Dialogue/RAG: {append_id}"
            )
        source = source_rows.get(append_id)
        if source is None:
            raise RetirementDataError(f"Scene merge source disappeared: {append_id}")
        if source["digest"] != raw.get("canonical_digest"):
            raise RetirementDataError(f"Scene merge digest changed: {append_id}")
        if source["seq"] != raw.get("source_seq"):
            raise RetirementDataError(f"Scene merge source seq changed: {append_id}")
        if source["flush_watermark"] < source["seq"]:
            raise RetirementDataError(
                f"Scene entry is not flushed and cannot be migrated: {append_id}"
            )
        existing = target_rows.get(append_id)
        if existing is not None:
            if existing["digest"] != source["digest"]:
                raise RetirementDataError(f"Scene digest conflict: {append_id}")
            if existing["conversation_id"] != source["conversation_id"]:
                raise RetirementDataError(
                    f"Scene conversation conflict: {append_id}"
                )
        else:
            conversation_rows = [
                value
                for value in target_rows.values()
                if value["conversation_id"] == source["conversation_id"]
            ]
            if conversation_rows:
                state = conversation_rows[0]
                if state["flush_watermark"] != state["conversation_scene_seq"]:
                    raise RetirementDataError(
                        "target has unflushed Scene entries; preserving migrated flush "
                        f"semantics would skip them: {source['conversation_id']}"
                    )
        actions.append(dict(source))
    if len(actions) != int(plan.get("merge_count", -1)):
        raise RetirementDataError("Scene merge plan count does not match its actions")
    actions.sort(
        key=lambda item: (
            str(item["conversation_id"]),
            int(item["seq"]),
            str(item["append_id"]),
        )
    )
    source_sequence_keys = [
        (str(item["conversation_id"]), int(item["seq"])) for item in actions
    ]
    if len(source_sequence_keys) != len(set(source_sequence_keys)):
        raise RetirementDataError("Scene merge has duplicate source sequence keys")
    with closing(_connect_read_only(target_database)) as connection:
        for conversation_id in sorted(
            {str(item["conversation_id"]) for item in actions}
        ):
            row = connection.execute(
                "SELECT scene_seq, flush_watermark FROM conversation_state "
                "WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is not None and int(row["scene_seq"]) != int(row["flush_watermark"]):
                raise RetirementDataError(
                    "target has unflushed Scene entries; preserving migrated flush "
                    f"semantics would skip them: {conversation_id}"
                )
    return actions


def _load_all_transaction_ids(path: Path | str) -> list[str]:
    resolved = _resolved(path)
    with closing(_connect_read_only(resolved)) as connection:
        if "transaction_id" not in _columns(connection, "transactions"):
            return []
        values = [
            str(row[0] or "").strip()
            for row in connection.execute(
                "SELECT transaction_id FROM transactions ORDER BY transaction_id"
            ).fetchall()
        ]
    if any(not value for value in values) or len(values) != len(set(values)):
        raise RetirementDataError(f"invalid transaction identities in {resolved}")
    return values


def _validate_no_pending_legacy_obligations(path: Path | str) -> Dict[str, int]:
    resolved = _resolved(path)
    blockers: Dict[str, int] = {}
    with closing(_connect_read_only(resolved)) as connection:
        for table, (column, unsafe_statuses) in _UNSAFE_LEGACY_STATUSES.items():
            if column not in _columns(connection, table):
                blockers[table] = 0
                continue
            placeholders = ", ".join("?" for _ in unsafe_statuses)
            row = connection.execute(
                f'SELECT COUNT(*) FROM "{table}" '
                f'WHERE "{column}" IN ({placeholders})',
                tuple(sorted(unsafe_statuses)),
            ).fetchone()
            blockers[table] = int(row[0])
        engine_mismatches = 0
        if "payload_json" in _columns(connection, "transactions"):
            for row in connection.execute("SELECT payload_json FROM transactions"):
                try:
                    payload = json.loads(str(row[0] or "{}"))
                except json.JSONDecodeError:
                    engine_mismatches += 1
                    continue
                if (
                    not isinstance(payload, Mapping)
                    or str(payload.get("runtime_engine", "") or "")
                    != LEGACY_ENGINE_ID
                ):
                    engine_mismatches += 1
        blockers["engine_store_mismatches"] = engine_mismatches
    pending = {name: count for name, count in blockers.items() if count}
    if pending:
        raise RetirementDataError(
            f"legacy database has pending obligations: {resolved}: {pending}"
        )
    return blockers


def _validate_transaction_execution(
    plan: Mapping[str, Any],
    *,
    database: Path,
    decisions_manifest: Mapping[str, Any],
    operator: str,
) -> list[Dict[str, Any]]:
    validation = validate_transaction_decision_plan(
        plan,
        database=database,
        decisions_manifest=decisions_manifest,
    )
    if validation.get("valid") is not True:
        raise RetirementDataError("transaction decision plan is not valid")
    if str(plan.get("operator", "") or "").strip() != str(operator or "").strip():
        raise RetirementDataError("transaction plan operator does not match retirement request")
    decisions = {
        str(raw.get("transaction_id", "") or "").strip(): dict(raw)
        for raw in decisions_manifest.get("decisions", [])
        if isinstance(raw, Mapping)
    }
    abandonments: list[Dict[str, Any]] = []
    idempotency_keys: set[str] = set()
    transition_ids: set[str] = set()
    for raw in plan.get("actions", []):
        if not isinstance(raw, Mapping):
            raise RetirementDataError("transaction plan action must be an object")
        transaction_id = str(raw.get("transaction_id", "") or "").strip()
        if raw.get("planned_action") != "archive_under_maintenance_lock":
            raise RetirementDataError(
                "retirement isolation has an unresolved transaction decision: "
                f"{transaction_id}"
            )
        decision = decisions.get(transaction_id, {})
        if (
            decision.get("decision") != "abandon"
            or decision.get("reason") != ABANDON_REASON
        ):
            raise RetirementDataError(
                f"retirement abandonment is not explicit: {transaction_id}"
            )
        idempotency_key = str(decision.get("idempotency_key", "") or "").strip()
        transition_id = str(decision.get("transition_id", "") or "").strip()
        created_at = str(decision.get("created_at", "") or "").strip()
        if not idempotency_key or idempotency_key in idempotency_keys:
            raise RetirementDataError(
                f"retirement decision needs a unique idempotency_key: {transaction_id}"
            )
        if not transition_id or transition_id in transition_ids:
            raise RetirementDataError(
                f"retirement decision needs a unique transition_id: {transaction_id}"
            )
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RetirementDataError(
                f"retirement decision created_at is invalid: {transaction_id}"
            ) from exc
        if created.tzinfo is None:
            raise RetirementDataError(
                f"retirement decision created_at needs a timezone: {transaction_id}"
            )
        idempotency_keys.add(idempotency_key)
        transition_ids.add(transition_id)
        abandonments.append(
            {
                "transaction_id": transaction_id,
                "expected_revision": int(raw.get("expected_revision", -1)),
                "decision": "abandon",
                "reason": ABANDON_REASON,
                "idempotency_key": idempotency_key,
                "transition_id": transition_id,
                "created_at": created_at,
                "automatic_complete": False,
                "checkpoint_migrated": False,
                "delegate_or_effect_replayed": False,
            }
        )
    if len(abandonments) != int(plan.get("active_transaction_count", -1)):
        raise RetirementDataError("transaction plan count does not match its actions")
    return abandonments


def _retirement_sidecar_paths(database: Path) -> list[Path]:
    return [
        candidate
        for candidate in (
            Path(str(database) + "-wal"),
            Path(str(database) + "-shm"),
            Path(str(database) + "-journal"),
        )
        if candidate.is_file()
    ]


def _load_schedule_document(path: Path | str) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    resolved = _resolved(path)
    payload = _load_json_object(resolved)
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise RetirementDataError(f"schedule document items must be a list: {resolved}")
    items: list[Dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise RetirementDataError(
                f"schedule document item must be an object: {resolved}:{index}"
            )
        items.append(dict(raw))
    return payload, items


def _build_schedule_migration(
    raw: Mapping[str, Any],
    *,
    schedules_root: Path,
    runtime_root: Path,
    backup_by_source: Mapping[Path, Mapping[str, Any]],
) -> Dict[str, Any]:
    path = _resolved(str(raw.get("path", "") or ""))
    _require_inside(path, schedules_root, label="schedule file")
    if _inside(path, runtime_root):
        raise RetirementDataError("schedule and runtime roots must be disjoint")
    if path.name != "schedules.json" or not path.is_file():
        raise RetirementDataError(f"invalid schedule file: {path}")
    if path not in backup_by_source:
        raise RetirementDataError(f"validated backup does not cover schedule file: {path}")
    raw_ids = raw.get("schedule_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise RetirementDataError(
            f"schedule migration needs an explicit non-empty id list: {path}"
        )
    schedule_ids = [str(value or "").strip() for value in raw_ids]
    if any(not value for value in schedule_ids) or len(schedule_ids) != len(
        set(schedule_ids)
    ):
        raise RetirementDataError(f"schedule migration ids are missing or duplicated: {path}")
    _, items = _load_schedule_document(path)
    indexed: Dict[str, tuple[int, Dict[str, Any]]] = {}
    for index, item in enumerate(items):
        schedule_id = str(item.get("schedule_id", "") or "").strip()
        if schedule_id:
            if schedule_id in indexed:
                raise RetirementDataError(
                    f"schedule document has duplicate id {schedule_id}: {path}"
                )
            indexed[schedule_id] = (index, item)
    missing = sorted(set(schedule_ids) - set(indexed))
    if missing:
        raise RetirementDataError(
            f"listed schedules do not exist in {path}: {missing}"
        )
    actions: list[Dict[str, Any]] = []
    for schedule_id in schedule_ids:
        index, item = indexed[schedule_id]
        if str(item.get("status", "") or "").strip() != SCHEDULE_STATUS_LEASED:
            raise RetirementDataError(
                f"listed schedule is not stale leased work: {schedule_id}"
            )
        if item.get("schema_version") == SCHEDULE_SCHEMA_VERSION:
            raise RetirementDataError(
                f"listed schedule is already on the current schema: {schedule_id}"
            )
        origin = item.get("origin")
        if isinstance(origin, Mapping) and any(
            str(value or "").strip() for value in origin.values()
        ):
            raise RetirementDataError(
                f"listed schedule has a transaction origin: {schedule_id}"
            )
        if origin is not None and not isinstance(origin, Mapping):
            raise RetirementDataError(
                f"listed schedule has an invalid origin: {schedule_id}"
            )
        try:
            parsed = ScheduleItem.from_dict(item)
        except (TypeError, ValueError) as exc:
            raise RetirementDataError(
                f"listed schedule cannot migrate to schema v2: {schedule_id}"
            ) from exc
        required = {
            "schedule_id": parsed.schedule_id,
            "thread_id": parsed.thread_id,
            "due_at_utc": parsed.due_at_utc,
            "objective": parsed.deferred_objective.description,
        }
        if any(not str(value or "").strip() for value in required.values()):
            raise RetirementDataError(
                f"listed schedule loses required data during migration: {schedule_id}"
            )
        migrated = ScheduleItem.from_dict(parsed.to_dict())
        migrated.status = SCHEDULE_STATUS_PENDING
        migrated.origin = {}
        migrated_payload = migrated.to_dict()
        if (
            migrated_payload["schedule_id"] != parsed.schedule_id
            or migrated_payload["thread_id"] != parsed.thread_id
            or migrated_payload["due_at_utc"] != parsed.due_at_utc
            or migrated_payload["deferred_objective"]["description"]
            != parsed.deferred_objective.description
        ):
            raise RetirementDataError(
                f"schedule migration would not preserve identity/objective: {schedule_id}"
            )
        actions.append(
            {
                "index": index,
                "schedule_id": schedule_id,
                "source_digest": canonical_json_digest(item),
                "source_status": SCHEDULE_STATUS_LEASED,
                "target_status": SCHEDULE_STATUS_PENDING,
                "source_schema_version": item.get("schema_version", 0),
                "target_schema_version": SCHEDULE_SCHEMA_VERSION,
                "origin": {},
                "preserved": {
                    "thread_id": parsed.thread_id,
                    "due_at_utc": parsed.due_at_utc,
                    "objective": parsed.deferred_objective.description,
                },
                "target_payload": migrated_payload,
            }
        )
    return {
        "path": str(path),
        "source": _stable_metadata(backup_by_source[path]["source"]),
        "backup": _stable_metadata(backup_by_source[path]["backup"]),
        "action_count": len(actions),
        "actions": actions,
    }


def build_retirement_execution_plan(
    request: Mapping[str, Any],
    *,
    maintenance_lock: Path | str,
) -> Dict[str, Any]:
    """Build a read-only, fully validated retirement execution plan."""

    if request.get("schema_version") != 1:
        raise RetirementDataError("retirement request schema_version must be 1")
    if str(request.get("kind", "")) != RETIREMENT_REQUEST_KIND:
        raise RetirementDataError("invalid retirement request kind")
    operator = str(request.get("operator", "") or "").strip()
    if not operator:
        raise RetirementDataError("retirement request requires an operator")
    runtime_root = _resolved(str(request.get("runtime_root", "") or ""))
    archive_dir = _resolved(str(request.get("archive_dir", "") or ""))
    if not runtime_root.is_dir():
        raise RetirementDataError(f"runtime root is not a directory: {runtime_root}")
    _require_disjoint_roots(runtime_root, archive_dir, label="runtime/archive")
    if archive_dir.exists():
        raise RetirementDataError(
            f"retirement archive directory must not already exist: {archive_dir}"
        )
    lock_path = _resolved(maintenance_lock)
    lock = validate_maintenance_lock(lock_path, operator=operator)
    backup_manifest_path = _resolved(
        str(request.get("backup_manifest", "") or "")
    )
    if _inside(backup_manifest_path, runtime_root):
        raise RetirementDataError("backup manifest must be outside the runtime root")
    backup_report = load_and_validate_backup_manifest(
        backup_manifest_path,
        operator=operator,
        maintenance_lock=lock,
        runtime_root=runtime_root,
        verify_sources=True,
    )
    if not backup_report["valid"]:
        raise RetirementDataError(
            "backup manifest is not valid: " + ", ".join(backup_report["errors"])
        )
    backup_by_source = {
        _resolved(str(entry["source"]["path"])): entry
        for entry in backup_report["entries"]
    }
    raw_items = request.get("databases")
    if not isinstance(raw_items, list) or not raw_items:
        raise RetirementDataError("retirement request databases must be a non-empty list")
    planned_items: list[Dict[str, Any]] = []
    normalized_items: list[Dict[str, str]] = []
    seen_legacy: set[Path] = set()
    seen_targets: set[Path] = set()
    seen_destinations: set[Path] = set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, Mapping):
            raise RetirementDataError(f"retirement database item {index} is invalid")
        legacy = _resolved(str(raw.get("legacy_database", "") or ""))
        raw_target = str(raw.get("target_database", "") or "").strip()
        raw_scene_plan = str(raw.get("scene_plan", "") or "").strip()
        target = _resolved(raw_target) if raw_target else None
        scene_plan_path = _resolved(raw_scene_plan) if raw_scene_plan else None
        transaction_plan_path = _resolved(
            str(raw.get("transaction_plan", "") or "")
        )
        decisions_path = _resolved(
            str(raw.get("transaction_decisions", "") or "")
        )
        _require_inside(legacy, runtime_root, label="legacy database")
        if not legacy.is_file():
            raise RetirementDataError(f"legacy database does not exist: {legacy}")
        if legacy.name != LEGACY_DATABASE_NAME:
            raise RetirementDataError(f"unexpected legacy database name: {legacy}")
        legacy_scene_count = len(load_scene_entries(legacy))
        if bool(target) != bool(scene_plan_path):
            raise RetirementDataError(
                "target_database and scene_plan must be supplied together"
            )
        if legacy_scene_count and target is None:
            raise RetirementDataError(
                f"legacy database with Scene history requires a target: {legacy}"
            )
        if target is not None:
            _require_inside(target, runtime_root, label="target database")
            if not target.is_file():
                raise RetirementDataError(f"target database does not exist: {target}")
            if target.name != CURRENT_DATABASE_NAME:
                raise RetirementDataError(f"unexpected target database name: {target}")
            if legacy.parent != target.parent:
                raise RetirementDataError(
                    "legacy and target databases must share a directory"
                )
        if legacy in seen_legacy or (target is not None and target in seen_targets):
            raise RetirementDataError("retirement request contains duplicate databases")
        seen_legacy.add(legacy)
        if target is not None:
            seen_targets.add(target)
        artifacts = [transaction_plan_path, decisions_path]
        if scene_plan_path is not None:
            artifacts.append(scene_plan_path)
        for artifact in artifacts:
            if _inside(artifact, runtime_root):
                raise RetirementDataError(
                    f"retirement control document must be outside runtime root: {artifact}"
                )
            if not artifact.is_file():
                raise RetirementDataError(
                    f"retirement control document does not exist: {artifact}"
                )
        if legacy not in backup_by_source or (
            target is not None and target not in backup_by_source
        ):
            raise RetirementDataError(
                f"validated backup does not cover retirement pair: {legacy.parent}"
            )
        scene_actions: list[Dict[str, Any]] = []
        if target is not None and scene_plan_path is not None:
            scene_plan = _load_json_object(scene_plan_path)
            scene_actions = _validate_scene_execution(
                scene_plan,
                source_database=legacy,
                target_database=target,
            )
        transaction_plan = _load_json_object(transaction_plan_path)
        decisions = _load_json_object(decisions_path)
        abandonments = _validate_transaction_execution(
            transaction_plan,
            database=legacy,
            decisions_manifest=decisions,
            operator=operator,
        )
        obligation_blockers = _validate_no_pending_legacy_obligations(legacy)
        relative = legacy.relative_to(runtime_root)
        quarantine_database = archive_dir / "restore-only" / relative
        _require_inside(
            quarantine_database,
            archive_dir,
            label="quarantine destination",
        )
        source_files = [legacy, *_retirement_sidecar_paths(legacy)]
        quarantine_files: list[Dict[str, Any]] = []
        for source_file in source_files:
            suffix = str(source_file)[len(str(legacy)) :]
            destination = Path(str(quarantine_database) + suffix)
            _require_inside(destination, archive_dir, label="quarantine destination")
            if destination in seen_destinations or destination.exists():
                raise RetirementDataError(
                    f"quarantine destination would be overwritten: {destination}"
                )
            seen_destinations.add(destination)
            quarantine_files.append(
                {
                    "source": str(source_file),
                    "destination": str(destination),
                    "metadata": _stable_metadata(inspect_file(source_file)),
                }
            )
        normalized_item = {
            "legacy_database": str(legacy),
            "target_database": str(target) if target is not None else "",
            "scene_plan": str(scene_plan_path) if scene_plan_path is not None else "",
            "transaction_plan": str(transaction_plan_path),
            "transaction_decisions": str(decisions_path),
        }
        normalized_items.append(normalized_item)
        planned_items.append(
            {
                **normalized_item,
                "legacy_source": _stable_metadata(
                    backup_by_source[legacy]["source"]
                ),
                "legacy_backup": _stable_metadata(
                    backup_by_source[legacy]["backup"]
                ),
                "target_source": (
                    _stable_metadata(backup_by_source[target]["source"])
                    if target is not None
                    else {}
                ),
                "target_backup": (
                    _stable_metadata(backup_by_source[target]["backup"])
                    if target is not None
                    else {}
                ),
                "scene_plan_sha256": (
                    sha256_file(scene_plan_path)
                    if scene_plan_path is not None
                    else ""
                ),
                "scene_merge_count": len(scene_actions),
                "scene_actions": scene_actions,
                "transaction_plan_sha256": sha256_file(transaction_plan_path),
                "transaction_decisions_sha256": sha256_file(decisions_path),
                "transaction_ids": _load_all_transaction_ids(legacy),
                "transaction_abandonments": abandonments,
                "pending_obligations": obligation_blockers,
                "quarantine_files": quarantine_files,
            }
        )
    raw_schedule_items = request.get("schedules", [])
    if not isinstance(raw_schedule_items, list):
        raise RetirementDataError("retirement request schedules must be a list")
    schedules_root_value = str(
        backup_report["payload"].get("schedules_root", "") or ""
    ).strip()
    if raw_schedule_items and not schedules_root_value:
        raise RetirementDataError("validated backup has no schedules root")
    schedules_root = _resolved(schedules_root_value) if schedules_root_value else None
    planned_schedules: list[Dict[str, Any]] = []
    normalized_schedules: list[Dict[str, Any]] = []
    seen_schedule_paths: set[Path] = set()
    for raw_schedule in raw_schedule_items:
        if not isinstance(raw_schedule, Mapping) or schedules_root is None:
            raise RetirementDataError("retirement schedule request is invalid")
        schedule_plan = _build_schedule_migration(
            raw_schedule,
            schedules_root=schedules_root,
            runtime_root=runtime_root,
            backup_by_source=backup_by_source,
        )
        schedule_path = _resolved(schedule_plan["path"])
        if schedule_path in seen_schedule_paths:
            raise RetirementDataError(
                f"retirement request repeats a schedule file: {schedule_path}"
            )
        seen_schedule_paths.add(schedule_path)
        normalized_schedule = {
            "path": str(schedule_path),
            "schedule_ids": [
                str(action["schedule_id"]) for action in schedule_plan["actions"]
            ],
        }
        normalized_schedules.append(normalized_schedule)
        planned_schedules.append(schedule_plan)
    normalized_request = {
        "schema_version": 1,
        "kind": RETIREMENT_REQUEST_KIND,
        "operator": operator,
        "runtime_root": str(runtime_root),
        "archive_dir": str(archive_dir),
        "backup_manifest": str(backup_manifest_path),
        "databases": normalized_items,
        "schedules": normalized_schedules,
    }
    validation_payload = {
        "request": normalized_request,
        "maintenance_lock_id": str(lock["lock_id"]),
        "backup_manifest_sha256": sha256_file(backup_manifest_path),
        "items": planned_items,
        "schedules": planned_schedules,
    }
    validation_digest = canonical_json_digest(validation_payload)
    batch_id = str(request.get("batch_id", "") or "").strip()
    if not batch_id:
        batch_id = f"retirement-{validation_digest[:16]}"
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in batch_id):
        raise RetirementDataError("retirement batch_id contains unsafe characters")
    normalized_request["batch_id"] = batch_id
    return {
        "schema_version": 1,
        "kind": RETIREMENT_PLAN_KIND,
        "mode": "dry_run",
        "created_at": _utc_now(),
        "batch_id": batch_id,
        "operator": operator,
        "runtime_root": str(runtime_root),
        "archive_dir": str(archive_dir),
        "backup_manifest": str(backup_manifest_path),
        "backup_manifest_sha256": validation_payload["backup_manifest_sha256"],
        "maintenance_lock": str(lock_path),
        "maintenance_lock_id": str(lock["lock_id"]),
        "database_count": len(planned_items),
        "scene_merge_count": sum(
            int(item["scene_merge_count"]) for item in planned_items
        ),
        "transaction_abandonment_count": sum(
            len(item["transaction_abandonments"]) for item in planned_items
        ),
        "schedule_migration_count": sum(
            int(item["action_count"]) for item in planned_schedules
        ),
        "items": planned_items,
        "schedules": planned_schedules,
        "request": normalized_request,
        "validation_digest": validation_digest,
        "manifest_path": str(archive_dir / "retirement-archive-manifest.json"),
        "tombstone_path": str(archive_dir / "retirement-tombstone.json"),
        "writes_performed": False,
    }


def _apply_scene_actions(
    source: Path,
    target: Path,
    actions: Sequence[Mapping[str, Any]],
) -> int:
    if not actions:
        return 0
    ordered_actions = sorted(
        actions,
        key=lambda item: (
            str(item["conversation_id"]),
            int(item["seq"]),
            str(item["append_id"]),
        ),
    )
    ordering_keys = [
        (str(item["conversation_id"]), int(item["seq"]))
        for item in ordered_actions
    ]
    if len(ordering_keys) != len(set(ordering_keys)):
        raise RetirementDataError("Scene migration has duplicate source sequence keys")
    connection = sqlite3.connect(target, uri=True)
    connection.row_factory = sqlite3.Row
    inserted = 0
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "ATTACH DATABASE ? AS legacy_source",
            (f"file:{source.as_posix()}?mode=ro",),
        )
        connection.execute("BEGIN IMMEDIATE")
        scene_columns = _columns(connection, "scene_entries")
        state_columns = _columns(connection, "conversation_state")
        if not {"append_id", "conversation_id", "seq", "payload_json"}.issubset(
            scene_columns
        ) or not {
            "conversation_id",
            "thread_id",
            "scene_seq",
            "flush_watermark",
            "revision",
            "updated_at",
        }.issubset(state_columns):
            raise RetirementDataError("target database lacks Scene migration schema")
        source_scene_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA legacy_source.table_info(scene_entries)"
            )
        }
        source_state_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA legacy_source.table_info(conversation_state)"
            )
        }
        if not {"append_id", "conversation_id", "seq", "payload_json"}.issubset(
            source_scene_columns
        ) or not {"conversation_id", "flush_watermark"}.issubset(
            source_state_columns
        ):
            raise RetirementDataError("source database lacks Scene migration schema")
        for raw in ordered_actions:
            append_id = str(raw["append_id"])
            conversation_id = str(raw["conversation_id"])
            fresh_source = connection.execute(
                "SELECT s.conversation_id, s.seq, s.payload_json, "
                "c.flush_watermark FROM legacy_source.scene_entries AS s "
                "JOIN legacy_source.conversation_state AS c "
                "ON c.conversation_id = s.conversation_id "
                "WHERE s.append_id = ?",
                (append_id,),
            ).fetchone()
            if fresh_source is None:
                raise RetirementDataError(
                    f"Scene source changed during retirement: {append_id}"
                )
            try:
                fresh_payload = json.loads(str(fresh_source["payload_json"] or "{}"))
            except json.JSONDecodeError as exc:
                raise RetirementDataError(
                    f"Scene source payload became invalid: {append_id}"
                ) from exc
            if (
                str(fresh_source["conversation_id"]) != conversation_id
                or int(fresh_source["seq"]) != int(raw["seq"])
                or int(fresh_source["flush_watermark"]) < int(fresh_source["seq"])
                or canonical_scene_digest(fresh_payload) != raw["digest"]
            ):
                raise RetirementDataError(
                    f"Scene source failed in-transaction validation: {append_id}"
                )
            existing = connection.execute(
                "SELECT conversation_id, payload_json FROM scene_entries "
                "WHERE append_id = ?",
                (append_id,),
            ).fetchone()
            if existing is not None:
                payload = json.loads(str(existing["payload_json"] or "{}"))
                if (
                    str(existing["conversation_id"]) != conversation_id
                    or canonical_scene_digest(payload) != raw["digest"]
                ):
                    raise RetirementDataError(f"Scene idempotency conflict: {append_id}")
                continue
            state = connection.execute(
                "SELECT scene_seq, flush_watermark FROM conversation_state "
                "WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            now = _utc_now()
            if state is None:
                thread_id = str(raw.get("thread_id", "") or "").strip()
                if not thread_id:
                    thread_id = conversation_id.rsplit("::", 1)[0]
                connection.execute(
                    "INSERT INTO conversation_state(" 
                    "conversation_id, thread_id, scene_seq, flush_watermark, "
                    "revision, updated_at) VALUES (?, ?, 0, 0, 0, ?)",
                    (conversation_id, thread_id, now),
                )
                scene_seq = 0
                flush_watermark = 0
            else:
                scene_seq = int(state["scene_seq"])
                flush_watermark = int(state["flush_watermark"])
            if flush_watermark != scene_seq:
                raise RetirementDataError(
                    "target acquired unflushed Scene work during retirement: "
                    f"{conversation_id}"
                )
            new_seq = scene_seq + 1
            payload = dict(fresh_payload)
            payload.update(
                {
                    "append_id": append_id,
                    "seq": new_seq,
                    "runtime_engine": CURRENT_ENGINE_ID,
                    "source_engine": LEGACY_ENGINE_ID,
                    "source_seq": int(raw["seq"]),
                }
            )
            connection.execute(
                "INSERT INTO scene_entries(" 
                "append_id, conversation_id, seq, payload_json) VALUES (?, ?, ?, ?)",
                (
                    append_id,
                    conversation_id,
                    new_seq,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.execute(
                "UPDATE conversation_state SET scene_seq = ?, flush_watermark = ?, "
                "revision = revision + 1, updated_at = ? WHERE conversation_id = ?",
                (new_seq, new_seq, now, conversation_id),
            )
            inserted += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return inserted


def _apply_schedule_migration(
    schedule_plan: Mapping[str, Any],
    *,
    batch_id: str,
) -> Dict[str, Any]:
    path = _resolved(str(schedule_plan["path"]))
    current_metadata = inspect_file(path)
    if not _metadata_matches(schedule_plan["source"], current_metadata):
        raise RetirementDataError(f"schedule file changed after planning: {path}")
    payload, items = _load_schedule_document(path)
    for action in schedule_plan["actions"]:
        index = int(action["index"])
        if index < 0 or index >= len(items):
            raise RetirementDataError(f"schedule index changed after planning: {path}")
        current = items[index]
        if (
            str(current.get("schedule_id", "") or "") != action["schedule_id"]
            or canonical_json_digest(current) != action["source_digest"]
        ):
            raise RetirementDataError(
                f"schedule entry changed after planning: {action['schedule_id']}"
            )
        target = dict(action["target_payload"])
        if target.get("origin") != {}:
            raise RetirementDataError(
                f"schedule migration attempted to forge an origin: {action['schedule_id']}"
            )
        items[index] = target
    updated = dict(payload)
    updated["items"] = items
    updated["item_count"] = len(items)
    temp_path = path.with_name(f"{path.name}.{batch_id}.tmp")
    if temp_path.exists():
        raise RetirementDataError(f"schedule migration temp file exists: {temp_path}")
    try:
        with temp_path.open("x", encoding="utf-8") as handle:
            json.dump(updated, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temp_path.replace(path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise
    _, verified_items = _load_schedule_document(path)
    for action in schedule_plan["actions"]:
        migrated = verified_items[int(action["index"])]
        if (
            migrated != action["target_payload"]
            or migrated.get("schema_version") != SCHEDULE_SCHEMA_VERSION
            or migrated.get("status") != SCHEDULE_STATUS_PENDING
            or migrated.get("origin") != {}
        ):
            raise RetirementDataError(
                f"schedule migration verification failed: {action['schedule_id']}"
            )
    return {
        "path": str(path),
        "before": dict(schedule_plan["source"]),
        "after": inspect_file(path),
        "migrated_schedule_ids": [
            str(action["schedule_id"]) for action in schedule_plan["actions"]
        ],
        "status_transition": f"{SCHEDULE_STATUS_LEASED}->{SCHEDULE_STATUS_PENDING}",
        "target_schema_version": SCHEDULE_SCHEMA_VERSION,
        "origin_fabricated": False,
    }


def _make_restore_only(path: Path) -> None:
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    writable_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    if path.stat().st_mode & writable_bits:
        raise RetirementDataError(f"quarantined file is still writable: {path}")


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise RetirementDataError(f"refusing to overwrite retirement file: {path}") from exc


def _validate_tombstone_payload(payload: Mapping[str, Any]) -> None:
    required_true = (
        "legacy_runtime_quarantined",
        "production_scan_excluded",
        "restore_only",
    )
    if payload.get("schema_version") != 1 or payload.get("kind") != RETIREMENT_TOMBSTONE_KIND:
        raise RetirementDataError("invalid retirement tombstone identity")
    if any(payload.get(field) is not True for field in required_true):
        raise RetirementDataError("retirement tombstone safety flags are incomplete")
    if not str(payload.get("batch_id", "") or "").strip():
        raise RetirementDataError("retirement tombstone batch_id is missing")
    databases = payload.get("databases")
    if not isinstance(databases, list) or not databases:
        raise RetirementDataError("retirement tombstone has no quarantined databases")
    for item in databases:
        if not isinstance(item, Mapping) or not item.get("files"):
            raise RetirementDataError("retirement tombstone database entry is invalid")
        for raw in item["files"]:
            if not isinstance(raw, Mapping):
                raise RetirementDataError("retirement tombstone file entry is invalid")
            destination = _resolved(str(raw.get("quarantine_path", "") or ""))
            try:
                current = inspect_file(destination)
            except (OSError, sqlite3.Error, RetirementDataError) as exc:
                raise RetirementDataError(
                    f"quarantined file is unreadable: {destination}"
                ) from exc
            if not _metadata_matches(raw, current):
                raise RetirementDataError(
                    f"quarantined file metadata changed: {destination}"
                )
            writable_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
            if raw.get("read_only") is not True or destination.stat().st_mode & writable_bits:
                raise RetirementDataError(
                    f"quarantined file is not restore-only: {destination}"
                )


def execute_retirement_execution_plan(
    plan: Mapping[str, Any],
    *,
    maintenance_lock: Path | str,
    execute: bool,
) -> Dict[str, Any]:
    """Migrate Scene history and quarantine legacy databases after preflight."""

    if not execute:
        raise RetirementDataError("retirement execution requires --execute")
    if str(plan.get("kind", "")) != RETIREMENT_PLAN_KIND:
        raise RetirementDataError("invalid retirement execution plan kind")
    if plan.get("mode") != "dry_run" or plan.get("writes_performed") is not False:
        raise RetirementDataError("retirement execution requires an unexecuted dry-run plan")
    lock_path = _resolved(maintenance_lock)
    if lock_path != _resolved(str(plan.get("maintenance_lock", "") or "")):
        raise RetirementDataError("maintenance lock does not match retirement plan")
    operator = str(plan.get("operator", "") or "").strip()
    lock = validate_maintenance_lock(lock_path, operator=operator)
    if str(lock.get("lock_id", "") or "") != str(
        plan.get("maintenance_lock_id", "") or ""
    ):
        raise RetirementDataError("maintenance lock changed after retirement planning")
    request = plan.get("request")
    if not isinstance(request, Mapping):
        raise RetirementDataError("retirement plan does not contain its normalized request")
    current = build_retirement_execution_plan(request, maintenance_lock=lock_path)
    critical_fields = (
        "batch_id",
        "operator",
        "runtime_root",
        "archive_dir",
        "backup_manifest",
        "backup_manifest_sha256",
        "maintenance_lock",
        "maintenance_lock_id",
        "database_count",
        "scene_merge_count",
        "transaction_abandonment_count",
        "schedule_migration_count",
        "items",
        "schedules",
        "request",
        "validation_digest",
        "manifest_path",
        "tombstone_path",
    )
    changed = [field for field in critical_fields if plan.get(field) != current.get(field)]
    if changed:
        raise RetirementDataError(
            "retirement inputs or plan changed after planning: " + ", ".join(changed)
        )
    effective = current
    archive_dir = _resolved(str(effective["archive_dir"]))
    runtime_root = _resolved(str(effective["runtime_root"]))
    _require_disjoint_roots(runtime_root, archive_dir, label="runtime/archive")
    archive_dir.mkdir(parents=True, exist_ok=False)
    prepared_path = archive_dir / "retirement-prepared-plan.json"
    _write_new_json(
        prepared_path,
        {
            **dict(effective),
            "prepared_at": _utc_now(),
            "mode": "prepared",
            "writes_performed": False,
        },
    )
    inserted_count = 0
    for item in effective["items"]:
        if item["target_database"]:
            inserted_count += _apply_scene_actions(
                _resolved(str(item["legacy_database"])),
                _resolved(str(item["target_database"])),
                item["scene_actions"],
            )
            merged = build_scene_merge_plan(
                item["legacy_database"],
                item["target_database"],
            )
            if merged["conflict_count"] != 0 or merged["merge_count"] != 0:
                raise RetirementDataError(
                    f"Scene merge verification failed: {item['legacy_database']}"
                )
    schedule_migrations = [
        _apply_schedule_migration(
            schedule_plan,
            batch_id=str(effective["batch_id"]),
        )
        for schedule_plan in effective["schedules"]
    ]
    quarantined_databases: list[Dict[str, Any]] = []
    for item in effective["items"]:
        files: list[Dict[str, Any]] = []
        prepared_moves: list[tuple[Mapping[str, Any], Path, Path]] = []
        for raw in item["quarantine_files"]:
            source = _resolved(str(raw["source"]))
            destination = _resolved(str(raw["destination"]))
            _require_inside(destination, archive_dir, label="quarantine destination")
            if destination.exists():
                raise RetirementDataError(
                    f"quarantine destination would be overwritten: {destination}"
                )
            current_source = inspect_file(source)
            if not _metadata_matches(raw["metadata"], current_source):
                raise RetirementDataError(f"quarantine source changed: {source}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            prepared_moves.append((raw, source, destination))
        # Move the database and all of its WAL/SHM/journal sidecars as one
        # preflighted group before opening the quarantined SQLite image.
        for _, source, destination in prepared_moves:
            shutil.move(str(source), str(destination))
        for raw, source, destination in prepared_moves:
            current_destination = inspect_file(destination)
            if not _metadata_matches(raw["metadata"], current_destination):
                raise RetirementDataError(
                    f"quarantine verification failed: {destination}"
                )
            _make_restore_only(destination)
            files.append(
                {
                    **current_destination,
                    "online_path": str(source),
                    "quarantine_path": str(destination),
                    "read_only": True,
                }
            )
        quarantined_databases.append(
            {
                "online_database": item["legacy_database"],
                "quarantine_database": item["quarantine_files"][0]["destination"],
                "files": files,
                "transaction_ids": list(item["transaction_ids"]),
                "transaction_abandonments": list(item["transaction_abandonments"]),
                "scene_merge_count": int(item["scene_merge_count"]),
            }
        )
    tombstone = {
        "schema_version": 1,
        "kind": RETIREMENT_TOMBSTONE_KIND,
        "batch_id": str(effective["batch_id"]),
        "created_at": _utc_now(),
        "operator": operator,
        "maintenance_lock_id": str(lock["lock_id"]),
        "runtime_root": str(runtime_root),
        "quarantine_root": str(archive_dir / "restore-only"),
        "legacy_runtime_quarantined": True,
        "production_scan_excluded": True,
        "restore_only": True,
        "automatic_completion_performed": False,
        "checkpoint_migration_performed": False,
        "side_effect_replay_performed": False,
        "source_transaction_rows_terminalized": False,
        "transaction_disposition": "controlled_abandonment_by_quarantine",
        "schedule_migrations": schedule_migrations,
        "databases": quarantined_databases,
    }
    _validate_tombstone_payload(tombstone)
    tombstone_path = _resolved(str(effective["tombstone_path"]))
    _require_inside(tombstone_path, archive_dir, label="tombstone")
    _write_new_json(tombstone_path, tombstone)
    tombstone_metadata = inspect_file(tombstone_path)
    legacy_transaction_ids = sorted(
        {
            transaction_id
            for item in quarantined_databases
            for transaction_id in item["transaction_ids"]
        }
    )
    sources = [dict(item["legacy_source"]) for item in effective["items"]]
    backups = [dict(item["legacy_backup"]) for item in effective["items"]]
    archive_manifest = {
        "schema_version": 1,
        "kind": ARCHIVE_KIND,
        "batch_id": str(effective["batch_id"]),
        "operator": operator,
        "created_at": _utc_now(),
        "maintenance_lock_id": str(lock["lock_id"]),
        "runtime_root": str(runtime_root),
        "backup_manifest": str(effective["backup_manifest"]),
        "backup_manifest_sha256": str(effective["backup_manifest_sha256"]),
        "legacy_transaction_ids": legacy_transaction_ids,
        "sources": sources,
        "backups": backups,
        "tombstone": {
            "legacy_runtime_quarantined": True,
            "production_scan_excluded": True,
            "restore_only": True,
            "path": str(tombstone_path),
            "sha256": tombstone_metadata["sha256"],
        },
        "scene_merge": {
            "validated": True,
            "conflict_count": 0,
            "legacy_only_count": 0,
            "migrated_count": inserted_count,
            "materialized_dialogue_or_rag_count": 0,
        },
        "transaction_decisions": {
            "validated": True,
            "unresolved_count": 0,
            "abandoned_count": sum(
                len(item["transaction_abandonments"])
                for item in quarantined_databases
            ),
            "reason": ABANDON_REASON,
            "automatic_complete": False,
            "checkpoint_migrated": False,
            "delegate_or_effect_replayed": False,
            "source_rows_terminalized": False,
            "disposition": "controlled_abandonment_by_quarantine",
        },
        "schedule_migrations": {
            "validated": True,
            "migrated_count": sum(
                len(item["migrated_schedule_ids"]) for item in schedule_migrations
            ),
            "files": schedule_migrations,
            "origin_fabricated": False,
        },
        "quarantine": {
            "root": str(archive_dir / "restore-only"),
            "database_count": len(quarantined_databases),
            "databases": quarantined_databases,
        },
    }
    archive_validation = validate_archive_manifest_payload(archive_manifest)
    if not archive_validation["valid"]:
        raise RetirementDataError(
            "generated archive manifest is invalid: "
            + ", ".join(archive_validation["errors"])
        )
    manifest_path = _resolved(str(effective["manifest_path"]))
    _require_inside(manifest_path, archive_dir, label="archive manifest")
    _write_new_json(manifest_path, archive_manifest)
    final_validation = load_and_validate_archive_manifest(manifest_path)
    if not final_validation["valid"]:
        raise RetirementDataError(
            "written archive manifest failed validation: "
            + ", ".join(final_validation["errors"])
        )
    return {
        **archive_manifest,
        "mode": "executed",
        "writes_performed": True,
        "manifest_path": str(manifest_path),
        "tombstone_path": str(tombstone_path),
        "prepared_plan_path": str(prepared_path),
    }


_MANIFEST_FIELDS = frozenset(
    {
        "path",
        "size",
        "row_count",
        "schema_version",
        "sha256",
        "integrity_check",
    }
)


def validate_archive_manifest_payload(
    payload: Mapping[str, Any],
    *,
    verify_backups: bool = True,
) -> Dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version")
    if str(payload.get("kind", "")) != ARCHIVE_KIND:
        errors.append("kind")
    if not str(payload.get("operator", "") or "").strip():
        errors.append("operator")
    if not str(payload.get("created_at", "") or "").strip():
        errors.append("created_at")
    legacy_transaction_ids = payload.get("legacy_transaction_ids")
    if not isinstance(legacy_transaction_ids, list) or any(
        not str(value or "").strip() for value in legacy_transaction_ids
    ):
        errors.append("legacy_transaction_ids")
        legacy_transaction_ids = []
    else:
        legacy_transaction_ids = [str(value).strip() for value in legacy_transaction_ids]
        if len(set(legacy_transaction_ids)) != len(legacy_transaction_ids):
            errors.append("legacy_transaction_ids.duplicates")
    sources = payload.get("sources")
    backups = payload.get("backups")
    if not isinstance(sources, list) or not sources:
        errors.append("sources")
        sources = []
    if not isinstance(backups, list) or not backups:
        errors.append("backups")
        backups = []
    if len(sources) != len(backups):
        errors.append("source_backup_count")
    for label, entries in (("source", sources), ("backup", backups)):
        for index, raw in enumerate(entries):
            if not isinstance(raw, Mapping):
                errors.append(f"{label}[{index}]")
                continue
            missing = sorted(_MANIFEST_FIELDS - set(raw))
            if missing:
                errors.append(f"{label}[{index}].missing={missing}")
                continue
            if not str(raw.get("path", "") or "").strip():
                errors.append(f"{label}[{index}].path")
            for field in ("size", "row_count", "schema_version"):
                value = raw.get(field)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    errors.append(f"{label}[{index}].{field}")
            digest = str(raw.get("sha256", "") or "")
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest.lower()
            ):
                errors.append(f"{label}[{index}].sha256")
            if raw.get("integrity_check") not in {"ok", "not_applicable"}:
                errors.append(f"{label}[{index}].integrity_check")
            if label == "backup" and verify_backups:
                try:
                    current = inspect_file(str(raw.get("path", "")))
                except (OSError, RetirementDataError):
                    errors.append(f"backup[{index}].unreadable")
                else:
                    for field in (
                        "size",
                        "row_count",
                        "schema_version",
                        "sha256",
                        "integrity_check",
                    ):
                        if current.get(field) != raw.get(field):
                            errors.append(f"backup[{index}].{field}")
    for index, (source, backup) in enumerate(zip(sources, backups)):
        if not isinstance(source, Mapping) or not isinstance(backup, Mapping):
            continue
        source_path = _resolved(str(source.get("path", "") or ""))
        backup_path = _resolved(str(backup.get("path", "") or ""))
        if source_path == backup_path:
            errors.append(f"source_backup[{index}].same_path")
        # Backup API output may have a different byte layout, but the logical
        # row/schema/integrity inventory must match its indexed source.
        for field in ("row_count", "schema_version", "integrity_check"):
            if source.get(field) != backup.get(field):
                errors.append(f"source_backup[{index}].{field}")
        if source_path.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
            for field in ("size", "sha256"):
                if source.get(field) != backup.get(field):
                    errors.append(f"source_backup[{index}].{field}")
    tombstone = payload.get("tombstone")
    if not isinstance(tombstone, Mapping):
        errors.append("tombstone")
    else:
        for field in (
            "legacy_runtime_quarantined",
            "production_scan_excluded",
            "restore_only",
        ):
            if tombstone.get(field) is not True:
                errors.append(f"tombstone.{field}")
    scene = payload.get("scene_merge")
    if not isinstance(scene, Mapping) or scene.get("validated") is not True:
        errors.append("scene_merge.validated")
    else:
        try:
            scene_incomplete = (
                int(scene.get("conflict_count", -1)) != 0
                or int(scene.get("legacy_only_count", -1)) != 0
            )
        except (TypeError, ValueError):
            scene_incomplete = True
        if scene_incomplete:
            errors.append("scene_merge.incomplete")
    transactions = payload.get("transaction_decisions")
    if not isinstance(transactions, Mapping) or transactions.get("validated") is not True:
        errors.append("transaction_decisions.validated")
    else:
        try:
            transactions_unresolved = int(
                transactions.get("unresolved_count", -1)
            ) != 0
        except (TypeError, ValueError):
            transactions_unresolved = True
        if transactions_unresolved:
            errors.append("transaction_decisions.unresolved")
    return {
        "provided": True,
        "valid": not errors,
        "errors": errors,
        "source_count": len(sources),
        "backup_count": len(backups),
        "sources": [dict(item) for item in sources if isinstance(item, Mapping)],
        "backups": [dict(item) for item in backups if isinstance(item, Mapping)],
        "legacy_transaction_ids": legacy_transaction_ids,
    }


def load_and_validate_archive_manifest(
    path: Path | str,
    *,
    verify_backups: bool = True,
) -> Dict[str, Any]:
    resolved = _resolved(path)
    try:
        with resolved.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "provided": True,
            "valid": False,
            "errors": [f"unreadable: {exc}"],
            "source_count": 0,
            "backup_count": 0,
            "sources": [],
            "backups": [],
            "path": str(resolved),
        }
    if not isinstance(payload, Mapping):
        return {
            "provided": True,
            "valid": False,
            "errors": ["manifest must be an object"],
            "source_count": 0,
            "backup_count": 0,
            "sources": [],
            "backups": [],
            "path": str(resolved),
        }
    return {
        **validate_archive_manifest_payload(
            payload,
            verify_backups=verify_backups,
        ),
        "path": str(resolved),
    }


def _load_json_object(path: Path | str) -> Dict[str, Any]:
    resolved = _resolved(path)
    try:
        with resolved.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RetirementDataError(f"cannot read JSON object: {resolved}") from exc
    if not isinstance(value, dict):
        raise RetirementDataError(f"JSON document must be an object: {resolved}")
    return value


def _write_optional(payload: Mapping[str, Any], output: Path | None) -> None:
    if output is None:
        _print_stdout_json(payload)
        return
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    resolved = _resolved(output)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(rendered + "\n", encoding="utf-8")


def _print_stdout_json(payload: Mapping[str, Any]) -> None:
    """Emit portable JSON even when the Windows console uses a legacy code page."""

    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guarded backup and dry-run Runtime retirement data tooling."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup")
    backup.add_argument("--root", type=Path, required=True)
    backup.add_argument("--schedules-root", type=Path, required=True)
    backup.add_argument("--output-dir", type=Path, required=True)
    backup.add_argument("--operator", required=True)
    backup.add_argument("--maintenance-lock", type=Path, required=True)
    backup.add_argument("--execute", action="store_true")

    scene = subparsers.add_parser("scene-merge-plan")
    scene.add_argument("--source", type=Path, required=True)
    scene.add_argument("--target", type=Path, required=True)
    scene.add_argument("--output", type=Path)

    scene_validate = subparsers.add_parser("validate-scene-plan")
    scene_validate.add_argument("--plan", type=Path, required=True)
    scene_validate.add_argument("--source", type=Path, required=True)
    scene_validate.add_argument("--target", type=Path, required=True)

    transactions = subparsers.add_parser("transaction-plan")
    transactions.add_argument("--database", type=Path, required=True)
    transactions.add_argument("--decisions", type=Path, required=True)
    transactions.add_argument("--output", type=Path)

    transaction_validate = subparsers.add_parser("validate-transaction-plan")
    transaction_validate.add_argument("--plan", type=Path, required=True)
    transaction_validate.add_argument("--database", type=Path, required=True)
    transaction_validate.add_argument("--decisions", type=Path, required=True)

    archive = subparsers.add_parser("validate-archive")
    archive.add_argument("--manifest", type=Path, required=True)

    retire = subparsers.add_parser(
        "retire",
        help="Validate a retirement request; mutate only with --execute.",
    )
    retire.add_argument("--request", type=Path, required=True)
    retire.add_argument("--maintenance-lock", type=Path, required=True)
    retire.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.command == "backup":
            plan = build_backup_plan(
                runtime_root=args.root,
                schedules_root=args.schedules_root,
                output_dir=args.output_dir,
                operator=args.operator,
                maintenance_lock=args.maintenance_lock,
            )
            report = (
                execute_backup_plan(
                    plan,
                    operator=args.operator,
                    maintenance_lock=args.maintenance_lock,
                    execute=True,
                )
                if args.execute
                else plan
            )
        elif args.command == "scene-merge-plan":
            report = build_scene_merge_plan(args.source, args.target)
            _write_optional(report, args.output)
            return 0
        elif args.command == "validate-scene-plan":
            report = validate_scene_merge_plan(
                _load_json_object(args.plan),
                source_database=args.source,
                target_database=args.target,
            )
            _print_stdout_json(report)
            return 0
        elif args.command == "transaction-plan":
            report = build_transaction_decision_plan(
                args.database,
                _load_json_object(args.decisions),
            )
            _write_optional(report, args.output)
            return 0
        elif args.command == "validate-transaction-plan":
            report = validate_transaction_decision_plan(
                _load_json_object(args.plan),
                database=args.database,
                decisions_manifest=_load_json_object(args.decisions),
            )
            _print_stdout_json(report)
            return 0
        elif args.command == "retire":
            plan = build_retirement_execution_plan(
                _load_json_object(args.request),
                maintenance_lock=args.maintenance_lock,
            )
            report = (
                execute_retirement_execution_plan(
                    plan,
                    maintenance_lock=args.maintenance_lock,
                    execute=True,
                )
                if args.execute
                else plan
            )
        else:
            report = load_and_validate_archive_manifest(args.manifest)
            _print_stdout_json(report)
            return 0 if report["valid"] else 2
    except RetirementDataError as exc:
        _print_stdout_json({"success": False, "error": str(exc)})
        return 2
    _print_stdout_json(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
