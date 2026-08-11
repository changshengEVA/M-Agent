"""The user-visible reply must share the active Scene conversation segment."""
from __future__ import annotations

import logging
from typing import Any

from m_agent.systems.tools.base import ControllerCapabilityContext
from m_agent.systems.tools.default.capabilities.reply_to_user import (
    _build_reply_to_user_tool,
)


class _RecordingSceneWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def append(self, conversation_id: str, entry: Any) -> Any:
        self.calls.append((conversation_id, entry))
        return entry


def test_reply_is_appended_to_conversation_segment() -> None:
    writer = _RecordingSceneWriter()
    context = ControllerCapabilityContext(
        active_thread_id="owner::thread",
        recall_state={},
        controller_state={
            "runtime": {
                "conversation_id": "owner::thread::7",
                "transaction_id": "txn-1",
                "scene_writer": writer,
            }
        },
        tool_defaults={},
        logger=logging.getLogger(__name__),
    )
    reply = _build_reply_to_user_tool(context, "Reply to the user")

    result = reply.invoke({"message": "Hello", "finalize": True})

    assert result["success"] is True
    assert len(writer.calls) == 1
    conversation_id, entry = writer.calls[0]
    assert conversation_id == "owner::thread::7"
    assert entry.actor.value == "assistant"
    assert entry.actor_name == "Agent"
    assert entry.entry_type.value == "Action"
    assert entry.tool_name == "reply_to_user"
    assert entry.text == "Hello"
    assert context.controller_state["history"] == [
        {
            "tool_name": "reply_to_user",
            "params": {"message": "Hello", "finalize": True},
            "result": {
                "success": True,
                "message": "Hello",
                "finalize": True,
            },
        }
    ]
