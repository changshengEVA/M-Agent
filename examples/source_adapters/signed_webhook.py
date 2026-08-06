"""Minimal HMAC-signed webhook → Observation → runtime.ingest example."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping, Optional

from m_agent.sdk.stimulus import Observation
from m_agent.sdk.stimulus.contracts import IngestResult


class SignedWebhookAdapter:
    """Verify an HMAC signature, then ingest a normalized Observation."""

    def __init__(
        self,
        runtime: Any,
        *,
        secret: str,
        source: str = "webhook",
    ) -> None:
        self.runtime = runtime
        self.secret = str(secret or "").encode("utf-8")
        self.source = source

    def verify(self, body: bytes, signature: str) -> bool:
        digest = hmac.new(
            self.secret,
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(digest, str(signature or "").strip())

    def handle(
        self,
        *,
        body: bytes,
        signature: str,
        thread_id: str,
        conversation_id: str,
        observed_at: str,
        headers: Optional[Mapping[str, str]] = None,
    ) -> IngestResult:
        del headers
        if not self.verify(body, signature):
            raise PermissionError("invalid webhook signature")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("webhook body must be a JSON object")
        event_id = str(payload.get("id", payload.get("event_id", "")) or "").strip()
        occurred_at = str(
            payload.get("occurred_at", observed_at) or observed_at
        ).strip()
        observation = Observation(
            source=self.source,
            type=str(payload.get("type", "webhook") or "webhook"),
            thread_id=thread_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            observed_at=observed_at,
            subject=str(payload.get("subject", "") or ""),
            text=str(payload.get("text", "") or ""),
            idempotency_key=(
                f"{self.source}:{event_id}" if event_id else None
            ),
            causation_id=str(payload.get("causation_id", "") or "") or None,
            payload=payload,
        )
        return self.runtime.ingest(observation, schedule_drainer=True)
