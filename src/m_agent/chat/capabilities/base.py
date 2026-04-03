from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


ControllerToolBuilder = Callable[["ControllerCapabilityContext", str], Any]


@dataclass(frozen=True)
class ControllerCapabilitySpec:
    name: str
    build_tool: ControllerToolBuilder


@dataclass
class ControllerCapabilityContext:
    active_thread_id: str
    memory_agent: Any
    recall_state: Dict[str, Any]
    controller_state: Dict[str, Any]
    tool_defaults: Dict[str, Dict[str, Any]]
    logger: logging.Logger
    email_agent_provider: Optional[Callable[[], Any]] = None

    def tool_default(self, tool_name: str, key: str, default: Any = None) -> Any:
        tool_config = self.tool_defaults.get(tool_name)
        if not isinstance(tool_config, dict):
            return default
        return tool_config.get(key, default)

    def next_call_id(self) -> int:
        next_call_id = int(self.controller_state.get("call_seq", 0) or 0) + 1
        self.controller_state["call_seq"] = next_call_id
        return next_call_id

    def start_tool_call(self, tool_name: str, params: Dict[str, Any]) -> int:
        call_id = self.next_call_id()
        self.logger.info(
            "TOOL CALL DETAIL: %s",
            json.dumps(
                {
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "status": "started",
                    "params": params,
                },
                ensure_ascii=False,
            ),
        )
        return call_id

    def finish_tool_call(
        self,
        call_id: int,
        tool_name: str,
        *,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        payload = {
            "call_id": call_id,
            "tool_name": tool_name,
            "status": "completed" if not error else "failed",
            "result": result,
        }
        if error:
            payload["error"] = error
        self.logger.info("TOOL RESULT DETAIL: %s", json.dumps(payload, ensure_ascii=False))

    def record_tool_use(self, tool_name: str, params: Dict[str, Any], result: Any) -> None:
        history = self.controller_state.setdefault("history", [])
        if not isinstance(history, list):
            history = []
            self.controller_state["history"] = history
        history.append(
            {
                "tool_name": tool_name,
                "params": params,
                "result": result,
            }
        )

    def record_recall_result(self, mode: str, result: Dict[str, Any]) -> None:
        self.recall_state["mode"] = mode
        self.recall_state["result"] = result
        history = self.recall_state.setdefault("history", [])
        if not isinstance(history, list):
            history = []
            self.recall_state["history"] = history
        history.append({"mode": mode, "result": result})

    def get_email_agent(self) -> Any:
        provider = self.email_agent_provider
        if provider is None:
            raise RuntimeError("Email tool is unavailable because email_agent_provider is not configured.")
        return provider()
