"""User-visible reply tool (Think-life external output)."""
from __future__ import annotations

from typing import Any, Dict

from langchain.tools import tool

from ...base import ControllerCapabilityContext, ControllerCapabilitySpec


def _think_life_hooks(context: ControllerCapabilityContext) -> Dict[str, Any]:
    state = context.controller_state.get("think_life")
    return state if isinstance(state, dict) else {}


def _build_reply_to_user_tool(context: ControllerCapabilityContext, description: str):
    @tool("reply_to_user", description=description)
    def reply_to_user(message: str, finalize: bool = True) -> Dict[str, Any]:
        """Send the user-visible assistant message (Think-life only)."""

        text = str(message or "").strip()
        if not text:
            return {"success": False, "error": "message must be non-empty"}

        hooks = _think_life_hooks(context)
        on_reply = hooks.get("on_reply")
        scene_writer = hooks.get("scene_writer")
        transaction_id = str(hooks.get("transaction_id", "") or "").strip()
        delegate_id = str(hooks.get("delegate_id", "") or "").strip()
        conversation_id = (
            str(hooks.get("conversation_id", "") or "").strip()
            or context.active_thread_id
        )

        if callable(on_reply):
            on_reply(text, finalize=bool(finalize))

        if scene_writer is not None and transaction_id:
            from m_agent.api.chat_api_shared import _now_iso
            from m_agent.runtime.think_life.contracts import (
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

        return {
            "success": True,
            "message": text,
            "finalize": bool(finalize),
        }

    return reply_to_user


REPLY_TO_USER_CAPABILITY = ControllerCapabilitySpec(
    name="reply_to_user",
    build_tool=_build_reply_to_user_tool,
    delivery_guarantee="at_most_once",
)
