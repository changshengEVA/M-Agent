from __future__ import annotations

from pathlib import Path
from typing import Any

from m_agent.paths import CONFIG_DIR, PROJECT_ROOT


AGENTS_CONFIG_DIR = CONFIG_DIR / "agents"
CHAT_AGENT_CONFIG_DIR = AGENTS_CONFIG_DIR / "chat"
EMAIL_AGENT_CONFIG_DIR = AGENTS_CONFIG_DIR / "email"
SCHEDULE_AGENT_CONFIG_DIR = AGENTS_CONFIG_DIR / "schedule"

PROMPTS_CONFIG_DIR = CONFIG_DIR / "prompts"
PROMPTS_EXAMPLES_DIR = PROMPTS_CONFIG_DIR / "examples"

INTEGRATIONS_CONFIG_DIR = CONFIG_DIR / "integrations"

DEFAULT_CHAT_AGENT_CONFIG_PATH = CHAT_AGENT_CONFIG_DIR / "chat_controller.yaml"
DEFAULT_CHAT_MODEL_CONFIG_PATH = CHAT_AGENT_CONFIG_DIR / "chat_model.yaml"
DEFAULT_EMAIL_AGENT_CONFIG_PATH = EMAIL_AGENT_CONFIG_DIR / "gmail_email_agent.yaml"
DEFAULT_SCHEDULE_AGENT_CONFIG_PATH = SCHEDULE_AGENT_CONFIG_DIR / "schedule_agent.yaml"
CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH = CHAT_AGENT_CONFIG_DIR / "runtime" / "chat_controller_runtime.yaml"
EXAMPLE_WEATHER_PROMPT_CONFIG_PATH = PROMPTS_EXAMPLES_DIR / "quick_start.yaml"
NEO4J_CONFIG_PATH = INTEGRATIONS_CONFIG_DIR / "neo4j.yaml"
WEB_SEARCH_CONFIG_PATH = INTEGRATIONS_CONFIG_DIR / "web_search.yaml"
OBSERVATION_MONITORS_CONFIG_PATH = (
    INTEGRATIONS_CONFIG_DIR / "observation_monitors.yaml"
)


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
