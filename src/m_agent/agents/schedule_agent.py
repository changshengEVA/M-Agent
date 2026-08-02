from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from m_agent.config_paths import DEFAULT_SCHEDULE_AGENT_CONFIG_PATH, resolve_config_path, resolve_related_config_path
from m_agent.schedule.parsing import parse_iso_due_at
from m_agent.schedule.service import ScheduleService
from m_agent.schedule.store import ANONYMOUS_OWNER_ID, ScheduleStore
from m_agent.utils.time_utils import resolve_timezone


DEFAULT_CONFIG_PATH = DEFAULT_SCHEDULE_AGENT_CONFIG_PATH

_SCHEDULE_ID_RE = re.compile(r"^sch_[A-Za-z0-9]{6,}$")

_BULK_SCHEDULE_MARKERS = (
    "一周",
    "七天",
    "7天",
    "每天",
    "每日",
    "多个",
    "批量",
    "each day",
    "every day",
    "daily",
    "for a week",
    "whole week",
)


class ScheduleAgent:
    """Domain controller for schedule create, query, and delete."""

    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH) -> None:
        self.config_path = resolve_config_path(config_path)
        self.config = self._load_config(self.config_path)
        self.default_timezone_name = str(self.config.get("default_timezone_name", "UTC") or "UTC").strip() or "UTC"
        storage_root = self._resolve_storage_root(self.config.get("storage_dir"))
        self.store = ScheduleStore(storage_root=storage_root)
        self.service = ScheduleService(
            store=self.store,
            default_timezone_name=self.default_timezone_name,
        )
        self.execution_config = self._load_execution_config(self.config.get("execution"))

    @staticmethod
    def _load_config(path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"ScheduleAgent config not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"ScheduleAgent config must be a dict: {path}")
        return payload

    def _resolve_storage_root(self, raw_path: Any) -> Path:
        if raw_path is None or not str(raw_path).strip():
            return resolve_config_path("data/schedules")
        return resolve_related_config_path(self.config_path, raw_path)

    @staticmethod
    def _load_execution_config(raw: Any) -> Dict[str, Any]:
        defaults = {
            "query_limit_default": 10,
            "query_limit_max": 50,
        }
        if isinstance(raw, dict):
            defaults.update(raw)
        defaults["query_limit_default"] = max(1, int(defaults.get("query_limit_default", 10) or 10))
        defaults["query_limit_max"] = max(1, int(defaults.get("query_limit_max", 50) or 50))
        return defaults

    def handle_create_command(
        self,
        *,
        thread_id: str,
        due_at: str,
        deferred_objective: str = "",
        text: str = "",
        owner_id: Optional[str] = None,
        timezone_name: Optional[str] = None,
        now_context: Optional[Dict[str, Any]] = None,
        origin: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        safe_due_at = str(due_at or "").strip()
        safe_objective = str(deferred_objective or "").strip()
        legacy_text = str(text or "").strip()
        if safe_objective and legacy_text and safe_objective != legacy_text:
            return self._result(
                success=False,
                tool="schedule_create",
                action="create",
                answer="deferred_objective 与兼容字段 text 的内容冲突。",
                needs_clarification=True,
            )
        safe_objective = safe_objective or legacy_text
        if not safe_due_at:
            return self._result(
                success=False,
                tool="schedule_create",
                action="create",
                answer="请提供 due_at（ISO-8601 时间）。",
                needs_clarification=True,
            )
        if not safe_objective:
            return self._result(
                success=False,
                tool="schedule_create",
                action="create",
                answer="请提供 deferred_objective：日程到期后仍需要执行的目标。",
                needs_clarification=True,
            )

        scope = self._resolve_scope(thread_id=thread_id, owner_id=owner_id)
        effective_timezone = self._effective_timezone_name(timezone_name)
        parsed_due = parse_iso_due_at(
            safe_due_at,
            timezone_name=effective_timezone,
            now_context=now_context,
        )
        if parsed_due.due_local is None:
            return self._result(
                success=False,
                tool="schedule_create",
                action="create",
                answer="无法解析 due_at，请使用 ISO-8601 时间（如 2026-05-30T08:00:00+08:00）。",
                needs_clarification=True,
                machine={
                    "parse_error": parsed_due.error,
                    "timezone_name": effective_timezone,
                    "parse": parsed_due.to_payload(),
                },
            )

        item = self.service.create_schedule(
            owner_id=scope["owner_id"],
            thread_id=scope["thread_id"],
            due_at_utc=parsed_due.due_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            timezone_name=effective_timezone,
            deferred_objective=(safe_objective if str(deferred_objective or "").strip() else ""),
            text=(legacy_text if not str(deferred_objective or "").strip() else ""),
            origin=origin,
        )
        serialized = self.service.serialize_item(item)
        answer = f"已创建日程：{serialized['due_display']} {serialized['text']}。"
        if parsed_due.assumed_date:
            answer += " 我默认使用了最近的这个日期。"
        partial = self._text_looks_bulk(safe_objective)
        if partial:
            answer += " 本次仅创建 1 条日程；若用户要求多条/重复，请继续逐条创建。"
        return self._result(
            success=True,
            tool="schedule_create",
            action="create",
            answer=answer,
            item=serialized,
            count=1,
            partial=partial,
            schedule_id=serialized["schedule_id"],
            machine={
                "intent": "create",
                "owner_id": scope["owner_id"],
                "thread_id": scope["thread_id"],
                "timezone_name": effective_timezone,
                "parse": parsed_due.to_payload(),
            },
        )

    def handle_query_command(
        self,
        *,
        thread_id: str,
        keyword: str = "",
        start_at: str = "",
        end_at: str = "",
        owner_id: Optional[str] = None,
        timezone_name: Optional[str] = None,
        include_completed: bool = False,
        limit: Optional[int] = None,
        now_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        del now_context  # bounds are explicit ISO; kept for API compatibility
        safe_keyword = str(keyword or "").strip()
        scope = self._resolve_scope(thread_id=thread_id, owner_id=owner_id)
        effective_timezone = self._effective_timezone_name(timezone_name)
        safe_limit = max(
            1,
            min(
                self.execution_config["query_limit_max"],
                int(limit or self.execution_config["query_limit_default"]),
            ),
        )
        start_utc = self._parse_bound_utc(start_at, timezone_name=effective_timezone)
        end_utc = self._parse_bound_utc(end_at, timezone_name=effective_timezone)
        items = self.service.list_schedules(
            owner_id=scope["owner_id"],
            thread_id=None,
            statuses=None,
            keyword=safe_keyword,
            start_utc=start_utc,
            end_utc=end_utc,
            include_completed=bool(include_completed),
            limit=safe_limit,
        )
        serialized = [self.service.serialize_item(item) for item in items]
        schedule_ids = [str(row.get("schedule_id", "") or "").strip() for row in serialized if row.get("schedule_id")]
        if not serialized:
            return self._result(
                success=True,
                tool="schedule_query",
                action="list",
                answer="我没有找到符合条件的日程。",
                items=[],
                count=0,
                schedule_ids=[],
                machine={
                    "timezone_name": effective_timezone,
                    "owner_id": scope["owner_id"],
                    "thread_id": scope["thread_id"],
                    "keyword": safe_keyword,
                    "start_at": str(start_at or "").strip(),
                    "end_at": str(end_at or "").strip(),
                    "start_utc": start_utc,
                    "end_utc": end_utc,
                },
            )
        if safe_keyword or start_at or end_at:
            answer = f"我找到了 {len(serialized)} 条符合条件的日程。"
        else:
            answer = f"这里是你当前的 {len(serialized)} 条日程。"
        return self._result(
            success=True,
            tool="schedule_query",
            action="list",
            answer=answer,
            items=serialized,
            count=len(serialized),
            schedule_ids=schedule_ids,
            machine={
                "timezone_name": effective_timezone,
                "owner_id": scope["owner_id"],
                "thread_id": scope["thread_id"],
                "keyword": safe_keyword,
                "start_at": str(start_at or "").strip(),
                "end_at": str(end_at or "").strip(),
                "start_utc": start_utc,
                "end_utc": end_utc,
            },
        )

    def handle_delete_command(
        self,
        *,
        thread_id: str,
        schedule_id: str,
        owner_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        safe_id = str(schedule_id or "").strip()
        if not safe_id:
            return self._result(
                success=False,
                tool="schedule_delete",
                action="delete",
                answer="请提供 schedule_id。",
                needs_clarification=True,
            )
        if not _SCHEDULE_ID_RE.match(safe_id):
            return self._result(
                success=False,
                tool="schedule_delete",
                action="delete",
                answer=f"schedule_id 格式无效：{safe_id}（应为 sch_ 开头的 id）。",
                needs_clarification=True,
            )

        scope = self._resolve_scope(thread_id=thread_id, owner_id=owner_id)
        existing = self.service.store.find_by_id(safe_id, owner_id=scope["owner_id"])
        if existing is None:
            return self._result(
                success=False,
                tool="schedule_delete",
                action="delete",
                answer=f"未找到日程：{safe_id}。",
                needs_clarification=True,
            )

        try:
            canceled = self.service.cancel_schedule(
                owner_id=scope["owner_id"],
                thread_id=None,
                schedule_id=safe_id,
            )
        except FileNotFoundError:
            return self._result(
                success=False,
                tool="schedule_delete",
                action="delete",
                answer=f"未找到日程：{safe_id}。",
                needs_clarification=True,
            )

        serialized = self.service.serialize_item(canceled)
        return self._result(
            success=True,
            tool="schedule_delete",
            action="delete",
            answer=f"已删除日程：{serialized['due_display']} {serialized['text']}。",
            item=serialized,
            count=1,
            schedule_id=serialized["schedule_id"],
            machine={
                "intent": "delete",
                "owner_id": scope["owner_id"],
                "thread_id": scope["thread_id"],
                "target_schedule_id": safe_id,
            },
        )

    def _parse_bound_utc(self, raw: str, *, timezone_name: str) -> Optional[str]:
        safe = str(raw or "").strip()
        if not safe:
            return None
        try:
            parsed = datetime.fromisoformat(safe.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                tz, _, _ = resolve_timezone(timezone_name)
                parsed = parsed.replace(tzinfo=tz)
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            return None

    def _effective_timezone_name(self, timezone_name: Optional[str]) -> str:
        raw = str(timezone_name or "").strip() or self.default_timezone_name
        _, resolved_name, _ = resolve_timezone(raw)
        return resolved_name

    @staticmethod
    def _resolve_scope(*, thread_id: str, owner_id: Optional[str] = None) -> Dict[str, str]:
        safe_thread_id = str(thread_id or "").strip()
        safe_owner_id = str(owner_id or "").strip()
        if safe_owner_id:
            return {
                "owner_id": safe_owner_id,
                "thread_id": safe_thread_id,
            }
        if "::" in safe_thread_id:
            candidate_owner_id, _, _ = safe_thread_id.partition("::")
            candidate_owner_id = str(candidate_owner_id or "").strip()
            if candidate_owner_id:
                return {
                    "owner_id": candidate_owner_id,
                    "thread_id": safe_thread_id,
                }
        return {
            "owner_id": ANONYMOUS_OWNER_ID,
            "thread_id": safe_thread_id,
        }

    @staticmethod
    def _text_looks_bulk(text: str) -> bool:
        safe = str(text or "").strip().lower()
        if not safe:
            return False
        for marker in _BULK_SCHEDULE_MARKERS:
            if marker.lower() in safe:
                return True
        if re.search(r"\b\d+\s*(个|条|天)\b", safe):
            return True
        return False

    @staticmethod
    def _result(
        *,
        success: bool,
        tool: str,
        action: str,
        answer: str,
        needs_clarification: bool = False,
        item: Optional[Dict[str, Any]] = None,
        items: Optional[List[Dict[str, Any]]] = None,
        candidates: Optional[List[Dict[str, Any]]] = None,
        count: int = 0,
        partial: bool = False,
        schedule_id: Optional[str] = None,
        schedule_ids: Optional[List[str]] = None,
        machine: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolved_schedule_id = str(schedule_id or "").strip()
        if not resolved_schedule_id and isinstance(item, dict):
            resolved_schedule_id = str(item.get("schedule_id", "") or "").strip()
        resolved_schedule_ids: List[str] = []
        if schedule_ids is not None:
            resolved_schedule_ids = [str(value or "").strip() for value in schedule_ids if str(value or "").strip()]
        elif items:
            resolved_schedule_ids = [
                str(row.get("schedule_id", "") or "").strip()
                for row in items
                if isinstance(row, dict) and str(row.get("schedule_id", "") or "").strip()
            ]
        payload: Dict[str, Any] = {
            "success": bool(success),
            "tool": tool,
            "action": action,
            "answer": str(answer or "").strip(),
            "message": str(answer or "").strip(),
            "needs_clarification": bool(needs_clarification),
            "partial": bool(partial),
            "item": item,
            "items": list(items or []),
            "candidates": list(candidates or []),
            "count": max(0, int(count)),
            "machine": dict(machine or {}),
        }
        if resolved_schedule_id:
            payload["schedule_id"] = resolved_schedule_id
        if resolved_schedule_ids:
            payload["schedule_ids"] = resolved_schedule_ids
        return payload


def create_schedule_agent(config_path: str | Path = DEFAULT_CONFIG_PATH) -> ScheduleAgent:
    return ScheduleAgent(config_path=config_path)
