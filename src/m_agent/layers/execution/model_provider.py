"""LLM model + retry/recursion provider shared by execution/thinking layers.

`ModelProvider` centralizes model access and retry/backoff knobs so execution
and thinking layers do not depend on a memory subsystem for model settings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from langchain.chat_models import init_chat_model

from m_agent.config_paths import resolve_related_config_path
from m_agent.paths import ENV_PATH
from m_agent.utils.api_error_utils import is_network_api_error


logger = logging.getLogger(__name__)


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
    extras: dict = field(default_factory=dict)

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
    )
