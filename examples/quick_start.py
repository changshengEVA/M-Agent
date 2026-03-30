from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from langchain.agents import create_agent
from langchain.tools import ToolRuntime, tool


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

from m_agent.config_paths import EXAMPLE_WEATHER_PROMPT_CONFIG_PATH
from m_agent.prompt_utils import load_resolved_prompt_config, normalize_prompt_language


PROMPT_LANGUAGE = normalize_prompt_language(os.getenv("PROMPT_LANGUAGE", "en"))
PROMPT_CONFIG = load_resolved_prompt_config(
    EXAMPLE_WEATHER_PROMPT_CONFIG_PATH,
    language=PROMPT_LANGUAGE,
)
SYSTEM_PROMPT = PROMPT_CONFIG.get("weather_demo", {}).get("system_prompt", "")
if not isinstance(SYSTEM_PROMPT, str) or not SYSTEM_PROMPT.strip():
    raise ValueError(
        f"Missing `weather_demo.system_prompt` in prompt config: {EXAMPLE_WEATHER_PROMPT_CONFIG_PATH}"
    )


@dataclass
class ResponseFormat:
    """Response schema for the agent."""

    punny_response: str
    weather_conditions: str | None = None


@dataclass
class Context:
    """Custom runtime context schema."""

    user_id: str


@tool
def get_weather_for_location(city: str) -> str:
    """Get weather for a given city."""

    return f"It's always sunny in {city}!"


@tool
def get_user_location(runtime: ToolRuntime[Context]) -> str:
    """Retrieve user information based on user ID."""

    user_id = runtime.context.user_id
    return "Florida" if user_id == "1" else "SF"


from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model


model = init_chat_model(
    "deepseek-chat",
    temperature=0.0,
    max_tokens=None,
    timeout=None,
    max_retries=2,
)

agent = create_agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=[get_user_location, get_weather_for_location],
    context_schema=Context,
    response_format=ToolStrategy(ResponseFormat),
)

print("Initialized agent successfully.")

config = {"configurable": {"thread_id": "1"}}

response = agent.invoke(
    {"messages": [{"role": "user", "content": "what is the weather outside?"}]},
    config=config,
    context=Context(user_id="1"),
)

print(response["structured_response"])
