"""ToolSuiteSystem dataclass + one-file-per-tool manifest loader.

The tool-suite system owns the top-level capability registry plus the
chat-stack's selection / tuning surface:

* ``capabilities_dir`` — directory containing one YAML manifest per tool
* ``registry`` — :class:`ControllerCapabilityRegistry` built from those manifests
* ``enabled`` — names of enabled capabilities (subset of the registry)
* ``defaults`` — per-tool defaults dict (timeouts, mail_scope, limits, ...)
* ``runtime_descriptions`` — per-tool descriptions loaded from manifests

Switching the tool suite = pointing ``chat_controller.yaml`` at a
different YAML file under ``config/systems/tools/``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

from ..loader import (
    SystemsConfigError,
    load_system_yaml,
    materialize_plugin,
)
from .manifest import ToolCapabilityManifest, load_tool_capability_manifest
from .default.registry import get_default_capability_registry
from .registry import (
    ControllerCapabilityRegistry,
    resolve_enabled_controller_capability_names,
)


logger = logging.getLogger(__name__)


@dataclass
class ToolSuiteSystem:
    """Bundle of the tool-suite plug-in point + its policy knobs.

    ``runtime_descriptions`` maps a tool name to either a plain string
    (already language-resolved) or a ``{language: str}`` mapping. The
    chat agent's ``_get_capability_description`` resolves the active
    language before prompting; storing the unresolved form keeps the
    same YAML usable across ``prompt_language`` settings.
    """

    registry: ControllerCapabilityRegistry
    enabled: List[str]
    defaults: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    runtime_descriptions: Dict[str, Any] = field(default_factory=dict)
    manifests: Dict[str, ToolCapabilityManifest] = field(default_factory=dict)


def build_default_tool_suite_system(
    *,
    enabled: Optional[List[str]] = None,
    defaults: Optional[Mapping[str, Mapping[str, Any]]] = None,
    runtime_descriptions: Optional[Mapping[str, Any]] = None,
) -> ToolSuiteSystem:
    """Build the legacy programmatic tool suite from the default registry."""
    registry = get_default_capability_registry()
    resolved_enabled = resolve_enabled_controller_capability_names(
        list(enabled) if enabled else None,
        registry=registry,
    )
    norm_defaults: Dict[str, Dict[str, Any]] = {}
    if defaults:
        for key, value in defaults.items():
            if isinstance(value, Mapping):
                norm_defaults[str(key)] = dict(value)
    norm_descriptions: Dict[str, Any] = {}
    if runtime_descriptions:
        for key, value in runtime_descriptions.items():
            if isinstance(value, str) and value.strip():
                norm_descriptions[str(key)] = value
            elif isinstance(value, Mapping):
                # Pass through ``{language: str}`` maps unchanged.
                norm_descriptions[str(key)] = dict(value)
    return ToolSuiteSystem(
        registry=registry,
        enabled=resolved_enabled,
        defaults=norm_defaults,
        runtime_descriptions=norm_descriptions,
        manifests={},
    )


def _resolve_manifest_paths(
    payload: Mapping[str, Any],
    *,
    source_path: Optional[Path],
) -> List[Path]:
    """Resolve ``capabilities_dir`` / ``capability_manifests`` entries."""
    paths: List[Path] = []
    base_dir = source_path.parent if source_path is not None else None

    capabilities_dir_raw = payload.get("capabilities_dir")
    if capabilities_dir_raw is not None:
        if not isinstance(capabilities_dir_raw, str) or not capabilities_dir_raw.strip():
            raise SystemsConfigError("tools.capabilities_dir must be a non-empty path string")
        if base_dir is None:
            raise SystemsConfigError(
                "tools.capabilities_dir requires a file-backed tool-suite config"
            )
        directory = Path(capabilities_dir_raw)
        if not directory.is_absolute():
            directory = (base_dir / directory).resolve()
        if not directory.is_dir():
            raise SystemsConfigError(f"tools.capabilities_dir not found: {directory}")
        paths.extend(sorted(directory.glob("*.yaml")))
        paths.extend(sorted(directory.glob("*.yml")))
        if not paths:
            raise SystemsConfigError(f"tools.capabilities_dir contains no YAML manifests: {directory}")

    manifests_raw = payload.get("capability_manifests")
    if manifests_raw is not None:
        if not isinstance(manifests_raw, list):
            raise SystemsConfigError("tools.capability_manifests must be a list of paths")
        if base_dir is None:
            raise SystemsConfigError(
                "tools.capability_manifests requires a file-backed tool-suite config"
            )
        for raw_path in manifests_raw:
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise SystemsConfigError(
                    "tools.capability_manifests entries must be non-empty path strings"
                )
            path = Path(raw_path)
            if not path.is_absolute():
                path = (base_dir / path).resolve()
            paths.append(path)

    # Preserve declaration order while rejecting duplicate physical files.
    unique: List[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _load_manifests(paths: List[Path]) -> Dict[str, ToolCapabilityManifest]:
    manifests: Dict[str, ToolCapabilityManifest] = {}
    for path in paths:
        manifest = load_tool_capability_manifest(path)
        if manifest.name in manifests:
            previous = manifests[manifest.name].source_path
            raise SystemsConfigError(
                f"duplicate tool manifest name {manifest.name!r}: {previous} and {path}"
            )
        manifests[manifest.name] = manifest
    return manifests


def _registry_from_manifests(
    manifests: Mapping[str, ToolCapabilityManifest],
) -> ControllerCapabilityRegistry:
    registry = ControllerCapabilityRegistry()
    for manifest in manifests.values():
        registry.register(manifest.build_spec())
    return registry


def load_tool_suite_system(source: Path | str | Mapping[str, Any]) -> ToolSuiteSystem:
    """Build a :class:`ToolSuiteSystem` from YAML.

    YAML shape::

        system: tools
        capabilities_dir: ./capabilities
        enabled: [shallow_recall, deep_recall, get_current_time, ...]
        defaults:
          __controller__: {max_calls_per_turn: 12}
          email_ask: {mail_scope: unread}
          ...

    The legacy ``registry`` and ``runtime_descriptions_path`` fields remain
    supported for third-party configurations that have not migrated yet.
    """
    if isinstance(source, Mapping):
        payload: dict[str, Any] = dict(source)
        source_path: Optional[Path] = None
    else:
        source_path = Path(source).resolve()
        payload = load_system_yaml(source_path, expected_kind="tools")

    # ---- one-file-per-tool manifests / legacy registry
    manifest_paths = _resolve_manifest_paths(payload, source_path=source_path)
    manifests = _load_manifests(manifest_paths)
    registry_spec = payload.get("registry")
    if manifests and registry_spec is not None:
        raise SystemsConfigError(
            "tools config cannot define both capability manifests and a legacy registry"
        )
    if manifests:
        registry = _registry_from_manifests(manifests)
    elif registry_spec is None:
        registry = get_default_capability_registry()
    else:
        registry = materialize_plugin("tools.registry", registry_spec)
        if not isinstance(registry, ControllerCapabilityRegistry):
            raise SystemsConfigError(
                f"tools.registry must produce a ControllerCapabilityRegistry; "
                f"got {type(registry).__name__}"
            )

    # ---- enabled list
    enabled_raw = payload.get("enabled")
    # Omitting ``enabled`` must never turn every discovered manifest into an
    # executable capability. In particular, a capabilities directory may
    # contain opt-in write/send tools next to the safe defaults. Resolve the
    # ordinary read/emit allow-list for both manifest and registry suites.
    enabled = resolve_enabled_controller_capability_names(
        enabled_raw if enabled_raw is not None else None,
        registry=registry,
    )

    # ---- per-tool defaults
    defaults_raw = payload.get("defaults") or {}
    if not isinstance(defaults_raw, Mapping):
        raise SystemsConfigError(
            f"tools.defaults must be a mapping (got {type(defaults_raw).__name__})"
        )
    defaults: Dict[str, Dict[str, Any]] = {
        name: dict(manifest.defaults)
        for name, manifest in manifests.items()
        if manifest.defaults
    }
    for key, value in defaults_raw.items():
        if isinstance(value, Mapping):
            defaults.setdefault(str(key), {})
            defaults[str(key)].update(dict(value))

    # ---- runtime descriptions (inline or sibling yaml)
    descriptions: Dict[str, Any] = {
        name: dict(manifest.descriptions)
        for name, manifest in manifests.items()
    }
    inline_desc = payload.get("runtime_descriptions")
    if isinstance(inline_desc, Mapping):
        for key, value in inline_desc.items():
            if isinstance(value, str) and value.strip():
                descriptions[str(key)] = value
            elif isinstance(value, Mapping):
                descriptions[str(key)] = dict(value)

    desc_path_raw = payload.get("runtime_descriptions_path")
    if isinstance(desc_path_raw, str) and desc_path_raw.strip():
        desc_path = Path(desc_path_raw)
        if not desc_path.is_absolute() and source_path is not None:
            desc_path = (source_path.parent / desc_path).resolve()
        if desc_path.exists():
            try:
                with open(desc_path, "r", encoding="utf-8") as f:
                    extra = yaml.safe_load(f) or {}
                if isinstance(extra, Mapping):
                    tools_section = extra.get("tools") if isinstance(extra.get("tools"), Mapping) else extra
                    if isinstance(tools_section, Mapping):
                        for key, value in tools_section.items():
                            if isinstance(value, Mapping):
                                desc = value.get("description")
                                if isinstance(desc, str) and desc.strip():
                                    descriptions.setdefault(str(key), desc.strip())
                                elif isinstance(desc, Mapping):
                                    # Language-map (``{zh: ..., en: ...}``).
                                    descriptions.setdefault(str(key), dict(desc))
                            elif isinstance(value, str) and value.strip():
                                descriptions.setdefault(str(key), value.strip())
            except Exception:
                logger.exception(
                    "Failed to load tools.runtime_descriptions_path=%s", desc_path
                )

    return ToolSuiteSystem(
        registry=registry,
        enabled=enabled,
        defaults=defaults,
        runtime_descriptions=descriptions,
        manifests=manifests,
    )


__all__ = [
    "ToolSuiteSystem",
    "build_default_tool_suite_system",
    "load_tool_suite_system",
]
