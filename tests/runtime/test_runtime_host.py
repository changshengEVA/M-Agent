from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from m_agent.runtime.host import (
    RuntimeHost,
    ThinkLifeRuntimeHost,
    create_runtime_host,
)
from m_agent.runtime.langgraph.runtime import LangGraphRuntime
from m_agent.runtime.routing import (
    DEFAULT_RUNTIME_ENGINE,
    LANGGRAPH_RUNTIME_ENGINE,
)
from m_agent.runtime.think_life.contracts import TransactionKind


class _FakeThinkingAgent:
    def resolve_transaction(self, *_args, **_kwargs):
        return None


def _fake_agent() -> SimpleNamespace:
    """Agent stub with no planning layer, so the R2 turn loop is rolled back."""

    return SimpleNamespace(
        thinking_agent=_FakeThinkingAgent(),
        config={
            "runtime": {
                "think_life": {},
                "langgraph": {"turn_loop": False},
            }
        },
        user_name="User",
        assistant_name="Assistant",
        default_thread_id="host-test-thread",
        persist_memory=False,
    )


def test_create_runtime_host_selects_langgraph(tmp_path: Path) -> None:
    host = create_runtime_host(
        agent=_fake_agent(),  # type: ignore[arg-type]
        owner_id="observation-user",
        runtime_engine=LANGGRAPH_RUNTIME_ENGINE,
        persist_root=tmp_path / "lg-user",
    )
    assert isinstance(host, LangGraphRuntime)
    assert host.runtime_engine_id == LANGGRAPH_RUNTIME_ENGINE
    assert host.turn_loop_enabled is False
    host.shutdown()


def test_langgraph_runtime_advance_transaction_mvp(tmp_path: Path) -> None:
    runtime = LangGraphRuntime(
        _fake_agent(),  # type: ignore[arg-type]
        owner_id="mvp-user",
        persist_root=tmp_path / "lg-runtime",
    )
    try:
        record = runtime.registry.create(
            thread_id="thread-a",
            conversation_id="thread-a::0",
            kind=TransactionKind.USER_TASK,
        )
        assert record.runtime_engine == LANGGRAPH_RUNTIME_ENGINE
        result = runtime.advance_transaction(
            record.transaction_id,
            user_text="hello from MVP host",
        )
        assert result["success"] is True
        assert result["graph_phase"] == "step_committed"
        loaded = runtime.registry.get(record.transaction_id)
        assert loaded is not None
        assert loaded.wm_entries
        assert loaded.task_state.goal == "hello from MVP host"
    finally:
        runtime.shutdown()


def test_langgraph_runtime_submit_and_run_thread(tmp_path: Path) -> None:
    runtime = LangGraphRuntime(
        _fake_agent(),  # type: ignore[arg-type]
        owner_id="mvp-user",
        persist_root=tmp_path / "lg-runtime-thread",
    )
    try:
        stimulus_id = runtime.submit_user_message(
            thread_id="thread-b",
            conversation_id="thread-b::0",
            text="queue me",
            schedule_drainer=False,
        )
        assert stimulus_id
        result = runtime.run_thread("thread-b")
        assert result["success"] is True
        assert result["runtime_engine_id"] == LANGGRAPH_RUNTIME_ENGINE
        assert result["results"][0]["graph_phase"] == "step_committed"
        assert runtime.inbox.pending_count("thread-b") == 0
    finally:
        runtime.shutdown()


def test_think_life_runtime_host_wraps_engine() -> None:
    engine = MagicMock()
    engine.health.return_value = {"profile": "think_life"}
    host = ThinkLifeRuntimeHost(engine)
    assert isinstance(host, RuntimeHost)
    assert host.runtime_engine_id == DEFAULT_RUNTIME_ENGINE
    assert host.health()["runtime_engine_id"] == DEFAULT_RUNTIME_ENGINE
