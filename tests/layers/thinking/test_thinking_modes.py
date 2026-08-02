"""Shared thinking-mode normalization and predicate semantics."""

from __future__ import annotations

from m_agent.layers.thinking.contracts import (
    is_execute_mode,
    is_reply_mode,
    is_silent_mode,
    normalize_thinking_mode,
)


def test_normalize_thinking_mode_aliases() -> None:
    assert normalize_thinking_mode("silent") == "silent"
    assert normalize_thinking_mode("wait") == "silent"
    assert normalize_thinking_mode("defer") == "silent"
    assert normalize_thinking_mode("answer_directly") == "answer_directly"
    assert normalize_thinking_mode("reply") == "answer_directly"
    assert normalize_thinking_mode("execute") == "execute"


def test_mode_predicates() -> None:
    assert is_silent_mode("wait")
    assert is_execute_mode("execute")
    assert is_reply_mode("answer_directly")
    assert not is_silent_mode("execute")
