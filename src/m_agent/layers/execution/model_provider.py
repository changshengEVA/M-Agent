"""LLM model + retry/recursion provider shared by execution/thinking layers.

`ModelProvider` centralizes model access and retry/backoff knobs so execution
and thinking layers do not depend on a memory subsystem for model settings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yaml
from langchain.chat_models import init_chat_model
from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from m_agent.config_paths import resolve_related_config_path
from m_agent.paths import ENV_PATH
from m_agent.utils.api_error_utils import is_network_api_error


logger = logging.getLogger(__name__)


_STRUCTURED_OUTPUT_METHODS = {
    "function_calling",
    "json_mode",
    "json_schema",
}


class StructuredOutputError(RuntimeError):
    """Raised after a model repeatedly returns invalid structured output."""

    def __init__(
        self,
        *,
        call_name: str,
        schema: Any,
        attempts: int,
    ) -> None:
        schema_name = str(getattr(schema, "__name__", "structured output"))
        super().__init__(
            f"{call_name} returned invalid {schema_name} output after "
            f"{attempts} attempts"
        )
        self.call_name = str(call_name)
        self.schema_name = schema_name
        self.attempts = int(attempts)


@dataclass
class ModelProvider:
    """Hold a configured LangChain chat model + retry / recursion limits.

    The intent is to keep all model invocation knobs in one place so that
    higher layers (ExecutionAgent, ThinkingAgent) don't reach into MemoryAgent
    just to get a model handle. ``model`` is the LangChain chat model object;
    the other fields are configuration knobs used by callers that want to
    issue network-retried invocations.
    """

    model: Any
    model_name: str = ""
    recursion_limit: int = 60
    retry_recursion_limit: int = 120
    network_retry_attempts: int = 4
    network_retry_backoff_seconds: float = 2.0
    network_retry_backoff_multiplier: float = 2.0
    network_retry_max_backoff_seconds: float = 20.0
    structured_output_method: str = "function_calling"
    structured_retry_attempts: int = 2
    extras: dict = field(default_factory=dict)

    def normalized_structured_output_method(self) -> str:
        method = str(self.structured_output_method or "").strip().lower()
        if method not in _STRUCTURED_OUTPUT_METHODS:
            supported = ", ".join(sorted(_STRUCTURED_OUTPUT_METHODS))
            raise ValueError(
                f"unsupported structured_output_method {method!r}; "
                f"expected one of: {supported}"
            )
        return method

    @staticmethod
    def _unexpected_keyword_error(exc: TypeError) -> bool:
        message = str(exc or "").lower()
        return "unexpected keyword" in message or "keyword argument" in message

    def bind_structured_output(
        self,
        schema: Any,
        *,
        include_raw: bool = True,
    ) -> Any:
        """Bind a schema using an explicit, provider-stable output protocol."""

        method = self.normalized_structured_output_method()
        bind = self.model.with_structured_output
        try:
            return bind(schema, method=method, include_raw=include_raw)
        except TypeError as exc:
            if not self._unexpected_keyword_error(exc):
                raise
        try:
            return bind(schema, method=method)
        except TypeError as exc:
            if not self._unexpected_keyword_error(exc):
                raise
        logger.warning(
            "structured output provider does not accept an explicit method; "
            "falling back to provider defaults"
        )
        try:
            return bind(schema, include_raw=include_raw)
        except TypeError as exc:
            if not self._unexpected_keyword_error(exc):
                raise
        return bind(schema)

    @staticmethod
    def _unwrap_structured_result(result: Any) -> Any:
        if not isinstance(result, dict):
            return result
        envelope_keys = {"raw", "parsed", "parsing_error"}
        if not envelope_keys.issubset(result):
            return result
        parsing_error = result.get("parsing_error")
        if parsing_error is not None:
            if isinstance(parsing_error, BaseException):
                raise parsing_error
            raise OutputParserException(str(parsing_error))
        return result.get("parsed")

    def invoke_structured(
        self,
        schema: Any,
        *,
        messages: Any,
        call_name: str,
        validator: Optional[Callable[[Any], Any]] = None,
    ) -> Any:
        """Invoke with separate network and structured-semantic retry loops."""

        structured_model = self.bind_structured_output(schema, include_raw=True)
        total_attempts = max(int(self.structured_retry_attempts), 1)
        last_exc: Optional[BaseException] = None
        retryable = (
            ValidationError,
            OutputParserException,
            json.JSONDecodeError,
            ValueError,
        )

        for semantic_attempt in range(1, total_attempts + 1):
            try:
                def _network_attempt(_: int) -> Any:
                    return structured_model.invoke(messages)

                result = self.invoke_with_network_retry(
                    _network_attempt,
                    call_name=call_name,
                )
                result = self._unwrap_structured_result(result)
                if validator is not None:
                    result = validator(result)
                return result
            except retryable as exc:
                last_exc = exc
                if semantic_attempt >= total_attempts:
                    break
                logger.warning(
                    "%s returned invalid structured output on attempt %d/%d "
                    "(%s); retrying",
                    call_name,
                    semantic_attempt,
                    total_attempts,
                    type(exc).__name__,
                )

        error = StructuredOutputError(
            call_name=call_name,
            schema=schema,
            attempts=total_attempts,
        )
        if last_exc is not None:
            raise error from last_exc
        raise error

    def compute_network_retry_delay(self, attempt: int) -> float:
        """Return the exponential-backoff delay (seconds) for a given attempt (1-indexed)."""
        exponent = max(int(attempt) - 1, 0)
        delay = self.network_retry_backoff_seconds * (
            self.network_retry_backoff_multiplier ** exponent
        )
        return min(delay, self.network_retry_max_backoff_seconds)

    def invoke_with_network_retry(self, fn, *, call_name: str = "model.invoke") -> Any:
        """Run ``fn()`` with network-error retries, returning the first successful result.

        ``fn`` must be a zero-argument callable; callers typically capture the model
        invocation in a closure to allow per-attempt argument tweaks (e.g. different
        thread_id on recursion retry). The provider is only responsible for the
        network/backoff policy.
        """
        total_attempts = max(int(self.network_retry_attempts), 1)
        last_exc: Optional[BaseException] = None
        for attempt in range(1, total_attempts + 1):
            try:
                return fn(attempt)
            except Exception as exc:
                last_exc = exc
                if not is_network_api_error(exc) or attempt >= total_attempts:
                    raise
                delay = self.compute_network_retry_delay(attempt)
                logger.warning(
                    "%s hit network/API error on attempt %d/%d: %s; retrying in %.2fs",
                    call_name,
                    attempt,
                    total_attempts,
                    exc,
                    delay,
                )
                if delay > 0:
                    threading.Event().wait(delay)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"{call_name}: exhausted retry attempts unexpectedly")


def _resolve_langchain_chat_model_id(model_name: str) -> str:
    raw = str(model_name or "").strip()
    if not raw or ":" in raw:
        return raw
    lower = raw.lower()
    if lower.startswith("gpt-") or lower.startswith("o1") or lower.startswith("o3") or lower.startswith("o4"):
        return f"openai:{raw}"
    return raw


def _sync_openai_env_for_langchain() -> None:
    key = (os.getenv("API_SECRET_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    if key:
        os.environ["OPENAI_API_KEY"] = key
    base = (os.getenv("BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip()
    if base:
        os.environ["OPENAI_BASE_URL"] = base


def _load_model_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Chat model config not found: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Chat model config must be a dict: {path}")
    return payload


def build_model_provider_from_config(
    config_path: str | Path,
    *,
    config_dir: Optional[Path] = None,
) -> ModelProvider:
    """Build a :class:`ModelProvider` from ``config/agents/chat/chat_model.yaml``."""
    base = config_dir or Path(config_path).parent
    resolved = resolve_related_config_path(base, config_path)
    config = _load_model_config(resolved)

    model_name = str(
        os.getenv("OPENAI_MODEL")
        or config.get("model_name", "openai:deepseek-chat")
    ).strip()
    agent_temperature = float(config.get("agent_temperature", 0.0))
    timeout_raw = config.get("model_timeout_seconds")
    model_timeout = float(timeout_raw) if timeout_raw is not None else None
    if model_timeout is not None and model_timeout <= 0:
        model_timeout = None
    model_max_retries = max(0, int(config.get("model_max_retries", 2)))

    resolved_chat_model = _resolve_langchain_chat_model_id(model_name)
    if ENV_PATH.exists():
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=ENV_PATH)
    if resolved_chat_model.startswith("openai:"):
        _sync_openai_env_for_langchain()

    model = init_chat_model(
        resolved_chat_model,
        temperature=agent_temperature,
        max_tokens=None,
        timeout=model_timeout,
        max_retries=model_max_retries,
    )
    return ModelProvider(
        model=model,
        model_name=model_name,
        recursion_limit=int(config.get("recursion_limit", 60)),
        retry_recursion_limit=int(config.get("retry_recursion_limit", 120)),
        network_retry_attempts=int(config.get("network_retry_attempts", 4)),
        network_retry_backoff_seconds=float(config.get("network_retry_backoff_seconds", 2.0)),
        network_retry_backoff_multiplier=float(
            config.get("network_retry_backoff_multiplier", 2.0)
        ),
        network_retry_max_backoff_seconds=float(
            config.get("network_retry_max_backoff_seconds", 20.0)
        ),
        structured_output_method=str(
            config.get("structured_output_method", "function_calling")
        ),
        structured_retry_attempts=max(
            1,
            int(config.get("structured_retry_attempts", 2)),
        ),
    )
