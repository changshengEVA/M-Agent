from __future__ import annotations

import json
import logging
import os
from typing import Any

import pytest
from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ConfigDict

from m_agent.layers.execution import (
    CapabilityDescriptor,
    ExecutionResult,
    ModelProvider,
)
from m_agent.layers.execution.model_provider import (
    StructuredOutputError,
    StructuredOutputSemanticError,
)
from m_agent.systems.episodic import EpisodeQueryModule
from m_agent.layers.execution import model_provider as model_provider_module


_SENTINEL_SECRET = "SENTINEL_SECRET_7f4c2d9a"


class _ProviderProbeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


class _QueuedStructuredModel:
    def __init__(self, owner: "_QueuedChatModel") -> None:
        self._owner = owner

    def invoke(self, messages: Any) -> Any:
        self._owner.calls.append(list(messages))
        if not self._owner.responses:
            raise AssertionError("queued model ran out of responses")
        response = self._owner.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class _QueuedChatModel:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []
        self.bindings: list[dict[str, Any]] = []

    def with_structured_output(
        self,
        _schema: type,
        **kwargs: Any,
    ) -> _QueuedStructuredModel:
        self.bindings.append(dict(kwargs))
        return _QueuedStructuredModel(self)


def _probe_validator(value: Any) -> _ProviderProbeOutput:
    return _ProviderProbeOutput.model_validate(value)


def _provider_for(
    responses: list[Any],
    *,
    attempts: int = 1,
) -> tuple[ModelProvider, _QueuedChatModel]:
    model = _QueuedChatModel(responses)
    provider = ModelProvider(
        model=model,
        network_retry_attempts=1,
        structured_retry_attempts=attempts,
    )
    return provider, model


def _invoke_probe(
    provider: ModelProvider,
    *,
    validator: Any = _probe_validator,
) -> _ProviderProbeOutput:
    return provider.invoke_structured(
        _ProviderProbeOutput,
        messages=[
            {"role": "system", "content": "Return the requested object."},
            {"role": "user", "content": "Give a short answer."},
        ],
        call_name="thinking.turn",
        validator=validator,
    )


def _failure_payload(error: StructuredOutputError) -> list[dict[str, Any]]:
    payload = error.to_safe_dict()
    failures = payload.get("failures")
    assert isinstance(failures, list)
    return failures


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


def test_structured_output_diagnoses_schema_errors_without_validation_input() -> None:
    provider, _model = _provider_for(
        [{_SENTINEL_SECRET: _SENTINEL_SECRET}],
    )

    with pytest.raises(StructuredOutputError) as exc_info:
        _invoke_probe(provider)

    failures = _failure_payload(exc_info.value)
    assert len(failures) == 1
    assert failures[0]["category"] == "schema"
    assert failures[0]["stage"] == "validate"
    assert "answer" in failures[0]["field_paths"]
    assert set(failures[0]["error_codes"]) == {
        "missing",
        "extra_forbidden",
    }
    assert _SENTINEL_SECRET not in json.dumps(
        exc_info.value.to_safe_dict(),
        ensure_ascii=False,
    )


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (
            OutputParserException(
                f"could not parse {_SENTINEL_SECRET}",
                llm_output=_SENTINEL_SECRET,
            ),
            "output_parser_error",
        ),
        (
            json.JSONDecodeError(
                "invalid structured JSON",
                _SENTINEL_SECRET,
                0,
            ),
            "invalid_json",
        ),
    ],
    ids=["output-parser", "json-syntax"],
)
def test_structured_output_diagnoses_parser_failures_as_parse(
    failure: BaseException,
    expected_code: str,
) -> None:
    provider, _model = _provider_for(
        [
            {
                "raw": _SENTINEL_SECRET,
                "parsed": None,
                "parsing_error": failure,
            }
        ]
    )

    with pytest.raises(StructuredOutputError) as exc_info:
        _invoke_probe(provider)

    failures = _failure_payload(exc_info.value)
    assert len(failures) == 1
    assert failures[0]["category"] == "parse"
    assert failures[0]["stage"] == "parse"
    assert failures[0]["error_codes"] == [expected_code]


def test_structured_output_diagnoses_typed_runtime_semantic_failure() -> None:
    provider, _model = _provider_for([{"answer": "valid schema"}])

    def reject_semantics(_value: Any) -> Any:
        raise StructuredOutputSemanticError(
            "unknown_or_disabled_tool",
            field_paths=("decision.tool_name",),
        )

    with pytest.raises(StructuredOutputError) as exc_info:
        _invoke_probe(provider, validator=reject_semantics)

    failures = _failure_payload(exc_info.value)
    assert len(failures) == 1
    assert failures[0]["category"] == "runtime_semantic"
    assert failures[0]["stage"] == "validate"
    assert failures[0]["field_paths"] == ["decision.tool_name"]
    assert failures[0]["error_codes"] == ["unknown_or_disabled_tool"]


def test_structured_retry_adds_content_free_repair_hint_then_recovers() -> None:
    provider, model = _provider_for(
        [
            {_SENTINEL_SECRET: _SENTINEL_SECRET},
            {"answer": "recovered"},
        ],
        attempts=2,
    )

    output = _invoke_probe(provider)

    assert output.answer == "recovered"
    assert len(model.calls) == 2
    retry_messages = model.calls[1]
    assert [message["role"] for message in retry_messages] == [
        message["role"] for message in model.calls[0]
    ]
    assert retry_messages[1] == model.calls[0][1]
    original_system = str(model.calls[0][0]["content"])
    retry_system = str(retry_messages[0]["content"])
    assert retry_system.startswith(original_system)
    repair_hint = retry_system[len(original_system) :]
    assert repair_hint
    assert "answer" in repair_hint
    assert "missing" in repair_hint
    assert _SENTINEL_SECRET not in repair_hint


def test_exhausted_structured_error_is_safe_across_all_surfaces(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failures = [
        OutputParserException(
            f"private parse failure: {_SENTINEL_SECRET}",
            llm_output=_SENTINEL_SECRET,
        ),
        OutputParserException(
            f"private parse failure: {_SENTINEL_SECRET}",
            llm_output=_SENTINEL_SECRET,
        ),
    ]
    provider, model = _provider_for(failures, attempts=2)

    with caplog.at_level(logging.WARNING), pytest.raises(
        StructuredOutputError
    ) as exc_info:
        _invoke_probe(provider)

    error = exc_info.value
    assert error.call_name == "thinking.turn"
    assert error.__cause__ is None
    assert _SENTINEL_SECRET not in str(error)
    assert _SENTINEL_SECRET not in json.dumps(
        error.to_safe_dict(),
        ensure_ascii=False,
    )
    assert _SENTINEL_SECRET not in caplog.text
    retry_request = json.dumps(
        model.calls[1],
        ensure_ascii=False,
    )
    assert "previous" in retry_request.lower()
    assert _SENTINEL_SECRET not in retry_request


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


def test_model_provider_defaults_to_function_calling_structured_output(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "chat_model.yaml"
    config_path.write_text(
        "model_name: openai:deepseek-chat\nagent_temperature: 0.0\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setattr(
        model_provider_module,
        "init_chat_model",
        lambda *_args, **_kwargs: object(),
    )

    provider = model_provider_module.build_model_provider_from_config(config_path)

    assert provider.structured_output_method == "function_calling"
    assert provider.structured_retry_attempts == 2


def test_model_provider_loads_explicit_structured_output_settings(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "chat_model.yaml"
    config_path.write_text(
        "\n".join(
            (
                "model_name: openai:deepseek-chat",
                "structured_output_method: json_schema",
                "structured_retry_attempts: 5",
                "",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setattr(
        model_provider_module,
        "init_chat_model",
        lambda *_args, **_kwargs: object(),
    )

    provider = model_provider_module.build_model_provider_from_config(config_path)

    assert provider.structured_output_method == "json_schema"
    assert provider.structured_retry_attempts == 5
