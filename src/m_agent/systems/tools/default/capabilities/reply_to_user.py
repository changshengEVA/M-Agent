"""User-visible reply tool (Runtime external output)."""
from __future__ import annotations

from typing import Any, Dict

from langchain.tools import tool

from ...base import ControllerCapabilityContext, ControllerCapabilitySpec


def _runtime_hooks(context: ControllerCapabilityContext) -> Dict[str, Any]:
    state = context.controller_state.get("runtime")
    return state if isinstance(state, dict) else {}


def _build_reply_to_user_tool(context: ControllerCapabilityContext, description: str):
    @tool("reply_to_user", description=description)
    def reply_to_user(message: str, finalize: bool = True) -> Dict[str, Any]:
        """Send the user-visible assistant message (Runtime only)."""

        text = str(message or "").strip()
        params = {"message": text, "finalize": bool(finalize)}
        call_id = context.start_tool_call("reply_to_user", params)
        limit_result = context.check_tool_call_limits("reply_to_user")
        if limit_result is not None:
            context.finish_tool_call(
                call_id,
                "reply_to_user",
                result=limit_result,
            )
            return limit_result
        if not text:
            result = {"success": False, "error": "message must be non-empty"}
            context.record_tool_use("reply_to_user", params, result)
            context.finish_tool_call(call_id, "reply_to_user", result=result)
            return result

        hooks = _runtime_hooks(context)
        on_reply = hooks.get("on_reply")
        scene_writer = hooks.get("scene_writer")
        transaction_id = str(hooks.get("transaction_id", "") or "").strip()
        delegate_id = str(hooks.get("delegate_id", "") or "").strip()
        conversation_id = (
            str(hooks.get("conversation_id", "") or "").strip()
            or context.active_thread_id
        )

        try:
            if callable(on_reply):
                on_reply(text, finalize=bool(finalize))

            if scene_writer is not None and transaction_id:
                from m_agent.api.chat_api_shared import _now_iso
                from m_agent.runtime.domain.contracts import (
                    SceneActor,
                    SceneEntry,
                    SceneEntryType,
                )

                scene_writer.append(
                    conversation_id,
                    SceneEntry(
                        seq=0,
                        occurred_at=_now_iso(),
                        entry_type=SceneEntryType.REPLY,
                        actor=SceneActor.ASSISTANT,
                        text=text,
                        transaction_id=transaction_id or None,
                        delegate_id=delegate_id or None,
                        tool_name="reply_to_user",
                    ),
                )
        except Exception as exc:
            context.finish_tool_call(call_id, "reply_to_user", error=str(exc))
            raise

        result = {
            "success": True,
            "message": text,
            "finalize": bool(finalize),
        }
        context.record_tool_use("reply_to_user", params, result)
        context.finish_tool_call(call_id, "reply_to_user", result=result)
        return result

    return reply_to_user


REPLY_TO_USER_CAPABILITY = ControllerCapabilitySpec(
    name="reply_to_user",
    build_tool=_build_reply_to_user_tool,
    delivery_guarantee="at_most_once",
)
