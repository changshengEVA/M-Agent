"""Product Chat Source Adapter (v0.3.1).

Adapter-private ChatSignal stays here. Runtime only sees Observation via ingest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from m_agent.runtime.perception.observation import user_message_observation
from m_agent.sdk.stimulus.contracts import IngestResult


@dataclass(frozen=True)
class ChatSignal:
    """Adapter-private chat event. Not part of the public SDK."""

    text: str
    message_id: str = ""
    occurred_at: str = ""
    subject: str = "user"
    payload: Mapping[str, Any] = field(default_factory=dict)


class ChatSourceAdapter:
    """Normalize private ChatSignals and ingest Observations."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    @staticmethod
    def idempotency_key(*, thread_id: str, message_id: str) -> Optional[str]:
        mid = str(message_id or "").strip()
        tid = str(thread_id or "").strip()
        if not mid or not tid:
            return None
        return f"chat:{tid}:{mid}"

    def to_observation(
        self,
        signal: ChatSignal,
        *,
        thread_id: str,
        conversation_id: str,
        observed_at: Optional[str] = None,
    ):
        occurred_at = str(signal.occurred_at or "").strip()
        observed = str(observed_at or occurred_at).strip()
        if not occurred_at:
            occurred_at = observed
        return user_message_observation(
            thread_id=thread_id,
            conversation_id=conversation_id,
            text=signal.text,
            occurred_at=occurred_at,
            observed_at=observed or occurred_at,
            payload=dict(signal.payload or {}),
            idempotency_key=self.idempotency_key(
                thread_id=thread_id,
                message_id=signal.message_id,
            ),
            subject=str(signal.subject or "").strip() or "user",
        )

    def handle_message(
        self,
        signal: ChatSignal,
        *,
        thread_id: str,
        conversation_id: str,
        observed_at: Optional[str] = None,
        schedule_drainer: bool = True,
    ) -> IngestResult:
        observation = self.to_observation(
            signal,
            thread_id=thread_id,
            conversation_id=conversation_id,
            observed_at=observed_at,
        )
        return self.runtime.ingest(
            observation,
            schedule_drainer=schedule_drainer,
        )
