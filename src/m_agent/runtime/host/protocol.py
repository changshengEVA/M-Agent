"""Engine-neutral Runtime Host contract."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

ThreadEventEmitter = Callable[[str, str, Dict[str, Any]], None]


@runtime_checkable
class RuntimeHost(Protocol):
    """Product-facing runtime facade shared by ThinkLife and LangGraph engines.

    Chat API and future orchestrators should depend on this protocol rather than
    concrete engine classes. MVP surfaces the minimum thread lifecycle needed
    for internal gray rollout; flush/schedule helpers remain engine-specific
    extensions until the host contract stabilizes.
    """

    @property
    def runtime_engine_id(self) -> str:
        """Stable engine identifier persisted on transactions."""

    def submit_user_message(
        self,
        *,
        thread_id: str,
        conversation_id: Optional[str] = None,
        text: str,
        payload: Optional[dict] = None,
        schedule_drainer: bool = True,
    ) -> str:
        """Enqueue one user stimulus and optionally schedule background drain."""

    def run_thread(
        self,
        thread_id: str,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Drain the conversation inbox for one thread (blocking)."""

    def list_transactions(
        self,
        thread_id: str,
        *,
        conversation_id: Optional[str] = None,
        include_history: bool = False,
    ) -> Dict[str, Any]:
        """Project transactions in the requested product scope."""

    def delete_transaction(
        self,
        *,
        thread_id: str,
        conversation_id: str,
        transaction_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """Tombstone one transaction and cancel its pending runtime work."""

    def health(self) -> Dict[str, Any]:
        """Return a small health snapshot for observability endpoints."""

    def shutdown(self) -> None:
        """Release durable resources such as SQLite handles."""

    def set_thread_event_emitter(
        self,
        emitter: Optional[ThreadEventEmitter],
    ) -> None:
        """Optional hook for UI/runtime event streaming."""


__all__ = ["RuntimeHost", "ThreadEventEmitter"]
