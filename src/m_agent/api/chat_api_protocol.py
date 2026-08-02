from __future__ import annotations

import logging
from typing import Any, Dict

from .chat_api_shared import (
    _short_text,
    _summarize_mapping_fields,
    _summarize_result_value,
)

protocol_logger = logging.getLogger("m_agent.api.protocol")

# =====================================================================
# SSE event logging allow-list — frontend contract surface.
#
# Event records are stored independently; this set controls concise protocol
# logging for documented SSE types. Treat additions as frontend-visible
# protocol changes and update the API reference with the code.
#
# Runtime planning events (``thinking_*``) are additionally guarded by
# ``ChatServiceRuntime._RUNTIME_PLANNING_EVENTS`` so a misbehaving
# subsystem cannot inject arbitrary types.
# =====================================================================
_PROTOCOL_SSE_EVENTS = frozenset({
    "run_started",
    "recall_started",
    "tool_call",
    "tool_result",
    "recall_completed",
    "assistant_message",
    "memory_capture_updated",
    "run_completed",
    "run_failed",
    "flush_started",
    "flush_stage",
    "flush_completed",
    "thread_state_updated",
    # Runtime planning events. Delegated tool work is reported separately by
    # Scene/runtime events rather than an inline execution pass.
    "thinking_started",
    "thinking_task_state",
    "thinking_plan",
    "thinking_completed",
    "turn_failed",
    "transaction_deleted",
})


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
    if event_type == "thinking_started":
        return (
            f"thread={payload.get('thread_id')} conv={payload.get('conversation_id')} "
            f"turn={payload.get('turn')} source={payload.get('source')}"
        )
    if event_type == "thinking_task_state":
        progress = payload.get("task_progress") if isinstance(payload.get("task_progress"), dict) else {}
        return (
            f"goal={_short_text(progress.get('goal'))} "
            f"status={progress.get('completion_status') or 'processing'} "
            f"completed={len(progress.get('completed') or [])} "
            f"remaining={len(progress.get('remaining') or [])}"
        )
    if event_type == "thinking_plan":
        mode = payload.get("mode")
        return (
            f"mode={mode} "
            f"tool={payload.get('tool_name') or '-'} "
            f"instruction={_short_text(payload.get('instruction'))}"
        )
    if event_type == "thinking_completed":
        phases = payload.get("phases") or []
        phase_text = ",".join(str(p) for p in phases) if isinstance(phases, list) else ""
        return f"executed={payload.get('executed')} phases={phase_text or '-'}"
    if event_type == "turn_failed":
        return (
            f"thread={payload.get('thread_id')} "
            f"txn={payload.get('transaction_id')} "
            f"error={_short_text(payload.get('error'))}"
        )
    if event_type == "transaction_deleted":
        transaction = (
            payload.get("transaction")
            if isinstance(payload.get("transaction"), dict)
            else {}
        )
        return (
            f"txn={transaction.get('transaction_id')} "
            f"revision={transaction.get('revision')} "
            f"outcome={payload.get('outcome')}"
        )
    return ""


def _log_protocol_event(channel: str, channel_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    if event_type not in _PROTOCOL_SSE_EVENTS:
        return
    summary = _summarize_event_payload(event_type, payload)
    suffix = f" {summary}" if summary else ""
    protocol_logger.info("SSE -> %s[%s] %s%s", channel, channel_id, event_type, suffix)
