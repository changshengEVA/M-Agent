from __future__ import annotations

import json

import pytest

from m_agent.api.chat_api_shared import _normalize_memory_mode, _with_public_result_thread_id
from m_agent.api.chat_api_web import _encode_sse


pytestmark = pytest.mark.unit


def test_with_public_result_thread_id_rewrites_nested_thread_state() -> None:
    original = {
        "thread_id": "alice::demo-thread",
        "answer": "ok",
        "thread_state": {
            "thread_id": "alice::demo-thread",
            "mode": "manual",
        },
    }

    public_payload = _with_public_result_thread_id(original, public_thread_id="demo-thread")

    assert public_payload["thread_id"] == "demo-thread"
    assert public_payload["thread_state"]["thread_id"] == "demo-thread"
    assert original["thread_id"] == "alice::demo-thread"
    assert original["thread_state"]["thread_id"] == "alice::demo-thread"


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
