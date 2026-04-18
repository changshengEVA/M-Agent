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

    assert "system_prompt" in memory_agent
    assert "workspace_judge_prompt" in memory_agent
    assert "action_plan_prompt" in memory_agent
    assert "final_answer_from_workspace_prompt" in memory_agent
    assert "tool_descriptions" not in memory_agent
    assert "prompt_profiles" not in memory_agent


def test_tool_descriptions_yaml_exists_and_valid() -> None:
    tool_desc_path = PROJECT_ROOT / "config" / "agents" / "memory" / "tool_descriptions.yaml"
    assert tool_desc_path.exists(), "tool_descriptions.yaml must exist"
    payload = _load_yaml(tool_desc_path)
    assert len(payload) > 0, "tool_descriptions.yaml must define at least one tool"
    for action_type, tool_cfg in payload.items():
        assert isinstance(tool_cfg, dict), f"{action_type} must be a dict"
        desc = tool_cfg.get("description")
        assert isinstance(desc, dict), f"{action_type}.description must be a zh/en dict"
        assert "zh" in desc and "en" in desc, f"{action_type}.description must have zh and en"
        assert "params" in tool_cfg, f"{action_type} must have params"


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
