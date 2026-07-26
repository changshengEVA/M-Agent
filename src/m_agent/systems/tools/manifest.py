"""One-file-per-tool capability manifest loading and validation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml

from m_agent.systems.loader import SystemsConfigError, resolve_dotted_path

from .base import ControllerCapabilitySpec


SUPPORTED_INPUT_MODES = frozenset({"param_llm", "instruction_arg", "no_args", "reply"})


@dataclass(frozen=True)
class ToolCapabilityManifest:
    """Validated declarative definition for one executable capability."""

    name: str
    version: int
    category: str
    builder_path: str
    descriptions: Dict[str, str]
    input_mode: str
    instruction_arg: Optional[str] = None
    input_schema: Optional[str] = None
    output_schema: Optional[str] = None
    feedback_projector: Optional[str] = None
    memory_projector: Optional[str] = None
    side_effect: str = "unspecified"
    dependencies: tuple[str, ...] = ()
    defaults: Dict[str, Any] = field(default_factory=dict)
    source_path: Optional[Path] = None

    def build_spec(self) -> ControllerCapabilitySpec:
        builder = resolve_dotted_path(self.builder_path)
        if not callable(builder):
            raise SystemsConfigError(
                f"tool manifest {self.name!r}: builder {self.builder_path!r} is not callable"
            )
        return ControllerCapabilitySpec(
            name=self.name,
            build_tool=builder,
            version=self.version,
            category=self.category,
            input_mode=self.input_mode,
            instruction_arg=self.instruction_arg,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            feedback_projector=self.feedback_projector,
            memory_projector=self.memory_projector,
            side_effect=self.side_effect,
            dependencies=self.dependencies,
        )


def _non_empty_string(value: Any, *, field_name: str, source: Path) -> str:
    text = str(value or "").strip()
    if not text:
        raise SystemsConfigError(f"tool manifest {source}: {field_name} must be a non-empty string")
    return text


def _optional_string(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _validate_dotted_reference(
    reference: Optional[str],
    *,
    field_name: str,
    source: Path,
    require_callable: bool = False,
) -> None:
    if reference is None or reference == "inferred_from_tool":
        return
    target = resolve_dotted_path(reference)
    if require_callable and not callable(target):
        raise SystemsConfigError(
            f"tool manifest {source}: {field_name} {reference!r} is not callable"
        )


def load_tool_capability_manifest(path: Path | str) -> ToolCapabilityManifest:
    """Load one capability YAML and validate its executable contract metadata."""
    source = Path(path).resolve()
    if not source.exists():
        raise SystemsConfigError(f"tool capability manifest not found: {source}")
    with open(source, "r", encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, Mapping):
        raise SystemsConfigError(f"tool manifest must be a mapping: {source}")

    name = _non_empty_string(payload.get("name"), field_name="name", source=source)
    builder_path = _non_empty_string(
        payload.get("builder"), field_name="builder", source=source
    )
    try:
        version = int(payload.get("version", 1) or 1)
    except (TypeError, ValueError) as exc:
        raise SystemsConfigError(f"tool manifest {source}: version must be an integer") from exc
    if version < 1:
        raise SystemsConfigError(f"tool manifest {source}: version must be >= 1")

    category = str(payload.get("category", "general") or "general").strip() or "general"

    descriptions_raw = payload.get("descriptions") or {}
    if not isinstance(descriptions_raw, Mapping):
        raise SystemsConfigError(f"tool manifest {source}: descriptions must be a mapping")
    descriptions = {
        str(language): str(description).strip()
        for language, description in descriptions_raw.items()
        if str(language).strip() and str(description or "").strip()
    }
    if not descriptions:
        raise SystemsConfigError(
            f"tool manifest {source}: descriptions must contain at least one language"
        )

    input_raw = payload.get("input") or {}
    if not isinstance(input_raw, Mapping):
        raise SystemsConfigError(f"tool manifest {source}: input must be a mapping")
    input_mode = str(input_raw.get("mode", "param_llm") or "param_llm").strip()
    if input_mode not in SUPPORTED_INPUT_MODES:
        raise SystemsConfigError(
            f"tool manifest {source}: input.mode must be one of {sorted(SUPPORTED_INPUT_MODES)}"
        )
    instruction_arg = _optional_string(input_raw.get("instruction_arg"))
    if input_mode == "instruction_arg" and instruction_arg is None:
        raise SystemsConfigError(
            f"tool manifest {source}: input.instruction_arg is required for instruction_arg mode"
        )
    if input_mode == "no_args" and instruction_arg is not None:
        raise SystemsConfigError(
            f"tool manifest {source}: no_args mode cannot define input.instruction_arg"
        )

    output_raw = payload.get("output") or {}
    if not isinstance(output_raw, Mapping):
        raise SystemsConfigError(f"tool manifest {source}: output must be a mapping")

    policy_raw = payload.get("policy") or {}
    if not isinstance(policy_raw, Mapping):
        raise SystemsConfigError(f"tool manifest {source}: policy must be a mapping")

    dependencies_raw = payload.get("dependencies") or []
    if not isinstance(dependencies_raw, list):
        raise SystemsConfigError(f"tool manifest {source}: dependencies must be a list")
    dependencies = tuple(
        dependency
        for dependency in (str(item or "").strip() for item in dependencies_raw)
        if dependency
    )

    defaults_raw = payload.get("defaults") or {}
    if not isinstance(defaults_raw, Mapping):
        raise SystemsConfigError(f"tool manifest {source}: defaults must be a mapping")
    defaults = dict(defaults_raw)
    if policy_raw.get("max_calls_per_turn") is not None:
        try:
            max_calls = int(policy_raw["max_calls_per_turn"])
        except (TypeError, ValueError) as exc:
            raise SystemsConfigError(
                f"tool manifest {source}: policy.max_calls_per_turn must be an integer"
            ) from exc
        if max_calls < 1:
            raise SystemsConfigError(
                f"tool manifest {source}: policy.max_calls_per_turn must be >= 1"
            )
        defaults["max_calls_per_turn"] = max_calls

    manifest = ToolCapabilityManifest(
        name=name,
        version=version,
        category=category,
        builder_path=builder_path,
        descriptions=descriptions,
        input_mode=input_mode,
        instruction_arg=instruction_arg,
        input_schema=_optional_string(input_raw.get("schema")),
        output_schema=_optional_string(output_raw.get("schema")),
        feedback_projector=_optional_string(output_raw.get("feedback_projector")),
        memory_projector=_optional_string(output_raw.get("memory_projector")),
        side_effect=str(policy_raw.get("side_effect", "unspecified") or "unspecified").strip(),
        dependencies=dependencies,
        defaults=defaults,
        source_path=source,
    )
    # Resolve executable references during loading so configuration errors fail
    # at startup instead of during the first scheduled/tool invocation.
    manifest.build_spec()
    _validate_dotted_reference(
        manifest.input_schema,
        field_name="input.schema",
        source=source,
    )
    _validate_dotted_reference(
        manifest.output_schema,
        field_name="output.schema",
        source=source,
    )
    _validate_dotted_reference(
        manifest.feedback_projector,
        field_name="output.feedback_projector",
        source=source,
        require_callable=True,
    )
    _validate_dotted_reference(
        manifest.memory_projector,
        field_name="output.memory_projector",
        source=source,
        require_callable=True,
    )
    return manifest


__all__ = [
    "SUPPORTED_INPUT_MODES",
    "ToolCapabilityManifest",
    "load_tool_capability_manifest",
]
