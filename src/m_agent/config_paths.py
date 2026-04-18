from __future__ import annotations

from pathlib import Path
from typing import Any

from m_agent.paths import CONFIG_DIR, PROJECT_ROOT


AGENTS_CONFIG_DIR = CONFIG_DIR / "agents"
MEMORY_AGENT_CONFIG_DIR = AGENTS_CONFIG_DIR / "memory"
CHAT_AGENT_CONFIG_DIR = AGENTS_CONFIG_DIR / "chat"
EMAIL_AGENT_CONFIG_DIR = AGENTS_CONFIG_DIR / "email"
SCHEDULE_AGENT_CONFIG_DIR = AGENTS_CONFIG_DIR / "schedule"

MEMORY_CONFIG_DIR = CONFIG_DIR / "memory"
MEMORY_CORE_CONFIG_DIR = MEMORY_CONFIG_DIR / "core"

PROMPTS_CONFIG_DIR = CONFIG_DIR / "prompts"
PROMPTS_MEMORY_BUILD_DIR = PROMPTS_CONFIG_DIR / "memory_build"
PROMPTS_FILTERING_DIR = PROMPTS_CONFIG_DIR / "filtering"
PROMPTS_ENTITY_DIR = PROMPTS_CONFIG_DIR / "entity"
PROMPTS_EXAMPLES_DIR = PROMPTS_CONFIG_DIR / "examples"
PROMPTS_EVALUATION_DIR = PROMPTS_CONFIG_DIR / "evaluation"

INTEGRATIONS_CONFIG_DIR = CONFIG_DIR / "integrations"

DEFAULT_MEMORY_AGENT_CONFIG_PATH = MEMORY_AGENT_CONFIG_DIR / "locomo_eval_memory_agent.yaml"
DEFAULT_CHAT_AGENT_CONFIG_PATH = CHAT_AGENT_CONFIG_DIR / "chat_controller.yaml"
DEFAULT_EMAIL_AGENT_CONFIG_PATH = EMAIL_AGENT_CONFIG_DIR / "gmail_email_agent.yaml"
DEFAULT_SCHEDULE_AGENT_CONFIG_PATH = SCHEDULE_AGENT_CONFIG_DIR / "schedule_agent.yaml"
DEFAULT_MEMORY_CORE_CONFIG_PATH = MEMORY_CORE_CONFIG_DIR / "locomo_eval_memory_core.yaml"

EPISODE_PROMPT_CONFIG_PATH = PROMPTS_MEMORY_BUILD_DIR / "episode.yaml"
FACT_EXTRACTION_PROMPT_CONFIG_PATH = PROMPTS_MEMORY_BUILD_DIR / "fact_extraction.yaml"
SCENE_PROMPT_CONFIG_PATH = PROMPTS_MEMORY_BUILD_DIR / "scene.yaml"
FACTS_FILTER_PROMPT_CONFIG_PATH = PROMPTS_FILTERING_DIR / "facts_filter.yaml"
KG_FILTER_PROMPT_CONFIG_PATH = PROMPTS_FILTERING_DIR / "kg_filter.yaml"
ENTITY_STATEMENT_PROMPT_CONFIG_PATH = PROMPTS_ENTITY_DIR / "make_entity_statement.yaml"
MEMORY_AGENT_TOOL_DESCRIPTIONS_PATH = MEMORY_AGENT_CONFIG_DIR / "tool_descriptions.yaml"
AGENT_RUNTIME_PROMPT_CONFIG_PATH = MEMORY_AGENT_CONFIG_DIR / "runtime" / "agent_runtime.yaml"
CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH = CHAT_AGENT_CONFIG_DIR / "runtime" / "chat_controller_runtime.yaml"
MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH = MEMORY_CORE_CONFIG_DIR / "runtime" / "memory_core_runtime.yaml"
EXAMPLE_WEATHER_PROMPT_CONFIG_PATH = PROMPTS_EXAMPLES_DIR / "quick_start.yaml"
QA_LLM_JUDGE_PROMPT_CONFIG_PATH = PROMPTS_EVALUATION_DIR / "qa_llm_judge.yaml"

NEO4J_CONFIG_PATH = INTEGRATIONS_CONFIG_DIR / "neo4j.yaml"
EMAIL_CONFIG_PATH = INTEGRATIONS_CONFIG_DIR / "email.yaml"


def resolve_config_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (PROJECT_ROOT / candidate).resolve()


def resolve_related_config_path(
    base_path: str | Path,
    raw_path: Any,
    *,
    default_path: str | Path | None = None,
) -> Path:
    if raw_path is None or not str(raw_path).strip():
        if default_path is None:
            return resolve_config_path(base_path)
        return resolve_config_path(default_path)

    candidate = Path(str(raw_path).strip())
    if candidate.is_absolute():
        return resolve_config_path(candidate)

    base_resolved = resolve_config_path(base_path)
    return (base_resolved.parent / candidate).resolve()
