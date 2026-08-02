from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from m_agent.runtime.domain.contracts import TransactionKind
from m_agent.runtime.host import RuntimeHost, create_runtime_host
from m_agent.runtime.langgraph.runtime import LangGraphRuntime
from m_agent.runtime.routing import (
    LANGGRAPH_RUNTIME_ENGINE,
    resolve_runtime_engine_from_config,
)


class _FakeThinkingAgent:
    def resolve_transaction(self, *_args, **_kwargs):
        return None


def _fake_agent(runtime: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        thinking_agent=_FakeThinkingAgent(),
        execution_agent=None,
        systems=SimpleNamespace(wm=None),
        config={
            "runtime": runtime
            or {
                "common": {},
                "langgraph": {"turn_loop": False},
            }
        },
        user_name="User",
        assistant_name="Assistant",
        default_thread_id="host-test-thread",
        persist_memory=False,
    )


def test_factory_returns_the_runtime_protocol(tmp_path: Path) -> None:
    host = create_runtime_host(
        agent=_fake_agent(),  # type: ignore[arg-type]
        owner_id="host-user",
        persist_root=tmp_path / "runtime",
    )
    try:
        assert isinstance(host, LangGraphRuntime)
        assert isinstance(host, RuntimeHost)
        assert host.runtime_engine_id == LANGGRAPH_RUNTIME_ENGINE
        assert host.turn_loop_enabled is False
    finally:
        host.shutdown()


def test_factory_rejects_removed_selector_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("M_AGENT_DEFAULT_RUNTIME_ENGINE", LANGGRAPH_RUNTIME_ENGINE)
    with pytest.raises(ValueError, match="was removed"):
        resolve_runtime_engine_from_config({})


@pytest.mark.parametrize(
    "runtime_config",
    [
        {"default_engine": LANGGRAPH_RUNTIME_ENGINE},
        {"runtime_engine": LANGGRAPH_RUNTIME_ENGINE},
        {"_".join(("think", "life")): {}},
        {"selected": "_".join(("think", "life", "v1"))},
    ],
)
def test_factory_rejects_removed_runtime_configuration(
    runtime_config: dict,
) -> None:
    with pytest.raises(ValueError, match="removed runtime configuration"):
        resolve_runtime_engine_from_config(runtime_config)


def test_factory_rejects_unknown_explicit_runtime() -> None:
    with pytest.raises(ValueError, match="only 'langgraph_v1'"):
        resolve_runtime_engine_from_config({}, explicit="unknown_v2")


def test_final_runtime_rejects_online_retired_database(tmp_path: Path) -> None:
    persistence_root = tmp_path / "runtime-user"
    runtime_dir = persistence_root / "runtime"
    runtime_dir.mkdir(parents=True)
    retired_name = "".join(("think", "_", "life", ".sqlite3"))
    (runtime_dir / retired_name).write_bytes(b"retired")

    with pytest.raises(RuntimeError, match="retired runtime database remains"):
        LangGraphRuntime(
            _fake_agent(),  # type: ignore[arg-type]
            owner_id="retired-data-user",
            persist_root=persistence_root,
        )


def test_langgraph_runtime_advance_transaction_mvp(tmp_path: Path) -> None:
    runtime = LangGraphRuntime(
        _fake_agent(),  # type: ignore[arg-type]
        owner_id="mvp-user",
        persist_root=tmp_path / "runtime",
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
        owner_id="thread-user",
        persist_root=tmp_path / "runtime",
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
        assert runtime.pending_count("thread-b") == 0
    finally:
        runtime.shutdown()
