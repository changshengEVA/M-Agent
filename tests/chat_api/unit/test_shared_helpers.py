from __future__ import annotations

import json

import pytest

from m_agent.api.chat_api_shared import _normalize_memory_mode, _with_public_result_thread_id
from m_agent.api.chat_api_web import (
    _encode_sse,
    _with_public_transaction_scope,
)


pytestmark = pytest.mark.unit


def test_with_public_result_thread_id_rewrites_nested_thread_state() -> None:
    original = {
        "thread_id": "alice::demo-thread",
        "answer": "ok",
        "thread_state": {
            "thread_id": "alice::demo-thread",
            "conversation_id": "alice::demo-thread::3",
            "mode": "manual",
        },
    }

    public_payload = _with_public_result_thread_id(original, public_thread_id="demo-thread")

    assert public_payload["thread_id"] == "demo-thread"
    assert public_payload["thread_state"]["thread_id"] == "demo-thread"
    assert public_payload["thread_state"]["conversation_id"] == "demo-thread::3"
    assert original["thread_id"] == "alice::demo-thread"
    assert original["thread_state"]["thread_id"] == "alice::demo-thread"
    assert original["thread_state"]["conversation_id"] == "alice::demo-thread::3"


def test_normalize_memory_mode_handles_case_and_fallback() -> None:
    assert _normalize_memory_mode("OFF", fallback="manual") == "off"
    assert _normalize_memory_mode("manual", fallback="off") == "manual"
    assert _normalize_memory_mode("unknown", fallback="off") == "off"


def test_encode_sse_uses_id_event_data_lines() -> None:
    event = {"seq": 3, "type": "assistant_message", "payload": {"answer": "hello"}}

    encoded = _encode_sse(event).decode("utf-8")
    lines = [line for line in encoded.splitlines() if line]

    assert lines[0] == "id: 3"
    assert lines[1] == "event: assistant_message"
    assert lines[2].startswith("data: ")
    payload = json.loads(lines[2][len("data: ") :])
    assert payload["payload"]["answer"] == "hello"


def test_transaction_event_scope_is_recursively_public() -> None:
    event = {
        "thread_id": "alice::alice-thread",
        "type": "transaction_deleted",
        "payload": {
            "thread_id": "alice::alice-thread",
            "conversation_id": "alice::alice-thread::3",
            "transaction": {
                "thread_id": "alice::alice-thread",
                "conversation_id": "alice::alice-thread::3",
            },
        },
    }

    public = _with_public_transaction_scope(
        event,
        internal_thread_id="alice::alice-thread",
        public_thread_id="alice-thread",
    )

    assert public["thread_id"] == "alice-thread"
    assert public["payload"]["conversation_id"] == "alice-thread::3"
    assert public["payload"]["transaction"]["thread_id"] == "alice-thread"
    assert event["payload"]["conversation_id"] == "alice::alice-thread::3"
