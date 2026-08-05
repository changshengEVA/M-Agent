from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
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


DEFAULT_SCHEDULE_LEASE_SECONDS = 60
DEFAULT_SCHEDULE_LEASE_OWNER = "schedule-service"


class ScheduleLeaseConflictError(RuntimeError):
    """A stale worker attempted to mutate a lease it no longer owns."""


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).astimezone(timezone.utc)


def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
        def append_item(items: List[ScheduleItem]) -> None:
            items.append(item)

        self.store.mutate_thread_items(
            normalized_owner_id,
            safe_thread_id,
            append_item,
        )
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
        lease_owner: str = "",
        lease_seconds: float = DEFAULT_SCHEDULE_LEASE_SECONDS,
    ) -> List[ScheduleItem]:
        normalized_owner_id = self.store._normalize_owner_id(owner_id)
        cutoff = _parse_utc_iso(now_utc) if now_utc else datetime.now(timezone.utc)
        safe_limit = max(1, min(200, int(limit or 20)))
        safe_lease_owner = (
            _clean_text(lease_owner) or DEFAULT_SCHEDULE_LEASE_OWNER
        )
        safe_lease_seconds = max(1.0, float(lease_seconds or DEFAULT_SCHEDULE_LEASE_SECONDS))
        lease_until = _to_utc_iso(
            cutoff + timedelta(seconds=safe_lease_seconds)
        )
        all_items = self.store.iter_owner_items(normalized_owner_id)
        grouped: Dict[str, List[ScheduleItem]] = {}
        for item in all_items:
            grouped.setdefault(item.thread_id, []).append(item)

        leased: List[ScheduleItem] = []
        candidate_threads = {
            thread_id
            for thread_id, items in grouped.items()
            if any(
                _parse_utc_iso(item.due_at_utc) <= cutoff
                and (
                    item.status == SCHEDULE_STATUS_PENDING
                    or (
                        item.status == SCHEDULE_STATUS_LEASED
                        and self._lease_expired(item, cutoff=cutoff)
                    )
                )
                for item in items
            )
        }
        ordered_threads = sorted(
            candidate_threads,
            key=lambda thread_id: min(
                (
                    _parse_utc_iso(item.due_at_utc),
                    item.schedule_id,
                )
                for item in grouped[thread_id]
            ),
        )
        for thread_id in ordered_threads:
            remaining = safe_limit - len(leased)
            if remaining <= 0:
                break

            def claim_thread(items: List[ScheduleItem]) -> List[ScheduleItem]:
                claimed: List[ScheduleItem] = []
                for item in sorted(
                    items,
                    key=lambda entry: (
                        _parse_utc_iso(entry.due_at_utc),
                        entry.schedule_id,
                    ),
                ):
                    if len(claimed) >= remaining:
                        break
                    if _parse_utc_iso(item.due_at_utc) > cutoff:
                        continue
                    reclaimable = bool(
                        item.status == SCHEDULE_STATUS_LEASED
                        and self._lease_expired(item, cutoff=cutoff)
                    )
                    if item.status != SCHEDULE_STATUS_PENDING and not reclaimable:
                        continue
                    item.status = SCHEDULE_STATUS_LEASED
                    item.lease_token = f"lease_{uuid.uuid4().hex}"
                    item.lease_owner = safe_lease_owner
                    item.lease_until = lease_until
                    item.attempt = max(0, int(item.attempt or 0)) + 1
                    claimed.append(item)
                return claimed

            leased.extend(
                self.store.mutate_thread_items(
                    normalized_owner_id,
                    thread_id,
                    claim_thread,
                )
            )
            if len(leased) >= safe_limit:
                break
        return leased

    @staticmethod
    def _lease_expired(item: ScheduleItem, *, cutoff: datetime) -> bool:
        lease_until = str(item.lease_until or "").strip()
        if not lease_until:
            # v2 records had a permanent ``leased`` status with no lease
            # metadata.  Treat them as immediately reclaimable on upgrade.
            return True
        try:
            return _parse_utc_iso(lease_until) <= cutoff
        except (TypeError, ValueError):
            # Corrupt expiry data must not permanently strand due work.
            return True

    def release_lease(
        self,
        *,
        owner_id: str,
        schedule_id: str,
        lease_token: str,
        thread_id: Optional[str] = None,
        error: str = "",
    ) -> ScheduleItem:
        """Return a failed admission claim to pending without clobbering a retry."""

        safe_token = str(lease_token or "").strip()
        if not safe_token:
            raise ValueError("lease_token is required")
        return self._set_status(
            owner_id=owner_id,
            thread_id=thread_id,
            schedule_id=schedule_id,
            status=SCHEDULE_STATUS_PENDING,
            lease_token=safe_token,
            last_error=_clean_text(error),
        )

    def mark_running(
        self,
        *,
        owner_id: str,
        schedule_id: str,
        thread_id: Optional[str] = None,
        lease_token: str = "",
    ) -> ScheduleItem:
        return self._set_status(
            owner_id=owner_id,
            thread_id=thread_id,
            schedule_id=schedule_id,
            status=SCHEDULE_STATUS_RUNNING,
            lease_token=lease_token,
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
            last_error="",
        )

    def mark_failed(
        self,
        *,
        owner_id: str,
        thread_id: str,
        schedule_id: str,
        error: str = "",
    ) -> ScheduleItem:
        return self._set_status(
            owner_id=owner_id,
            thread_id=thread_id,
            schedule_id=schedule_id,
            status=SCHEDULE_STATUS_FAILED,
            last_error=_clean_text(error),
        )

    def _set_status(
        self,
        *,
        owner_id: str,
        thread_id: Optional[str],
        schedule_id: str,
        status: str,
        lease_token: str = "",
        last_error: Optional[str] = None,
    ) -> ScheduleItem:
        normalized_owner_id = self.store._normalize_owner_id(owner_id)
        target = self.store.find_by_id(schedule_id, owner_id=normalized_owner_id)
        if target is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        target_thread_id = str(thread_id or target.thread_id or "").strip()
        if not target_thread_id or target_thread_id != target.thread_id:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")

        safe_lease_token = str(lease_token or "").strip()

        def update_item(items: List[ScheduleItem]) -> ScheduleItem:
            for item in items:
                if item.schedule_id != schedule_id:
                    continue
                if safe_lease_token and item.lease_token != safe_lease_token:
                    raise ScheduleLeaseConflictError(
                        f"stale lease for schedule: {schedule_id}"
                    )
                item.status = status
                if status != SCHEDULE_STATUS_LEASED:
                    item.lease_token = ""
                    item.lease_owner = ""
                    item.lease_until = ""
                if last_error is not None:
                    item.last_error = str(last_error or "").strip()
                return item
            raise FileNotFoundError(f"schedule not found: {schedule_id}")

        return self.store.mutate_thread_items(
            normalized_owner_id,
            target_thread_id,
            update_item,
        )

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
