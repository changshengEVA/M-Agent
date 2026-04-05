from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


SUPPORTED_PROMPT_LANGUAGES = {"zh", "en"}

_PROMPT_LANGUAGE_ALIASES = {
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "cn": "zh",
    "chinese": "zh",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "english": "en",
}


def normalize_prompt_language(value: Any, default: str = "zh") -> str:
    raw = str(value or default).strip().lower().replace("_", "-")
    normalized = _PROMPT_LANGUAGE_ALIASES.get(raw, raw)
    if normalized not in SUPPORTED_PROMPT_LANGUAGES:
        supported = ", ".join(sorted(SUPPORTED_PROMPT_LANGUAGES))
        raise ValueError(f"Unsupported prompt language: {value!r}. Use one of: {supported}")
    return normalized


def load_yaml_config(path: str | Path) -> Dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Prompt config not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    if not isinstance(config, dict):
        raise ValueError(f"Prompt config must be a dict: {config_path}")
    return config


def is_prompt_variant_mapping(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    language_keys = [key for key in node.keys() if str(key) in SUPPORTED_PROMPT_LANGUAGES]
    return bool(language_keys)


def resolve_prompt_value(node: Any, language: str, path_desc: str) -> str:
    if isinstance(node, str):
        text = node.strip()
        if not text:
            raise ValueError(f"Prompt text is empty: {path_desc}")
        return text

    if not is_prompt_variant_mapping(node):
        raise ValueError(f"Prompt entry must be a string or bilingual mapping: {path_desc}")

    lang_key = normalize_prompt_language(language)
    text = node.get(lang_key)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"Prompt variant '{lang_key}' is missing or empty: {path_desc}")
    return text.strip()


def resolve_prompt_tree(node: Any, language: str, path_desc: str = "root") -> Any:
    if isinstance(node, str):
        return node

    if is_prompt_variant_mapping(node):
        return resolve_prompt_value(node, language=language, path_desc=path_desc)

    if isinstance(node, dict):
        return {
            str(key): resolve_prompt_tree(
                value,
                language=language,
                path_desc=f"{path_desc}.{key}",
            )
            for key, value in node.items()
        }

    if isinstance(node, list):
        return [
            resolve_prompt_tree(
                item,
                language=language,
                path_desc=f"{path_desc}[{index}]",
            )
            for index, item in enumerate(node)
        ]

    return node


def load_resolved_prompt_config(path: str | Path, language: str) -> Dict[str, Any]:
    config = load_yaml_config(path)
    resolved = resolve_prompt_tree(config, language=language, path_desc=str(Path(path)))
    if not isinstance(resolved, dict):
        raise ValueError(f"Resolved prompt config must be a dict: {path}")
    return resolved


def render_prompt_template(template: str, replacements: Dict[str, Any]) -> str:
    rendered = str(template or "")
    for placeholder, value in replacements.items():
        rendered = rendered.replace(str(placeholder), str(value))
    return rendered


def replace_prompt_placeholders(value: Any, replacements: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        return render_prompt_template(value, replacements)
    if isinstance(value, dict):
        return {
            key: replace_prompt_placeholders(sub_value, replacements)
            for key, sub_value in value.items()
        }
    if isinstance(value, list):
        return [replace_prompt_placeholders(item, replacements) for item in value]
    return value
