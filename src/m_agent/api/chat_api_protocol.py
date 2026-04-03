from __future__ import annotations

import logging
from typing import Any, Dict

from .chat_api_shared import (
    _short_text,
    _summarize_mapping_fields,
    _summarize_result_value,
)

protocol_logger = logging.getLogger("m_agent.api.protocol")

_PROTOCOL_SSE_EVENTS = {
    "run_started",
    "question_strategy",
    "plan_update",
    "recall_started",
    "tool_call",
    "tool_result",
    "sub_question_started",
    "sub_question_completed",
    "direct_answer_payload",
    "direct_answer_fallback",
    "final_answer_payload",
    "recall_completed",
    "assistant_message",
    "memory_capture_updated",
    "chat_result",
    "run_completed",
    "run_failed",
    "flush_started",
    "flush_stage",
    "flush_completed",
    "thread_state_updated",
}


def _should_protocol_log_path(path: str) -> bool:
    clean = str(path or "").strip()
    if not clean:
        return False
    if clean in {"/", "/healthz", "/docs", "/openapi.json"}:
        return False
    return clean.startswith("/v1/chat/")


def _summarize_event_payload(event_type: str, payload: Dict[str, Any]) -> str:
    if event_type == "run_started":
        return f"thread={payload.get('thread_id')} message={_short_text(payload.get('message'))}"
    if event_type == "question_strategy":
        return (
            f"decompose_first={payload.get('decompose_first')} "
            f"reason={_short_text(payload.get('reason'))} "
            f"question={_short_text(payload.get('question'))}"
        )
    if event_type == "plan_update":
        suggested_tools = payload.get("suggested_tool_order")
        tool_text = ",".join(str(item) for item in suggested_tools[:3]) if isinstance(suggested_tools, list) else ""
        return (
            f"type={payload.get('question_type')} "
            f"sub_questions={len(payload.get('sub_questions', [])) if isinstance(payload.get('sub_questions'), list) else 0} "
            f"tools={tool_text or '-'}"
        )
    if event_type == "recall_started":
        return (
            f"mode={payload.get('mode')} "
            f"question={_short_text(payload.get('question'))}"
        )
    if event_type == "tool_call":
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        params_text = _summarize_mapping_fields(params, max_items=3)
        suffix = f" {params_text}" if params_text else ""
        return (
            f"call_id={payload.get('call_id')} tool={payload.get('tool_name')} "
            f"status={payload.get('status')}{suffix}"
        )
    if event_type == "tool_result":
        result_text = _summarize_result_value(payload.get("result"))
        if payload.get("error"):
            result_text = f"error={_short_text(payload.get('error'))}"
        return (
            f"call_id={payload.get('call_id')} tool={payload.get('tool_name')} "
            f"status={payload.get('status')} {result_text}"
        )
    if event_type == "sub_question_started":
        return (
            f"index={payload.get('index')} status={payload.get('status')} "
            f"question={_short_text(payload.get('question'))}"
        )
    if event_type == "sub_question_completed":
        if payload.get("error"):
            return (
                f"index={payload.get('index')} status={payload.get('status')} "
                f"error={_short_text(payload.get('error'))}"
            )
        return (
            f"index={payload.get('index')} status={payload.get('status')} "
            f"answer={_short_text(payload.get('answer'))}"
        )
    if event_type == "direct_answer_payload":
        return (
            f"answer={_short_text(payload.get('answer'))} "
            f"gold_answer={_short_text(payload.get('gold_answer'))} "
            f"evidence_present={bool(str(payload.get('evidence', '') or '').strip())}"
        )
    if event_type == "direct_answer_fallback":
        return (
            f"reason={_short_text(payload.get('reason'))} "
            f"question={_short_text(payload.get('question'))}"
        )
    if event_type == "final_answer_payload":
        return (
            f"answer={_short_text(payload.get('answer'))} "
            f"gold_answer={_short_text(payload.get('gold_answer'))} "
            f"tool_calls={payload.get('tool_call_count')}"
        )
    if event_type == "recall_completed":
        return (
            f"mode={payload.get('mode')} "
            f"answer={_short_text(payload.get('answer'))}"
        )
    if event_type == "assistant_message":
        return f"thread={payload.get('thread_id')} answer={_short_text(payload.get('answer'))}"
    if event_type == "memory_capture_updated":
        return (
            f"mode={payload.get('mode')} status={payload.get('status')} "
            f"pending_rounds={payload.get('pending_rounds')}"
        )
    if event_type == "chat_result":
        agent_result = payload.get("agent_result") if isinstance(payload.get("agent_result"), dict) else {}
        return (
            f"tool_calls={agent_result.get('tool_call_count')} "
            f"plan_summary={_short_text(agent_result.get('plan_summary'))}"
        )
    if event_type == "run_completed":
        return f"thread={payload.get('thread_id')}"
    if event_type == "run_failed":
        return f"thread={payload.get('thread_id')} error={_short_text(payload.get('error'))}"
    if event_type == "flush_started":
        return (
            f"thread={payload.get('thread_id')} reason={payload.get('flush_reason')} "
            f"pending_rounds={payload.get('pending_rounds')}"
        )
    if event_type == "flush_stage":
        return (
            f"thread={payload.get('thread_id')} stage={payload.get('stage')} "
            f"status={payload.get('status')}"
        )
    if event_type == "flush_completed":
        return (
            f"thread={payload.get('thread_id')} status={payload.get('status')} "
            f"success={payload.get('success')}"
        )
    if event_type == "thread_state_updated":
        state = payload.get("thread_state") if isinstance(payload.get("thread_state"), dict) else payload
        return (
            f"thread={state.get('thread_id')} mode={state.get('mode')} "
            f"pending_rounds={state.get('pending_rounds')}"
        )
    return ""


def _log_protocol_event(channel: str, channel_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    if event_type not in _PROTOCOL_SSE_EVENTS:
        return
    summary = _summarize_event_payload(event_type, payload)
    suffix = f" {summary}" if summary else ""
    protocol_logger.info("SSE -> %s[%s] %s%s", channel, channel_id, event_type, suffix)
