"""Reference source that emits Observations under a Virtual Clock."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from m_agent.runtime.clock import VirtualClock
from m_agent.sdk.stimulus import Observation
from m_agent.sdk.stimulus.contracts import IngestResult


class VirtualClockSource:
    """Replay timed events with a shared injectable clock."""

    def __init__(self, runtime: Any, *, clock: VirtualClock) -> None:
        self.runtime = runtime
        self.clock = clock

    def emit(
        self,
        events: Iterable[Mapping[str, Any]],
        *,
        thread_id: str,
        conversation_id: str,
        source: str = "virtual_clock",
    ) -> list[IngestResult]:
        results: list[IngestResult] = []
        for event in events:
            when = str(event.get("occurred_at", "") or "").strip()
            if when:
                self.clock.set(when)
            now = self.clock()
            observation = Observation(
                source=source,
                type=str(event.get("type", "external_event") or "external_event"),
                thread_id=thread_id,
                conversation_id=conversation_id,
                occurred_at=str(event.get("occurred_at", now) or now),
                observed_at=now,
                subject=str(event.get("subject", "") or ""),
                text=str(event.get("text", "") or ""),
                idempotency_key=str(event.get("idempotency_key", "") or "")
                or None,
                expires_at=str(event.get("expires_at", "") or "") or None,
                payload=dict(event.get("payload") or {}),
            )
            results.append(
                self.runtime.ingest(observation, schedule_drainer=False)
            )
        return results
