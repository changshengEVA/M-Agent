from __future__ import annotations

import os

import pytest

from m_agent.layers.execution import (
    CapabilityDescriptor,
    ExecutionResult,
    ModelProvider,
)
from m_agent.systems.episodic import EpisodeQueryModule
from m_agent.layers.execution import model_provider as model_provider_module


def test_execution_result_tool_call_count_and_names() -> None:
    result = ExecutionResult(
        summary="done",
        tool_history=[
            {"tool_name": "shallow_recall", "result": {"answer": "x"}},
            {"tool_name": "shallow_recall", "result": {"answer": "y"}},
            {"tool_name": "get_current_time", "result": {"ok": True}},
            {"tool_name": "", "result": {}},  # should be ignored
            {},  # should be ignored
        ],
    )
    assert result.tool_call_count == 3
    assert result.tool_names == ["shallow_recall", "get_current_time"]


def test_episode_query_module_filters_when_disabled() -> None:
    module = EpisodeQueryModule(enabled=False)
    filtered = module.filter_capability_names(
        ["shallow_recall", "deep_recall", "email_ask", "get_current_time"]
    )
    assert filtered == ["email_ask", "get_current_time"]
    assert module.is_blocked("shallow_recall") is True
    assert module.is_blocked("email_ask") is False


def test_episode_query_module_enabled_passes_through() -> None:
    module = EpisodeQueryModule(enabled=True)
    filtered = module.filter_capability_names(
        ["shallow_recall", "deep_recall", "email_ask"]
    )
    assert filtered == ["shallow_recall", "deep_recall", "email_ask"]
    assert module.is_blocked("shallow_recall") is False


def test_capability_descriptor_is_frozen() -> None:
    desc = CapabilityDescriptor(
        name="shallow_recall",
        category="episode_query",
        short_description="...",
    )
    with pytest.raises((AttributeError, Exception)):
        desc.name = "deep_recall"  # type: ignore[misc]


def test_model_provider_backoff_is_capped() -> None:
    provider = ModelProvider(
        model=None,
        network_retry_backoff_seconds=2.0,
        network_retry_backoff_multiplier=10.0,
        network_retry_max_backoff_seconds=15.0,
    )
    # attempt=1 -> 2.0 ; attempt=2 -> 20.0 capped at 15.0
    assert provider.compute_network_retry_delay(1) == pytest.approx(2.0)
    assert provider.compute_network_retry_delay(2) == pytest.approx(15.0)
    assert provider.compute_network_retry_delay(5) == pytest.approx(15.0)


def test_model_provider_invoke_with_network_retry_returns_first_success() -> None:
    calls: list[int] = []

    def fake_invoke(attempt: int) -> str:
        calls.append(attempt)
        return f"ok-{attempt}"

    provider = ModelProvider(model=None, network_retry_attempts=3)
    assert provider.invoke_with_network_retry(fake_invoke) == "ok-1"
    assert calls == [1]


def test_model_provider_honors_openai_compatible_environment(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "chat_model.yaml"
    config_path.write_text(
        "model_name: openai:deepseek-chat\nagent_temperature: 0.0\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_init_chat_model(model: str, **kwargs):
        captured["model"] = model
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setenv("API_SECRET_KEY", "compatible-test-key")
    monkeypatch.setenv("BASE_URL", "https://compatible.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(
        model_provider_module,
        "init_chat_model",
        fake_init_chat_model,
    )

    provider = model_provider_module.build_model_provider_from_config(
        config_path,
    )

    assert provider.model_name == "gpt-4o-mini"
    assert captured["model"] == "openai:gpt-4o-mini"
    assert os.environ["OPENAI_API_KEY"] == "compatible-test-key"
    assert os.environ["OPENAI_BASE_URL"] == "https://compatible.example/v1"
