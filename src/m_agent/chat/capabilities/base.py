from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional, Sequence


ControllerToolBuilder = Callable[["ControllerCapabilityContext", str], Any]
_CONTROLLER_LIMIT_KEY = "__controller__"


def _normalize_positive_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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
    schedule_agent_provider: Optional[Callable[[], Any]] = None

    def tool_default(self, tool_name: str, key: str, default: Any = None) -> Any:
        tool_config = self.tool_defaults.get(tool_name)
        if not isinstance(tool_config, dict):
            return default
        return tool_config.get(key, default)

    def _history(self) -> list[Dict[str, Any]]:
        history = self.controller_state.get("history")
        if isinstance(history, list):
            return history
        history = []
        self.controller_state["history"] = history
        return history

    def tool_use_count(self, tool_names: Optional[Iterable[str]] = None) -> int:
        history = self._history()
        if tool_names is None:
            return sum(
                1
                for item in history
                if isinstance(item, dict) and str(item.get("tool_name", "") or "").strip()
            )

        normalized_tool_names = {
            str(tool_name or "").strip()
            for tool_name in tool_names
            if str(tool_name or "").strip()
        }
        if not normalized_tool_names:
            return 0

        return sum(
            1
            for item in history
            if isinstance(item, dict)
            and str(item.get("tool_name", "") or "").strip() in normalized_tool_names
        )

    def max_total_tool_calls_per_turn(self) -> Optional[int]:
        return _normalize_positive_int(
            self.tool_default(_CONTROLLER_LIMIT_KEY, "max_calls_per_turn")
        )

    def max_tool_calls_per_turn(self, tool_name: str) -> Optional[int]:
        return _normalize_positive_int(
            self.tool_default(tool_name, "max_calls_per_turn")
        )

    def max_tool_group_calls_per_turn(self, group_name: str) -> Optional[int]:
        return _normalize_positive_int(
            self.tool_default(group_name, "max_calls_per_turn")
        )

    @staticmethod
    def _build_limit_result(
        *,
        tool_name: str,
        scope: str,
        current_count: int,
        max_calls_per_turn: int,
        group_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        if scope == "controller":
            message = (
                f"Top-level tool call limit reached ({max_calls_per_turn}) for this chat turn. "
                "Do not call more tools. Answer with the evidence you already have, or say it is insufficient."
            )
        elif scope == "group":
            label = str(group_name or "tool_group").strip() or "tool_group"
            message = (
                f"Tool-group call limit reached for {label} ({max_calls_per_turn}) in this chat turn. "
                "Do not call more tools from this group. Answer with the evidence you already have, "
                "or say it is insufficient."
            )
        else:
            message = (
                f"Tool call limit reached for {tool_name} ({max_calls_per_turn}) in this chat turn. "
                "Do not call it again. Answer with the evidence you already have, or say it is insufficient."
            )

        return {
            "limit_reached": True,
            "limit_scope": scope,
            "tool_name": tool_name,
            "group_name": str(group_name or "").strip() or None,
            "current_count": int(current_count),
            "max_calls_per_turn": int(max_calls_per_turn),
            "message": message,
        }

    def check_tool_call_limits(
        self,
        tool_name: str,
        *,
        group_name: Optional[str] = None,
        group_tool_names: Optional[Sequence[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        total_limit = self.max_total_tool_calls_per_turn()
        total_count = self.tool_use_count()
        if total_limit is not None and total_count >= total_limit:
            return self._build_limit_result(
                tool_name=tool_name,
                scope="controller",
                current_count=total_count,
                max_calls_per_turn=total_limit,
            )

        if group_name and group_tool_names:
            group_limit = self.max_tool_group_calls_per_turn(group_name)
            group_count = self.tool_use_count(group_tool_names)
            if group_limit is not None and group_count >= group_limit:
                return self._build_limit_result(
                    tool_name=tool_name,
                    scope="group",
                    current_count=group_count,
                    max_calls_per_turn=group_limit,
                    group_name=group_name,
                )

        tool_limit = self.max_tool_calls_per_turn(tool_name)
        tool_count = self.tool_use_count([tool_name])
        if tool_limit is not None and tool_count >= tool_limit:
            return self._build_limit_result(
                tool_name=tool_name,
                scope="tool",
                current_count=tool_count,
                max_calls_per_turn=tool_limit,
            )

        return None

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
        history = self._history()
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

    def get_schedule_agent(self) -> Any:
        provider = self.schedule_agent_provider
        if provider is None:
            raise RuntimeError("Schedule tool is unavailable because schedule_agent_provider is not configured.")
        return provider()
