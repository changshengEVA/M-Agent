"""Schedule capabilities — create, query, delete via ``ScheduleAgent``."""
from __future__ import annotations

from typing import Annotated, Any, Dict, Optional

from langchain.tools import tool
from pydantic import Field

from m_agent.utils.time_utils import get_current_time_context

from ...base import ControllerCapabilityContext, ControllerCapabilitySpec


def _build_schedule_create_tool(context: ControllerCapabilityContext, description: str):
    @tool("schedule_create", description=description)
    def schedule_create(
        due_at: Annotated[
            str,
            Field(description="ISO-8601 due time (UTC Z or offset), e.g. 2026-05-30T08:00:00+08:00"),
        ],
        action: Annotated[
            str,
            Field(description="Concrete action or reminder text at due time (becomes schedule title)"),
        ],
        timezone_name: Annotated[
            Optional[str],
            Field(description="IANA timezone; omit to use configured default"),
        ] = None,
    ) -> Dict[str, Any]:
        """Create one schedule with explicit due time and action."""

        effective_timezone_name = (
            str(timezone_name or context.tool_default("schedule_create", "timezone_name") or "").strip() or None
        )
        params = {
            "due_at": str(due_at or "").strip(),
            "action": str(action or "").strip(),
            "timezone_name": effective_timezone_name,
        }
        call_id = context.start_tool_call("schedule_create", params)
        limit_result = context.check_tool_call_limits("schedule_create")
        if limit_result is not None:
            context.finish_tool_call(call_id, "schedule_create", result=limit_result)
            return limit_result
        try:
            result = context.get_schedule_agent().handle_create_command(
                thread_id=context.active_thread_id,
                due_at=params["due_at"],
                action=params["action"],
                timezone_name=effective_timezone_name,
                now_context=get_current_time_context(effective_timezone_name),
            )
        except Exception as exc:
            context.finish_tool_call(call_id, "schedule_create", error=str(exc))
            raise

        context.record_tool_use("schedule_create", params, result)
        context.finish_tool_call(call_id, "schedule_create", result=result)
        return result

    return schedule_create


def _build_schedule_query_tool(context: ControllerCapabilityContext, description: str):
    @tool("schedule_query", description=description)
    def schedule_query(
        keyword: Annotated[
            str,
            Field(
                default="",
                description="Optional keyword matched against title/source_text; empty matches all titles",
            ),
        ] = "",
        start_at: Annotated[
            str,
            Field(
                default="",
                description="Optional range start (ISO-8601); empty means no lower bound",
            ),
        ] = "",
        end_at: Annotated[
            str,
            Field(
                default="",
                description="Optional range end (ISO-8601); empty means no upper bound",
            ),
        ] = "",
        timezone_name: Annotated[
            Optional[str],
            Field(description="Timezone for parsing start_at/end_at when offset omitted"),
        ] = None,
        include_completed: Annotated[
            bool,
            Field(description="Include done/canceled/failed schedules when true"),
        ] = False,
        limit: Annotated[
            Optional[int],
            Field(description="Max items to return; omit for configured default"),
        ] = None,
    ) -> Dict[str, Any]:
        """Query schedules by keyword and optional time range."""

        effective_timezone_name = (
            str(timezone_name or context.tool_default("schedule_query", "timezone_name") or "").strip() or None
        )
        safe_limit = limit
        if safe_limit is None:
            default_limit = context.tool_default("schedule_query", "limit")
            safe_limit = int(default_limit) if default_limit is not None else None
        params = {
            "keyword": str(keyword or "").strip(),
            "start_at": str(start_at or "").strip(),
            "end_at": str(end_at or "").strip(),
            "timezone_name": effective_timezone_name,
            "include_completed": bool(include_completed),
            "limit": safe_limit,
        }
        call_id = context.start_tool_call("schedule_query", params)
        limit_result = context.check_tool_call_limits("schedule_query")
        if limit_result is not None:
            context.finish_tool_call(call_id, "schedule_query", result=limit_result)
            return limit_result
        try:
            result = context.get_schedule_agent().handle_query_command(
                thread_id=context.active_thread_id,
                keyword=params["keyword"],
                start_at=params["start_at"],
                end_at=params["end_at"],
                timezone_name=effective_timezone_name,
                include_completed=bool(include_completed),
                limit=safe_limit,
                now_context=get_current_time_context(effective_timezone_name),
            )
        except Exception as exc:
            context.finish_tool_call(call_id, "schedule_query", error=str(exc))
            raise

        context.record_tool_use("schedule_query", params, result)
        context.finish_tool_call(call_id, "schedule_query", result=result)
        return result

    return schedule_query


def _build_schedule_delete_tool(context: ControllerCapabilityContext, description: str):
    @tool("schedule_delete", description=description)
    def schedule_delete(
        schedule_id: Annotated[
            str,
            Field(description="Schedule id from schedule_create or schedule_query (e.g. sch_abc123)"),
        ],
    ) -> Dict[str, Any]:
        """Cancel/delete one schedule by id."""

        safe_id = str(schedule_id or "").strip()
        params = {"schedule_id": safe_id}
        call_id = context.start_tool_call("schedule_delete", params)
        limit_result = context.check_tool_call_limits("schedule_delete")
        if limit_result is not None:
            context.finish_tool_call(call_id, "schedule_delete", result=limit_result)
            return limit_result
        try:
            result = context.get_schedule_agent().handle_delete_command(
                thread_id=context.active_thread_id,
                schedule_id=safe_id,
            )
        except Exception as exc:
            context.finish_tool_call(call_id, "schedule_delete", error=str(exc))
            raise

        context.record_tool_use("schedule_delete", params, result)
        context.finish_tool_call(call_id, "schedule_delete", result=result)
        return result

    return schedule_delete


SCHEDULE_CREATE_CAPABILITY = ControllerCapabilitySpec(
    name="schedule_create",
    build_tool=_build_schedule_create_tool,
)

SCHEDULE_QUERY_CAPABILITY = ControllerCapabilitySpec(
    name="schedule_query",
    build_tool=_build_schedule_query_tool,
)

SCHEDULE_DELETE_CAPABILITY = ControllerCapabilitySpec(
    name="schedule_delete",
    build_tool=_build_schedule_delete_tool,
)


__all__ = [
    "SCHEDULE_CREATE_CAPABILITY",
    "SCHEDULE_QUERY_CAPABILITY",
    "SCHEDULE_DELETE_CAPABILITY",
]
