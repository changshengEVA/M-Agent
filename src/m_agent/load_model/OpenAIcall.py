import os
from pathlib import Path

from m_agent.paths import ENV_PATH


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    try:
        from dotenv import load_dotenv  # type: ignore
    except ModuleNotFoundError:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    else:
        load_dotenv(dotenv_path=path)


_load_env_file(ENV_PATH)

api_key = os.getenv("API_SECRET_KEY") or os.getenv("OPENAI_API_KEY")
base_url = os.getenv("BASE_URL") or "https://api.openai.com/v1"
default_model = os.getenv("OPENAI_MODEL") or "gpt-4o-mini"


def _build_client(
    api_key_override: str | None = None,
    base_url_override: str | None = None,
):
    import openai

    return openai.OpenAI(
        api_key=api_key_override or api_key,
        base_url=base_url_override or base_url,
    )


def get_chat_llm(
    model_temperature: float,
    model_name: str | None = None,
    api_key_override: str | None = None,
    base_url_override: str | None = None,
    system_prompt: str | None = None,
    max_tokens: int = 2000,
):
    """
    Return a simple chat callable used across the repo.

    The returned function accepts one user prompt string and returns text.
    """

    client = _build_client(api_key_override, base_url_override)
    resolved_model = model_name or default_model

    def llm(prompt: str) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            temperature=model_temperature,
            max_tokens=max_tokens,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0,
        )
        return response.choices[0].message.content.strip()

    return llm


def get_llm(model_temperature: float):
    """
    Backward-compatible default LLM factory used by the agent code.
    """

    return get_chat_llm(model_temperature=model_temperature)


def get_embed_model():
    """
    Get an instance of the OpenAI embedding model using OpenAI 1.x API.
    """

    client = _build_client()

    def embed_model(text):
        response = client.embeddings.create(
            model="text-embedding-ada-002",
            input=text,
        )
        return response.data[0].embedding

    return embed_model
