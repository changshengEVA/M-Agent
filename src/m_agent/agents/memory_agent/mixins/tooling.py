from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

from langchain.tools import tool

logger = logging.getLogger(__name__)


class MemoryAgentToolingMixin:
    def _build_search_details_usage_state(self, scope: str) -> Dict[str, int]:
        """Build the current usage snapshot for search_details throttling."""
        return {
            "scope_used": int(self._search_details_scope_counts.get(scope, 0)),
            "round_used": int(getattr(self, "_search_details_round_count", 0)),
            "consecutive_used": self._count_consecutive_search_details_calls(),
        }

    def _detect_search_details_limit(
        self,
        *,
        scope_used: int,
        round_used: int,
        consecutive_used: int,
    ) -> Tuple[str, str]:
        """Check whether search_details should be blocked by current limits."""
        if consecutive_used >= self.max_consecutive_search_details_calls:
            return (
                "search_details_consecutive_limit_reached",
                (
                    "consecutive search_details calls reached limit "
                    f"({consecutive_used}/{self.max_consecutive_search_details_calls})"
                ),
            )
        if scope_used >= self.max_search_details_calls_per_scope:
            return (
                "search_details_scope_limit_reached",
                (
                    "scope search_details calls reached limit "
                    f"({scope_used}/{self.max_search_details_calls_per_scope})"
                ),
            )
        if round_used >= self.max_search_details_calls_per_round:
            return (
                "search_details_round_limit_reached",
                (
                    "round search_details calls reached limit "
                    f"({round_used}/{self.max_search_details_calls_per_round})"
                ),
            )
        return "", ""

    def _build_search_details_blocked_result(
        self,
        *,
        detail: str,
        topk: int,
        scope: str,
        block_error: str,
        block_reason: str,
        usage: Dict[str, int],
    ) -> Dict[str, Any]:
        """Build the response payload when search_details is throttled."""
        return {
            "hit": False,
            "blocked": True,
            "error": block_error,
            "reason": block_reason,
            "scope": scope,
            "scope_used": usage.get("scope_used", 0),
            "scope_limit": self.max_search_details_calls_per_scope,
            "round_used": usage.get("round_used", 0),
            "round_limit": self.max_search_details_calls_per_round,
            "consecutive_used": usage.get("consecutive_used", 0),
            "consecutive_limit": self.max_consecutive_search_details_calls,
            "detail": detail,
            "topk": topk,
        }

    def _search_details_with_trace(self, detail: str, topk: Optional[int] = None) -> Dict[str, Any]:
        """Run search_details with tracing, limits, and consistent logging."""
        cfg_topk = self._resolve_topk(topk)
        scope = self._get_active_search_scope()
        usage = self._build_search_details_usage_state(scope)
        block_error, block_reason = self._detect_search_details_limit(
            scope_used=usage["scope_used"],
            round_used=usage["round_used"],
            consecutive_used=usage["consecutive_used"],
        )

        call_entry = self._record_tool_call(
            "search_details",
            {"detail": detail, "topk": cfg_topk},
        )
        if block_error:
            blocked_result = self._build_search_details_blocked_result(
                detail=detail,
                topk=cfg_topk,
                scope=scope,
                block_error=block_error,
                block_reason=block_reason,
                usage=usage,
            )
            self._finalize_tool_call(call_entry, result=blocked_result)
            logger.warning("search_details blocked(scope=%s, reason=%s)", scope, block_reason)
            return blocked_result

        self._search_details_scope_counts[scope] = usage["scope_used"] + 1
        self._search_details_round_count = usage["round_used"] + 1
        logger.info("API call: search_details(detail=%s, topk=%s)", detail, cfg_topk)
        try:
            result = self.memory_sys.search_details(
                detail_query=detail,
                topk=cfg_topk,
            )
        except Exception as exc:
            self._finalize_tool_call(call_entry, error=exc)
            raise

        self._finalize_tool_call(call_entry, result=result)
        logger.info(
            "API response: search_details(success=%s, result_count=%s)",
            result.get("hit") if isinstance(result, dict) else None,
            (
                len(result.get("results", []))
                if isinstance(result, dict) and isinstance(result.get("results"), list)
                else None
            ),
        )
        return result

    @staticmethod
    def _dict_field(payload: Any, key: str, default: Any = None) -> Any:
        """Safely read a field from a dict-like payload."""
        if isinstance(payload, dict):
            return payload.get(key, default)
        return default

    def _execute_traced_tool_call(
        self,
        *,
        tool_name: str,
        params: Dict[str, Any],
        call_log: str,
        call_log_args: Tuple[Any, ...],
        invoke: Callable[[], Any],
        response_log: str,
        response_log_args: Callable[[Any], Tuple[Any, ...]],
    ) -> Any:
        """Execute a tool call with unified trace recording and logging."""
        call_entry = self._record_tool_call(tool_name, params)
        logger.info(call_log, *call_log_args)
        try:
            result = invoke()
        except Exception as exc:
            self._finalize_tool_call(call_entry, error=exc)
            raise

        self._finalize_tool_call(call_entry, result=result)
        logger.info(response_log, *response_log_args(result))
        return result

    def _build_tools(self):
        """Build the MemoryAgent runtime tool list.

        Canonical tool behavior and usage policy are maintained in:
        `config/agents/memory/runtime/agent_runtime.yaml`

        This method intentionally keeps tool wrappers thin and only wires
        tool names to traced `memory_sys` calls.

        Registered tools:
        - resolve_entity_id
        - get_entity_profile
        - search_entity_feature
        - search_entity_event
        - search_entity_events_by_time
        - search_content
        - search_events_by_time_range
        - search_details
        """
        tool_entry_doc = (
            "Memory tool wrapper. Detailed usage is defined in "
            "config/agents/memory/runtime/agent_runtime.yaml."
        )

        @tool(description=tool_entry_doc)
        def resolve_entity_id(entity_name_or_id: str) -> Dict[str, Any]:
            return self._execute_traced_tool_call(
                tool_name="resolve_entity_id",
                params={"entity_name_or_id": entity_name_or_id},
                call_log="API call: resolve_entity_id(entity_name_or_id=%s)",
                call_log_args=(entity_name_or_id,),
                invoke=lambda: self.memory_sys.resolve_entity_id(entity_name_or_id=entity_name_or_id),
                response_log="API response: resolve_entity_id(hit=%s, entity_id=%s, match_type=%s)",
                response_log_args=lambda result: (
                    self._dict_field(result, "hit"),
                    self._dict_field(result, "entity_id"),
                    self._dict_field(result, "match_type"),
                ),
            )

        @tool(description=tool_entry_doc)
        def get_entity_profile(entity_id: str) -> Dict[str, Any]:
            return self._execute_traced_tool_call(
                tool_name="get_entity_profile",
                params={"entity_id": entity_id},
                call_log="API call: get_entity_profile(entity_id=%s)",
                call_log_args=(entity_id,),
                invoke=lambda: self.memory_sys.get_entity_profile(entity_id=entity_id),
                response_log="API response: get_entity_profile(hit=%s, summary_len=%s)",
                response_log_args=lambda result: (
                    self._dict_field(result, "hit"),
                    (
                        len(str(self._dict_field(result, "summary", "") or ""))
                        if isinstance(result, dict)
                        else None
                    ),
                ),
            )

        @tool(description=tool_entry_doc)
        def search_entity_feature(
            entity_id: str,
            feature_query: str,
            topk: Optional[int] = None,
        ) -> Dict[str, Any]:
            cfg_topk = self._resolve_topk(topk)
            return self._execute_traced_tool_call(
                tool_name="search_entity_feature",
                params={"entity_id": entity_id, "feature_query": feature_query, "topk": cfg_topk},
                call_log="API call: search_entity_feature(entity_id=%s, feature_query=%s, topk=%s)",
                call_log_args=(entity_id, feature_query, cfg_topk),
                invoke=lambda: self.memory_sys.search_entity_feature(
                    entity_id=entity_id,
                    feature_query=feature_query,
                    topk=cfg_topk,
                ),
                response_log="API response: search_entity_feature(hit=%s, matched_count=%s)",
                response_log_args=lambda result: (
                    self._dict_field(result, "hit"),
                    self._dict_field(result, "matched_count"),
                ),
            )

        @tool(description=tool_entry_doc)
        def search_entity_event(
            entity_id: str,
            event_query: str,
            topk: Optional[int] = None,
        ) -> Dict[str, Any]:
            cfg_topk = self._resolve_topk(topk)
            return self._execute_traced_tool_call(
                tool_name="search_entity_event",
                params={"entity_id": entity_id, "event_query": event_query, "topk": cfg_topk},
                call_log="API call: search_entity_event(entity_id=%s, event_query=%s, topk=%s)",
                call_log_args=(entity_id, event_query, cfg_topk),
                invoke=lambda: self.memory_sys.search_entity_event(
                    entity_id=entity_id,
                    event_query=event_query,
                    topk=cfg_topk,
                ),
                response_log="API response: search_entity_event(hit=%s, matched_count=%s)",
                response_log_args=lambda result: (
                    self._dict_field(result, "hit"),
                    self._dict_field(result, "matched_count"),
                ),
            )

        @tool(description=tool_entry_doc)
        def search_entity_events_by_time(
            entity_id: str,
            start_time: str,
            end_time: Optional[str] = None,
        ) -> Dict[str, Any]:
            return self._execute_traced_tool_call(
                tool_name="search_entity_events_by_time",
                params={"entity_id": entity_id, "start_time": start_time, "end_time": end_time},
                call_log="API call: search_entity_events_by_time(entity_id=%s, start_time=%s, end_time=%s)",
                call_log_args=(entity_id, start_time, end_time),
                invoke=lambda: self.memory_sys.search_entity_events_by_time(
                    entity_id=entity_id,
                    start_time=start_time,
                    end_time=end_time,
                ),
                response_log="API response: search_entity_events_by_time(hit=%s, matched_count=%s)",
                response_log_args=lambda result: (
                    self._dict_field(result, "hit"),
                    self._dict_field(result, "matched_count"),
                ),
            )

        @tool(description=tool_entry_doc)
        def search_content(dialogue_id: str, episode_id: str) -> Dict[str, Any]:
            return self._execute_traced_tool_call(
                tool_name="search_content",
                params={"dialogue_id": dialogue_id, "episode_id": episode_id},
                call_log="API call: search_content(dialogue_id=%s, episode_id=%s)",
                call_log_args=(dialogue_id, episode_id),
                invoke=lambda: self.memory_sys.search_content(
                    dialogue_id=dialogue_id,
                    episode_id=episode_id,
                ),
                response_log="API response: search_content(success=%s)",
                response_log_args=lambda result: (self._dict_field(result, "success"),),
            )

        @tool(description=tool_entry_doc)
        def search_events_by_time_range(start_time: str, end_time: str) -> list[Dict[str, Any]]:
            return self._execute_traced_tool_call(
                tool_name="search_events_by_time_range",
                params={"start_time": start_time, "end_time": end_time},
                call_log="API call: search_events_by_time_range(start_time=%s, end_time=%s)",
                call_log_args=(start_time, end_time),
                invoke=lambda: self.memory_sys.search_events_by_time_range(
                    start_time=start_time,
                    end_time=end_time,
                ),
                response_log="API response: search_events_by_time_range(result_count=%s)",
                response_log_args=lambda result: (len(result) if isinstance(result, list) else None,),
            )

        @tool(description=tool_entry_doc)
        def search_details(detail: str, topk: Optional[int] = None) -> Dict[str, Any]:
            return self._search_details_with_trace(detail=detail, topk=topk)

        return [
            resolve_entity_id,
            get_entity_profile,
            search_entity_feature,
            search_entity_event,
            search_entity_events_by_time,
            search_content,
            search_events_by_time_range,
            search_details,
        ]
