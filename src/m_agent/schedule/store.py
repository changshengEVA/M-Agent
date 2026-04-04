from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Dict, List

from .models import ScheduleItem


ANONYMOUS_OWNER_ID = "__anonymous__"


def _safe_slug(value: str, fallback: str = "thread") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    return cleaned[:80] or fallback


class ScheduleStore:
    def __init__(self, storage_root: Path) -> None:
        self.storage_root = Path(storage_root).resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _owner_dir(self, owner_id: str) -> Path:
        return self.storage_root / "by_user" / _safe_slug(owner_id, fallback=ANONYMOUS_OWNER_ID)

    def _thread_dir(self, owner_id: str, thread_id: str) -> Path:
        return self._owner_dir(owner_id) / "by_thread" / _safe_slug(thread_id, fallback="thread")

    def _thread_file(self, owner_id: str, thread_id: str) -> Path:
        return self._thread_dir(owner_id, thread_id) / "schedules.json"

    def _legacy_thread_file(self, thread_id: str) -> Path:
        return self.storage_root / "by_thread" / _safe_slug(thread_id, fallback="thread") / "schedules.json"

    @staticmethod
    def _normalize_owner_id(owner_id: str) -> str:
        return str(owner_id or "").strip() or ANONYMOUS_OWNER_ID

    def load_thread_items(self, owner_id: str, thread_id: str) -> List[ScheduleItem]:
        normalized_owner_id = self._normalize_owner_id(owner_id)
        path = self._thread_file(normalized_owner_id, thread_id)
        if not path.exists():
            legacy_path = self._legacy_thread_file(thread_id)
            if not legacy_path.exists():
                return []
            items = self._load_items_from_file(legacy_path)
            if items:
                migrated_items = []
                for item in items:
                    migrated_item = ScheduleItem.from_dict(item.to_dict())
                    migrated_item.owner_id = normalized_owner_id
                    migrated_items.append(migrated_item)
                self.save_thread_items(normalized_owner_id, thread_id, migrated_items)
                return migrated_items
            return []
        return self._load_items_from_file(path)

    def iter_owner_items(self, owner_id: str) -> List[ScheduleItem]:
        normalized_owner_id = self._normalize_owner_id(owner_id)
        items: List[ScheduleItem] = []
        owner_root = self._owner_dir(normalized_owner_id)
        if owner_root.exists():
            for path in owner_root.glob("by_thread/*/schedules.json"):
                items.extend(self._load_items_from_file(path))
        if items:
            return items
        if normalized_owner_id != ANONYMOUS_OWNER_ID:
            return items
        by_thread_root = self.storage_root / "by_thread"
        if not by_thread_root.exists():
            return items
        for path in by_thread_root.glob("*/schedules.json"):
            items.extend(self._load_items_from_file(path))
        return items

    def _load_items_from_file(self, path: Path) -> List[ScheduleItem]:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        items_raw = payload.get("items", []) if isinstance(payload, dict) else []
        items: List[ScheduleItem] = []
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            try:
                parsed = ScheduleItem.from_dict(item)
            except Exception:
                continue
            if parsed.thread_id and parsed.schedule_id:
                items.append(parsed)
        return items

    def save_thread_items(self, owner_id: str, thread_id: str, items: List[ScheduleItem]) -> Path:
        normalized_owner_id = self._normalize_owner_id(owner_id)
        path = self._thread_file(normalized_owner_id, thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized_items: List[ScheduleItem] = []
        for item in items:
            copied = ScheduleItem.from_dict(item.to_dict())
            copied.owner_id = normalized_owner_id
            copied.thread_id = str(thread_id or "").strip()
            normalized_items.append(copied)
        serialized = [item.to_dict() for item in sorted(normalized_items, key=lambda x: (x.due_at_utc, x.schedule_id))]
        payload = {
            "owner_id": normalized_owner_id,
            "thread_id": str(thread_id or "").strip(),
            "item_count": len(serialized),
            "items": serialized,
        }
        temp_path = path.with_suffix(".tmp")
        with self._lock:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            temp_path.replace(path)
        return path

    def iter_all_items(self) -> List[ScheduleItem]:
        items: List[ScheduleItem] = []
        by_user_root = self.storage_root / "by_user"
        if by_user_root.exists():
            for path in by_user_root.glob("*/*/*/schedules.json"):
                items.extend(self._load_items_from_file(path))
            return items
        by_thread_root = self.storage_root / "by_thread"
        if not by_thread_root.exists():
            return items
        for path in by_thread_root.glob("*/schedules.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f) or {}
            except Exception:
                continue
            items_raw = payload.get("items", []) if isinstance(payload, dict) else []
            for item in items_raw:
                if not isinstance(item, dict):
                    continue
                try:
                    migrated = ScheduleItem.from_dict(item)
                    migrated.owner_id = ANONYMOUS_OWNER_ID
                    items.append(migrated)
                except Exception:
                    continue
        return items

    def find_by_id(self, schedule_id: str, *, owner_id: str | None = None) -> ScheduleItem | None:
        target_id = str(schedule_id or "").strip()
        if not target_id:
            return None
        for item in self.iter_all_items():
            if owner_id is not None and item.owner_id != self._normalize_owner_id(owner_id):
                continue
            if item.schedule_id == target_id:
                return item
        return None
