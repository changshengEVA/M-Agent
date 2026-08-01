"""Scene system bundle."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from m_agent.systems.loader import SystemsConfigError, load_system_yaml

from .default import SceneLogStore, SceneReaderAdapter, SceneWriterAdapter
from .protocols import SceneReader, SceneWriter


@dataclass
class SceneSystem:
    writer: SceneWriter
    reader: SceneReader
    store: SceneLogStore
    persist_dir: Optional[Path] = None


def build_default_scene_system(
    *,
    persist_dir: Optional[Path] = None,
    persist_enabled: bool = True,
    runtime_store: Any = None,
) -> SceneSystem:
    store = SceneLogStore(
        persist_dir=persist_dir,
        persist_enabled=persist_enabled,
        runtime_store=runtime_store,
    )
    return SceneSystem(
        writer=SceneWriterAdapter(store),
        reader=SceneReaderAdapter(store),
        store=store,
        persist_dir=persist_dir,
    )


def load_scene_system(source: Path | str | Mapping[str, Any]) -> SceneSystem:
    if isinstance(source, Mapping):
        payload = dict(source)
    else:
        payload = load_system_yaml(Path(source), expected_kind="scene")

    persist_enabled = bool(payload.get("persist_jsonl", True))
    persist_dir_raw = str(payload.get("persist_dir", "") or "").strip()
    persist_dir = Path(persist_dir_raw).expanduser() if persist_dir_raw else None

    store_spec = payload.get("store")
    if store_spec is None:
        return build_default_scene_system(persist_dir=persist_dir, persist_enabled=persist_enabled)

    from m_agent.systems.loader import materialize_plugin

    store = materialize_plugin("scene.store", store_spec)
    if not isinstance(store, SceneLogStore):
        raise SystemsConfigError(f"scene.store must be SceneLogStore, got {type(store).__name__}")
    return SceneSystem(
        writer=SceneWriterAdapter(store),
        reader=SceneReaderAdapter(store),
        store=store,
        persist_dir=persist_dir,
    )


__all__ = ["SceneSystem", "build_default_scene_system", "load_scene_system"]
