"""WMSystem dataclass + YAML loader.

A WM system is a self-contained plug-in unit covering two access points:

* ``writer`` — a :class:`~m_agent.systems.wm.protocols.WMWriter`
* ``reader`` — a :class:`~m_agent.systems.wm.protocols.WMReader`
* ``display`` — a :class:`~m_agent.systems.wm.protocols.WMDisplay` for
  optional execution-layer diagnostics / custom runtimes

Both share a :class:`WorkingMemoryConfig` (rendering / projection knobs)
that is built from the ``render:`` section of the YAML. Switching the WM
system = pointing ``chat_controller.yaml`` at a different YAML file under
``config/systems/wm/``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from m_agent.chat.working_memory import (
    WorkingMemoryConfig,
    normalize_working_memory_config,
)

from ..loader import (
    SystemsConfigError,
    load_system_yaml,
    materialize_plugin,
)
from .default import DefaultWMDisplay, DefaultWMReader, DefaultWMWriter
from .protocols import WMDisplay, WMReader, WMWriter


@dataclass
class WMSystem:
    """Bundle of the WM plug-in points + their shared config."""

    writer: WMWriter
    reader: WMReader
    display: WMDisplay
    config: WorkingMemoryConfig

    def render(self, entries, *, language: str) -> str:
        return self.reader.render(entries, language=language)

    def display_for_execution(self, entries, *, language: str) -> str:
        return self.display.render(entries, language=language)

    def write(self, entries, tool_history) -> None:
        self.writer.write(entries, tool_history)


def build_default_wm_system(
    *,
    config: WorkingMemoryConfig | None = None,
) -> WMSystem:
    """Build the built-in WMSystem (default reader/writer + default config)."""
    cfg = config if isinstance(config, WorkingMemoryConfig) else WorkingMemoryConfig()
    return WMSystem(
        writer=DefaultWMWriter(cfg),
        reader=DefaultWMReader(cfg),
        display=DefaultWMDisplay(cfg),
        config=cfg,
    )


def load_wm_system(source: Path | str | Mapping[str, Any]) -> WMSystem:
    """Build a :class:`WMSystem` from a YAML path, a dict, or a Path.

    The YAML must look like::

        system: wm
        writer:
          path: m_agent.systems.wm.default.defaults:DefaultWMWriter
        reader:
          path: m_agent.systems.wm.default.defaults:DefaultWMReader
        display:
          path: m_agent.systems.wm.default.defaults:DefaultWMDisplay
        config:    # raw working_memory parameters (all fields of WorkingMemoryConfig)
          enable: true
          inject_max_entries: 20
          ...
    """
    if isinstance(source, Mapping):
        payload: dict[str, Any] = dict(source)
    else:
        payload = load_system_yaml(Path(source), expected_kind="wm")

    cfg = normalize_working_memory_config(payload.get("config"))

    writer_spec = payload.get("writer")
    if writer_spec is None:
        writer = DefaultWMWriter(cfg)
    else:
        writer = materialize_plugin(
            "wm.writer",
            writer_spec,
            default_kwargs={"config": cfg},
        )

    reader_spec = payload.get("reader")
    if reader_spec is None:
        reader = DefaultWMReader(cfg)
    else:
        reader = materialize_plugin(
            "wm.reader",
            reader_spec,
            default_kwargs={"config": cfg},
        )

    if not isinstance(writer, WMWriter):
        raise SystemsConfigError(
            f"wm.writer must satisfy WMWriter protocol; got {type(writer).__name__}"
        )
    if not isinstance(reader, WMReader):
        raise SystemsConfigError(
            f"wm.reader must satisfy WMReader protocol; got {type(reader).__name__}"
        )

    display_spec = payload.get("display")
    if display_spec is None:
        display = DefaultWMDisplay(cfg)
    else:
        display = materialize_plugin(
            "wm.display",
            display_spec,
            default_kwargs={"config": cfg},
        )
    if not isinstance(display, WMDisplay):
        raise SystemsConfigError(
            f"wm.display must satisfy WMDisplay protocol; got {type(display).__name__}"
        )

    return WMSystem(writer=writer, reader=reader, display=display, config=cfg)


__all__ = [
    "WMSystem",
    "build_default_wm_system",
    "load_wm_system",
]
