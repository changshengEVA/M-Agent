"""Canonical, deterministic event traces for runtime semantic scenarios."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


_ID_FIELDS = {
    "stimulus_id": "stim",
    "transaction_id": "tx",
    "delegate_id": "dlg",
    "conversation_id": "conv",
    "thread_id": "thread",
    "schedule_id": "schedule",
    "effect_key": "effect",
}
_VOLATILE_FIELDS = {
    "occurred_at",
    "created_at",
    "updated_at",
    "terminal_at",
    "duration_seconds",
    "absolute_path",
}


@dataclass(frozen=True)
class TraceEvent:
    schema_version: int
    seq: int
    event_type: str
    phase: str
    source: str
    data: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "seq": self.seq,
            "type": self.event_type,
            "phase": self.phase,
            "source": self.source,
            "data": dict(self.data),
        }


class SemanticTrace:
    """Thread-safe recorder that removes UUID/timestamp noise from evidence."""

    schema_version = 1

    def __init__(self, *, scenario_id: str) -> None:
        self.scenario_id = str(scenario_id)
        self._lock = threading.Lock()
        self._events: List[TraceEvent] = []
        self._id_maps: Dict[str, Dict[str, str]] = {
            field: {} for field in _ID_FIELDS
        }

    def _stable_id(self, field: str, value: Any) -> Any:
        text = str(value or "").strip()
        if not text:
            return value
        mapping = self._id_maps[field]
        if text not in mapping:
            mapping[text] = f"{_ID_FIELDS[field]}#{len(mapping) + 1}"
        return mapping[text]

    def _normalize(self, value: Any, *, field: str = "") -> Any:
        if field in _VOLATILE_FIELDS:
            return "<volatile>"
        if field in _ID_FIELDS:
            return self._stable_id(field, value)
        if isinstance(value, dict):
            return {
                str(key): self._normalize(item, field=str(key))
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                if str(key) not in _VOLATILE_FIELDS
            }
        if isinstance(value, (list, tuple)):
            return [self._normalize(item) for item in value]
        return value

    def record(
        self,
        event_type: str,
        *,
        phase: str = "",
        source: str = "",
        **data: Any,
    ) -> TraceEvent:
        with self._lock:
            event = TraceEvent(
                schema_version=self.schema_version,
                seq=len(self._events) + 1,
                event_type=str(event_type),
                phase=str(phase),
                source=str(source),
                data=self._normalize(data),
            )
            self._events.append(event)
            return event

    @property
    def events(self) -> List[TraceEvent]:
        with self._lock:
            return list(self._events)

    def count(self, event_type: str) -> int:
        return sum(1 for event in self.events if event.event_type == event_type)

    def first_seq(self, event_type: str) -> Optional[int]:
        for event in self.events:
            if event.event_type == event_type:
                return event.seq
        return None

    def assert_before(self, earlier: str, later: str) -> None:
        first = self.first_seq(earlier)
        second = self.first_seq(later)
        assert first is not None, f"trace has no event {earlier!r}"
        assert second is not None, f"trace has no event {later!r}"
        assert first < second, f"expected {earlier} before {later}: {self.to_dict()}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "events": [event.to_dict() for event in self.events],
        }

    def attach(self, request: Any) -> None:
        """Attach normalized evidence to the custom pytest subprocess report."""

        request.node.user_properties.append(
            ("semantic_trace", json.dumps(self.to_dict(), ensure_ascii=False))
        )
