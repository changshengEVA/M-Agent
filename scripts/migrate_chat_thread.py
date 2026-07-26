"""Safely migrate persisted chat memory between public thread ids.

The migration is intentionally offline: a running Chat API may keep Scene and
episodic state in memory and later overwrite the migrated files.  The command
therefore refuses to execute while the configured API port is accepting
connections.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import socket
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class MigrationError(RuntimeError):
    """Raised when a migration cannot be performed safely."""


@dataclass(frozen=True)
class DialogueMove:
    source: Path
    target: Path
    payload: dict[str, Any]


@dataclass(frozen=True)
class SceneMove:
    source: Path
    target: Path


@dataclass(frozen=True)
class MigrationPlan:
    project_root: Path
    user_root: Path
    username: str
    source_thread: str
    target_thread: str
    source_internal: str
    target_internal: str
    dialogues: tuple[DialogueMove, ...]
    scenes: tuple[SceneMove, ...]
    chunks_path: Path | None
    chunk_records: tuple[dict[str, Any], ...]
    changed_chunks: int
    source_hashes: tuple[tuple[Path, str], ...]


def _chat_user_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_.")
    return slug[:48] or "user"


def _dialogue_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    return slug[:48] or "thread"


def _scene_stem(value: str) -> str:
    stem = str(value or "").strip().replace("::", "__")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).rstrip(". ") or "thread"
    if len(stem) > 200:
        digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
        stem = f"{stem[:180]}_{digest}"
    return stem


def _validate_component(label: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise MigrationError(f"{label} is required")
    if "::" in normalized:
        raise MigrationError(f"{label} must be a public id without an owner prefix")
    if any(char in normalized for char in ("/", "\\", "\x00")):
        raise MigrationError(f"{label} contains an unsafe path character")
    return normalized


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"invalid JSON file: {path}") from exc
    if not isinstance(payload, dict):
        raise MigrationError(f"expected a JSON object: {path}")
    return payload


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise MigrationError(f"could not read JSONL file: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MigrationError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(record, dict):
            raise MigrationError(f"expected a JSON object at {path}:{line_number}")
        records.append(record)
    return records


def _assert_target_available(source: Path, target: Path) -> None:
    if target == source:
        return
    if target.exists():
        raise MigrationError(f"migration target already exists: {target}")


def build_plan(
    *,
    project_root: Path,
    username: str,
    source_thread: str,
    target_thread: str,
) -> MigrationPlan:
    root = project_root.resolve()
    safe_username = _validate_component("username", username)
    source_public = _validate_component("source thread", source_thread)
    target_public = _validate_component("target thread", target_thread)
    if source_public == target_public:
        raise MigrationError("source and target threads are identical")

    source_internal = f"{safe_username}::{source_public}"
    target_internal = f"{safe_username}::{target_public}"
    user_root = root / "data" / "memory" / "chat-api" / _chat_user_slug(safe_username)
    if not user_root.is_dir():
        raise MigrationError(f"user memory root does not exist: {user_root}")

    source_dialogue_slug = _dialogue_slug(source_internal)
    target_dialogue_slug = _dialogue_slug(target_internal)
    dialogue_moves: list[DialogueMove] = []
    dialogues_root = user_root / "dialogues"
    if dialogues_root.is_dir():
        for path in sorted(dialogues_root.rglob("*.json")):
            payload = _load_json(path)
            meta = payload.get("meta")
            if not isinstance(meta, dict) or str(meta.get("thread_id", "")).strip() != source_internal:
                continue
            updated = dict(payload)
            updated_meta = dict(meta)
            updated_meta["thread_id"] = target_internal
            updated["meta"] = updated_meta
            dialogue_id = str(updated.get("dialogue_id", "") or "")
            if source_dialogue_slug in dialogue_id:
                updated["dialogue_id"] = dialogue_id.replace(
                    source_dialogue_slug, target_dialogue_slug, 1
                )
            target_name = path.name
            if source_dialogue_slug in target_name:
                target_name = target_name.replace(source_dialogue_slug, target_dialogue_slug, 1)
            target = path.with_name(target_name)
            _assert_target_available(path, target)
            dialogue_moves.append(DialogueMove(path, target, updated))

    chunks_path = user_root / "episodic" / "chunks.jsonl"
    chunk_records: list[dict[str, Any]] = []
    changed_chunks = 0
    selected_chunks_path: Path | None = None
    if chunks_path.is_file():
        selected_chunks_path = chunks_path
        for record in _load_jsonl(chunks_path):
            updated_record = dict(record)
            if str(updated_record.get("thread_id", "")).strip() == source_internal:
                updated_record["thread_id"] = target_internal
                changed_chunks += 1
            chunk_records.append(updated_record)

    scene_moves: list[SceneMove] = []
    scene_root = user_root / "scene"
    source_scene_stem = _scene_stem(source_internal)
    target_scene_stem = _scene_stem(target_internal)
    if scene_root.is_dir():
        for path in sorted(scene_root.glob(f"{source_scene_stem}*")):
            if not path.is_file():
                continue
            suffix = path.name[len(source_scene_stem) :]
            if not re.fullmatch(r"(?:\.jsonl|\.meta\.json|\.conversation\.json|__\d+\.jsonl|__\d+\.meta\.json)", suffix):
                continue
            target = path.with_name(f"{target_scene_stem}{suffix}")
            _assert_target_available(path, target)
            scene_moves.append(SceneMove(path, target))

    if not dialogue_moves and not scene_moves and changed_chunks == 0:
        raise MigrationError(
            f"no persisted data found for {source_internal!r} under {user_root}"
        )

    source_paths = [move.source for move in dialogue_moves]
    source_paths.extend(move.source for move in scene_moves)
    if selected_chunks_path is not None and changed_chunks:
        source_paths.append(selected_chunks_path)
        embeddings_path = selected_chunks_path.with_name("embeddings.npy")
        if embeddings_path.is_file():
            source_paths.append(embeddings_path)

    return MigrationPlan(
        project_root=root,
        user_root=user_root,
        username=safe_username,
        source_thread=source_public,
        target_thread=target_public,
        source_internal=source_internal,
        target_internal=target_internal,
        dialogues=tuple(dialogue_moves),
        scenes=tuple(scene_moves),
        chunks_path=selected_chunks_path,
        chunk_records=tuple(chunk_records),
        changed_chunks=changed_chunks,
        source_hashes=tuple((path, _sha256(path)) for path in source_paths),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.replace(path)


def _copy_atomic_verified(source: Path, target: Path) -> None:
    """Copy a file without ever exposing a partial final target."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
        shutil.copy2(source, temporary)
        if _sha256(source) != _sha256(temporary):
            raise MigrationError(f"copy verification failed: {source}")
        temporary.replace(target)
        temporary = None
    finally:
        if temporary is not None and temporary.is_file():
            temporary.unlink()


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _jsonl_text(records: Iterable[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def _relative(path: Path, root: Path) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise MigrationError(f"path escapes project root: {path}") from exc


def _copy_to_backup(path: Path, *, project_root: Path, backup_dir: Path) -> dict[str, Any]:
    relative = _relative(path, project_root)
    destination = backup_dir / "original" / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = _sha256(path)
    shutil.copy2(path, destination)
    saved_hash = _sha256(destination)
    current_source_hash = _sha256(path)
    if saved_hash != source_hash or current_source_hash != source_hash:
        raise MigrationError(f"backup verification failed: {path}")
    return {
        "path": relative.as_posix(),
        "sha256": source_hash,
        "size": path.stat().st_size,
    }


def _planned_targets(plan: MigrationPlan) -> list[Path]:
    targets = [move.target for move in plan.dialogues if move.target != move.source]
    targets.extend(move.target for move in plan.scenes if move.target != move.source)
    return targets


def _verify_plan_sources(plan: MigrationPlan) -> None:
    for path, expected_hash in plan.source_hashes:
        if not path.is_file():
            raise MigrationError(f"migration source disappeared after planning: {path}")
        if _sha256(path) != expected_hash:
            raise MigrationError(f"migration source changed after planning: {path}")


def _snapshot_paths(paths: Iterable[Path], *, project_root: Path) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        relative = _relative(resolved, project_root)
        exists = resolved.is_file()
        snapshot.append(
            {
                "path": relative.as_posix(),
                "exists": exists,
                "sha256": _sha256(resolved) if exists else None,
            }
        )
    return snapshot


def _validate_post_migration_state(
    manifest: dict[str, Any],
    *,
    project_root: Path,
) -> None:
    state = manifest.get("post_migration_state")
    if manifest.get("status") != "complete" or not isinstance(state, list):
        return
    for item in state:
        if not isinstance(item, dict):
            raise MigrationError("invalid post-migration state in backup manifest")
        path = project_root / Path(str(item.get("path", "")))
        _relative(path, project_root)
        expected_exists = bool(item.get("exists"))
        if path.is_file() != expected_exists:
            raise MigrationError(f"migration data changed after completion: {path}")
        if expected_exists and _sha256(path) != str(item.get("sha256", "") or ""):
            raise MigrationError(f"migration data changed after completion: {path}")


def rollback_backup(
    *,
    project_root: Path,
    backup_dir: Path,
    require_port_free: tuple[str, int] | None = ("127.0.0.1", 8777),
) -> dict[str, Any]:
    """Restore original files and remove planned migration targets."""
    if require_port_free and _port_is_open(*require_port_free):
        host, port = require_port_free
        raise MigrationError(
            f"refusing live rollback while Chat API is reachable at {host}:{port}"
        )
    root = project_root.resolve()
    backup = backup_dir.resolve()
    manifest_path = backup / "manifest.json"
    manifest = _load_json(manifest_path)
    files = manifest.get("files")
    targets = manifest.get("targets")
    if not isinstance(files, list) or not isinstance(targets, list):
        raise MigrationError(f"backup manifest is incomplete: {manifest_path}")

    restore_entries: list[tuple[Path, Path]] = []
    for item in files:
        if not isinstance(item, dict):
            raise MigrationError(f"invalid backup entry in {manifest_path}")
        relative = Path(str(item.get("path", "")))
        expected_hash = str(item.get("sha256", "") or "")
        saved = backup / "original" / relative
        original = root / relative
        _relative(original, root)
        if not saved.is_file() or _sha256(saved) != expected_hash:
            raise MigrationError(f"backup verification failed: {saved}")
        restore_entries.append((saved, original))

    _validate_post_migration_state(manifest, project_root=root)

    for raw_target in reversed(targets):
        target = root / Path(str(raw_target))
        _relative(target, root)
        if target.is_file():
            target.unlink()

    for saved, original in restore_entries:
        _copy_atomic_verified(saved, original)

    manifest["status"] = "rolled_back"
    manifest["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(manifest_path, _json_text(manifest))
    return manifest


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=0.4):
            return True
    except OSError:
        return False


def execute_plan(
    plan: MigrationPlan,
    *,
    backup_dir: Path,
    require_port_free: tuple[str, int] | None = ("127.0.0.1", 8777),
) -> dict[str, Any]:
    if require_port_free and _port_is_open(*require_port_free):
        host, port = require_port_free
        raise MigrationError(
            f"refusing live migration while Chat API is reachable at {host}:{port}"
        )
    _verify_plan_sources(plan)

    backup = backup_dir.resolve()
    if backup.exists():
        raise MigrationError(f"backup directory already exists: {backup}")
    backup.mkdir(parents=True, exist_ok=False)

    mutable_sources = [path for path, _ in plan.source_hashes]
    planned_targets = _planned_targets(plan)

    manifest: dict[str, Any] = {
        "status": "backed_up",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "username": plan.username,
        "source_thread": plan.source_thread,
        "target_thread": plan.target_thread,
        "source_internal": plan.source_internal,
        "target_internal": plan.target_internal,
        "dialogues": len(plan.dialogues),
        "scene_files": len(plan.scenes),
        "episodic_chunks": plan.changed_chunks,
        "targets": [
            _relative(path, plan.project_root).as_posix()
            for path in planned_targets
        ],
        "files": [
            _copy_to_backup(path, project_root=plan.project_root, backup_dir=backup)
            for path in mutable_sources
        ],
    }
    manifest_path = backup / "manifest.json"
    _atomic_write(manifest_path, _json_text(manifest))

    created_targets: list[Path] = []
    try:
        for move in plan.dialogues:
            if move.target != move.source:
                created_targets.append(move.target)
            _atomic_write(move.target, _json_text(move.payload))

        for move in plan.scenes:
            if move.target != move.source:
                created_targets.append(move.target)
            _copy_atomic_verified(move.source, move.target)

        if plan.chunks_path is not None and plan.changed_chunks:
            _atomic_write(plan.chunks_path, _jsonl_text(plan.chunk_records))
            migrated = sum(
                1
                for record in _load_jsonl(plan.chunks_path)
                if str(record.get("thread_id", "")).strip() == plan.target_internal
            )
            remaining = sum(
                1
                for record in _load_jsonl(plan.chunks_path)
                if str(record.get("thread_id", "")).strip() == plan.source_internal
            )
            if migrated < plan.changed_chunks or remaining:
                raise MigrationError("episodic chunk verification failed")

        for move in plan.dialogues:
            payload = _load_json(move.target)
            meta = payload.get("meta")
            if not isinstance(meta, dict) or meta.get("thread_id") != plan.target_internal:
                raise MigrationError(f"dialogue verification failed: {move.target}")

        for move in plan.dialogues:
            if move.target != move.source:
                move.source.unlink()
        for move in plan.scenes:
            if move.target != move.source:
                move.source.unlink()

        manifest["status"] = "complete"
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        manifest["post_migration_state"] = _snapshot_paths(
            [*mutable_sources, *planned_targets],
            project_root=plan.project_root,
        )
        _atomic_write(manifest_path, _json_text(manifest))
        return manifest
    except Exception:
        rollback_backup(
            project_root=plan.project_root,
            backup_dir=backup,
            require_port_free=None,
        )
        raise


def _default_backup_dir(project_root: Path, username: str, source: str, target: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_name = _dialogue_slug(f"{username}-{source}-to-{target}")
    return project_root / "data" / "memory" / "thread-migration-backups" / f"{timestamp}_{safe_name}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username")
    parser.add_argument("--from-thread", dest="source_thread")
    parser.add_argument("--to-thread", dest="target_thread")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument(
        "--rollback-backup",
        type=Path,
        help="Restore a prior migration backup and remove its planned targets.",
    )
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=8777)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the migration. Without this flag only a dry-run summary is printed.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.rollback_backup is not None:
            result = rollback_backup(
                project_root=args.project_root,
                backup_dir=args.rollback_backup,
                require_port_free=(args.api_host, args.api_port),
            )
            print(
                json.dumps(
                    {
                        "mode": "rollback",
                        "backup": str(args.rollback_backup),
                        "status": result["status"],
                    },
                    indent=2,
                )
            )
            return 0
        if not args.username or not args.source_thread or not args.target_thread:
            raise MigrationError(
                "--username, --from-thread, and --to-thread are required for migration"
            )
        if args.execute and _port_is_open(args.api_host, args.api_port):
            raise MigrationError(
                f"refusing live migration while Chat API is reachable at {args.api_host}:{args.api_port}"
            )
        plan = build_plan(
            project_root=args.project_root,
            username=args.username,
            source_thread=args.source_thread,
            target_thread=args.target_thread,
        )
        summary = {
            "username": plan.username,
            "source": plan.source_internal,
            "target": plan.target_internal,
            "dialogues": len(plan.dialogues),
            "scene_files": len(plan.scenes),
            "episodic_chunks": plan.changed_chunks,
        }
        if not args.execute:
            print(json.dumps({"mode": "dry-run", **summary}, indent=2))
            return 0
        backup_dir = args.backup_dir or _default_backup_dir(
            plan.project_root,
            plan.username,
            plan.source_thread,
            plan.target_thread,
        )
        result = execute_plan(
            plan,
            backup_dir=backup_dir,
            require_port_free=(args.api_host, args.api_port),
        )
        print(json.dumps({"mode": "executed", **summary, "backup": str(backup_dir), "status": result["status"]}, indent=2))
        return 0
    except MigrationError as exc:
        print(f"migration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
