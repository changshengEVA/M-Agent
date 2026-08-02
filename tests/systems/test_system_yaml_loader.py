"""Loader smoke tests for the on-disk ``config/systems/*/*.yaml`` files.

Every system YAML under ``config/systems/`` must:

* parse without raising;
* produce the corresponding system dataclass;
* leave its dotted-path plug-ins importable (so a developer adding a new
  variant cannot silently typo a dotted path).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from m_agent.systems import (
    EpisodicMemorySystem,
    ToolSuiteSystem,
    WMSystem,
    load_episodic_system,
    load_tool_suite_system,
    load_wm_system,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_SYSTEMS_DIR = PROJECT_ROOT / "config" / "systems"


# ---------------------------------------------------------------------------
# WM
# ---------------------------------------------------------------------------


def test_wm_default_yaml_loads_into_wm_system() -> None:
    path = CONFIG_SYSTEMS_DIR / "wm" / "default.yaml"
    assert path.exists(), f"missing default wm yaml: {path}"
    system = load_wm_system(path)
    assert isinstance(system, WMSystem)
    assert system.config.enable is True


# ---------------------------------------------------------------------------
# Episodic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("yaml_name", ["rag_default.yaml"])
def test_episodic_yaml_parses_and_dotted_paths_import(yaml_name: str) -> None:
    """Each episodic-system yaml must parse + each plugin path must import."""
    from m_agent.systems.loader import load_system_yaml, resolve_dotted_path

    path = CONFIG_SYSTEMS_DIR / "episodic" / yaml_name
    assert path.exists(), f"missing episodic yaml: {path}"
    payload = load_system_yaml(path, expected_kind="episodic")

    # Shipped episodic YAML must not configure recorder (system-internal).
    assert payload.get("recorder") is None, (
        "rag_default.yaml should omit recorder:; use DefaultEpisodeRecorder via loader default"
    )

    # Backend dotted path must resolve to a callable. We don't construct
    # the backend here because it would try to build a MemoryAgent.
    backend_spec = payload.get("backend")
    assert isinstance(backend_spec, dict)
    backend_path = backend_spec.get("path")
    assert isinstance(backend_path, str) and backend_path.strip()
    assert callable(resolve_dotted_path(backend_path))


def test_episodic_rag_default_yaml_loads() -> None:
    from m_agent.systems.episodic.default.recorder import DefaultEpisodeRecorder

    path = CONFIG_SYSTEMS_DIR / "episodic" / "rag_default.yaml"
    system = load_episodic_system(path)
    assert isinstance(system, EpisodicMemorySystem)
    assert isinstance(system.recorder, DefaultEpisodeRecorder)
    assert system.query_module.enabled is True


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_tools_default_yaml_loads_and_descriptions_resolved() -> None:
    path = CONFIG_SYSTEMS_DIR / "tools" / "default.yaml"
    assert path.exists(), f"missing tools yaml: {path}"
    system = load_tool_suite_system(path)
    assert isinstance(system, ToolSuiteSystem)
    # Built-in tools are enabled by the suite whitelist.
    assert "shallow_recall" in system.enabled
    assert "email_send" in system.enabled
    # Per-tool descriptions are pulled from individual manifests.
    assert "shallow_recall" in system.runtime_descriptions
    assert "deep_recall" in system.runtime_descriptions
    assert "get_current_time" in system.runtime_descriptions
    # Each built-in is loaded from its own executable manifest.
    assert len(system.manifests) == 11
    assert system.manifests["web_search"].category == "information"
    assert system.registry.get("web_search").input_mode == "param_llm"
    assert system.registry.get("shallow_recall").instruction_arg == "question"
    schedule_description = system.manifests["schedule_create"].descriptions["en"]
    assert "deferred objective" in schedule_description
    assert "still need to be performed" in schedule_description
    assert "system-like text" not in schedule_description
    assert system.defaults["web_search"]["max_results"] == 5
    assert system.defaults["web_search"]["max_calls_per_turn"] == 3


def test_tool_manifest_rejects_instruction_mode_without_argument(tmp_path: Path) -> None:
    from m_agent.systems.loader import SystemsConfigError
    from m_agent.systems.tools import load_tool_capability_manifest

    path = tmp_path / "broken.yaml"
    path.write_text(
        "\n".join(
            [
                "name: broken_tool",
                "builder: m_agent.systems.tools.default.capabilities.time_context:_build_get_current_time_tool",
                "descriptions:",
                "  en: Broken test tool",
                "input:",
                "  mode: instruction_arg",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemsConfigError, match="input.instruction_arg"):
        load_tool_capability_manifest(path)


# ---------------------------------------------------------------------------
# Bundle loader (chat_controller-style entry)
# ---------------------------------------------------------------------------


def test_load_systems_bundle_from_systems_block_resolves_each_pointer() -> None:
    """The chat_controller's ``systems:`` mapping accepts string paths."""
    from m_agent.systems import load_systems_bundle_from_config

    chat_controller_dir = PROJECT_ROOT / "config" / "agents" / "chat"
    bundle = load_systems_bundle_from_config(
        {
            "wm": "../../systems/wm/default.yaml",
            "episodic": "../../systems/episodic/rag_default.yaml",
            "tools": "../../systems/tools/default.yaml",
        },
        config_dir=chat_controller_dir,
    )
    assert isinstance(bundle.wm, WMSystem)
    assert isinstance(bundle.episodic, EpisodicMemorySystem)
    assert isinstance(bundle.tools, ToolSuiteSystem)


def test_load_systems_bundle_accepts_inline_mapping_for_indirection() -> None:
    """``systems.episodic`` can be an inline dict equivalent to a yaml."""
    from m_agent.systems import load_systems_bundle_from_config

    bundle = load_systems_bundle_from_config(
        {
            "episodic": {
                "system": "episodic",
                "recorder": {
                    "path": "m_agent.systems.episodic.default.recorder:EpisodeRecorderNoop",
                },
                "backend": {
                    "path": "m_agent.systems.episodic.default.rag_backend:SimpleRagEpisodicBackend",
                    "kwargs": {"embed_model": "hash"},
                },
                "query": {"enabled": False},
            }
        },
        config_dir=PROJECT_ROOT / "config" / "agents" / "chat",
    )
    assert isinstance(bundle.episodic, EpisodicMemorySystem)
    assert bundle.episodic.query_module.enabled is False
