"""Minimal Source Adapter template for M-Agent v0.3 Stimulus Kernel.

Adapter-private Signal stays here. Runtime only sees Observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from m_agent.sdk.stimulus import Observation
from m_agent.sdk.stimulus.contracts import IngestResult


@dataclass(frozen=True)
class Signal:
    """Adapter-private raw event. Not part of the public SDK."""

    source: str
    event_type: str
    body: Mapping[str, Any]
    event_id: str
    occurred_at: str


class TemplateSourceAdapter:
    """Normalize private Signals and ingest Observations."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def handle_signal(
        self,
        signal: Signal,
        *,
        thread_id: str,
        conversation_id: str,
        observed_at: Optional[str] = None,
    ) -> IngestResult:
        observation = Observation(
            source=signal.source,
            type=signal.event_type,
            thread_id=thread_id,
            conversation_id=conversation_id,
            occurred_at=signal.occurred_at,
            observed_at=observed_at or signal.occurred_at,
            subject=str(signal.body.get("subject", "") or ""),
            text=str(signal.body.get("text", "") or ""),
            idempotency_key=f"{signal.source}:{signal.event_id}",
            causation_id=str(signal.body.get("causation_id", "") or "") or None,
            confidence=None,
            privacy_class=str(signal.body.get("privacy_class", "") or "") or None,
            payload_ref=str(signal.body.get("payload_ref", "") or "") or None,
            payload=dict(signal.body),
        )
        return self.runtime.ingest(observation, schedule_drainer=True)
