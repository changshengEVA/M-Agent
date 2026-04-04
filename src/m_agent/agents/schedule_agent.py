from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from m_agent.config_paths import DEFAULT_SCHEDULE_AGENT_CONFIG_PATH, resolve_config_path, resolve_related_config_path
from m_agent.schedule.parsing import parse_day_window, parse_due_datetime
from m_agent.schedule.service import ScheduleService
from m_agent.schedule.store import ANONYMOUS_OWNER_ID, ScheduleStore
from m_agent.utils.time_utils import resolve_timezone


DEFAULT_CONFIG_PATH = DEFAULT_SCHEDULE_AGENT_CONFIG_PATH

_CANCEL_MARKERS = ("取消", "删掉", "删除", "移除", "不要")
_UPDATE_CONNECTORS = ("改到", "改成", "改为", "调整到", "换到", "挪到", "推迟到", "延后到", "提前到")
_REMINDER_MARKERS = ("提醒我", "记得", "安排", "设个提醒", "设置提醒", "remind me to", "schedule")


class ScheduleAgent:
    """Domain controller for schedule management and query."""

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
            "target_candidate_limit": 5,
        }
        if isinstance(raw, dict):
            defaults.update(raw)
        defaults["query_limit_default"] = max(1, int(defaults.get("query_limit_default", 10) or 10))
        defaults["query_limit_max"] = max(1, int(defaults.get("query_limit_max", 50) or 50))
        defaults["target_candidate_limit"] = max(1, int(defaults.get("target_candidate_limit", 5) or 5))
        return defaults

    def handle_manage_command(
        self,
        *,
        thread_id: str,
        owner_id: Optional[str] = None,
        instruction: str,
        timezone_name: Optional[str] = None,
        now_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        safe_instruction = str(instruction or "").strip()
        if not safe_instruction:
            return self._result(
                success=False,
                tool="schedule_manage",
                action="clarify",
                answer="请提供要管理的日程指令。",
                needs_clarification=True,
            )

        scope = self._resolve_scope(thread_id=thread_id, owner_id=owner_id)
        effective_timezone = self._effective_timezone_name(timezone_name)
        intent = self._route_manage_intent(safe_instruction)
        if intent == "cancel":
            return self._handle_cancel(
                owner_id=scope["owner_id"],
                thread_id=scope["thread_id"],
                instruction=safe_instruction,
                timezone_name=effective_timezone,
                now_context=now_context,
            )
        if intent == "update":
            return self._handle_update(
                owner_id=scope["owner_id"],
                thread_id=scope["thread_id"],
                instruction=safe_instruction,
                timezone_name=effective_timezone,
                now_context=now_context,
            )
        return self._handle_create(
            owner_id=scope["owner_id"],
            thread_id=scope["thread_id"],
            instruction=safe_instruction,
            timezone_name=effective_timezone,
            now_context=now_context,
        )

    def handle_query_command(
        self,
        *,
        thread_id: str,
        owner_id: Optional[str] = None,
        query: str = "",
        timezone_name: Optional[str] = None,
        include_completed: bool = False,
        limit: Optional[int] = None,
        now_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        safe_query = str(query or "").strip()
        scope = self._resolve_scope(thread_id=thread_id, owner_id=owner_id)
        effective_timezone = self._effective_timezone_name(timezone_name)
        safe_limit = max(
            1,
            min(
                self.execution_config["query_limit_max"],
                int(limit or self.execution_config["query_limit_default"]),
            ),
        )
        query_requests_all = include_completed or any(token in safe_query for token in ("全部", "所有", "all"))
        statuses = self._query_statuses(safe_query, include_completed=include_completed)
        window = parse_day_window(
            safe_query,
            timezone_name=effective_timezone,
            now_context=now_context,
        )
        keyword = self._query_keyword(safe_query)
        items = self.service.list_schedules(
            owner_id=scope["owner_id"],
            thread_id=None,
            statuses=statuses,
            keyword=keyword,
            start_utc=window["start_utc"] if window else None,
            end_utc=window["end_utc"] if window else None,
            include_completed=query_requests_all or bool(statuses),
            limit=safe_limit,
        )
        serialized = [self.service.serialize_item(item) for item in items]
        if not serialized:
            return self._result(
                success=True,
                tool="schedule_query",
                action="list",
                answer="我没有找到符合条件的日程。",
                items=[],
                count=0,
                machine={
                    "timezone_name": effective_timezone,
                    "owner_id": scope["owner_id"],
                    "thread_id": scope["thread_id"],
                    "scope": "owner",
                    "query": safe_query,
                    "keyword": keyword,
                    "window": self._serialize_window(window),
                    "statuses": list(statuses or []),
                },
            )
        if safe_query:
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
            machine={
                "timezone_name": effective_timezone,
                "owner_id": scope["owner_id"],
                "thread_id": scope["thread_id"],
                "scope": "owner",
                "query": safe_query,
                "keyword": keyword,
                "window": self._serialize_window(window),
                "statuses": list(statuses or []),
            },
        )

    def _handle_create(
        self,
        *,
        owner_id: str,
        thread_id: str,
        instruction: str,
        timezone_name: str,
        now_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        parsed_due = parse_due_datetime(
            instruction,
            timezone_name=timezone_name,
            now_context=now_context,
        )
        if parsed_due.due_local is None:
            return self._result(
                success=False,
                tool="schedule_manage",
                action="create",
                answer="我还缺少一个明确的时间点，比如“明天上午九点”。",
                needs_clarification=True,
                machine={
                    "intent": "create",
                    "parse_error": parsed_due.error,
                    "timezone_name": timezone_name,
                },
            )

        title = self._infer_title(instruction, matched_time_text=parsed_due.matched_text)
        item = self.service.create_schedule(
            owner_id=owner_id,
            thread_id=thread_id,
            title=title,
            due_at_utc=parsed_due.due_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            timezone_name=timezone_name,
            original_time_text=parsed_due.matched_text or instruction,
            action_type="chat_prompt",
            action_payload={
                "prompt": title,
                "source": "schedule",
                "hidden_context": {
                    "created_from_user_text": instruction,
                    "trigger_kind": "time_due",
                },
            },
            source_text=instruction,
            metadata={
                "assumptions": dict(parsed_due.assumptions or {}),
            },
        )
        serialized = self.service.serialize_item(item)
        answer = f"已创建日程：{serialized['due_display']} {serialized['title']}。"
        if parsed_due.assumed_date:
            answer += " 我默认使用了最近的这个日期。"
        return self._result(
            success=True,
            tool="schedule_manage",
            action="create",
            answer=answer,
            item=serialized,
            count=1,
            machine={
                "intent": "create",
                "owner_id": owner_id,
                "thread_id": thread_id,
                "timezone_name": timezone_name,
                "parse": parsed_due.to_payload(),
            },
        )

    def _handle_update(
        self,
        *,
        owner_id: str,
        thread_id: str,
        instruction: str,
        timezone_name: str,
        now_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        split_payload = self._split_update_instruction(instruction)
        target_text = split_payload.get("target_text", "")
        new_time_text = split_payload.get("new_time_text", "")
        if not new_time_text:
            return self._result(
                success=False,
                tool="schedule_manage",
                action="update",
                answer="我还不知道你想改到什么时间，请补充一个明确的新时间。",
                needs_clarification=True,
            )
        candidates = self.service.resolve_schedule_targets(
            owner_id=owner_id,
            thread_id=None,
            target_text=target_text,
            statuses=None,
            limit=self.execution_config["target_candidate_limit"],
            timezone_name=timezone_name,
            now_context=now_context,
        )
        if not candidates:
            return self._result(
                success=False,
                tool="schedule_manage",
                action="update",
                answer="我没有找到你要修改的日程。",
                needs_clarification=True,
                candidates=[],
            )
        if len(candidates) > 1:
            return self._result(
                success=False,
                tool="schedule_manage",
                action="update",
                answer=f"我找到了 {len(candidates)} 个候选日程，请确认你要修改哪一个。",
                needs_clarification=True,
                candidates=[self.service.serialize_item(item) for item in candidates],
            )
        target = candidates[0]
        target_local_date = self._item_local_datetime(target).date()
        parsed_due = parse_due_datetime(
            new_time_text,
            timezone_name=timezone_name,
            now_context=now_context,
            default_date=target_local_date,
        )
        if parsed_due.due_local is None:
            return self._result(
                success=False,
                tool="schedule_manage",
                action="update",
                answer="我还缺少一个明确的新时间，比如“改到后天下午三点”。",
                needs_clarification=True,
            )
        updated = self.service.update_schedule(
            owner_id=owner_id,
            thread_id=None,
            schedule_id=target.schedule_id,
            due_at_utc=parsed_due.due_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            timezone_name=timezone_name,
            original_time_text=parsed_due.matched_text or new_time_text,
            source_text=instruction,
            action_payload_patch={
                "prompt": target.title,
                "hidden_context": {
                    "created_from_user_text": target.source_text,
                    "updated_from_user_text": instruction,
                    "trigger_kind": "time_due",
                },
            },
            metadata_patch={
                "updated_from": instruction,
            },
        )
        serialized = self.service.serialize_item(updated)
        return self._result(
            success=True,
            tool="schedule_manage",
            action="update",
            answer=f"已更新日程：{serialized['due_display']} {serialized['title']}。",
            item=serialized,
            count=1,
            machine={
                "intent": "update",
                "owner_id": owner_id,
                "thread_id": thread_id,
                "timezone_name": timezone_name,
                "target_schedule_id": target.schedule_id,
                "parse": parsed_due.to_payload(),
            },
        )

    def _handle_cancel(
        self,
        *,
        owner_id: str,
        thread_id: str,
        instruction: str,
        timezone_name: str,
        now_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        target_text = self._extract_cancel_target(instruction)
        candidates = self.service.resolve_schedule_targets(
            owner_id=owner_id,
            thread_id=None,
            target_text=target_text,
            statuses=None,
            limit=self.execution_config["target_candidate_limit"],
            timezone_name=timezone_name,
            now_context=now_context,
        )
        if not candidates:
            return self._result(
                success=False,
                tool="schedule_manage",
                action="cancel",
                answer="我没有找到你要取消的日程。",
                needs_clarification=True,
                candidates=[],
            )
        if len(candidates) > 1:
            return self._result(
                success=False,
                tool="schedule_manage",
                action="cancel",
                answer=f"我找到了 {len(candidates)} 个候选日程，请确认你要取消哪一个。",
                needs_clarification=True,
                candidates=[self.service.serialize_item(item) for item in candidates],
            )
        canceled = self.service.cancel_schedule(
            owner_id=owner_id,
            thread_id=None,
            schedule_id=candidates[0].schedule_id,
            source_text=instruction,
        )
        serialized = self.service.serialize_item(canceled)
        return self._result(
            success=True,
            tool="schedule_manage",
            action="cancel",
            answer=f"已取消日程：{serialized['due_display']} {serialized['title']}。",
            item=serialized,
            count=1,
            machine={
                "intent": "cancel",
                "owner_id": owner_id,
                "thread_id": thread_id,
                "timezone_name": timezone_name,
                "target_schedule_id": candidates[0].schedule_id,
            },
        )

    def _route_manage_intent(self, instruction: str) -> str:
        safe = str(instruction or "").strip()
        if any(marker in safe for marker in _CANCEL_MARKERS):
            return "cancel"
        if any(marker in safe for marker in _UPDATE_CONNECTORS):
            return "update"
        return "create"

    @staticmethod
    def _split_update_instruction(instruction: str) -> Dict[str, str]:
        safe = str(instruction or "").strip()
        normalized = safe
        if normalized.startswith("把"):
            normalized = normalized[1:].strip()
        for connector in _UPDATE_CONNECTORS:
            if connector not in normalized:
                continue
            left, right = normalized.split(connector, 1)
            return {
                "target_text": left.strip(),
                "new_time_text": right.strip(),
                "connector": connector,
            }
        return {"target_text": normalized, "new_time_text": "", "connector": ""}

    @staticmethod
    def _extract_cancel_target(instruction: str) -> str:
        safe = str(instruction or "").strip()
        normalized = safe
        if normalized.startswith("把"):
            normalized = normalized[1:].strip()
        for marker in _CANCEL_MARKERS:
            normalized = normalized.replace(marker, " ")
        return re.sub(r"\s+", " ", normalized).strip()

    def _infer_title(self, instruction: str, *, matched_time_text: str) -> str:
        safe = str(instruction or "").strip()
        candidate = safe
        if matched_time_text:
            candidate = candidate.replace(matched_time_text, " ", 1)
            for token in matched_time_text.split():
                if token:
                    candidate = candidate.replace(token, " ", 1)
        for marker in _REMINDER_MARKERS:
            candidate = candidate.replace(marker, " ")
        candidate = re.sub(r"[，。！？,.;:]+", " ", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if candidate:
            return candidate
        return safe

    def _query_statuses(self, query: str, *, include_completed: bool) -> Optional[Sequence[str]]:
        safe = str(query or "").strip()
        if include_completed or any(token in safe for token in ("全部", "所有", "all")):
            return None
        if any(token in safe for token in ("已完成", "完成", "done", "completed")):
            return ["done"]
        if any(token in safe for token in ("已取消", "取消的", "canceled", "cancelled")):
            return ["canceled"]
        if any(token in safe for token in ("失败", "failed")):
            return ["failed"]
        return None

    def _query_keyword(self, query: str) -> str:
        safe = str(query or "").strip()
        if not safe:
            return ""
        candidate = re.sub(r"\b(all|today|tomorrow)\b", " ", safe, flags=re.IGNORECASE)
        for token in ("今天", "明天", "后天", "全部", "所有", "已完成", "已取消"):
            candidate = candidate.replace(token, " ")
        candidate = re.sub(r"[，。！？、,.;:()\[\]{}<>]+", " ", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if len(candidate) <= 1:
            return ""
        return candidate

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
    def _serialize_window(window: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(window, dict):
            return None
        return {
            "matched_text": window.get("matched_text"),
            "start_utc": window.get("start_utc"),
            "end_utc": window.get("end_utc"),
        }

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
        machine: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "success": bool(success),
            "tool": tool,
            "action": action,
            "answer": str(answer or "").strip(),
            "message": str(answer or "").strip(),
            "needs_clarification": bool(needs_clarification),
            "item": item,
            "items": list(items or []),
            "candidates": list(candidates or []),
            "count": max(0, int(count)),
            "machine": dict(machine or {}),
        }

    @staticmethod
    def _item_local_datetime(item: Any) -> datetime:
        tz, _, _ = resolve_timezone(getattr(item, "timezone_name", "UTC"))
        due_utc = datetime.fromisoformat(str(getattr(item, "due_at_utc", "")).replace("Z", "+00:00"))
        return due_utc.astimezone(tz)


def create_schedule_agent(config_path: str | Path = DEFAULT_CONFIG_PATH) -> ScheduleAgent:
    return ScheduleAgent(config_path=config_path)
