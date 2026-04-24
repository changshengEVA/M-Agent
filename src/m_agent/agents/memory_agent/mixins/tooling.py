from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

from langchain.tools import tool

logger = logging.getLogger(__name__)


class MemoryAgentToolingMixin:
    def _search_details_with_trace(self, detail: str, topk: Optional[int] = None) -> Dict[str, Any]:
        """Run search_details with tracing and consistent logging."""
        cfg_topk = self._resolve_topk(topk)
        return self._execute_traced_tool_call(
            tool_name="search_details",
            params={"detail": detail, "topk": cfg_topk},
            call_log="API call: search_details(detail=%s, topk=%s)",
            call_log_args=(detail, cfg_topk),
            invoke=lambda: self.memory_sys.search_details(
                detail_query=detail,
                topk=cfg_topk,
            ),
            response_log="API response: search_details(hit=%s, result_count=%s)",
            response_log_args=lambda result: (
                self._dict_field(result, "hit"),
                (
                    len(result.get("results", []))
                    if isinstance(result, dict) and isinstance(result.get("results"), list)
                    else None
                ),
            ),
        )

    def _search_details_multi_route_with_trace(
        self,
        detail: str,
        topk: Optional[int] = None,
    ) -> Dict[str, Any]:
        cfg_topk = self._resolve_topk(topk)
        return self._execute_traced_tool_call(
            tool_name="search_details_multi_route",
            params={"detail": detail, "topk": cfg_topk},
            call_log="API call: search_details_multi_route(detail=%s, topk=%s)",
            call_log_args=(detail, cfg_topk),
            invoke=lambda: self.memory_sys.search_details_multi_route(
                detail_query=detail,
                topk=cfg_topk,
            ),
            response_log="API response: search_details_multi_route(hit=%s, result_count=%s)",
            response_log_args=lambda result: (
                self._dict_field(result, "hit"),
                (
                    len(result.get("results", []))
                    if isinstance(result, dict) and isinstance(result.get("results"), list)
                    else None
                ),
            ),
        )

    def _search_contents_by_episode_refs_with_trace(
        self,
        episode_refs: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        return self._execute_traced_tool_call(
            tool_name="search_contents_by_episode_refs",
            params={"episode_refs": episode_refs},
            call_log="API call: search_contents_by_episode_refs(ref_count=%s)",
            call_log_args=(len(episode_refs),),
            invoke=lambda: self.memory_sys.search_contents_by_episode_refs(episode_refs),
            response_log="API response: search_contents_by_episode_refs(result_count=%s)",
            response_log_args=lambda result: (len(result) if isinstance(result, list) else None,),
        )

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
        def search_entity_profile(
            entity_uid: str,
            optional_query: Optional[str] = None,
        ) -> Dict[str, Any]:
            return self._execute_traced_tool_call(
                tool_name="search_entity_profile",
                params={"entity_uid": entity_uid, "optional_query": optional_query},
                call_log="API call: search_entity_profile(entity_uid=%s, optional_query=%s)",
                call_log_args=(entity_uid, optional_query),
                invoke=lambda: self.memory_sys.search_entity_profile(
                    entity_uid=entity_uid,
                    optional_query=optional_query,
                ),
                response_log="API response: search_entity_profile(hit=%s)",
                response_log_args=lambda result: (self._dict_field(result, "hit"),),
            )

        @tool(description=tool_entry_doc)
        def search_entity_status(
            entity_uid: str,
            field_yield: str,
            user_question: str,
            topk: Optional[int] = None,
        ) -> Dict[str, Any]:
            cfg_topk = self._resolve_topk(topk)
            return self._execute_traced_tool_call(
                tool_name="search_entity_status",
                params={
                    "entity_uid": entity_uid,
                    "field_yield": field_yield,
                    "user_question": user_question,
                    "topk": cfg_topk,
                },
                call_log="API call: search_entity_status(entity_uid=%s, field_yield=%s, topk=%s)",
                call_log_args=(entity_uid, field_yield, cfg_topk),
                invoke=lambda: self.memory_sys.search_entity_status_answer(
                    entity_uid=entity_uid,
                    field_yield=field_yield,
                    user_question=user_question,
                    topk=cfg_topk,
                ),
                response_log="API response: search_entity_status(hit=%s)",
                response_log_args=lambda result: (self._dict_field(result, "hit"),),
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

        all_tools = {
            "resolve_entity_id": resolve_entity_id,
            "get_entity_profile": get_entity_profile,
            "search_entity_profile": search_entity_profile,
            "search_entity_status": search_entity_status,
            "search_entity_feature": search_entity_feature,
            "search_entity_event": search_entity_event,
            "search_entity_events_by_time": search_entity_events_by_time,
            "search_content": search_content,
            "search_events_by_time_range": search_events_by_time_range,
            "search_details": search_details,
        }
        default_order = [
            "resolve_entity_id",
            "get_entity_profile",
            "search_entity_profile",
            "search_entity_status",
            "search_entity_feature",
            "search_entity_event",
            "search_entity_events_by_time",
            "search_content",
            "search_events_by_time_range",
            "search_details",
        ]
        facts_only_mode = bool(getattr(self.memory_sys, "facts_only_mode", False))
        if facts_only_mode:
            enabled_order = [
                "search_details",
                # Note: RECALL_REMEDY_MULTI_ROUTE uses the internal multi-route
                # details workflow (not exposed as a direct tool here).
            ]
            logger.info(
                "facts_only_mode=true, restrict memory agent tools to: %s",
                ", ".join(enabled_order),
            )
        else:
            enabled_order = default_order

        self.available_memory_tool_names = list(enabled_order)
        return [all_tools[name] for name in enabled_order]
