# 瀵煎叆鐜鍙橀噺
from dotenv import load_dotenv
load_dotenv()

SYSTEM_PROMPT = """You are an expert weather forecaster, who speaks in puns.

You have access to two tools:

- get_weather_for_location: use this to get the weather for a specific location
- get_user_location: use this to get the user's location

If a user asks you for the weather, make sure you know the location. If you can tell from the question that they mean wherever they are, use the get_user_location tool to find their location."""

from dataclasses import dataclass
from langchain.tools import tool, ToolRuntime
from langchain.agents import create_agent

# We use a dataclass here, but Pydantic models are also supported.
@dataclass
class ResponseFormat:
    """Response schema for the agent."""
    # A punny response (always required)
    punny_response: str
    # Any interesting information about the weather if available
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

from langchain.chat_models import init_chat_model

model = init_chat_model(
    "deepseek-chat",
    temperature=0.0,
    max_tokens=None,
    timeout=None,
    max_retries=2,
)

from langchain.agents.structured_output import ToolStrategy

agent = create_agent(
    model = model,
    system_prompt=SYSTEM_PROMPT,
    tools = [get_user_location, get_weather_for_location],
    context_schema=Context,
    response_format = ToolStrategy(ResponseFormat)
)

print("Initialized agent successfully.")

# `thread_id` is a unique identifier for a given conversation.
config = {"configurable": {"thread_id": "1"}}

response = agent.invoke(
    {"messages": [{"role": "user", "content": "what is the weather outside?"}]},
    config=config,
    context=Context(user_id="1")
)

print(response['structured_response'])
