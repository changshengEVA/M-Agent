"""ThinkLife engine adapter implementing the RuntimeHost protocol."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from m_agent.runtime.routing import DEFAULT_RUNTIME_ENGINE
from m_agent.runtime.think_life.runtime import ThinkLifeRuntime

from .protocol import RuntimeHost, ThreadEventEmitter


class ThinkLifeRuntimeHost:
    """Thin adapter over the existing production ThinkLife runtime."""

    def __init__(self, runtime: ThinkLifeRuntime) -> None:
        self._runtime = runtime

    @property
    def runtime(self) -> ThinkLifeRuntime:
        return self._runtime

    @property
    def runtime_engine_id(self) -> str:
        return DEFAULT_RUNTIME_ENGINE

    def submit_user_message(
        self,
        *,
        thread_id: str,
        conversation_id: Optional[str] = None,
        text: str,
        payload: Optional[dict] = None,
        schedule_drainer: bool = True,
    ) -> str:
        return self._runtime.submit_user_message(
            thread_id=thread_id,
            conversation_id=conversation_id,
            text=text,
            payload=payload,
            schedule_drainer=schedule_drainer,
        )

    def run_thread(
        self,
        thread_id: str,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        return self._runtime.run_thread(
            thread_id,
            history_messages=history_messages,
            event_emitter=event_emitter,
        )

    def list_transactions(
        self,
        thread_id: str,
        *,
        conversation_id: Optional[str] = None,
        include_history: bool = False,
    ) -> Dict[str, Any]:
        return self._runtime.list_transactions(
            thread_id,
            conversation_id=conversation_id,
            include_history=include_history,
        )

    def delete_transaction(
        self,
        *,
        thread_id: str,
        conversation_id: str,
        transaction_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        return self._runtime.delete_transaction(
            thread_id=thread_id,
            conversation_id=conversation_id,
            transaction_id=transaction_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def health(self) -> Dict[str, Any]:
        payload = dict(self._runtime.health())
        payload.setdefault("runtime_engine_id", self.runtime_engine_id)
        payload["runtime_engine_id"] = self.runtime_engine_id
        return payload

    def shutdown(self) -> None:
        close_store = getattr(self._runtime.runtime_store, "close", None)
        if callable(close_store):
            close_store()

    def set_thread_event_emitter(
        self,
        emitter: Optional[ThreadEventEmitter],
    ) -> None:
        self._runtime.set_thread_event_emitter(emitter)


def as_runtime_host(runtime: ThinkLifeRuntime) -> RuntimeHost:
    return ThinkLifeRuntimeHost(runtime)


__all__ = ["ThinkLifeRuntimeHost", "as_runtime_host"]
