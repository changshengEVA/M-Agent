"""``ChatServiceRuntime`` propagates a ``systems_override`` into the chat agent.

The runtime is the programmatic entry point the API/CLI use to spin up
the chat stack. Phase A of the refactor lets callers inject a
:class:`~m_agent.systems.SystemsBundle` at construction time so a
custom subsystem (e.g. a stub WM reader in tests, or a sandboxed
episodic backend) can be wired without editing YAML.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from m_agent.api.chat_api_runtime import ChatServiceRuntime
from m_agent.chat.working_memory import WorkingMemoryConfig
from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE
from m_agent.systems import (
    DefaultWMDisplay,
    DefaultWMReader,
    DefaultWMWriter,
    SystemsBundle,
    WMSystem,
)


class _FakeAgent:
    """Minimal stand-in mirroring the ThreeLayerChatAgent surface the runtime touches."""

    arch_mode = "three_layer"
    default_thread_id = "test-thread"
    user_name = "user"
    assistant_name = "assistant"
    persist_memory = False
    working_memory_config = WorkingMemoryConfig(enable=True)
    config = {"runtime": {}}

    def __init__(self, systems: SystemsBundle | None = None) -> None:
        self.systems = systems
        self.received_kwargs: dict | None = None


class _FakeRuntimeHost:
    """Minimal RuntimeHost shell needed while testing agent-factory wiring."""

    runtime_engine_id = LANGGRAPH_RUNTIME_ENGINE

    def set_thread_event_emitter(self, emitter) -> None:
        self.emitter = emitter

    def set_history_provider(self, provider) -> None:
        self.history_provider = provider

    def set_schedule_lifecycle(self, lifecycle) -> None:
        self.schedule_lifecycle = lifecycle

    def shutdown(self) -> None:
        return None


def test_runtime_rejects_non_systems_bundle(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="SystemsBundle"):
        ChatServiceRuntime(
            config_path=tmp_path / "irrelevant.yaml",
            systems_override="not a bundle",  # type: ignore[arg-type]
        )


def test_runtime_forwards_systems_override_to_factory(tmp_path: Path) -> None:
    """When ``systems_override`` is set, ``create_chat_agent`` must receive it."""
    captured: dict = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return _FakeAgent(systems=kwargs.get("systems"))

    wm_cfg = WorkingMemoryConfig(enable=False, max_stored_entries=5)
    bundle = SystemsBundle(
        wm=WMSystem(
            writer=DefaultWMWriter(wm_cfg),
            reader=DefaultWMReader(wm_cfg),
            display=DefaultWMDisplay(wm_cfg),
            config=wm_cfg,
        )
    )

    with (
        patch("m_agent.api.chat_api_runtime.create_chat_agent", side_effect=_factory),
        patch(
            "m_agent.api.chat_api_runtime.create_runtime_host",
            return_value=_FakeRuntimeHost(),
        ),
    ):
        rt = ChatServiceRuntime(
            config_path=tmp_path / "irrelevant.yaml",
            idle_flush_seconds=0,
            history_max_rounds=2,
            idle_scan_interval_seconds=60,
            systems_override=bundle,
        )

    try:
        assert captured["systems"] is bundle
        assert captured["config_path"] == (tmp_path / "irrelevant.yaml").resolve()
    finally:
        rt.shutdown()


def test_runtime_default_does_not_force_systems_override(tmp_path: Path) -> None:
    """Omitting ``systems_override`` keeps the factory call clean (``systems=None``)."""
    captured: dict = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return _FakeAgent()

    with (
        patch("m_agent.api.chat_api_runtime.create_chat_agent", side_effect=_factory),
        patch(
            "m_agent.api.chat_api_runtime.create_runtime_host",
            return_value=_FakeRuntimeHost(),
        ),
    ):
        rt = ChatServiceRuntime(
            config_path=tmp_path / "irrelevant.yaml",
            idle_flush_seconds=0,
            idle_scan_interval_seconds=60,
        )

    try:
        assert captured.get("systems") is None
    finally:
        rt.shutdown()
