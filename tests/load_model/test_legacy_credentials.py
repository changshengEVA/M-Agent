from __future__ import annotations

import inspect

import pytest
import yaml

from m_agent.config_paths import NEO4J_CONFIG_PATH
from m_agent.load_model import deepseekcall


def test_deepseek_example_requires_environment_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        deepseekcall.create_deepseek_client()

    assert "sk-" not in inspect.getsource(deepseekcall)


def test_neo4j_config_names_environment_secret() -> None:
    payload = yaml.safe_load(NEO4J_CONFIG_PATH.read_text(encoding="utf-8"))

    assert "password" not in payload
    assert payload["password_env"] == "NEO4J_PASSWORD"
