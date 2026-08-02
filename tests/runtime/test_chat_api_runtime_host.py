"""ChatServiceRuntime uses the single production RuntimeHost."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

from m_agent.api.chat_api_runtime import ChatServiceRuntime
from m_agent.runtime.host import create_runtime_host
from m_agent.runtime.langgraph.runtime import LangGraphRuntime
from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE


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


def _stub_agent(*, decisions: List[Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        thinking_agent=_ScriptedThinkingAgent(list(decisions or [])),
        execution_agent=None,
        systems=SimpleNamespace(wm=None),
        config={
            "runtime": {
                "common": {},
                "langgraph": {
                    "turn_loop": True,
                    "delegate_executor": "fake",
                    "fake_capabilities": ["fake_capability"],
                },
            }
        },
        user_name="User",
        assistant_name="Assistant",
        default_thread_id="runtime-thread",
        persist_memory=False,
        working_memory_config=None,
    )


def test_create_runtime_host_constructs_langgraph(tmp_path: Path) -> None:
    host = create_runtime_host(
        agent=_stub_agent(),  # type: ignore[arg-type]
        owner_id="runtime-user",
        persist_root=tmp_path / "runtime",
    )
    try:
        assert isinstance(host, LangGraphRuntime)
        assert host.runtime_engine_id == LANGGRAPH_RUNTIME_ENGINE
    finally:
        host.shutdown()


def test_chat_service_runtime_exposes_neutral_runtime_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from m_agent.layers.thinking.contracts import ThinkingDecision

    agent = _stub_agent(
        decisions=[
            ThinkingDecision(
                mode="answer_directly",
                answer="hello from langgraph host",
            ),
            ThinkingDecision(mode="silent", request_complete=True),
        ]
    )
    monkeypatch.setattr(
        "m_agent.api.chat_api_runtime.create_chat_agent",
        lambda **_kwargs: agent,
    )
    monkeypatch.setattr(
        "m_agent.api.chat_api_runtime.chat_user_slug",
        lambda _name: "runtime-user",
    )
    original_create = create_runtime_host

    def _create(**kwargs):
        kwargs.setdefault("persist_root", tmp_path / "runtime-user")
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
        assert health["runtime"]["turn_loop"] is True
        assert health["runtime"]["delegate_executor"] == "fake"

        result = runtime.run_chat(
            message="hello",
            thread_id="runtime-thread",
        )
        assert result["success"] is True
        assert result["runtime_engine_id"] == LANGGRAPH_RUNTIME_ENGINE
        assert result["answer"] == "hello from langgraph host"
        assert result["thread_state"]["runtime_engine_id"] == (
            LANGGRAPH_RUNTIME_ENGINE
        )
        transactions = runtime.get_transactions("runtime-thread")
        assert transactions["transaction_count"] >= 1
        assert "runtime" in result["thread_state"]
    finally:
        runtime.shutdown()
