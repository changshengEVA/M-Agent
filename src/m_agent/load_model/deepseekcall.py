"""Minimal DeepSeek chat example with environment-backed credentials."""

from __future__ import annotations

import os

from openai import OpenAI


def create_deepseek_client() -> OpenAI:
    """Create a client without embedding credentials in source control."""

    api_key = str(os.getenv("DEEPSEEK_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    base_url = (
        str(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com") or "").strip()
        or "https://api.deepseek.com"
    )
    return OpenAI(api_key=api_key, base_url=base_url)


def main() -> None:
    client = create_deepseek_client()
    response = client.chat.completions.create(
        model=str(os.getenv("DEEPSEEK_MODEL", "deepseek-reasoner") or "deepseek-reasoner"),
        messages=[{"role": "user", "content": "你是谁？"}],
    )
    print("response:", response.choices[0].message.content)


if __name__ == "__main__":
    main()
