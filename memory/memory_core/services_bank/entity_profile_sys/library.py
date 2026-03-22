#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Derived profile index used by entity_profile_sys service."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class EvidenceRef:
    """Evidence pointer back to source fact and dialogue/episode."""

    fact_id: str = ""
    fact_file: str = ""
    scene_id: str = ""
    dialogue_id: str = ""
    episode_id: str = ""
    start_time: str = ""
    end_time: str = ""

    def key(self) -> Tuple[str, str, str, str, str, str, str]:
        return (
            str(self.fact_id or "").strip(),
            str(self.fact_file or "").strip(),
            str(self.scene_id or "").strip(),
            str(self.dialogue_id or "").strip(),
            str(self.episode_id or "").strip(),
            str(self.start_time or "").strip(),
            str(self.end_time or "").strip(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "fact_file": self.fact_file,
            "scene_id": self.scene_id,
            "dialogue_id": self.dialogue_id,
            "episode_id": self.episode_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceRef":
        if not isinstance(data, dict):
            return cls()
        return cls(
            fact_id=str(data.get("fact_id", "") or ""),
            fact_file=str(data.get("fact_file", "") or ""),
            scene_id=str(data.get("scene_id", "") or ""),
            dialogue_id=str(data.get("dialogue_id", "") or ""),
            episode_id=str(data.get("episode_id", "") or ""),
            start_time=str(data.get("start_time", "") or ""),
            end_time=str(data.get("end_time", "") or ""),
        )


@dataclass
class EventTimeRange:
    """Concrete time interval extracted for one event."""

    start_time: str = ""
    end_time: str = ""

    def key(self) -> Tuple[str, str]:
        return (str(self.start_time or "").strip(), str(self.end_time or "").strip())

    def to_dict(self) -> Dict[str, str]:
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventTimeRange":
        if not isinstance(data, dict):
            return cls()
        return cls(
            start_time=str(data.get("start_time", "") or ""),
            end_time=str(data.get("end_time", "") or ""),
        )


@dataclass
class AttributeEntry:
    """Entity attribute item."""

    field: str
    content: List[str] = field(default_factory=list)
    text: str = ""
    embedding: Optional[List[float]] = None
    sources: List[EvidenceRef] = field(default_factory=list)
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "content": self.content,
            "text": self.text,
            "embedding": self.embedding,
            "sources": [src.to_dict() for src in self.sources],
            "confidence": self.confidence,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AttributeEntry":
        if not isinstance(data, dict):
            return cls(field="")

        raw_content = data.get("content", [])
        if isinstance(raw_content, list):
            content = [str(x).strip() for x in raw_content if str(x).strip()]
        elif isinstance(raw_content, str):
            content = [raw_content.strip()] if raw_content.strip() else []
        else:
            content = []

        sources_raw = data.get("sources", [])
        sources: List[EvidenceRef] = []
        if isinstance(sources_raw, list):
            sources = [EvidenceRef.from_dict(x) for x in sources_raw if isinstance(x, dict)]

        embedding = data.get("embedding")
        if not (isinstance(embedding, list) and embedding):
            embedding = None

        return cls(
            field=str(data.get("field", "") or ""),
            content=content,
            text=str(data.get("text", "") or ""),
            embedding=embedding,
            sources=sources,
            confidence=float(data.get("confidence", 0.0) or 0.0),
            metadata=data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {},
        )


@dataclass
class EventEntry:
    """Entity timeline event item."""

    event: str
    actual_time: List[EventTimeRange] = field(default_factory=list)
    abstract_time: List[str] = field(default_factory=list)
    text: str = ""
    embedding: Optional[List[float]] = None
    sources: List[EvidenceRef] = field(default_factory=list)
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event,
            "actual_time": [slot.to_dict() for slot in self.actual_time],
            "abstract_time": self.abstract_time,
            "text": self.text,
            "embedding": self.embedding,
            "sources": [src.to_dict() for src in self.sources],
            "confidence": self.confidence,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventEntry":
        if not isinstance(data, dict):
            return cls(event="")

        actual_raw = data.get("actual_time", [])
        actual_time: List[EventTimeRange] = []
        if isinstance(actual_raw, list):
            actual_time = [EventTimeRange.from_dict(x) for x in actual_raw if isinstance(x, dict)]

        abstract_raw = data.get("abstract_time", [])
        if isinstance(abstract_raw, list):
            abstract_time = [str(x).strip() for x in abstract_raw if str(x).strip()]
        elif isinstance(abstract_raw, str):
            abstract_time = [abstract_raw.strip()] if abstract_raw.strip() else []
        else:
            abstract_time = []

        sources_raw = data.get("sources", [])
        sources: List[EvidenceRef] = []
        if isinstance(sources_raw, list):
            sources = [EvidenceRef.from_dict(x) for x in sources_raw if isinstance(x, dict)]

        embedding = data.get("embedding")
        if not (isinstance(embedding, list) and embedding):
            embedding = None

        return cls(
            event=str(data.get("event", "") or ""),
            actual_time=actual_time,
            abstract_time=abstract_time,
            text=str(data.get("text", "") or ""),
            embedding=embedding,
            sources=sources,
            confidence=float(data.get("confidence", 0.0) or 0.0),
            metadata=data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {},
        )


@dataclass
class EntityProfileRecord:
    """Per-entity profile bundle."""

    entity_id: str
    attributes: List[AttributeEntry] = field(default_factory=list)
    events: List[EventEntry] = field(default_factory=list)
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "attributes": [item.to_dict() for item in self.attributes],
            "events": [item.to_dict() for item in self.events],
            "summary": self.summary,
            "metadata": self.metadata,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EntityProfileRecord":
        if not isinstance(data, dict):
            return cls(entity_id="")

        attrs_raw = data.get("attributes", [])
        events_raw = data.get("events", [])
        attributes = [AttributeEntry.from_dict(x) for x in attrs_raw if isinstance(x, dict)] if isinstance(attrs_raw, list) else []
        events = [EventEntry.from_dict(x) for x in events_raw if isinstance(x, dict)] if isinstance(events_raw, list) else []

        return cls(
            entity_id=str(data.get("entity_id", "") or ""),
            attributes=attributes,
            events=events,
            summary=str(data.get("summary", "") or ""),
            metadata=data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {},
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
        )


class EntityProfileLibrary:
    """Entity profile derived index."""

    def __init__(self, embed_func=None, data_path: Optional[str] = None):
        self.embed_func = embed_func
        self.data_path = str(data_path) if data_path else ""
        self.profiles: Dict[str, EntityProfileRecord] = {}
        self.last_rebuild_time: float = 0.0
        if self.data_path:
            self.load_from_path(self.data_path)

    @staticmethod
    def merge_sources(left: List[EvidenceRef], right: List[EvidenceRef]) -> List[EvidenceRef]:
        merged: List[EvidenceRef] = []
        seen = set()
        for item in (left or []) + (right or []):
            if not isinstance(item, EvidenceRef):
                continue
            key = item.key()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged

    @staticmethod
    def merge_time_ranges(left: List[EventTimeRange], right: List[EventTimeRange]) -> List[EventTimeRange]:
        merged: List[EventTimeRange] = []
        seen = set()
        for slot in (left or []) + (right or []):
            if not isinstance(slot, EventTimeRange):
                continue
            key = slot.key()
            if key in seen:
                continue
            seen.add(key)
            merged.append(slot)
        return merged

    @staticmethod
    def merge_abstract_time(left: List[str], right: List[str]) -> List[str]:
        merged: List[str] = []
        seen = set()
        for item in (left or []) + (right or []):
            value = str(item or "").strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(value)
        return merged

    @staticmethod
    def _safe_file_stem(entity_id: str) -> str:
        safe = "".join(ch for ch in str(entity_id or "") if ch.isalnum() or ch in ("_", "-", "."))
        return safe or "unknown_entity"

    @staticmethod
    def _load_json(path: Path) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.warning("EntityProfileLibrary load json failed (%s): %s", path, exc)
        return None

    @staticmethod
    def _save_json(path: Path, data: Dict[str, Any]) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.warning("EntityProfileLibrary save json failed (%s): %s", path, exc)
            return False

    def clear(self) -> None:
        self.profiles.clear()
        self.last_rebuild_time = 0.0

    def ensure_entity(self, entity_id: str) -> EntityProfileRecord:
        safe_id = str(entity_id or "").strip()
        if safe_id not in self.profiles:
            self.profiles[safe_id] = EntityProfileRecord(entity_id=safe_id, updated_at=time.time())
        return self.profiles[safe_id]

    def get_entity(self, entity_id: str) -> Optional[EntityProfileRecord]:
        return self.profiles.get(str(entity_id or "").strip())

    def delete_entity(self, entity_id: str) -> bool:
        safe_id = str(entity_id or "").strip()
        if safe_id in self.profiles:
            del self.profiles[safe_id]
            return True
        return False

    def rename_entity(self, old_id: str, new_id: str) -> bool:
        old_key = str(old_id or "").strip()
        new_key = str(new_id or "").strip()
        if not old_key or not new_key or old_key == new_key:
            return False
        if old_key not in self.profiles:
            return False

        old_record = self.profiles.pop(old_key)
        old_record.entity_id = new_key
        old_record.touch()

        if new_key in self.profiles:
            self.merge_entity(source_id=old_key, target_id=new_key, source_record=old_record)
        else:
            self.profiles[new_key] = old_record
        return True

    def merge_entity(
        self,
        source_id: str,
        target_id: str,
        source_record: Optional[EntityProfileRecord] = None,
    ) -> bool:
        source_key = str(source_id or "").strip()
        target_key = str(target_id or "").strip()
        if not source_key or not target_key or source_key == target_key:
            return False

        source = source_record if source_record is not None else self.profiles.get(source_key)
        if source is None:
            return False

        target = self.ensure_entity(target_key)
        target.attributes.extend(source.attributes)
        target.events.extend(source.events)
        if not target.summary and source.summary:
            target.summary = source.summary
        target.metadata = {**source.metadata, **target.metadata}
        target.touch()

        if source_record is None and source_key in self.profiles:
            del self.profiles[source_key]
        return True

    def load_from_path(self, data_path: str) -> bool:
        self.clear()
        root = Path(data_path)
        if not root.exists() or not root.is_dir():
            logger.info("EntityProfileLibrary load skipped, path missing: %s", root)
            return False

        loaded = 0
        for json_file in sorted(root.glob("*.json")):
            payload = self._load_json(json_file)
            if not payload:
                continue
            record = EntityProfileRecord.from_dict(payload)
            if not record.entity_id:
                record.entity_id = json_file.stem
            self.profiles[record.entity_id] = record
            loaded += 1

        self.last_rebuild_time = time.time()
        logger.info("EntityProfileLibrary loaded %s profiles from %s", loaded, root)
        return loaded > 0

    def save_to_path(self, data_path: Optional[str] = None) -> bool:
        target = Path(data_path or self.data_path or "")
        if not str(target):
            logger.warning("EntityProfileLibrary save skipped: empty target path")
            return False

        target.mkdir(parents=True, exist_ok=True)
        current_ids = set(self.profiles.keys())
        existing_ids = {p.stem for p in target.glob("*.json") if p.is_file()}
        ghost_ids = existing_ids - {self._safe_file_stem(x) for x in current_ids}
        for ghost in ghost_ids:
            ghost_file = target / f"{ghost}.json"
            try:
                ghost_file.unlink()
            except Exception:
                pass

        saved = 0
        for entity_id, record in self.profiles.items():
            file_name = f"{self._safe_file_stem(entity_id)}.json"
            ok = self._save_json(target / file_name, record.to_dict())
            if ok:
                saved += 1

        logger.info("EntityProfileLibrary saved %s profiles to %s", saved, target)
        return True

    def list_entity_ids(self) -> List[str]:
        return sorted(self.profiles.keys())

    def get_stats(self) -> Dict[str, Any]:
        profile_count = len(self.profiles)
        attr_count = 0
        event_count = 0
        for profile in self.profiles.values():
            attr_count += len(profile.attributes)
            event_count += len(profile.events)

        return {
            "profile_count": profile_count,
            "attribute_count": attr_count,
            "event_count": event_count,
            "last_rebuild_time": self.last_rebuild_time,
            "last_rebuild_human": time.ctime(self.last_rebuild_time) if self.last_rebuild_time else "Never",
            "data_path": self.data_path,
        }
