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
        deferred_objective: Annotated[
            str,
            Field(
                description=(
                    "The still-unfinished objective to activate when the due time arrives. "
                    "Describe what the agent must do then; do not state that it already happened, "
                    "and do not write the final assistant reply."
                )
            ),
        ],
        timezone_name: Annotated[
            Optional[str],
            Field(description="IANA timezone; omit to use configured default"),
        ] = None,
    ) -> Dict[str, Any]:
        """Create one schedule with an explicit due time and deferred objective."""

        effective_timezone_name = (
            str(timezone_name or context.tool_default("schedule_create", "timezone_name") or "").strip() or None
        )
        params = {
            "due_at": str(due_at or "").strip(),
            "deferred_objective": str(deferred_objective or "").strip(),
            "timezone_name": effective_timezone_name,
        }
        call_id = context.start_tool_call("schedule_create", params)
        limit_result = context.check_tool_call_limits("schedule_create")
        if limit_result is not None:
            context.finish_tool_call(call_id, "schedule_create", result=limit_result)
            return limit_result
        try:
            hooks = context.controller_state.get("runtime")
            hooks = hooks if isinstance(hooks, dict) else {}
            result = context.get_schedule_agent().handle_create_command(
                thread_id=context.active_thread_id,
                due_at=params["due_at"],
                deferred_objective=params["deferred_objective"],
                timezone_name=effective_timezone_name,
                now_context=get_current_time_context(effective_timezone_name),
                origin={
                    "transaction_id": str(hooks.get("transaction_id", "") or "").strip(),
                    "conversation_id": str(hooks.get("conversation_id", "") or "").strip(),
                },
            )
        except Exception as exc:
            context.finish_tool_call(call_id, "schedule_create", error=str(exc))
            raise

        on_schedule_created = hooks.get("on_schedule_created")
        if callable(on_schedule_created) and bool(result.get("success", False)):
            item = result.get("item")
            item = dict(item) if isinstance(item, dict) else {}
            machine = result.get("machine")
            machine = dict(machine) if isinstance(machine, dict) else {}
            try:
                on_schedule_created(
                    transaction_id=str(
                        hooks.get("transaction_id", "") or ""
                    ).strip(),
                    schedule_id=str(
                        result.get("schedule_id", "")
                        or item.get("schedule_id", "")
                        or ""
                    ).strip(),
                    due_at=str(item.get("due_at_utc", "") or "").strip(),
                    owner_id=str(machine.get("owner_id", "") or "").strip(),
                    result=result,
                )
                machine["transaction_binding"] = "recorded"
            except Exception as exc:
                # The durable product schedule still carries its origin and
                # can be reconciled by heartbeat. Surface the degraded bind
                # without pretending that schedule creation itself failed.
                machine["transaction_binding"] = "deferred"
                machine["transaction_binding_error"] = str(exc)
            result["machine"] = machine

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
                description="Optional keyword matched against schedule text or schedule id; empty matches all schedules",
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
    delivery_guarantee="at_most_once",
)

SCHEDULE_QUERY_CAPABILITY = ControllerCapabilitySpec(
    name="schedule_query",
    build_tool=_build_schedule_query_tool,
    delivery_guarantee="idempotent",
)

SCHEDULE_DELETE_CAPABILITY = ControllerCapabilitySpec(
    name="schedule_delete",
    build_tool=_build_schedule_delete_tool,
    delivery_guarantee="idempotent",
)


__all__ = [
    "SCHEDULE_CREATE_CAPABILITY",
    "SCHEDULE_QUERY_CAPABILITY",
    "SCHEDULE_DELETE_CAPABILITY",
]
