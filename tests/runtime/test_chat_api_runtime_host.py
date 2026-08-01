"""R3: ChatServiceRuntime holds RuntimeHost and honors runtime_engine selection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from m_agent.api.chat_api_runtime import ChatServiceRuntime
from m_agent.runtime.host import ThinkLifeRuntimeHost, create_runtime_host
from m_agent.runtime.langgraph.runtime import LangGraphRuntime
from m_agent.runtime.routing import (
    DEFAULT_RUNTIME_ENGINE,
    LANGGRAPH_RUNTIME_ENGINE,
    resolve_runtime_engine_from_config,
)


class _ScriptedThinkingAgent:
    def __init__(self, decisions: List[Any]) -> None:
        self._decisions = list(decisions)

    def resolve_transaction(self, *_args, **_kwargs):
        return None

    def handle(self, *_args, **_kwargs):
        from m_agent.layers.thinking.contracts import ThinkingDecision

        if not self._decisions:
            return ThinkingDecision(mode="silent", request_complete=True)
        return self._decisions.pop(0)

    def on_flush(self, *_args, **_kwargs):
        return []


def _stub_agent(
    *,
    default_engine: str = DEFAULT_RUNTIME_ENGINE,
    langgraph: Optional[Dict[str, Any]] = None,
    decisions: Optional[List[Any]] = None,
) -> SimpleNamespace:
    from m_agent.layers.thinking.contracts import ThinkingDecision

    script = list(decisions) if decisions is not None else []
    return SimpleNamespace(
        thinking_agent=_ScriptedThinkingAgent(script),
        execution_agent=None,
        systems=SimpleNamespace(wm=None),
        config={
            "runtime": {
                "default_engine": default_engine,
                "think_life": {},
                "langgraph": langgraph
                or {
                    "turn_loop": True,
                    "delegate_executor": "fake",
                    "fake_capabilities": ["fake_capability"],
                },
            }
        },
        user_name="User",
        assistant_name="Assistant",
        default_thread_id="r3-thread",
        persist_memory=False,
        working_memory_config=None,
        # Satisfy ThinkingDecision import side for clarity in callers.
        _ThinkingDecision=ThinkingDecision,
    )


def test_resolve_runtime_engine_from_config_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("M_AGENT_DEFAULT_RUNTIME_ENGINE", LANGGRAPH_RUNTIME_ENGINE)
    assert (
        resolve_runtime_engine_from_config(
            {"default_engine": DEFAULT_RUNTIME_ENGINE},
        )
        == DEFAULT_RUNTIME_ENGINE
    )
    assert (
        resolve_runtime_engine_from_config(
            {},
            explicit=DEFAULT_RUNTIME_ENGINE,
        )
        == DEFAULT_RUNTIME_ENGINE
    )
    assert resolve_runtime_engine_from_config({}) == LANGGRAPH_RUNTIME_ENGINE


def test_create_runtime_host_reads_yaml_default_engine(tmp_path: Path) -> None:
    host = create_runtime_host(
        agent=_stub_agent(default_engine=LANGGRAPH_RUNTIME_ENGINE),  # type: ignore[arg-type]
        owner_id="r3-user",
        persist_root=tmp_path / "lg",
    )
    assert isinstance(host, LangGraphRuntime)
    assert host.runtime_engine_id == LANGGRAPH_RUNTIME_ENGINE
    host.shutdown()


def test_chat_service_runtime_uses_runtime_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    agent = _stub_agent(default_engine=DEFAULT_RUNTIME_ENGINE)
    monkeypatch.setattr(
        "m_agent.api.chat_api_runtime.create_chat_agent",
        lambda **_kwargs: agent,
    )
    monkeypatch.setattr(
        "m_agent.api.chat_api_runtime.chat_user_slug",
        lambda _name: "r3-think-life",
    )
    # Avoid writing into real user persistence by forcing host construction here.
    original_create = create_runtime_host

    def _create(**kwargs):
        kwargs.setdefault("persist_root", tmp_path / "tl-user")
        return original_create(**kwargs)

    monkeypatch.setattr(
        "m_agent.api.chat_api_runtime.create_runtime_host",
        _create,
    )

    runtime = ChatServiceRuntime(
        config_path=tmp_path / "unused.yaml",
        idle_flush_seconds=0,
    )
    try:
        assert isinstance(runtime.runtime_host, ThinkLifeRuntimeHost)
        assert runtime.runtime_engine_id == DEFAULT_RUNTIME_ENGINE
        assert runtime.runtime_profile == DEFAULT_RUNTIME_ENGINE
        health = runtime.health_payload()
        assert health["runtime_engine_id"] == DEFAULT_RUNTIME_ENGINE
        assert health["runtime_engine"]["runtime_engine_id"] == DEFAULT_RUNTIME_ENGINE
        assert DEFAULT_RUNTIME_ENGINE in health["engines"]
        assert "pending_stimuli_total" in health["think_life"]
    finally:
        runtime.shutdown()


def test_chat_service_runtime_can_select_langgraph(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from m_agent.layers.thinking.contracts import ThinkingDecision

    agent = _stub_agent(
        default_engine=LANGGRAPH_RUNTIME_ENGINE,
        decisions=[
            ThinkingDecision(
                mode="answer_directly",
                answer="hello from langgraph host",
            ),
            ThinkingDecision(mode="silent", request_complete=True),
        ],
    )
    monkeypatch.setattr(
        "m_agent.api.chat_api_runtime.create_chat_agent",
        lambda **_kwargs: agent,
    )
    monkeypatch.setattr(
        "m_agent.api.chat_api_runtime.chat_user_slug",
        lambda _name: "r3-langgraph",
    )
    original_create = create_runtime_host

    def _create(**kwargs):
        kwargs.setdefault("persist_root", tmp_path / "lg-user")
        return original_create(**kwargs)

    monkeypatch.setattr(
        "m_agent.api.chat_api_runtime.create_runtime_host",
        _create,
    )

    runtime = ChatServiceRuntime(
        config_path=tmp_path / "unused.yaml",
        idle_flush_seconds=0,
    )
    try:
        assert isinstance(runtime.runtime_host, LangGraphRuntime)
        assert runtime.runtime_engine_id == LANGGRAPH_RUNTIME_ENGINE
        health = runtime.health_payload()
        assert health["runtime_engine_id"] == LANGGRAPH_RUNTIME_ENGINE
        assert health["runtime_engine"]["turn_loop"] is True
        assert health["runtime_engine"]["delegate_executor"] == "fake"

        result = runtime.run_chat(
            message="hello r3",
            thread_id="r3-thread",
        )
        assert result.get("success") is True
        assert result.get("runtime_engine_id") == LANGGRAPH_RUNTIME_ENGINE
        assert result.get("answer") == "hello from langgraph host"
        assert result["thread_state"]["runtime_engine_id"] == LANGGRAPH_RUNTIME_ENGINE
        txs = runtime.get_think_life_transactions("r3-thread")
        assert txs["transaction_count"] >= 1
    finally:
        runtime.shutdown()
