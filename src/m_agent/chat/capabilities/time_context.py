from __future__ import annotations

from typing import Dict, Optional

from langchain.tools import tool

from m_agent.utils.time_utils import get_current_time_context

from .base import ControllerCapabilityContext, ControllerCapabilitySpec


def _build_get_current_time_tool(context: ControllerCapabilityContext, description: str):
    @tool("get_current_time", description=description)
    def get_current_time(timezone_name: Optional[str] = None) -> Dict[str, object]:
        """Delegate current-time lookup to the shared time utility."""

        effective_timezone_name = (
            str(timezone_name or context.tool_default("get_current_time", "timezone_name") or "").strip() or None
        )
        params = {"timezone_name": effective_timezone_name}
        call_id = context.start_tool_call("get_current_time", params)
        try:
            result = get_current_time_context(effective_timezone_name)
        except Exception as exc:
            context.finish_tool_call(call_id, "get_current_time", error=str(exc))
            raise

        context.record_tool_use("get_current_time", params, result)
        context.finish_tool_call(call_id, "get_current_time", result=result)
        return result

    return get_current_time


GET_CURRENT_TIME_CAPABILITY = ControllerCapabilitySpec(
    name="get_current_time",
    build_tool=_build_get_current_time_tool,
)
