"""End-to-end tests for the three-layer plug-in machinery.

Two injection paths must both work and combine correctly:

1. **Programmatic** — pass a ``ThreeLayerPluginOverrides`` to
   :class:`ThreeLayerChatAgent`.
2. **YAML-driven** — declare ``plugins:`` in the chat-controller YAML and let
   :func:`load_plugin_overrides_from_config` materialise instances by
   dotted-path import.

We avoid booting a live LLM by patching :func:`build_model_provider_from_config`
and exercising :func:`load_plugin_overrides_from_config` in isolation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from m_agent.systems import (
    ControllerCapabilityRegistry,
    DefaultEpisodeRecorder,
    DefaultWMReader,
    DefaultWMWriter,
    EpisodeQueryModule,
    get_default_capability_registry,
)
from m_agent.chat.three_layer_plugins import (
    PluginConfigError,
    ThreeLayerPluginOverrides,
    _resolve_dotted_path,
    load_plugin_overrides_from_config,
)


# --- Reference plug-in stubs used by both the programmatic and YAML paths.
#
# They live at module scope (rather than inside the test functions) so the
# dotted-path resolver can import them by name. They're only called inside
# tests, so the no-op behaviour is fine.


class _MarkerWMWriter:
    """Stub WMWriter for assertions; tracks .write() invocations."""

    def __init__(self) -> None:
        self.calls: List[int] = []

    def write(self, entries, tool_history):  # noqa: D401 - protocol shape
        self.calls.append(len(tool_history))


class _MarkerEpisodeRecorder:
    def __init__(self, label: str = "default-label") -> None:
        self.label = label
        self.notes: List[str] = []

    def append(self, buffer, *, note, turn_meta):  # noqa: D401
        if note:
            self.notes.append(str(note))

    def flush(self, buffer, *, thread_id, conversation_id):  # noqa: D401
        buffer.clear()


def _marker_registry_factory() -> ControllerCapabilityRegistry:
    """Factory form: returns a *copy* of the default registry as a marker."""
    return get_default_capability_registry().copy()


# ============================================================================
# Dotted-path resolver
# ============================================================================


def test_resolve_dotted_path_with_colon_form() -> None:
    obj = _resolve_dotted_path(f"{__name__}:_MarkerWMWriter")
    assert obj is _MarkerWMWriter


def test_resolve_dotted_path_with_legacy_dot_form() -> None:
    obj = _resolve_dotted_path(f"{__name__}._MarkerWMWriter")
    assert obj is _MarkerWMWriter


def test_resolve_dotted_path_empty_string_raises() -> None:
    with pytest.raises(PluginConfigError, match="empty"):
        _resolve_dotted_path("")


def test_resolve_dotted_path_module_not_found() -> None:
    with pytest.raises(PluginConfigError, match="failed to import module"):
        _resolve_dotted_path("nonexistent_pkg_xyz123:Whatever")


def test_resolve_dotted_path_attribute_not_found() -> None:
    with pytest.raises(PluginConfigError, match="has no attribute"):
        _resolve_dotted_path(f"{__name__}:DoesNotExistClass")


def test_resolve_dotted_path_missing_separator() -> None:
    with pytest.raises(PluginConfigError, match="must contain"):
        _resolve_dotted_path("no_separator_here")


# ============================================================================
# YAML-form materialisation
# ============================================================================


def test_load_plugin_overrides_empty_mapping_returns_all_none() -> None:
    overrides = load_plugin_overrides_from_config({})
    assert overrides.wm_writer is None
    assert overrides.wm_reader is None
    assert overrides.episode_recorder is None
    assert overrides.episode_query_module is None
    assert overrides.capability_registry is None


def test_load_plugin_overrides_none_returns_default_overrides() -> None:
    overrides = load_plugin_overrides_from_config(None)
    assert isinstance(overrides, ThreeLayerPluginOverrides)
    assert overrides.episode_recorder is None  # spot-check


def test_load_plugin_overrides_string_form_resolves_to_instance() -> None:
    cfg = {"wm_writer": f"{__name__}:_MarkerWMWriter"}
    overrides = load_plugin_overrides_from_config(cfg)
    assert isinstance(overrides.wm_writer, _MarkerWMWriter)


def test_load_plugin_overrides_mapping_form_passes_kwargs() -> None:
    cfg = {
        "episode_recorder": {
            "path": f"{__name__}:_MarkerEpisodeRecorder",
            "kwargs": {"label": "custom-channel"},
        }
    }
    overrides = load_plugin_overrides_from_config(cfg)
    assert isinstance(overrides.episode_recorder, _MarkerEpisodeRecorder)
    assert overrides.episode_recorder.label == "custom-channel"


def test_load_plugin_overrides_episode_query_module_with_disabled_flag() -> None:
    cfg = {
        "episode_query_module": {
            "path": "m_agent.systems.episodic.query_module:EpisodeQueryModule",
            "kwargs": {"enabled": False},
        }
    }
    overrides = load_plugin_overrides_from_config(cfg)
    assert isinstance(overrides.episode_query_module, EpisodeQueryModule)
    assert overrides.episode_query_module.enabled is False


def test_load_plugin_overrides_factory_function_form() -> None:
    cfg = {"capability_registry": f"{__name__}:_marker_registry_factory"}
    overrides = load_plugin_overrides_from_config(cfg)
    assert isinstance(overrides.capability_registry, ControllerCapabilityRegistry)
    # Sanity-check the factory really ran (registry should hold default capabilities).
    assert "shallow_recall" in overrides.capability_registry


def test_load_plugin_overrides_unknown_slot_raises() -> None:
    with pytest.raises(PluginConfigError, match="unknown plugin slot"):
        load_plugin_overrides_from_config({"not_a_real_slot": "anything"})


def test_load_plugin_overrides_top_level_wrong_type_raises() -> None:
    with pytest.raises(PluginConfigError, match="must be a mapping"):
        load_plugin_overrides_from_config(["not", "a", "mapping"])


def test_load_plugin_overrides_mapping_kwargs_wrong_type_raises() -> None:
    cfg = {
        "wm_writer": {
            "path": f"{__name__}:_MarkerWMWriter",
            "kwargs": "not-a-mapping",
        }
    }
    with pytest.raises(PluginConfigError, match="'kwargs' must be a mapping"):
        load_plugin_overrides_from_config(cfg)


def test_load_plugin_overrides_class_requires_args_raises_actionable_error() -> None:
    cfg = {"episode_recorder": "m_agent.systems.wm.default.defaults:DefaultWMWriter"}
    # DefaultWMWriter needs a config arg; calling it with no args must produce
    # a helpful PluginConfigError pointing at the 'kwargs' escape hatch.
    with pytest.raises(PluginConfigError, match="not callable with zero args"):
        load_plugin_overrides_from_config(cfg)


# ============================================================================
# Programmatic > YAML precedence
# ============================================================================


def test_merge_with_yaml_explicit_wins_per_slot() -> None:
    explicit = ThreeLayerPluginOverrides(
        wm_writer=_MarkerWMWriter(),
    )
    yaml_recorder = _MarkerEpisodeRecorder(label="yaml-default")
    yaml_writer = _MarkerWMWriter()
    yaml_overrides = ThreeLayerPluginOverrides(
        wm_writer=yaml_writer,
        episode_recorder=yaml_recorder,
    )
    merged = explicit.merge_with_yaml(yaml_overrides)
    # Explicit wins for wm_writer; YAML fills the un-set episode_recorder slot.
    assert merged.wm_writer is explicit.wm_writer
    assert merged.wm_writer is not yaml_writer
    assert merged.episode_recorder is yaml_recorder


# ============================================================================
# End-to-end: ThreeLayerChatAgent honours the resolved overrides
# ============================================================================


@pytest.fixture()
def _fake_model_provider_factory(monkeypatch: pytest.MonkeyPatch):
    """Patch model provider so ThreeLayerChatAgent boots without live LLM access."""
    from m_agent.layers.execution.model_provider import ModelProvider

    fake_provider = ModelProvider(
        model=object(),
        model_name="fake-model",
        recursion_limit=10,
        retry_recursion_limit=10,
        network_retry_attempts=1,
        network_retry_backoff_seconds=0,
        network_retry_backoff_multiplier=1,
        network_retry_max_backoff_seconds=0,
    )
    monkeypatch.setattr(
        "m_agent.chat.three_layer_chat_agent.build_model_provider_from_config",
        lambda *_a, **_k: fake_provider,
    )


def test_explicit_plugins_override_yaml_in_three_layer_agent(
    _fake_model_provider_factory,
) -> None:
    """The full ``ThreeLayerChatAgent.__init__`` boots and wires plug-ins correctly."""
    from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent
    from m_agent.config_paths import DEFAULT_CHAT_AGENT_CONFIG_PATH

    explicit_writer = _MarkerWMWriter()
    explicit_recorder = _MarkerEpisodeRecorder(label="explicit")
    explicit_query_module = EpisodeQueryModule(enabled=False)

    agent = ThreeLayerChatAgent(
        config_path=DEFAULT_CHAT_AGENT_CONFIG_PATH,
        plugins=ThreeLayerPluginOverrides(
            wm_writer=explicit_writer,
            episode_recorder=explicit_recorder,
            episode_query_module=explicit_query_module,
        ),
    )

    # Plug-ins reached their consumers:
    assert agent.wm_writer is explicit_writer
    assert agent.episode_recorder is explicit_recorder
    assert agent.episode_query_module is explicit_query_module
    # Unspecified slots fell back to defaults:
    assert isinstance(agent.wm_reader, DefaultWMReader)
    # Server-owned identity is explicit in the actual model system context.
    identity_prompt = agent.thinking_agent.system_prompt
    assert "[服务器提供的会话身份]" in identity_prompt
    assert '当前用户的名称是 "User"' in identity_prompt
    assert '助手名称是 "Memory Assistant"' in identity_prompt
    assert "绝不能把用户名称当作自己的名称" in identity_prompt
    # The YAML tool suite now builds its registry from one manifest per tool.
    assert set(agent.capability_registry.names()) == set(get_default_capability_registry().names())
    # The disabled episode-query module must hide recall capabilities upward:
    descriptors = agent.execution_agent.describe_capabilities()
    names = [d.name for d in descriptors]
    assert "shallow_recall" not in names
    assert "deep_recall" not in names


def test_yaml_plugins_section_drives_overrides_when_no_explicit_args(
    _fake_model_provider_factory, tmp_path
) -> None:
    """YAML ``plugins:`` mapping alone is enough to flip implementations."""
    from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent
    from m_agent.config_paths import DEFAULT_CHAT_AGENT_CONFIG_PATH

    # We don't want to mutate the real chat_controller.yaml; instead patch the
    # loader to inject a ``plugins`` section programmatically.
    original_load = ThreeLayerChatAgent._load_config

    def _patched_load(path):
        cfg = original_load(path)
        cfg["plugins"] = {
            "wm_writer": f"{__name__}:_MarkerWMWriter",
            "episode_recorder": {
                "path": f"{__name__}:_MarkerEpisodeRecorder",
                "kwargs": {"label": "yaml-channel"},
            },
        }
        return cfg

    with patch.object(ThreeLayerChatAgent, "_load_config", staticmethod(_patched_load)):
        agent = ThreeLayerChatAgent(config_path=DEFAULT_CHAT_AGENT_CONFIG_PATH)

    assert isinstance(agent.wm_writer, _MarkerWMWriter)
    assert isinstance(agent.episode_recorder, _MarkerEpisodeRecorder)
    assert agent.episode_recorder.label == "yaml-channel"
    # Unspecified slots fall back to default impls:
    assert isinstance(agent.wm_reader, DefaultWMReader)


def test_explicit_plugins_win_over_yaml_when_both_specified(
    _fake_model_provider_factory,
) -> None:
    from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent
    from m_agent.config_paths import DEFAULT_CHAT_AGENT_CONFIG_PATH

    original_load = ThreeLayerChatAgent._load_config

    def _patched_load(path):
        cfg = original_load(path)
        cfg["plugins"] = {
            "episode_recorder": {
                "path": f"{__name__}:_MarkerEpisodeRecorder",
                "kwargs": {"label": "yaml-loser"},
            }
        }
        return cfg

    explicit_recorder = _MarkerEpisodeRecorder(label="explicit-winner")
    with patch.object(ThreeLayerChatAgent, "_load_config", staticmethod(_patched_load)):
        agent = ThreeLayerChatAgent(
            config_path=DEFAULT_CHAT_AGENT_CONFIG_PATH,
            plugins=ThreeLayerPluginOverrides(episode_recorder=explicit_recorder),
        )

    assert agent.episode_recorder is explicit_recorder
    assert agent.episode_recorder.label == "explicit-winner"
