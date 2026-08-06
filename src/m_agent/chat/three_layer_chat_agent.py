"""Three-layer chat agent.

Constructs the perception/thinking/execution stack from a single
``chat_controller.yaml`` config. This is the only chat agent in the
codebase; :class:`ChatServiceRuntime` instantiates this class directly
via :func:`m_agent.chat.create_chat_agent`.

Layering:

* :class:`ExecutionAgent` (persona-less capability registry and direct tool invoker)
* :class:`ThinkingAgent` (persona-guided, transaction-scoped planning)
* This module wires them together via a :class:`SystemsBundle`
  (3 subsystems × 6 access points; see :mod:`m_agent.systems`).

Plug-in resolution order at startup:

    explicit ``systems=`` argument
        > legacy explicit ``plugins=`` argument (adapted to SystemsBundle)
        > YAML ``systems:`` block (per-system YAMLs)
        > YAML ``plugins:`` block (legacy flat shape; DeprecationWarning)
        > built-in defaults
"""
from __future__ import annotations

import json
import logging
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from m_agent.agents.email_agent import EmailAgent
from m_agent.agents.schedule_agent import ScheduleAgent
from m_agent.layers.execution import ExecutionAgent
from m_agent.layers.execution.model_provider import build_model_provider_from_config
from m_agent.layers.thinking import (
    ConversationStateRegistry,
    ThinkingAgent,
)
from m_agent.layers.thinking.persona import merge_system_with_persona
from m_agent.chat.three_layer_plugins import (
    ThreeLayerPluginOverrides,
    load_plugin_overrides_from_config,
)
from m_agent.chat.working_memory import (
    WorkingMemoryConfig,
    normalize_working_memory_config,
)
from m_agent.config_paths import (
    CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH,
    DEFAULT_CHAT_AGENT_CONFIG_PATH,
    DEFAULT_CHAT_MODEL_CONFIG_PATH,
    DEFAULT_EMAIL_AGENT_CONFIG_PATH,
    DEFAULT_SCHEDULE_AGENT_CONFIG_PATH,
    resolve_config_path,
    resolve_related_config_path,
)
from m_agent.paths import (
    chat_memory_workflow_id,
    chat_user_dialogues_dir,
    chat_user_episodic_rag_paths,
    chat_user_persistence_root,
    chat_user_slug,
)
from m_agent.prompt_utils import (
    load_resolved_prompt_config,
    normalize_prompt_language,
    resolve_prompt_value,
)
from m_agent.systems.episodic.default.rag_backend import SimpleRagEpisodicBackend
from m_agent.systems import (
    DefaultEpisodeRecorder,
    DefaultWMDisplay,
    DefaultWMReader,
    DefaultWMWriter,
    EpisodeQueryModule,
    EpisodicMemorySystem,
    SystemsBundle,
    build_default_episodic_system,
    ToolSuiteSystem,
    WMSystem,
    build_default_tool_suite_system,
    get_default_capability_registry,
    load_systems_bundle_from_config,
    resolve_enabled_controller_capability_names,
)


logger = logging.getLogger(__name__)

DEFAULT_CHAT_CONFIG_PATH = DEFAULT_CHAT_AGENT_CONFIG_PATH


class ThreeLayerChatAgent:
    """Three-layer (perception/thinking/execution) chat agent."""

    def __init__(
        self,
        config_path: str | Path = DEFAULT_CHAT_CONFIG_PATH,
        *,
        systems: Optional[SystemsBundle] = None,
        plugins: Optional[ThreeLayerPluginOverrides] = None,
    ) -> None:
        self.config_path = resolve_config_path(config_path)
        self.config = self._load_config(self.config_path)
        self.prompt_language = normalize_prompt_language(self.config.get("prompt_language", "zh"))

        # ---- Sub-agent paths
        self.model_config_path = resolve_related_config_path(
            self.config_path,
            self.config.get("model_config_path"),
            default_path=DEFAULT_CHAT_MODEL_CONFIG_PATH,
        )
        self.email_agent_config_path = resolve_related_config_path(
            self.config_path,
            self.config.get("email_agent_config_path"),
            default_path=DEFAULT_EMAIL_AGENT_CONFIG_PATH,
        )
        self.schedule_agent_config_path = resolve_related_config_path(
            self.config_path,
            self.config.get("schedule_agent_config_path"),
            default_path=DEFAULT_SCHEDULE_AGENT_CONFIG_PATH,
        )
        self.runtime_prompt_config_path = resolve_related_config_path(
            self.config_path,
            self.config.get("runtime_prompt_config_path"),
            default_path=CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH,
        )
        self.runtime_prompts = self._load_runtime_prompts(self.runtime_prompt_config_path)

        # ---- Eager / lazy sub-agents (email + schedule on first use)
        self._email_agent: Optional[EmailAgent] = None
        self._email_agent_lock = threading.Lock()
        self._schedule_agent: Optional[ScheduleAgent] = None
        self._schedule_agent_lock = threading.Lock()

        # ---- Names / persistence settings
        # Display name for prompts/UI only. Durable storage keys use owner_id.
        self.user_name = str(self.config.get("chat_user_name", "User") or "User")
        self.owner_id = self._resolve_chat_owner_id()
        self.assistant_name = str(
            self.config.get("chat_assistant_name", "Memory Assistant") or "Memory Assistant"
        )
        self.persist_memory = bool(self.config.get("persist_memory", True))
        self.default_thread_id = (
            str(self.config.get("thread_id", "test-agent-1")).strip() or "test-agent-1"
        )

        # ---- Resolve systems: explicit > legacy plugins= > YAML systems: > legacy plugins: > defaults.
        # All four resolution paths are folded into a single SystemsBundle; subsequent code only sees
        # the bundle so the three-layer wiring stays uniform.
        self.systems: SystemsBundle = self._resolve_systems_bundle(
            explicit=systems,
            legacy_plugins=plugins,
        )

        # ---- Snapshot working-memory config for the runtime / SSE payload helpers.
        self.working_memory_config: WorkingMemoryConfig = self.systems.wm.config

        # ---- Episode-query switch lives inside the episodic system.
        self.episode_query_module: EpisodeQueryModule = self.systems.episodic.query_module

        # ---- Dialogue archive (JSON under dialogues/) + episodic backend for RAG.
        # Round-by-round persistence routes through the episodic backend;
        # thread flush writes dialogue JSON via ``ChatDialogueArchive``.
        self.model_provider = build_model_provider_from_config(self.model_config_path)

        # ---- Tool defaults: merge YAML legacy ``tool_defaults`` with ToolSuiteSystem.defaults.
        # System defaults win; legacy YAML fills missing keys.
        self.tool_defaults = self._merge_tool_defaults(
            legacy=self._load_legacy_tool_defaults(),
            system=self.systems.tools.defaults,
        )
        self.capability_registry = self.systems.tools.registry
        all_enabled = list(self.systems.tools.enabled)
        capability_descriptions = {
            name: self._get_capability_description(name) for name in all_enabled
        }
        thinking_prompts = self._get_runtime_section("thinking", "thinking_layer")
        thinking_turn_prompts = (
            thinking_prompts.get("thinking_turn")
            if isinstance(thinking_prompts.get("thinking_turn"), dict)
            else {}
        )
        resolution_prompts = (
            thinking_prompts.get("resolve_transaction")
            if isinstance(thinking_prompts.get("resolve_transaction"), dict)
            else {}
        )
        task_state_prompts = (
            thinking_prompts.get("pre_gen_task_state")
            if isinstance(thinking_prompts.get("pre_gen_task_state"), dict)
            else {}
        )
        decision_prompts = (
            thinking_prompts.get("make_decision")
            if isinstance(thinking_prompts.get("make_decision"), dict)
            else {}
        )
        execution_prompts = self._get_runtime_section("execution", "execution_layer")
        legacy_persona = self._legacy_persona_prompts()

        base_prompt = self._nested_runtime_text(
            thinking_turn_prompts,
            "base_prompt",
            legacy_keys=("base_role_prompt",),
        )
        if not base_prompt:
            base_prompt = self._nested_runtime_text(
                decision_prompts,
                "base_prompt",
                legacy_keys=("base_role_prompt",),
            )
        if not base_prompt:
            base_prompt = self._nested_runtime_text(
                thinking_prompts,
                "base_prompt",
                legacy_keys=("base_role_prompt",),
            )
        if not base_prompt:
            base_prompt = self._nested_runtime_text(
                task_state_prompts,
                "base_prompt",
            )
        task_state_base_prompt = self._nested_runtime_text(
            task_state_prompts,
            "base_prompt",
        )
        task_state_instructions_prompt = self._nested_runtime_text(
            task_state_prompts,
            "instructions",
        )
        if not base_prompt:
            base_prompt = self._nested_runtime_text(
                legacy_persona,
                "base_role_prompt",
                legacy_keys=("system_prompt", "base_prompt"),
            )
        if not base_prompt:
            raise ValueError(
                f"`chat_controller.thinking.thinking_turn.base_prompt` is required in runtime prompt config: "
                f"{self.runtime_prompt_config_path}"
            )

        runtime_config = self.config.get("runtime")
        runtime_config = runtime_config if isinstance(runtime_config, dict) else {}
        langgraph_config = runtime_config.get("langgraph")
        langgraph_config = langgraph_config if isinstance(langgraph_config, dict) else {}
        thinking_mode = str(
            langgraph_config.get("thinking_mode", "single_call") or "single_call"
        ).strip()
        if thinking_mode.lower() == "legacy_two_call":
            legacy_decision_base = self._nested_runtime_text(
                decision_prompts,
                "base_prompt",
                legacy_keys=("base_role_prompt",),
            )
            if legacy_decision_base:
                base_prompt = legacy_decision_base

        if "chat_persona_prompt" in self.config:
            persona_override = self.config.get("chat_persona_prompt")
            if not isinstance(persona_override, str):
                raise ValueError("`chat_persona_prompt` must be a string")
            persona_tone_prompt = persona_override.strip()
        else:
            persona_tone_prompt = self._nested_runtime_text(
                thinking_prompts,
                "persona_tone_prompt",
                legacy_keys=("persona_prompt",),
            )
            if not persona_tone_prompt:
                persona_tone_prompt = self._nested_runtime_text(
                    legacy_persona,
                    "persona_tone_prompt",
                    legacy_keys=("persona_prompt",),
                )

        persona_merge_template = self._nested_runtime_text(
            thinking_prompts,
            "persona_merge_template",
            legacy_keys=("merge_system_with_persona",),
        )
        if not persona_merge_template:
            persona_merge_template = self._nested_runtime_text(
                legacy_persona,
                "persona_merge_template",
                legacy_keys=("merge_system_with_persona",),
            )

        self.execution_agent = ExecutionAgent(
            model_provider=self.model_provider,
            enabled_capability_names=all_enabled,
            capability_descriptions=capability_descriptions,
            tool_defaults=self.tool_defaults,
            email_agent_provider=self._get_email_agent,
            schedule_agent_provider=self._get_schedule_agent,
            registry=self.capability_registry,
            episode_query_module=self.episode_query_module,
            episodic_backend=self.systems.episodic.backend,
            prompt_language=self.prompt_language,
            capability_block_header=str(execution_prompts.get("capability_block_header", "") or "").strip(),
        )

        # ---- Build thinking layer
        merge_template = persona_merge_template
        base_system_prompt = base_prompt
        persona_prompt = persona_tone_prompt
        merged_persona_prompt = merge_system_with_persona(
            base_prompt=base_system_prompt,
            persona_prompt=persona_prompt,
            merge_template=merge_template,
        )
        identity_context = self._server_owned_identity_context()
        if identity_context:
            merged_persona_prompt = "\n\n".join(
                part for part in (merged_persona_prompt, identity_context) if part
            )

        self.state_registry = ConversationStateRegistry()
        # Convenience aliases so external code that snapshotted the per-slot
        # objects directly keeps working.
        self.wm_reader = self.systems.wm.reader
        self.wm_writer = self.systems.wm.writer
        self.episode_recorder = self.systems.episodic.recorder
        self._dialogue_archive = None
        self.memory_persistence = None

        self.thinking_agent = ThinkingAgent(
            execution_agent=self.execution_agent,
            model_provider=self.model_provider,
            system_prompt=merged_persona_prompt,
            persona_prompt="",  # already merged into system_prompt
            wm_reader=self.wm_reader,
            episode_recorder=self.episode_recorder,
            state_registry=self.state_registry,
            prompt_language=self.prompt_language,
            task_state_base_prompt=task_state_base_prompt,
            task_state_instructions_prompt=task_state_instructions_prompt,
            plan_instructions_prompt=(
                self._nested_runtime_text(decision_prompts, "instructions")
                or str(thinking_prompts.get("plan_instructions", "") or "").strip()
            ),
            thinking_turn_instructions_prompt=self._nested_runtime_text(
                thinking_turn_prompts,
                "instructions",
            ),
            thinking_mode=thinking_mode,
            capability_boundary_header=(
                self._nested_runtime_text(decision_prompts, "capability_boundary_header")
                or str(thinking_prompts.get("capability_boundary_header", "") or "").strip()
            ),
            fallback_answer_prompt=(
                self._nested_runtime_text(decision_prompts, "fallback_answer")
                or str(thinking_prompts.get("fallback_answer", "") or "").strip()
            ),
            transaction_resolution_base_prompt=self._nested_runtime_text(
                resolution_prompts, "base_prompt"
            ),
            transaction_resolution_instructions_prompt=self._nested_runtime_text(
                resolution_prompts, "instructions"
            ),
        )

        backend_persistence = getattr(self.systems.episodic.backend, "persistence", None)
        if backend_persistence is not None:
            self.memory_persistence = backend_persistence

    # ----------------------------------------------------------------------
    # SystemsBundle resolution
    # ----------------------------------------------------------------------

    def _resolve_systems_bundle(
        self,
        *,
        explicit: Optional[SystemsBundle],
        legacy_plugins: Optional[ThreeLayerPluginOverrides],
    ) -> SystemsBundle:
        """Merge explicit + legacy + YAML + defaults into one bundle.

        Resolution order (highest precedence first):

        1. Explicit ``systems=`` constructor argument.
        2. Explicit ``plugins=`` constructor argument (legacy adapter).
        3. YAML ``systems:`` block (new; per-system YAMLs).
        4. YAML ``plugins:`` block (legacy; DeprecationWarning).
        5. Built-in defaults.
        """
        explicit_bundle = explicit if isinstance(explicit, SystemsBundle) else SystemsBundle()

        systems_section = self.config.get("systems")
        config_dir = self.config_path.parent
        yaml_bundle = load_systems_bundle_from_config(
            systems_section,
            config_dir=config_dir,
        )

        # YAML "plugins:" block (legacy) + explicit legacy plugins= argument —
        # collapsed via load_plugin_overrides_from_config so deprecation
        # warnings are emitted once per source.
        yaml_legacy_overrides = load_plugin_overrides_from_config(self.config.get("plugins"))
        legacy_overrides = (
            (legacy_plugins or ThreeLayerPluginOverrides()).merge_with_yaml(yaml_legacy_overrides)
        )
        legacy_bundle = self._legacy_to_systems_bundle(legacy_overrides)

        # Merge in precedence: explicit -> legacy explicit/plugins -> yaml systems -> default
        merged = explicit_bundle.merge_with(legacy_bundle).merge_with(yaml_bundle)

        # ---- WM fallback
        if merged.wm is None:
            merged = SystemsBundle(
                wm=self._build_default_wm_system_from_legacy_yaml(),
                episodic=merged.episodic,
                tools=merged.tools,
            )

        # ---- Episodic fallback
        if merged.episodic is None:
            merged = SystemsBundle(
                wm=merged.wm,
                episodic=self._build_default_episodic_system_from_legacy_yaml(),
                tools=merged.tools,
            )

        # ---- Tools fallback
        if merged.tools is None:
            merged = SystemsBundle(
                wm=merged.wm,
                episodic=merged.episodic,
                tools=self._build_default_tools_system_from_legacy_yaml(),
            )

        return self._rebind_episodic_for_chat_user(merged)

    def _legacy_to_systems_bundle(
        self,
        overrides: ThreeLayerPluginOverrides,
    ) -> SystemsBundle:
        """Map legacy ``ThreeLayerPluginOverrides`` onto a partial :class:`SystemsBundle`.

        Only the slots that the user actually overrode are filled; everything
        else remains ``None`` so the YAML / default fallback can take over.
        """
        if overrides.is_empty():
            return SystemsBundle()

        wm_system: Optional[WMSystem] = None
        if overrides.wm_reader is not None or overrides.wm_writer is not None:
            wm_cfg = normalize_working_memory_config(self.config.get("working_memory"))
            wm_system = WMSystem(
                writer=overrides.wm_writer or DefaultWMWriter(wm_cfg),
                reader=overrides.wm_reader or DefaultWMReader(wm_cfg),
                display=DefaultWMDisplay(wm_cfg),
                config=wm_cfg,
            )

        episodic_system: Optional[EpisodicMemorySystem] = None
        if overrides.episode_recorder is not None or overrides.episode_query_module is not None:
            episodic_system = EpisodicMemorySystem(
                recorder=overrides.episode_recorder or DefaultEpisodeRecorder(),
                backend=self._build_default_episodic_system_from_legacy_yaml().backend,
                query_module=overrides.episode_query_module
                or EpisodeQueryModule(enabled=bool(self.config.get("episode_query_enabled", True))),
            )

        tools_system: Optional[ToolSuiteSystem] = None
        if overrides.capability_registry is not None:
            registry = overrides.capability_registry
            enabled = resolve_enabled_controller_capability_names(
                self.config.get("enabled_tools"),
                registry=registry,
            )
            tools_system = ToolSuiteSystem(
                registry=registry,
                enabled=enabled,
                defaults=self._load_legacy_tool_defaults(),
                runtime_descriptions={},
            )

        return SystemsBundle(
            wm=wm_system,
            episodic=episodic_system,
            tools=tools_system,
        )

    def _build_default_wm_system_from_legacy_yaml(self) -> WMSystem:
        """Build the default WMSystem honoring the legacy ``working_memory:`` block."""
        wm_cfg = normalize_working_memory_config(self.config.get("working_memory"))
        return WMSystem(
            writer=DefaultWMWriter(wm_cfg),
            reader=DefaultWMReader(wm_cfg),
            display=DefaultWMDisplay(wm_cfg),
            config=wm_cfg,
        )

    def _resolve_chat_owner_id(self) -> str:
        """Immutable storage owner; prefer ``chat_owner_id`` over display name."""
        explicit = str(self.config.get("chat_owner_id", "") or "").strip()
        if explicit:
            return chat_user_slug(explicit)
        # Account bundles live at ``.../users/<username>/chat.yaml``.
        parent = self.config_path.parent
        parent_name = str(parent.name or "").strip()
        parent_slug = chat_user_slug(parent_name)
        if (
            parent_name
            and str(parent.parent.name or "").strip().lower() == "users"
            and parent_slug == parent_name.lower()
        ):
            return parent_slug
        return chat_user_slug(self.user_name)

    def _build_default_episodic_system_from_legacy_yaml(self) -> EpisodicMemorySystem:
        """Build the default EpisodicMemorySystem (simple RAG backend)."""
        user_root, workflow_id, _index_root = chat_user_episodic_rag_paths(self.owner_id)
        return build_default_episodic_system(
            query_enabled=bool(self.config.get("episode_query_enabled", True)),
            user_name=self.user_name,
            assistant_name=self.assistant_name,
            storage_dir=str(user_root),
            workflow_id=workflow_id,
        )

    def _rebind_episodic_for_chat_user(self, bundle: SystemsBundle) -> SystemsBundle:
        """Point episodic RAG storage at ``data/memory/chat-api/<owner>/episodic/``."""
        episodic = bundle.episodic
        if episodic is None:
            return bundle
        backend = episodic.backend
        if not isinstance(backend, SimpleRagEpisodicBackend):
            return bundle

        user_root, workflow_id, _index_root = chat_user_episodic_rag_paths(self.owner_id)
        new_backend = SimpleRagEpisodicBackend(
            storage_dir=str(user_root),
            workflow_id=workflow_id,
            top_k=backend.top_k,
            min_score=backend.min_score,
            embed_model=backend.embed_model,
            user_name=self.user_name,
            assistant_name=self.assistant_name,
        )
        return SystemsBundle(
            wm=bundle.wm,
            episodic=EpisodicMemorySystem(
                recorder=episodic.recorder,
                backend=new_backend,
                query_module=episodic.query_module,
            ),
            tools=bundle.tools,
        )

    def describe_episodic_persistence(self) -> Dict[str, Any]:
        """Expose episodic + dialogue persistence paths for API clients."""
        backend = self.systems.episodic.backend
        episodic_info: Dict[str, Any] = {}
        if hasattr(backend, "describe_persistence"):
            episodic_info = dict(backend.describe_persistence())

        user_root = chat_user_persistence_root(self.owner_id)
        dialogues_dir = chat_user_dialogues_dir(self.owner_id)
        return {
            "owner_id": self.owner_id,
            "workflow_id": chat_memory_workflow_id(self.owner_id),
            "user_persistence_root": str(user_root),
            "dialogues_dir": str(dialogues_dir),
            "episodic": episodic_info,
        }

    def _build_default_tools_system_from_legacy_yaml(self) -> ToolSuiteSystem:
        """Build the default ToolSuiteSystem honoring legacy ``enabled_tools`` / ``tool_defaults``."""
        registry = get_default_capability_registry()
        enabled = resolve_enabled_controller_capability_names(
            self.config.get("enabled_tools"),
            registry=registry,
        )
        return ToolSuiteSystem(
            registry=registry,
            enabled=enabled,
            defaults=self._load_legacy_tool_defaults(),
            runtime_descriptions={},
        )

    @staticmethod
    def _merge_tool_defaults(
        *,
        legacy: Dict[str, Dict[str, Any]],
        system: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Merge tool defaults: system wins per-key, legacy fills missing keys."""
        merged: Dict[str, Dict[str, Any]] = {}
        for src in (legacy, system):
            if not isinstance(src, dict):
                continue
            for tool_name, tool_cfg in src.items():
                if not isinstance(tool_cfg, dict):
                    continue
                merged.setdefault(str(tool_name), {})
                merged[str(tool_name)].update(tool_cfg)
        return merged

    def _episodic_workflow_id(self) -> str:
        store = getattr(self.systems.episodic.backend, "store", None)
        if store is not None:
            return str(getattr(store, "workflow_id", "") or "default")
        return "default"

    # ----------------------------------------------------------------------
    # Config + prompt helpers
    # ----------------------------------------------------------------------

    @staticmethod
    def _load_config(path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Chat controller config not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        if not isinstance(config, dict):
            raise ValueError(f"Chat controller config must be a dict: {path}")
        if not isinstance(config.get("model_config_path"), str) or not str(
            config.get("model_config_path")
        ).strip():
            raise ValueError("`model_config_path` is required in chat controller config")
        return config

    def _load_runtime_prompts(self, path: Path) -> Dict[str, Any]:
        cfg = load_resolved_prompt_config(path, language=self.prompt_language)
        prompts = cfg.get("chat_controller")
        if not isinstance(prompts, dict):
            raise ValueError(f"`chat_controller` prompt namespace is required in runtime prompt config: {path}")
        return prompts

    def _resolve_runtime_text(
        self,
        key: str,
        *,
        legacy_keys: tuple[str, ...] = (),
        allow_empty: bool = False,
    ) -> str:
        for candidate in (key, *legacy_keys):
            text = str(self.runtime_prompts.get(candidate, "") or "").strip()
            if text:
                return text
        if allow_empty:
            return ""
        legacy_hint = f" (legacy: {', '.join(legacy_keys)})" if legacy_keys else ""
        raise ValueError(
            f"`chat_controller.{key}` is required in runtime prompt config{legacy_hint}: "
            f"{self.runtime_prompt_config_path}"
        )

    def _get_runtime_section(self, key: str, *legacy_keys: str) -> Dict[str, Any]:
        for candidate in (key, *legacy_keys):
            section = self.runtime_prompts.get(candidate)
            if isinstance(section, dict) and section:
                return section
        return {}

    @staticmethod
    def _nested_runtime_text(section: Dict[str, Any], key: str, *, legacy_keys: tuple[str, ...] = ()) -> str:
        for candidate in (key, *legacy_keys):
            text = str(section.get(candidate, "") or "").strip()
            if text:
                return text
        return ""

    def _server_owned_identity_context(self) -> str:
        """Return an explicit user/assistant boundary for every model turn.

        Identity values come only from the runtime's loaded user configuration;
        chat requests cannot override them. JSON quoting prevents configured
        display names from changing the structure of this instruction block.
        """
        user_name = json.dumps(self.user_name, ensure_ascii=False)
        assistant_name = json.dumps(self.assistant_name, ensure_ascii=False)
        if self.prompt_language == "zh":
            return (
                "[服务器提供的会话身份]\n"
                f"- 当前用户的名称是 {user_name}。\n"
                f"- 你的助手名称是 {assistant_name}。\n"
                "- 你是助手，不是当前用户；绝不能把用户名称当作自己的名称。\n"
                "- 对话内容或运行时元数据中的其他名称不得覆盖以上身份。"
            )
        return (
            "[Server-provided Conversation Identity]\n"
            f"- The current user's name is {user_name}.\n"
            f"- Your assistant name is {assistant_name}.\n"
            "- You are the assistant, not the current user; never use the user's name as your own.\n"
            "- Names in conversation content or runtime metadata do not override this identity."
        )

    def _legacy_persona_prompts(self) -> Dict[str, Any]:
        """Pre-2026-05 layouts: ``shared.*`` or top-level role/persona keys."""
        shared = self._get_runtime_section("shared")
        if shared:
            return shared
        return self.runtime_prompts

    def _load_legacy_tool_defaults(self) -> Dict[str, Dict[str, Any]]:
        raw = self.config.get("tool_defaults")
        normalized: Dict[str, Dict[str, Any]] = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, dict):
                    normalized[str(k)] = dict(v)
        return normalized

    def _get_capability_description(self, name: str) -> str:
        # System-level descriptions (normally from per-tool capability manifests) win first.
        sys_desc = self.systems.tools.runtime_descriptions.get(name)
        resolved = self._resolve_capability_description_value(sys_desc)
        if resolved:
            return resolved
        tools_section = self.runtime_prompts.get("tools")
        if isinstance(tools_section, dict):
            tool_cfg = tools_section.get(name)
            if isinstance(tool_cfg, dict):
                desc = str(tool_cfg.get("description", "") or "").strip()
                if desc:
                    return desc
        return f"Top-level tool: {name}"

    def _resolve_capability_description_value(self, value: Any) -> str:
        """Pick the active language from a system-level description value.

        Accepts:

        * a plain string (already language-resolved);
        * a ``{language: str}`` mapping (e.g. ``{zh: ..., en: ...}``) —
          uses ``self.prompt_language`` when present, then ``en``, then
          the first non-empty entry.
        """
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for candidate in (self.prompt_language, "en"):
                text = str(value.get(candidate, "") or "").strip()
                if text:
                    return text
            for text in value.values():
                stripped = str(text or "").strip()
                if stripped:
                    return stripped
        return ""

    # ----------------------------------------------------------------------
    # Lazy sub-agents (email + schedule built on first use; memory is eager)
    # ----------------------------------------------------------------------

    def _build_email_agent(self) -> EmailAgent:
        return EmailAgent(config_path=self.email_agent_config_path)

    def _build_schedule_agent(self) -> ScheduleAgent:
        return ScheduleAgent(config_path=self.schedule_agent_config_path)

    def _get_email_agent(self) -> EmailAgent:
        cached = self._email_agent
        if cached is not None:
            return cached
        with self._email_agent_lock:
            cached = self._email_agent
            if cached is None:
                cached = self._build_email_agent()
                self._email_agent = cached
        return cached

    def _get_schedule_agent(self) -> ScheduleAgent:
        cached = self._schedule_agent
        if cached is not None:
            return cached
        with self._schedule_agent_lock:
            cached = self._schedule_agent
            if cached is None:
                cached = self._build_schedule_agent()
                self._schedule_agent = cached
        return cached

    def get_schedule_agent(self) -> ScheduleAgent:
        return self._get_schedule_agent()

    # ----------------------------------------------------------------------
    # Persistence used by ChatServiceRuntime
    # ----------------------------------------------------------------------

    def ensure_dialogue_archive(self) -> Optional[Any]:
        from m_agent.api.chat_api_shared import ensure_dialogue_archive

        archive = ensure_dialogue_archive(self)
        if archive is not None:
            self.memory_persistence = archive
        return archive

    def persist_dialogue(
        self,
        *,
        thread_id: str,
        rounds: List[Dict[str, Any]],
        reason: str = "chat_thread_flush",
        source: str = "chat_api_thread_flush",
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Persist flushed rounds to dialogue archives and the episodic backend."""
        archive = self.memory_persistence or self.ensure_dialogue_archive()
        if archive is not None:
            archive_result = archive.persist_dialogue(
                thread_id=thread_id,
                rounds=rounds,
                reason=reason,
                source=source,
                progress_callback=progress_callback,
            )
            backend = self.systems.episodic.backend
            if getattr(backend, "persistence", None) is archive:
                return archive_result
            dialogue_id = str(
                archive_result.get("dialogue_id", "") or ""
            ).strip()
            backend_rounds = [
                {
                    **dict(item),
                    **({"dialogue_id": dialogue_id} if dialogue_id else {}),
                }
                for item in rounds
                if isinstance(item, dict)
            ]
            try:
                rag_result = backend.persist_dialogue(
                    thread_id=thread_id,
                    rounds=backend_rounds,
                    reason=reason,
                    source=source,
                    progress_callback=progress_callback,
                )
                if isinstance(archive_result, dict) and isinstance(rag_result, dict):
                    archive_result.setdefault("rag_store", rag_result)
                    if rag_result.get("success") is False:
                        archive_result["success"] = False
                        archive_result["archive_write_success"] = True
                        archive_result["error"] = str(
                            rag_result.get("error", "episodic backend write failed")
                            or "episodic backend write failed"
                        )
            except Exception as exc:
                logger.exception("Episodic backend persist_dialogue failed for thread_id=%s", thread_id)
                archive_result["success"] = False
                archive_result["archive_write_success"] = True
                archive_result["error"] = (
                    f"episodic backend persist_dialogue failed: {exc}"
                )
            return archive_result

        return self.systems.episodic.backend.persist_dialogue(
            thread_id=thread_id,
            rounds=rounds,
            reason=reason,
            source=source,
            progress_callback=progress_callback,
        )

    def persist_dialogue_payload(
        self,
        *,
        dialogue_payload: Dict[str, Any],
        thread_id: str,
        reason: str = "chat_thread_flush",
        source: str = "chat_api_thread_flush",
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Persist a system-built dialogue document and index episodic memory from it."""
        from m_agent.chat.dialogue_import import turns_to_rounds

        archive = self.memory_persistence or self.ensure_dialogue_archive()
        if archive is None:
            return {"success": False, "error": "no dialogue archive configured"}

        archive_result = archive.persist_dialogue_payload(
            dialogue_payload=dialogue_payload,
            progress_callback=progress_callback,
        )
        if not archive_result.get("success"):
            return archive_result

        meta = dialogue_payload.get("meta") if isinstance(dialogue_payload.get("meta"), dict) else {}
        tid = str(thread_id or meta.get("thread_id") or "").strip()
        dialogue_id = str(
            dialogue_payload.get("dialogue_id")
            or archive_result.get("dialogue_id")
            or ""
        ).strip()
        turns = dialogue_payload.get("turns") if isinstance(dialogue_payload.get("turns"), list) else []
        user_name = str(dialogue_payload.get("user_id") or getattr(self, "user_name", "User") or "User")
        participants = dialogue_payload.get("participants") if isinstance(dialogue_payload.get("participants"), list) else []
        assistant_name = str(
            participants[1]
            if len(participants) >= 2
            else getattr(self, "assistant_name", "Memory Assistant") or "Memory Assistant"
        )
        rounds = turns_to_rounds(turns, user_speaker=user_name, assistant_speaker=assistant_name)
        rounds = [
            {**dict(item), "dialogue_id": dialogue_id}
            for item in rounds
            if isinstance(item, dict)
        ]
        trace = meta.get("trace_summary") if isinstance(meta.get("trace_summary"), dict) else {}
        episode_notes = trace.get("episode_notes") if isinstance(trace.get("episode_notes"), list) else []
        if rounds and episode_notes:
            rounds[-1]["episode_notes"] = deepcopy(episode_notes)

        backend = self.systems.episodic.backend
        if rounds and getattr(backend, "persistence", None) is not archive:
            try:
                rag_result = backend.persist_dialogue(
                    thread_id=tid,
                    rounds=rounds,
                    reason=reason,
                    source=source,
                    progress_callback=progress_callback,
                )
                if isinstance(archive_result, dict) and isinstance(rag_result, dict):
                    archive_result.setdefault("rag_store", rag_result)
                    if rag_result.get("success") is False:
                        archive_result["success"] = False
                        archive_result["archive_write_success"] = True
                        archive_result["error"] = str(
                            rag_result.get("error", "episodic backend write failed")
                            or "episodic backend write failed"
                        )
            except Exception as exc:
                logger.exception("Episodic backend persist_dialogue failed for thread_id=%s", tid)
                archive_result["success"] = False
                archive_result["archive_write_success"] = True
                archive_result["error"] = (
                    f"episodic backend persist_dialogue failed: {exc}"
                )

        return archive_result

    def on_flush(self, *, conversation_id: str, thread_id: str) -> List[Dict[str, Any]]:
        """Run the scoped compatibility-note flush for direct-mode callers.

        Transaction-bound notes are already frozen from committed Scene into
        the Dialogue payload. This hook drains only the legacy/direct recorder
        and asks the backend to merge those notes within the exact
        thread/conversation scope.
        """
        drained = list(self.thinking_agent.on_flush(conversation_id, thread_id=thread_id) or [])
        try:
            self.systems.episodic.backend.on_flush(
                thread_id=thread_id,
                conversation_id=conversation_id,
                episode_notes=drained,
            )
        except Exception:
            logger.exception(
                "EpisodicMemoryBackend.on_flush failed for conversation_id=%s thread_id=%s",
                conversation_id,
                thread_id,
            )
        return drained

def create_three_layer_chat_agent(
    config_path: str | Path = DEFAULT_CHAT_CONFIG_PATH,
    *,
    systems: Optional[SystemsBundle] = None,
    plugins: Optional[ThreeLayerPluginOverrides] = None,
) -> ThreeLayerChatAgent:
    """Factory for :class:`ThreeLayerChatAgent`.

    Prefer ``systems=`` (a :class:`SystemsBundle`); ``plugins=`` is
    retained for backward compatibility and is adapted internally.
    """
    return ThreeLayerChatAgent(config_path=config_path, systems=systems, plugins=plugins)
