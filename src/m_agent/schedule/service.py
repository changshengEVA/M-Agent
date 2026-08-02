from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from m_agent.utils.time_utils import resolve_timezone

from .models import (
    ACTIVE_SCHEDULE_STATUSES,
    SCHEDULE_STATUS_CANCELED,
    SCHEDULE_STATUS_DONE,
    SCHEDULE_STATUS_FAILED,
    SCHEDULE_STATUS_LEASED,
    SCHEDULE_STATUS_PENDING,
    SCHEDULE_STATUS_RUNNING,
    DeferredObjective,
    OBJECTIVE_ENCODING_LEGACY_TEXT,
    OBJECTIVE_ENCODING_NATIVE,
    ScheduleItem,
)
from .store import ScheduleStore


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).astimezone(timezone.utc)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


class ScheduleService:
    """Create, query, delete, and activate minimal durable schedules."""

    def __init__(self, *, store: ScheduleStore, default_timezone_name: str = "UTC") -> None:
        self.store = store
        self.default_timezone_name = str(default_timezone_name or "UTC").strip() or "UTC"

    def create_schedule(
        self,
        *,
        owner_id: str,
        thread_id: str,
        due_at_utc: str,
        timezone_name: str,
        deferred_objective: str = "",
        text: str = "",
        origin: Optional[Dict[str, str]] = None,
    ) -> ScheduleItem:
        normalized_owner_id = self.store._normalize_owner_id(owner_id)
        safe_thread_id = str(thread_id or "").strip()
        safe_due_at_utc = str(due_at_utc or "").strip()
        safe_objective = _clean_text(deferred_objective)
        legacy_text = _clean_text(text)
        if safe_objective and legacy_text and safe_objective != legacy_text:
            raise ValueError("deferred_objective conflicts with legacy text")
        objective_encoding = (
            OBJECTIVE_ENCODING_NATIVE
            if safe_objective
            else OBJECTIVE_ENCODING_LEGACY_TEXT
        )
        safe_objective = safe_objective or legacy_text
        if not safe_thread_id:
            raise ValueError("thread_id is required")
        if not safe_due_at_utc:
            raise ValueError("due_at_utc is required")
        _parse_utc_iso(safe_due_at_utc)
        if not safe_objective:
            raise ValueError("deferred_objective is required")

        item = ScheduleItem(
            schedule_id=f"sch_{uuid.uuid4().hex[:12]}",
            thread_id=safe_thread_id,
            due_at_utc=safe_due_at_utc,
            timezone_name=str(timezone_name or self.default_timezone_name).strip() or self.default_timezone_name,
            deferred_objective=DeferredObjective(
                description=safe_objective,
                encoding=objective_encoding,
            ),
            status=SCHEDULE_STATUS_PENDING,
            created_at=_now_utc_iso(),
            origin={
                str(key): str(value or "").strip()
                for key, value in dict(origin or {}).items()
                if str(key).strip() and str(value or "").strip()
            },
        )
        items = self.store.load_thread_items(normalized_owner_id, safe_thread_id)
        items.append(item)
        self.store.save_thread_items(normalized_owner_id, safe_thread_id, items)
        return item

    def list_schedules(
        self,
        *,
        owner_id: str,
        thread_id: Optional[str] = None,
        statuses: Optional[Sequence[str]] = None,
        keyword: str = "",
        start_utc: Optional[str] = None,
        end_utc: Optional[str] = None,
        include_completed: bool = False,
        limit: int = 10,
    ) -> List[ScheduleItem]:
        normalized_owner_id = self.store._normalize_owner_id(owner_id)
        safe_thread_id = str(thread_id or "").strip()
        items = (
            self.store.load_thread_items(normalized_owner_id, safe_thread_id)
            if safe_thread_id
            else self.store.iter_owner_items(normalized_owner_id)
        )
        safe_limit = max(1, min(100, int(limit or 10)))
        keyword_norm = _clean_text(keyword).lower()
        allowed_statuses: Optional[set[str]] = None
        if statuses:
            allowed_statuses = {
                str(status or "").strip()
                for status in statuses
                if str(status or "").strip()
            }
        elif not include_completed:
            allowed_statuses = set(ACTIVE_SCHEDULE_STATUSES)

        start_dt = _parse_utc_iso(start_utc) if start_utc else None
        end_dt = _parse_utc_iso(end_utc) if end_utc else None
        filtered: List[ScheduleItem] = []
        for item in items:
            if allowed_statuses is not None and item.status not in allowed_statuses:
                continue
            due_dt = _parse_utc_iso(item.due_at_utc)
            if start_dt and due_dt < start_dt:
                continue
            if end_dt and due_dt > end_dt:
                continue
            if keyword_norm and keyword_norm not in item.text.lower() and keyword_norm not in item.schedule_id.lower():
                continue
            filtered.append(item)
        filtered.sort(key=lambda entry: (_parse_utc_iso(entry.due_at_utc), entry.schedule_id))
        return filtered[:safe_limit]

    def cancel_schedule(
        self,
        *,
        owner_id: str,
        schedule_id: str,
        thread_id: Optional[str] = None,
    ) -> ScheduleItem:
        return self._set_status(
            owner_id=owner_id,
            thread_id=thread_id,
            schedule_id=schedule_id,
            status=SCHEDULE_STATUS_CANCELED,
        )

    def lease_due_schedules(
        self,
        *,
        owner_id: str,
        now_utc: Optional[str] = None,
        limit: int = 20,
    ) -> List[ScheduleItem]:
        normalized_owner_id = self.store._normalize_owner_id(owner_id)
        cutoff = _parse_utc_iso(now_utc) if now_utc else datetime.now(timezone.utc)
        safe_limit = max(1, min(200, int(limit or 20)))
        all_items = self.store.iter_owner_items(normalized_owner_id)
        grouped: Dict[str, List[ScheduleItem]] = {}
        for item in all_items:
            grouped.setdefault(item.thread_id, []).append(item)

        leased: List[ScheduleItem] = []
        for thread_id, items in grouped.items():
            changed = False
            for item in sorted(items, key=lambda entry: (_parse_utc_iso(entry.due_at_utc), entry.schedule_id)):
                if len(leased) >= safe_limit:
                    break
                if item.status != SCHEDULE_STATUS_PENDING or _parse_utc_iso(item.due_at_utc) > cutoff:
                    continue
                item.status = SCHEDULE_STATUS_LEASED
                leased.append(item)
                changed = True
            if changed:
                self.store.save_thread_items(normalized_owner_id, thread_id, items)
            if len(leased) >= safe_limit:
                break
        return leased

    def mark_running(
        self,
        *,
        owner_id: str,
        schedule_id: str,
        thread_id: Optional[str] = None,
    ) -> ScheduleItem:
        return self._set_status(
            owner_id=owner_id,
            thread_id=thread_id,
            schedule_id=schedule_id,
            status=SCHEDULE_STATUS_RUNNING,
        )

    def mark_done(
        self,
        *,
        owner_id: str,
        thread_id: str,
        schedule_id: str,
        run_id: str = "",
        result: Optional[Dict[str, Any]] = None,
    ) -> ScheduleItem:
        del run_id, result
        return self._set_status(
            owner_id=owner_id,
            thread_id=thread_id,
            schedule_id=schedule_id,
            status=SCHEDULE_STATUS_DONE,
        )

    def mark_failed(
        self,
        *,
        owner_id: str,
        thread_id: str,
        schedule_id: str,
        error: str = "",
    ) -> ScheduleItem:
        del error
        return self._set_status(
            owner_id=owner_id,
            thread_id=thread_id,
            schedule_id=schedule_id,
            status=SCHEDULE_STATUS_FAILED,
        )

    def _set_status(
        self,
        *,
        owner_id: str,
        thread_id: Optional[str],
        schedule_id: str,
        status: str,
    ) -> ScheduleItem:
        normalized_owner_id = self.store._normalize_owner_id(owner_id)
        target = self.store.find_by_id(schedule_id, owner_id=normalized_owner_id)
        if target is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        target_thread_id = str(thread_id or target.thread_id or "").strip()
        if not target_thread_id or target_thread_id != target.thread_id:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")

        items = self.store.load_thread_items(normalized_owner_id, target_thread_id)
        for item in items:
            if item.schedule_id != schedule_id:
                continue
            item.status = status
            self.store.save_thread_items(normalized_owner_id, target_thread_id, items)
            return item
        raise FileNotFoundError(f"schedule not found: {schedule_id}")

    def serialize_item(self, item: ScheduleItem) -> Dict[str, Any]:
        tz, _, _ = resolve_timezone(item.timezone_name)
        due_local = _parse_utc_iso(item.due_at_utc).astimezone(tz)
        return {
            **item.to_dict(),
            # Deprecated API compatibility. Durable storage does not write
            # this untyped alias; callers should migrate to deferred_objective.
            "text": item.text,
            "due_at_local": due_local.isoformat(),
            "due_display": due_local.strftime("%Y-%m-%d %H:%M"),
        }
