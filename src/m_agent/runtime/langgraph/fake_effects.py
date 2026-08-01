"""Deterministic async fake effects for the P6 LangGraph PoC."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

@dataclass
class FakeEffectIntent:
    effect_id: str
    capability: str
    delegate_id: str
    transaction_id: str
    activation_id: str
    conversation_id: str
    thread_id: str
    idempotency_key: str
    status: str = "pending"
    attempts: int = 0
    visible_effects: int = 0
    result_summary: str = ""


@dataclass
class FakeEffectExecutor:
    """Run side-effect-free fake capabilities and relay Feedback."""

    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _effects: Dict[str, FakeEffectIntent] = field(default_factory=dict, init=False)
    _by_idempotency: Dict[str, str] = field(default_factory=dict, init=False)
    _relay_feedback: Optional[
        Callable[[FakeEffectIntent], str]
    ] = None
    _auto_complete: bool = True

    def install_relay(
        self,
        relay: Callable[[FakeEffectIntent], str],
    ) -> None:
        self._relay_feedback = relay

    def set_auto_complete(self, enabled: bool) -> None:
        self._auto_complete = bool(enabled)

    def dispatch(
        self,
        *,
        effect_id: str,
        capability: str,
        delegate_id: str,
        transaction_id: str,
        activation_id: str,
        conversation_id: str,
        thread_id: str,
        idempotency_key: str,
    ) -> FakeEffectIntent:
        key = str(idempotency_key or "").strip()
        with self._lock:
            existing_id = self._by_idempotency.get(key)
            if existing_id:
                existing = self._effects[existing_id]
                return existing
            intent = FakeEffectIntent(
                effect_id=str(effect_id or "").strip(),
                capability=str(capability or "").strip(),
                delegate_id=str(delegate_id or "").strip(),
                transaction_id=str(transaction_id or "").strip(),
                activation_id=str(activation_id or "").strip(),
                conversation_id=str(conversation_id or "").strip(),
                thread_id=str(thread_id or "").strip(),
                idempotency_key=key,
            )
            self._effects[intent.effect_id] = intent
            if key:
                self._by_idempotency[key] = intent.effect_id
        if self._auto_complete:
            self.complete(intent.effect_id)
        return intent

    def complete(self, effect_id: str) -> FakeEffectIntent:
        with self._lock:
            intent = self._require(effect_id)
            if intent.status == "completed":
                return intent
            intent.attempts += 1
            intent.visible_effects = 1
            intent.status = "completed"
            intent.result_summary = (
                f"{intent.capability} completed for {intent.delegate_id}"
            )
        relay = self._relay_feedback
        if relay is not None:
            relay(intent)
        return intent

    def get(self, effect_id: str) -> Optional[FakeEffectIntent]:
        with self._lock:
            return self._effects.get(str(effect_id or "").strip())

    def list_for_transaction(
        self,
        transaction_id: str,
    ) -> List[FakeEffectIntent]:
        tx_id = str(transaction_id or "").strip()
        with self._lock:
            return [
                item
                for item in self._effects.values()
                if item.transaction_id == tx_id
            ]

    def reset(self) -> None:
        with self._lock:
            self._effects.clear()
            self._by_idempotency.clear()

    def _require(self, effect_id: str) -> FakeEffectIntent:
        intent = self._effects.get(str(effect_id or "").strip())
        if intent is None:
            raise KeyError(f"unknown fake effect: {effect_id}")
        return intent

