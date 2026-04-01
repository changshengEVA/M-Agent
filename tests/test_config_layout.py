from __future__ import annotations

import yaml

from m_agent.config_paths import (
    AGENT_RUNTIME_PROMPT_CONFIG_PATH,
    CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH,
    DEFAULT_CHAT_AGENT_CONFIG_PATH,
    DEFAULT_MEMORY_AGENT_CONFIG_PATH,
    DEFAULT_MEMORY_CORE_CONFIG_PATH,
    EPISODE_PROMPT_CONFIG_PATH,
    EXAMPLE_WEATHER_PROMPT_CONFIG_PATH,
    FACTS_FILTER_PROMPT_CONFIG_PATH,
    MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH,
    NEO4J_CONFIG_PATH,
    QA_LLM_JUDGE_PROMPT_CONFIG_PATH,
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
        AGENT_RUNTIME_PROMPT_CONFIG_PATH,
        CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH,
        MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH,
        EXAMPLE_WEATHER_PROMPT_CONFIG_PATH,
        QA_LLM_JUDGE_PROMPT_CONFIG_PATH,
        NEO4J_CONFIG_PATH,
    )
    for path in paths:
        assert path.exists(), f"Missing config file: {path}"


def test_canonical_agent_config_resolves_directly() -> None:
    resolved = resolve_config_path("config/agents/memory/locomo_eval_memory_agent.yaml")
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


def test_chat_controller_config_links_to_existing_memory_agent_config() -> None:
    with open(DEFAULT_CHAT_AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
        chat_config = yaml.safe_load(f) or {}

    memory_agent_path = resolve_related_config_path(
        DEFAULT_CHAT_AGENT_CONFIG_PATH,
        chat_config.get("memory_agent_config_path"),
    )

    assert memory_agent_path.exists()

    with open(memory_agent_path, "r", encoding="utf-8") as f:
        memory_agent_config = yaml.safe_load(f) or {}

    memory_core_path = resolve_related_config_path(
        memory_agent_path,
        memory_agent_config.get("memory_core_config_path"),
        default_path=DEFAULT_MEMORY_CORE_CONFIG_PATH,
    )

    assert memory_core_path.exists()


def test_runtime_prompt_configs_expose_bilingual_variants() -> None:
    with open(CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH, "r", encoding="utf-8") as f:
        chat_runtime = yaml.safe_load(f) or {}
    with open(AGENT_RUNTIME_PROMPT_CONFIG_PATH, "r", encoding="utf-8") as f:
        memory_agent_runtime = yaml.safe_load(f) or {}
    with open(MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH, "r", encoding="utf-8") as f:
        memory_runtime = yaml.safe_load(f) or {}

    merge_prompt = (
        chat_runtime.get("chat_controller", {})
        .get("merge_system_with_persona", {})
    )
    chat_system_prompt = (
        chat_runtime.get("chat_controller", {})
        .get("system_prompt", {})
    )
    chat_persona_prompt = (
        chat_runtime.get("chat_controller", {})
        .get("persona_prompt", {})
    )
    current_time_description = (
        chat_runtime.get("chat_controller", {})
        .get("tools", {})
        .get("get_current_time", {})
        .get("description", {})
    )
    final_synthesis_prompt = (
        memory_agent_runtime.get("memory_agent", {})
        .get("final_synthesis_prompt", {})
    )
    entity_search_prompt = (
        memory_runtime.get("entity_search", {})
        .get("llm_candidate_judge_prompt", {})
    )
    with open(EXAMPLE_WEATHER_PROMPT_CONFIG_PATH, "r", encoding="utf-8") as f:
        example_prompt_config = yaml.safe_load(f) or {}
    with open(QA_LLM_JUDGE_PROMPT_CONFIG_PATH, "r", encoding="utf-8") as f:
        eval_prompt_config = yaml.safe_load(f) or {}

    quick_start_prompt = (
        example_prompt_config.get("weather_demo", {})
        .get("system_prompt", {})
    )
    qa_judge_user_prompt = (
        eval_prompt_config.get("qa_llm_judge", {})
        .get("user_prompt_template", {})
    )

    for prompt_node in (
        chat_system_prompt,
        chat_persona_prompt,
        merge_prompt,
        current_time_description,
        final_synthesis_prompt,
        entity_search_prompt,
        quick_start_prompt,
        qa_judge_user_prompt,
    ):
        assert isinstance(prompt_node, dict)
        assert isinstance(prompt_node.get("zh"), str) and prompt_node["zh"].strip()
        assert isinstance(prompt_node.get("en"), str) and prompt_node["en"].strip()


def test_chat_agent_english_prompts_are_language_neutral() -> None:
    with open(DEFAULT_CHAT_AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
        chat_config = yaml.safe_load(f) or {}
    chat_runtime_path = resolve_related_config_path(
        DEFAULT_CHAT_AGENT_CONFIG_PATH,
        chat_config.get("runtime_prompt_config_path"),
        default_path=CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH,
    )
    with open(chat_runtime_path, "r", encoding="utf-8") as f:
        chat_runtime = yaml.safe_load(f) or {}
    chat_memory_agent_path = resolve_config_path("config/agents/memory/chat_memory_agent.yaml")
    with open(chat_memory_agent_path, "r", encoding="utf-8") as f:
        memory_config = yaml.safe_load(f) or {}

    chat_system_prompt_en = chat_runtime.get("chat_controller", {}).get("system_prompt", {}).get("en", "")
    chat_persona_prompt_en = chat_runtime.get("chat_controller", {}).get("persona_prompt", {}).get("en", "")
    memory_system_prompt_en = memory_config.get("system_prompt", {}).get("en", "")

    for text in (chat_system_prompt_en, chat_persona_prompt_en, memory_system_prompt_en):
        assert isinstance(text, str) and text.strip()
        assert "Chinese by default" not in text
        assert "Match the user's language" in text
