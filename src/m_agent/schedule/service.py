from __future__ import annotations

import re
import uuid
from copy import deepcopy
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
    ScheduleItem,
)
from .parsing import parse_day_window
from .store import ScheduleStore

_QUOTED_TERM_RE = re.compile(r"[\"']([^\"']{1,80})[\"']")
_NOISE_TOKENS = {
    "把",
    "那个",
    "这个",
    "提醒",
    "日程",
    "安排",
    "改到",
    "改成",
    "改为",
    "调整到",
    "取消",
    "删除",
    "删掉",
    "不要",
    "了",
}


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).astimezone(timezone.utc)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


class ScheduleService:
    def __init__(self, *, store: ScheduleStore, default_timezone_name: str = "UTC") -> None:
        self.store = store
        self.default_timezone_name = str(default_timezone_name or "UTC").strip() or "UTC"

    def create_schedule(
        self,
        *,
        owner_id: str,
        thread_id: str,
        title: str,
        due_at_utc: str,
        timezone_name: str,
        original_time_text: str,
        action_type: str = "chat_prompt",
        action_payload: Optional[Dict[str, Any]] = None,
        source_text: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ScheduleItem:
        safe_owner_id = self.store._normalize_owner_id(owner_id)
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise ValueError("thread_id is required")
        now_iso = _now_utc_iso()
        item = ScheduleItem(
            schedule_id=f"sch_{uuid.uuid4().hex[:12]}",
            owner_id=safe_owner_id,
            thread_id=safe_thread_id,
            title=_clean_text(title) or "Reminder",
            status=SCHEDULE_STATUS_PENDING,
            due_at_utc=str(due_at_utc or "").strip(),
            timezone_name=str(timezone_name or self.default_timezone_name).strip() or self.default_timezone_name,
            original_time_text=_clean_text(original_time_text),
            action_type=str(action_type or "chat_prompt").strip() or "chat_prompt",
            action_payload=deepcopy(action_payload) if isinstance(action_payload, dict) else {},
            created_at=now_iso,
            updated_at=now_iso,
            source_text=_clean_text(source_text),
            metadata=deepcopy(metadata) if isinstance(metadata, dict) else {},
        )
        items = self.store.load_thread_items(safe_owner_id, safe_thread_id)
        items.append(item)
        self.store.save_thread_items(safe_owner_id, safe_thread_id, items)
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
        if safe_thread_id:
            items = self.store.load_thread_items(normalized_owner_id, safe_thread_id)
        else:
            items = self.store.iter_owner_items(normalized_owner_id)
        safe_limit = max(1, min(100, int(limit or 10)))
        keyword_norm = _clean_text(keyword).lower()
        allowed_statuses: Optional[set[str]] = None
        if statuses:
            allowed_statuses = {str(status or "").strip() for status in statuses if str(status or "").strip()}
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
            if keyword_norm:
                title_text = item.title.lower()
                source_text = item.source_text.lower()
                if keyword_norm not in title_text and keyword_norm not in source_text and keyword_norm not in item.schedule_id.lower():
                    continue
            filtered.append(item)
        filtered.sort(key=lambda entry: (_parse_utc_iso(entry.due_at_utc), entry.schedule_id))
        return filtered[:safe_limit]

    def resolve_schedule_targets(
        self,
        *,
        owner_id: str,
        target_text: str,
        thread_id: Optional[str] = None,
        statuses: Optional[Sequence[str]] = None,
        limit: int = 5,
        timezone_name: Optional[str] = None,
        now_context: Optional[Dict[str, Any]] = None,
    ) -> List[ScheduleItem]:
        items = self.list_schedules(
            owner_id=owner_id,
            thread_id=thread_id,
            statuses=statuses or ACTIVE_SCHEDULE_STATUSES,
            include_completed=True,
            limit=100,
        )
        safe_target = _clean_text(target_text)
        if not safe_target:
            return items[: max(1, min(10, int(limit or 5)))]

        direct_id = re.search(r"\bsch_[A-Za-z0-9]{6,}\b", safe_target)
        if direct_id is not None:
            direct_value = direct_id.group(0)
            return [item for item in items if item.schedule_id == direct_value][:1]

        window = parse_day_window(
            safe_target,
            timezone_name=timezone_name or self.default_timezone_name,
            now_context=now_context,
        )
        candidate_keywords = self._extract_candidate_keywords(safe_target)
        scored: List[tuple[int, ScheduleItem]] = []
        for item in items:
            score = 0
            if window and self._item_matches_window(item, window["start_utc"], window["end_utc"]):
                score += 50
            if candidate_keywords:
                haystacks = (
                    item.title.lower(),
                    item.source_text.lower(),
                    item.schedule_id.lower(),
                )
                keyword_hits = sum(1 for keyword in candidate_keywords if any(keyword in hay for hay in haystacks))
                score += keyword_hits * 20
            if score > 0:
                scored.append((score, item))

        if scored:
            scored.sort(key=lambda pair: (-pair[0], _parse_utc_iso(pair[1].due_at_utc), pair[1].schedule_id))
            return [item for _, item in scored[: max(1, min(10, int(limit or 5)))]]
        return items[: max(1, min(10, int(limit or 5)))]

    def update_schedule(
        self,
        *,
        owner_id: str,
        thread_id: Optional[str],
        schedule_id: str,
        title: Optional[str] = None,
        due_at_utc: Optional[str] = None,
        timezone_name: Optional[str] = None,
        original_time_text: Optional[str] = None,
        action_payload_patch: Optional[Dict[str, Any]] = None,
        metadata_patch: Optional[Dict[str, Any]] = None,
        source_text: Optional[str] = None,
    ) -> ScheduleItem:
        normalized_owner_id = self.store._normalize_owner_id(owner_id)
        target = self.store.find_by_id(schedule_id, owner_id=normalized_owner_id)
        if target is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        target_thread_id = str(getattr(target, "thread_id", "") or "").strip()
        if not target_thread_id:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        items = self.store.load_thread_items(normalized_owner_id, target_thread_id)
        updated: Optional[ScheduleItem] = None
        for index, item in enumerate(items):
            if item.schedule_id != schedule_id:
                continue
            if title is not None:
                item.title = _clean_text(title) or item.title
            if due_at_utc is not None:
                item.due_at_utc = str(due_at_utc or "").strip() or item.due_at_utc
            if timezone_name is not None:
                item.timezone_name = str(timezone_name or "").strip() or item.timezone_name
            if original_time_text is not None:
                item.original_time_text = _clean_text(original_time_text)
            if source_text is not None:
                item.source_text = _clean_text(source_text)
            if isinstance(action_payload_patch, dict):
                merged = dict(item.action_payload or {})
                merged.update(action_payload_patch)
                item.action_payload = merged
            if isinstance(metadata_patch, dict):
                merged_meta = dict(item.metadata or {})
                merged_meta.update(metadata_patch)
                item.metadata = merged_meta
            item.updated_at = _now_utc_iso()
            items[index] = item
            updated = item
            break
        if updated is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        self.store.save_thread_items(normalized_owner_id, target_thread_id, items)
        return updated

    def cancel_schedule(
        self,
        *,
        owner_id: str,
        thread_id: Optional[str],
        schedule_id: str,
        source_text: str = "",
    ) -> ScheduleItem:
        normalized_owner_id = self.store._normalize_owner_id(owner_id)
        target = self.store.find_by_id(schedule_id, owner_id=normalized_owner_id)
        if target is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        target_thread_id = str(getattr(target, "thread_id", "") or "").strip()
        if not target_thread_id:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        items = self.store.load_thread_items(normalized_owner_id, target_thread_id)
        updated: Optional[ScheduleItem] = None
        for index, item in enumerate(items):
            if item.schedule_id != schedule_id:
                continue
            item.status = SCHEDULE_STATUS_CANCELED
            item.updated_at = _now_utc_iso()
            if source_text:
                item.source_text = _clean_text(source_text)
            item.metadata = dict(item.metadata or {})
            item.metadata["canceled_at"] = item.updated_at
            items[index] = item
            updated = item
            break
        if updated is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        self.store.save_thread_items(normalized_owner_id, target_thread_id, items)
        return updated

    def lease_due_schedules(
        self,
        *,
        owner_id: Optional[str] = None,
        now_utc: Optional[str] = None,
        limit: int = 20,
    ) -> List[ScheduleItem]:
        cutoff = _parse_utc_iso(now_utc) if now_utc else datetime.now(timezone.utc)
        leased: List[ScheduleItem] = []
        if owner_id is not None:
            all_items = self.store.iter_owner_items(owner_id)
        else:
            all_items = self.store.iter_all_items()
        grouped: Dict[tuple[str, str], List[ScheduleItem]] = {}
        for item in all_items:
            grouped.setdefault((item.owner_id, item.thread_id), []).append(item)
        for (owner_id, thread_id), items in grouped.items():
            changed = False
            for item in sorted(items, key=lambda entry: (_parse_utc_iso(entry.due_at_utc), entry.schedule_id)):
                if len(leased) >= max(1, min(200, int(limit or 20))):
                    break
                if item.status != SCHEDULE_STATUS_PENDING:
                    continue
                if _parse_utc_iso(item.due_at_utc) > cutoff:
                    continue
                retry_after_utc = str(item.metadata.get("retry_after_utc", "") or "").strip()
                if retry_after_utc:
                    try:
                        if _parse_utc_iso(retry_after_utc) > cutoff:
                            continue
                    except Exception:
                        pass
                item.status = SCHEDULE_STATUS_LEASED
                item.updated_at = _now_utc_iso()
                item.metadata = dict(item.metadata or {})
                item.metadata["leased_at"] = item.updated_at
                leased.append(item)
                changed = True
            if changed:
                self.store.save_thread_items(owner_id, thread_id, items)
            if len(leased) >= max(1, min(200, int(limit or 20))):
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
            metadata_patch={"running_at": _now_utc_iso()},
            clear_retry_after=True,
        )

    def release_lease(
        self,
        *,
        owner_id: str,
        schedule_id: str,
        thread_id: Optional[str] = None,
        reason: str = "",
        retry_after_seconds: int = 0,
    ) -> ScheduleItem:
        retry_after_utc = None
        safe_retry_after_seconds = max(0, int(retry_after_seconds or 0))
        if safe_retry_after_seconds > 0:
            retry_after_utc = (
                datetime.now(timezone.utc) + timedelta(seconds=safe_retry_after_seconds)
            ).isoformat().replace("+00:00", "Z")
        metadata_patch: Dict[str, Any] = {
            "lease_released_at": _now_utc_iso(),
        }
        safe_reason = str(reason or "").strip()
        if safe_reason:
            metadata_patch["last_release_reason"] = safe_reason
        if retry_after_utc:
            metadata_patch["retry_after_utc"] = retry_after_utc
        return self._set_status(
            owner_id=owner_id,
            thread_id=thread_id,
            schedule_id=schedule_id,
            status=SCHEDULE_STATUS_PENDING,
            metadata_patch=metadata_patch,
            clear_retry_after=not bool(retry_after_utc),
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
        return self._mark_terminal(
            owner_id=owner_id,
            thread_id=thread_id,
            schedule_id=schedule_id,
            status=SCHEDULE_STATUS_DONE,
            run_id=run_id,
            result=result,
        )

    def mark_failed(
        self,
        *,
        owner_id: str,
        thread_id: str,
        schedule_id: str,
        error: str,
        retry_at_utc: Optional[str] = None,
    ) -> ScheduleItem:
        item = self._mark_terminal(
            owner_id=owner_id,
            thread_id=thread_id,
            schedule_id=schedule_id,
            status=SCHEDULE_STATUS_FAILED,
            run_id="",
            result={"error": str(error or "").strip()},
        )
        if retry_at_utc:
            return self.update_schedule(
                owner_id=owner_id,
                thread_id=thread_id,
                schedule_id=schedule_id,
                due_at_utc=retry_at_utc,
                metadata_patch={"retry_at_utc": retry_at_utc},
            )
        return item

    def _set_status(
        self,
        *,
        owner_id: str,
        thread_id: Optional[str],
        schedule_id: str,
        status: str,
        metadata_patch: Optional[Dict[str, Any]] = None,
        clear_retry_after: bool = False,
    ) -> ScheduleItem:
        normalized_owner_id = self.store._normalize_owner_id(owner_id)
        target = self.store.find_by_id(schedule_id, owner_id=normalized_owner_id)
        if target is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        target_thread_id = str(getattr(target, "thread_id", "") or "").strip()
        if not target_thread_id:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        items = self.store.load_thread_items(normalized_owner_id, target_thread_id)
        updated: Optional[ScheduleItem] = None
        for index, item in enumerate(items):
            if item.schedule_id != schedule_id:
                continue
            item.status = status
            item.updated_at = _now_utc_iso()
            item.metadata = dict(item.metadata or {})
            if clear_retry_after:
                item.metadata.pop("retry_after_utc", None)
            if isinstance(metadata_patch, dict):
                item.metadata.update(deepcopy(metadata_patch))
            items[index] = item
            updated = item
            break
        if updated is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        self.store.save_thread_items(normalized_owner_id, target_thread_id, items)
        return updated

    def serialize_item(self, item: ScheduleItem) -> Dict[str, Any]:
        tz, _, _ = resolve_timezone(item.timezone_name)
        due_local = _parse_utc_iso(item.due_at_utc).astimezone(tz)
        metadata = deepcopy(item.metadata)
        payload = {
            "schedule_id": item.schedule_id,
            "owner_id": item.owner_id,
            "thread_id": item.thread_id,
            "title": item.title,
            "status": item.status,
            "due_at_utc": item.due_at_utc,
            "due_at_local": due_local.isoformat(),
            "due_display": due_local.strftime("%Y-%m-%d %H:%M"),
            "timezone_name": item.timezone_name,
            "original_time_text": item.original_time_text,
            "action_type": item.action_type,
            "action_payload": deepcopy(item.action_payload),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "source_text": item.source_text,
            "metadata": metadata,
        }
        schedule_kind = str(metadata.get("schedule_kind", "") or "").strip() or "time_due"
        payload["schedule_kind"] = schedule_kind
        event_at_utc = str(metadata.get("event_at_utc", "") or "").strip()
        if event_at_utc:
            try:
                event_local = _parse_utc_iso(event_at_utc).astimezone(tz)
                payload["event_at_utc"] = event_at_utc
                payload["event_at_local"] = event_local.isoformat()
                payload["event_display"] = event_local.strftime("%Y-%m-%d %H:%M")
            except Exception:
                payload["event_at_utc"] = event_at_utc
                payload["event_at_local"] = str(metadata.get("event_at_local", "") or "").strip() or None
                payload["event_display"] = str(metadata.get("event_display", "") or "").strip() or None
        else:
            payload["event_at_utc"] = None
            payload["event_at_local"] = None
            payload["event_display"] = None
        payload["reminder_offset_minutes"] = metadata.get("reminder_offset_minutes")
        payload["reminder_offset_label"] = str(metadata.get("reminder_offset_label", "") or "").strip() or None
        return payload

    @staticmethod
    def _item_matches_window(item: ScheduleItem, start_utc: str, end_utc: str) -> bool:
        due_dt = _parse_utc_iso(item.due_at_utc)
        return _parse_utc_iso(start_utc) <= due_dt <= _parse_utc_iso(end_utc)

    @staticmethod
    def _extract_candidate_keywords(text: str) -> List[str]:
        safe = _clean_text(text)
        quoted_terms = [match.strip().lower() for match in _QUOTED_TERM_RE.findall(safe) if match.strip()]
        if quoted_terms:
            return quoted_terms
        token_source = re.sub(r"[，。！？、,.;:()\[\]{}<>]+", " ", safe)
        tokens = []
        for raw in token_source.split():
            token = str(raw or "").strip().lower()
            if len(token) <= 1:
                continue
            if token in _NOISE_TOKENS:
                continue
            if token.startswith("sch_"):
                continue
            tokens.append(token)
        unique: List[str] = []
        for token in tokens:
            if token not in unique:
                unique.append(token)
        return unique[:5]

    def _mark_terminal(
        self,
        *,
        owner_id: str,
        thread_id: Optional[str],
        schedule_id: str,
        status: str,
        run_id: str,
        result: Optional[Dict[str, Any]],
    ) -> ScheduleItem:
        normalized_owner_id = self.store._normalize_owner_id(owner_id)
        target = self.store.find_by_id(schedule_id, owner_id=normalized_owner_id)
        if target is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        target_thread_id = str(getattr(target, "thread_id", "") or "").strip()
        if not target_thread_id:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        items = self.store.load_thread_items(normalized_owner_id, target_thread_id)
        updated: Optional[ScheduleItem] = None
        for index, item in enumerate(items):
            if item.schedule_id != schedule_id:
                continue
            item.status = status
            item.updated_at = _now_utc_iso()
            item.metadata = dict(item.metadata or {})
            if run_id:
                item.metadata["last_run_id"] = run_id
            if isinstance(result, dict):
                item.metadata["last_result"] = deepcopy(result)
            items[index] = item
            updated = item
            break
        if updated is None:
            raise FileNotFoundError(f"schedule not found: {schedule_id}")
        self.store.save_thread_items(normalized_owner_id, target_thread_id, items)
        return updated
