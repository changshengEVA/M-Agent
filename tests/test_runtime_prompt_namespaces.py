from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}
    assert isinstance(payload, dict)
    return payload


def test_chat_and_memory_agent_runtime_namespaces_are_isolated() -> None:
    chat_runtime = _load_yaml(PROJECT_ROOT / "config" / "agents" / "chat" / "runtime" / "chat_controller_runtime.yaml")
    memory_runtime = _load_yaml(PROJECT_ROOT / "config" / "agents" / "memory" / "runtime" / "agent_runtime.yaml")

    assert set(chat_runtime.keys()) == {"chat_controller"}
    assert set(memory_runtime.keys()) == {"memory_agent"}


def test_memory_core_runtime_does_not_mix_agent_level_namespaces() -> None:
    memory_core_runtime = _load_yaml(PROJECT_ROOT / "config" / "memory" / "core" / "runtime" / "memory_core_runtime.yaml")

    assert "chat_controller" not in memory_core_runtime
    assert "memory_agent" not in memory_core_runtime


def test_memory_agent_runtime_contains_unified_prompts() -> None:
    memory_runtime = _load_yaml(PROJECT_ROOT / "config" / "agents" / "memory" / "runtime" / "agent_runtime.yaml")
    memory_agent = memory_runtime["memory_agent"]

    assert "planner_prompt" in memory_agent
    assert "decomposition_gate_prompt" in memory_agent
    assert "system_prompt" in memory_agent
    assert "prompt_profiles" not in memory_agent


def test_memory_agent_configs_keep_prompt_content_in_runtime() -> None:
    config_paths = (
        PROJECT_ROOT / "config" / "agents" / "memory" / "chat_memory_agent.yaml",
        PROJECT_ROOT / "config" / "agents" / "memory" / "locomo_eval_memory_agent.yaml",
    )

    for config_path in config_paths:
        payload = _load_yaml(config_path)
        assert "runtime_prompt_profile" not in payload
        assert "planner_prompt" not in payload
        assert "system_prompt" not in payload
