"""MemoryAgent syncs API_SECRET_KEY / BASE_URL into OPENAI_* for LangChain openai: models."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from m_agent.agents.memory_agent.core import _sync_openai_env_for_langchain


@pytest.fixture
def clean_openai_env(monkeypatch: pytest.MonkeyPatch):
    for k in (
        "API_SECRET_KEY",
        "OPENAI_API_KEY",
        "BASE_URL",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


def test_sync_prefers_api_secret_key_and_base_url(clean_openai_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_SECRET_KEY", "sk-from-secret")
    monkeypatch.setenv("BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-wrong-openai-only")

    _sync_openai_env_for_langchain()

    assert os.environ["OPENAI_API_KEY"] == "sk-from-secret"
    assert os.environ["OPENAI_BASE_URL"] == "https://example.com/v1"


def test_sync_falls_back_to_openai_key_when_no_secret(clean_openai_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-only")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy/v1")

    _sync_openai_env_for_langchain()

    assert os.environ["OPENAI_API_KEY"] == "sk-only"
    assert os.environ["OPENAI_BASE_URL"] == "https://proxy/v1"
