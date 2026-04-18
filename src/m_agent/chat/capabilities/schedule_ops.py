from __future__ import annotations

from typing import Any, Dict, Optional

from langchain.tools import tool

from m_agent.utils.time_utils import get_current_time_context

from .base import ControllerCapabilityContext, ControllerCapabilitySpec


def _build_schedule_manage_tool(context: ControllerCapabilityContext, description: str):
    @tool("schedule_manage", description=description)
    def schedule_manage(
        instruction: str,
        timezone_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create, update, or cancel schedules via ScheduleAgent."""

        effective_timezone_name = (
            str(timezone_name or context.tool_default("schedule_manage", "timezone_name") or "").strip() or None
        )
        safe_instruction = str(instruction or "").strip()
        params = {
            "instruction": safe_instruction,
            "timezone_name": effective_timezone_name,
        }
        call_id = context.start_tool_call("schedule_manage", params)
        limit_result = context.check_tool_call_limits("schedule_manage")
        if limit_result is not None:
            context.finish_tool_call(call_id, "schedule_manage", result=limit_result)
            return limit_result
        try:
            result = context.get_schedule_agent().handle_manage_command(
                thread_id=context.active_thread_id,
                instruction=safe_instruction,
                timezone_name=effective_timezone_name,
                now_context=get_current_time_context(effective_timezone_name),
            )
        except Exception as exc:
            context.finish_tool_call(call_id, "schedule_manage", error=str(exc))
            raise

        context.record_tool_use("schedule_manage", params, result)
        context.finish_tool_call(call_id, "schedule_manage", result=result)
        return result

    return schedule_manage


def _build_schedule_query_tool(context: ControllerCapabilityContext, description: str):
    @tool("schedule_query", description=description)
    def schedule_query(
        query: str = "",
        timezone_name: Optional[str] = None,
        include_completed: bool = False,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Query schedules via ScheduleAgent."""

        effective_timezone_name = (
            str(timezone_name or context.tool_default("schedule_query", "timezone_name") or "").strip() or None
        )
        safe_limit = limit
        if safe_limit is None:
            default_limit = context.tool_default("schedule_query", "limit")
            safe_limit = int(default_limit) if default_limit is not None else None
        params = {
            "query": str(query or "").strip(),
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
                query=params["query"],
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


SCHEDULE_MANAGE_CAPABILITY = ControllerCapabilitySpec(
    name="schedule_manage",
    build_tool=_build_schedule_manage_tool,
)

SCHEDULE_QUERY_CAPABILITY = ControllerCapabilitySpec(
    name="schedule_query",
    build_tool=_build_schedule_query_tool,
)
