"""LLM model + retry/recursion provider shared by execution/thinking layers.

`ModelProvider` centralizes model access and retry/backoff knobs so execution
and thinking layers do not depend on a memory subsystem for model settings.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Optional,
    Sequence,
    Tuple,
    get_args,
)

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

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_CALL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")
_SAFE_DECLARED_PATH = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)


def _safe_identifier(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    if _SAFE_IDENTIFIER.fullmatch(text):
        return text[:80]
    return fallback


def _safe_call_name(value: Any) -> str:
    text = str(value or "").strip()
    if _SAFE_CALL_NAME.fullmatch(text):
        return text[:120]
    return "structured_call"


def _schema_field_names(schema: Any) -> set[str]:
    """Return model-declared field names without inspecting response data."""

    names: set[str] = set()
    seen: set[int] = set()

    def visit(candidate: Any) -> None:
        identity = id(candidate)
        if identity in seen:
            return
        seen.add(identity)
        fields = getattr(candidate, "model_fields", None)
        if isinstance(fields, dict):
            for name, info in fields.items():
                normalized = str(name or "").strip()
                if _SAFE_IDENTIFIER.fullmatch(normalized):
                    names.add(normalized)
                annotation = getattr(info, "annotation", None)
                if annotation is not None:
                    visit(annotation)
                    for argument in get_args(annotation):
                        visit(argument)
        for argument in get_args(candidate):
            visit(argument)

    visit(schema)
    return names


def _safe_field_path(
    location: Iterable[Any],
    *,
    allowed_fields: set[str],
    mask_terminal_extra: bool = False,
) -> str:
    values = list(location)
    parts = []
    for index, raw in enumerate(values):
        if isinstance(raw, int):
            parts.append(f"[{max(0, raw)}]")
            continue
        value = str(raw or "").strip()
        is_terminal_extra = mask_terminal_extra and index == len(values) - 1
        if is_terminal_extra:
            parts.append("<extra>")
        elif value in allowed_fields and _SAFE_IDENTIFIER.fullmatch(value):
            parts.append(value)
        else:
            # Loc segments can contain user/model supplied mapping keys. Never
            # expose even identifier-looking unknown values in diagnostics.
            parts.append("<field>")
    path = ".".join(parts).replace(".[", "[")
    return path[:160] or "<root>"


def _safe_declared_field_path(value: Any) -> str:
    """Validate a developer-authored semantic field path."""

    text = str(value or "").strip()
    if _SAFE_DECLARED_PATH.fullmatch(text):
        return text[:160]
    return "<field>"


@dataclass(frozen=True)
class StructuredOutputAttemptDiagnostic:
    """Content-free diagnostics for one rejected structured response.

    The diagnostic deliberately records only exception classes, stable error
    codes and schema field locations. Raw model output, prompts, validation
    inputs and exception messages are never retained.
    """

    attempt: int
    stage: str
    category: str
    error_type: str
    issue_count: int = 1
    field_paths: Tuple[str, ...] = ()
    error_codes: Tuple[str, ...] = ()
    retryable: bool = True

    def to_safe_dict(self) -> Dict[str, Any]:
        return {
            "attempt": int(self.attempt),
            "stage": self.stage,
            "category": self.category,
            "error_type": self.error_type,
            "issue_count": max(1, int(self.issue_count)),
            "field_paths": list(self.field_paths),
            "error_codes": list(self.error_codes),
            "retryable": bool(self.retryable),
        }


class StructuredOutputSemanticError(ValueError):
    """Typed runtime-semantic rejection without embedding response content."""

    def __init__(
        self,
        code: str,
        *,
        field_paths: Sequence[str] = (),
    ) -> None:
        normalized_code = _safe_identifier(code, fallback="semantic_invalid")
        super().__init__(normalized_code)
        self.code = normalized_code
        self.field_paths = tuple(
            _safe_declared_field_path(path) for path in field_paths
        )[:12]


def _diagnose_structured_failure(
    exc: BaseException,
    *,
    attempt: int,
    stage: str,
    schema: Any,
    retryable: bool = True,
) -> StructuredOutputAttemptDiagnostic:
    safe_stage = _safe_identifier(stage, fallback="unknown")
    error_type = _safe_identifier(type(exc).__name__, fallback="Exception")
    field_paths: Tuple[str, ...] = ()
    error_codes: Tuple[str, ...] = ()
    issue_count = 1

    if isinstance(exc, StructuredOutputSemanticError):
        category = "runtime_semantic"
        field_paths = tuple(exc.field_paths)[:12]
        error_codes = (exc.code,)
    elif isinstance(exc, ValidationError):
        category = "schema"
        errors = exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        issue_count = max(1, len(errors))
        allowed_fields = _schema_field_names(schema)
        field_paths = tuple(
            dict.fromkeys(
                _safe_field_path(
                    item.get("loc", ()),
                    allowed_fields=allowed_fields,
                    mask_terminal_extra=(item.get("type") == "extra_forbidden"),
                )
                for item in errors
            )
        )[:12]
        error_codes = tuple(
            dict.fromkeys(
                _safe_identifier(item.get("type"), fallback="validation_error")
                for item in errors
            )
        )[:12]
    elif isinstance(exc, OutputParserException):
        category = "parse"
        error_codes = ("output_parser_error",)
    elif isinstance(exc, json.JSONDecodeError):
        category = "parse"
        error_codes = ("invalid_json",)
    elif safe_stage == "parse":
        category = "parse"
        error_codes = ("invalid_structured_envelope",)
    elif safe_stage in {"validate", "runtime_validate"}:
        # Only StructuredOutputSemanticError is a known runtime contract
        # rejection. A bare ValueError is intentionally not mislabeled.
        category = "validator"
        error_codes = ("validator_rejected",)
    elif safe_stage == "bind":
        category = "configuration"
        error_codes = ("structured_binding_failed",)
    elif is_network_api_error(exc):
        category = "network"
        error_codes = ("model_network_failed",)
    else:
        category = "provider"
        error_codes = ("model_provider_failed",)

    return StructuredOutputAttemptDiagnostic(
        attempt=max(1, int(attempt)),
        stage=safe_stage,
        category=category,
        error_type=error_type,
        issue_count=issue_count,
        field_paths=field_paths,
        error_codes=error_codes,
        retryable=retryable,
    )


class StructuredOutputError(RuntimeError):
    """Raised after a model repeatedly returns invalid structured output."""

    def __init__(
        self,
        *,
        call_name: str,
        schema: Any,
        attempts: int,
        diagnostics: Sequence[StructuredOutputAttemptDiagnostic] = (),
    ) -> None:
        schema_name = _safe_identifier(
            getattr(schema, "__name__", ""),
            fallback="StructuredOutput",
        )
        self.diagnostics = tuple(diagnostics)
        safe_call_name = _safe_call_name(call_name)
        fingerprint_payload = {
            "call_name": safe_call_name,
            "schema_name": schema_name,
            "diagnostics": [item.to_safe_dict() for item in self.diagnostics],
        }
        failure_signature = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:12]
        diagnostic_id = f"mdiag_{uuid.uuid4().hex}"
        category_counts = Counter(item.category for item in self.diagnostics)
        categories = ",".join(
            f"{name}:{count}" for name, count in sorted(category_counts.items())
        )
        last = self.diagnostics[-1] if self.diagnostics else None
        suffix = f"; diagnostic_id={diagnostic_id}"
        if categories:
            suffix += f"; categories={categories}"
        if last is not None:
            suffix += f"; last_stage={last.stage}; last_type={last.error_type}"
            if last.field_paths:
                suffix += f"; fields={','.join(last.field_paths)}"
        super().__init__(
            f"{safe_call_name} returned invalid {schema_name} output after "
            f"{attempts} attempts ({suffix.removeprefix('; ')})"
        )
        self.call_name = safe_call_name
        self.schema_name = schema_name
        self.attempts = int(attempts)
        self.diagnostic_id = diagnostic_id
        self.failure_signature = failure_signature
        self.retryable = bool(
            self.diagnostics
            and any(item.retryable for item in self.diagnostics)
        )

    def to_safe_dict(self) -> Dict[str, Any]:
        """Return a persistence/SSE-safe diagnostic without model content."""

        return {
            "error_code": "structured_output_invalid",
            "diagnostic_id": self.diagnostic_id,
            "failure_signature": self.failure_signature,
            "call_name": self.call_name,
            "schema_name": self.schema_name,
            "attempts": self.attempts,
            "retryable": self.retryable,
            "failures": [item.to_safe_dict() for item in self.diagnostics],
        }


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

        safe_call = _safe_call_name(call_name)
        try:
            structured_model = self.bind_structured_output(
                schema,
                include_raw=True,
            )
        except Exception as exc:
            diagnostic = _diagnose_structured_failure(
                exc,
                attempt=1,
                stage="bind",
                schema=schema,
                retryable=False,
            )
            error = StructuredOutputError(
                call_name=safe_call,
                schema=schema,
                attempts=1,
                diagnostics=(diagnostic,),
            )
            logger.error(
                "%s structured binding failed diagnostic_id=%s type=%s",
                safe_call,
                error.diagnostic_id,
                diagnostic.error_type,
            )
            raise error from None
        total_attempts = max(int(self.structured_retry_attempts), 1)
        diagnostics: list[StructuredOutputAttemptDiagnostic] = []
        attempt_messages = messages
        retryable = (
            ValidationError,
            OutputParserException,
            json.JSONDecodeError,
            StructuredOutputSemanticError,
            ValueError,
        )

        for semantic_attempt in range(1, total_attempts + 1):
            stage = "invoke"
            try:
                def _network_attempt(_: int) -> Any:
                    return structured_model.invoke(attempt_messages)

                result = self.invoke_with_network_retry(
                    _network_attempt,
                    call_name=safe_call,
                )
                stage = "parse"
                result = self._unwrap_structured_result(result)
                if validator is not None:
                    stage = "validate"
                    result = validator(result)
                return result
            except retryable as exc:
                diagnostic = _diagnose_structured_failure(
                    exc,
                    attempt=semantic_attempt,
                    stage=stage,
                    schema=schema,
                )
                diagnostics.append(diagnostic)
                if semantic_attempt >= total_attempts:
                    break
                logger.warning(
                    "%s returned invalid structured output on attempt %d/%d "
                    "category=%s stage=%s type=%s fields=%s codes=%s; retrying",
                    safe_call,
                    semantic_attempt,
                    total_attempts,
                    diagnostic.category,
                    diagnostic.stage,
                    diagnostic.error_type,
                    ",".join(diagnostic.field_paths) or "-",
                    ",".join(diagnostic.error_codes) or "-",
                )
                attempt_messages = self._with_structured_repair_instruction(
                    messages,
                    schema_name=_safe_identifier(
                        getattr(schema, "__name__", ""),
                        fallback="StructuredOutput",
                    ),
                    diagnostic=diagnostic,
                )
            except Exception as exc:
                diagnostic = _diagnose_structured_failure(
                    exc,
                    attempt=semantic_attempt,
                    stage=stage,
                    schema=schema,
                    retryable=is_network_api_error(exc),
                )
                diagnostics.append(diagnostic)
                error = StructuredOutputError(
                    call_name=safe_call,
                    schema=schema,
                    attempts=semantic_attempt,
                    diagnostics=diagnostics,
                )
                logger.error(
                    "%s structured provider call failed diagnostic_id=%s "
                    "category=%s type=%s",
                    safe_call,
                    error.diagnostic_id,
                    diagnostic.category,
                    diagnostic.error_type,
                )
                raise error from None

        error = StructuredOutputError(
            call_name=safe_call,
            schema=schema,
            attempts=total_attempts,
            diagnostics=diagnostics,
        )
        logger.error(
            "%s structured output exhausted retries diagnostic_id=%s "
            "categories=%s",
            error.call_name,
            error.diagnostic_id,
            ",".join(item.category for item in error.diagnostics) or "unknown",
        )
        # A parser or Pydantic exception can embed model output in its
        # traceback. Keep it out of logs, SSE and durable runtime records.
        raise error from None

    @staticmethod
    def _with_structured_repair_instruction(
        messages: Any,
        *,
        schema_name: str,
        diagnostic: StructuredOutputAttemptDiagnostic,
    ) -> Any:
        """Append a content-free correction hint for the next semantic try."""

        if not isinstance(messages, (list, tuple)):
            return messages
        fields = ", ".join(diagnostic.field_paths) or "the declared schema fields"
        codes = ", ".join(diagnostic.error_codes) or diagnostic.category
        hint = (
            f"The previous {schema_name} response was rejected. Return only a valid "
            f"{schema_name} object. Correct {fields} (validation codes: {codes}); "
            "include every required field and do not add undeclared fields."
        )
        amended = list(messages)
        for index, message in enumerate(amended):
            if not isinstance(message, dict):
                continue
            if str(message.get("role", "") or "").strip().lower() != "system":
                continue
            copied = dict(message)
            existing = str(copied.get("content", "") or "").rstrip()
            copied["content"] = f"{existing}\n\n{hint}" if existing else hint
            amended[index] = copied
            return amended
        amended.insert(0, {"role": "system", "content": hint})
        return amended

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
                    "%s hit network/API error on attempt %d/%d "
                    "type=%s; retrying in %.2fs",
                    _safe_call_name(call_name),
                    attempt,
                    total_attempts,
                    _safe_identifier(type(exc).__name__, fallback="Exception"),
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
