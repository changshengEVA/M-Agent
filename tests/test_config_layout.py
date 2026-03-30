from __future__ import annotations

import yaml

from m_agent.config_paths import (
    DEFAULT_CHAT_AGENT_CONFIG_PATH,
    DEFAULT_MEMORY_AGENT_CONFIG_PATH,
    DEFAULT_MEMORY_CORE_CONFIG_PATH,
    EPISODE_PROMPT_CONFIG_PATH,
    FACTS_FILTER_PROMPT_CONFIG_PATH,
    NEO4J_CONFIG_PATH,
    resolve_config_path,
    resolve_related_config_path,
)


def test_canonical_config_paths_exist() -> None:
    paths = (
        DEFAULT_MEMORY_AGENT_CONFIG_PATH,
        DEFAULT_CHAT_AGENT_CONFIG_PATH,
        DEFAULT_MEMORY_CORE_CONFIG_PATH,
        EPISODE_PROMPT_CONFIG_PATH,
        FACTS_FILTER_PROMPT_CONFIG_PATH,
        NEO4J_CONFIG_PATH,
    )
    for path in paths:
        assert path.exists(), f"Missing config file: {path}"


def test_canonical_agent_config_resolves_directly() -> None:
    resolved = resolve_config_path("config/agents/memory/agent_sys.yaml")
    assert resolved == DEFAULT_MEMORY_AGENT_CONFIG_PATH.resolve()


def test_agent_config_links_to_existing_memory_core_config() -> None:
    with open(DEFAULT_MEMORY_AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
        agent_config = yaml.safe_load(f) or {}

    resolved = resolve_related_config_path(
        DEFAULT_MEMORY_AGENT_CONFIG_PATH,
        agent_config.get("memory_core_config_path"),
        default_path=DEFAULT_MEMORY_CORE_CONFIG_PATH,
    )

    assert resolved == DEFAULT_MEMORY_CORE_CONFIG_PATH.resolve()
    assert resolved.exists()
