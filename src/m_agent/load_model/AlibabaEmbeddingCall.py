import os
from pathlib import Path
from typing import List, Union

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

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "text-embedding-v4"


def _build_client():
    import openai

    api_key = os.getenv("ALIBABA_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ALIBABA_API_KEY is not set. Please configure it in .env")

    base_url = os.getenv("ALIBABA_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    return openai.OpenAI(api_key=api_key, base_url=base_url)


def get_embed_model(model_name: str = ""):
    client = _build_client()
    resolved_model = (model_name or os.getenv("ALIBABA_EMBED_MODEL", DEFAULT_MODEL)).strip() or DEFAULT_MODEL

    def embed_model(text: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
        if isinstance(text, str):
            query = text.strip()
            if not query:
                return []

            response = client.embeddings.create(
                model=resolved_model,
                input=query,
            )
            return response.data[0].embedding

        if isinstance(text, list):
            cleaned = [str(t).strip() for t in text if str(t).strip()]
            if not cleaned:
                return []

            response = client.embeddings.create(
                model=resolved_model,
                input=cleaned,
            )
            return [item.embedding for item in response.data]

        raise TypeError(f"Unsupported input type for embed_model: {type(text)}")

    return embed_model
