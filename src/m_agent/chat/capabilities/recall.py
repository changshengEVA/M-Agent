from __future__ import annotations

import json
from typing import Any, Dict

from langchain.tools import tool

from .base import ControllerCapabilityContext, ControllerCapabilitySpec


_MEMORY_RECALL_TOOL_NAMES = ("shallow_recall", "deep_recall")
_MEMORY_RECALL_GROUP = "memory_recall"


def _build_shallow_recall_tool(context: ControllerCapabilityContext, description: str):
    @tool("shallow_recall", description=description)
    def shallow_recall(question: str) -> Dict[str, Any]:
        """Delegate ordinary memory lookup to MemoryAgent.shallow_recall."""

        params = {"question": str(question or "").strip()}
        call_id = context.start_tool_call("shallow_recall", params)
        limit_result = context.check_tool_call_limits(
            "shallow_recall",
            group_name=_MEMORY_RECALL_GROUP,
            group_tool_names=_MEMORY_RECALL_TOOL_NAMES,
        )
        if limit_result is not None:
            context.finish_tool_call(call_id, "shallow_recall", result=limit_result)
            return limit_result

        context.logger.info(
            "RECALL START: %s",
            json.dumps(
                {
                    "mode": "shallow_recall",
                    "question": params["question"],
                },
                ensure_ascii=False,
            ),
        )
        try:
            result = context.memory_agent.shallow_recall(
                question=question,
                thread_id=f"{context.active_thread_id}:shallow",
            )
        except Exception as exc:
            context.finish_tool_call(call_id, "shallow_recall", error=str(exc))
            raise
        context.logger.info(
            "RECALL DONE: %s",
            json.dumps(
                {
                    "mode": "shallow_recall",
                    "question": params["question"],
                    "answer": str(result.get("answer", "") or "").strip(),
                },
                ensure_ascii=False,
            ),
        )
        context.record_tool_use("shallow_recall", params, result)
        context.record_recall_result("shallow_recall", result)
        context.finish_tool_call(call_id, "shallow_recall", result=result)
        return result

    return shallow_recall


def _build_deep_recall_tool(context: ControllerCapabilityContext, description: str):
    @tool("deep_recall", description=description)
    def deep_recall(question: str) -> Dict[str, Any]:
        """Delegate complex memory reasoning to MemoryAgent.deep_recall."""

        params = {"question": str(question or "").strip()}
        call_id = context.start_tool_call("deep_recall", params)
        limit_result = context.check_tool_call_limits(
            "deep_recall",
            group_name=_MEMORY_RECALL_GROUP,
            group_tool_names=_MEMORY_RECALL_TOOL_NAMES,
        )
        if limit_result is not None:
            context.finish_tool_call(call_id, "deep_recall", result=limit_result)
            return limit_result

        context.logger.info(
            "RECALL START: %s",
            json.dumps(
                {
                    "mode": "deep_recall",
                    "question": params["question"],
                },
                ensure_ascii=False,
            ),
        )
        try:
            result = context.memory_agent.deep_recall(
                question=question,
                thread_id=f"{context.active_thread_id}:deep",
            )
        except Exception as exc:
            context.finish_tool_call(call_id, "deep_recall", error=str(exc))
            raise
        context.logger.info(
            "RECALL DONE: %s",
            json.dumps(
                {
                    "mode": "deep_recall",
                    "question": params["question"],
                    "answer": str(result.get("answer", "") or "").strip(),
                },
                ensure_ascii=False,
            ),
        )
        context.record_tool_use("deep_recall", params, result)
        context.record_recall_result("deep_recall", result)
        context.finish_tool_call(call_id, "deep_recall", result=result)
        return result

    return deep_recall


SHALLOW_RECALL_CAPABILITY = ControllerCapabilitySpec(
    name="shallow_recall",
    build_tool=_build_shallow_recall_tool,
)

DEEP_RECALL_CAPABILITY = ControllerCapabilitySpec(
    name="deep_recall",
    build_tool=_build_deep_recall_tool,
)
