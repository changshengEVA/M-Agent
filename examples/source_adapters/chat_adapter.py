"""Chat Source Adapter template for M-Agent v0.3.1.

Mirrors the product adapter in ``m_agent.runtime.perception.chat_adapter``.
Adapter-private Signal stays here. Runtime only sees Observation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from m_agent.sdk.stimulus import Observation
from m_agent.sdk.stimulus.contracts import IngestResult


@dataclass(frozen=True)
class ChatSignal:
    """Adapter-private chat event. Not part of the public SDK."""

    text: str
    message_id: str
    occurred_at: str
    subject: str = "user"
    payload: Mapping[str, Any] = field(default_factory=dict)


class ChatSourceAdapter:
    """Normalize private ChatSignals and ingest Observations."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def handle_message(
        self,
        signal: ChatSignal,
        *,
        thread_id: str,
        conversation_id: str,
        observed_at: Optional[str] = None,
        schedule_drainer: bool = True,
    ) -> IngestResult:
        mid = str(signal.message_id or "").strip()
        tid = str(thread_id or "").strip()
        observation = Observation(
            source="chat",
            type="user_message",
            thread_id=tid,
            conversation_id=conversation_id,
            occurred_at=signal.occurred_at,
            observed_at=observed_at or signal.occurred_at,
            subject=str(signal.subject or "").strip() or "user",
            text=str(signal.text or "").strip(),
            idempotency_key=f"chat:{tid}:{mid}" if mid and tid else None,
            payload=dict(signal.payload),
        )
        return self.runtime.ingest(
            observation,
            schedule_drainer=schedule_drainer,
        )
