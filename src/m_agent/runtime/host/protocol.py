"""Engine-neutral Runtime Host contract."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, runtime_checkable

from m_agent.sdk.stimulus.contracts import IngestResult, Observation

ThreadEventEmitter = Callable[[str, str, Dict[str, Any]], None]


@runtime_checkable
class RuntimeHost(Protocol):
    """Complete product-facing contract of the LangGraph runtime host."""

    @property
    def runtime_engine_id(self) -> str:
        """Stable engine identifier persisted on transactions."""

    def ingest(
        self,
        observation: Observation,
        *,
        schedule_drainer: bool = True,
    ) -> IngestResult:
        """Admit one Observation into the durable Stimulus Pool."""

    def list_stimulus_trace(
        self,
        *,
        stimulus_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        ingress_key: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Return append-only Stimulus Trace events for audit / Lab replay."""

    def submit_user_message(
        self,
        *,
        thread_id: str,
        conversation_id: Optional[str] = None,
        text: str,
        payload: Optional[dict] = None,
        schedule_drainer: bool = True,
        message_id: Optional[str] = None,
        subject: Optional[str] = None,
        occurred_at: Optional[str] = None,
    ) -> str:
        """Compatibility facade: ChatSourceAdapter → ``ingest`` (prefer Adapter)."""

    def run_thread(
        self,
        thread_id: str,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Drain the conversation inbox for one thread (blocking)."""

    def pending_count(self, thread_id: Optional[str] = None) -> int:
        """Count queued stimuli globally or for one thread."""

    def active_user_transaction(self, conversation_id: str) -> Any:
        """Return the active user transaction, if one exists."""

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

    def list_scene(
        self,
        thread_id: str,
        *,
        conversation_id: Optional[str] = None,
        limit: int = 40,
        before_seq: Optional[int] = None,
        since_flush: bool = True,
    ) -> Dict[str, Any]:
        """Return the product Scene projection."""

    def enqueue_schedule(self, **kwargs: Any) -> Dict[str, Any]:
        """Durably enqueue one schedule stimulus."""

    def prepare_flush_segment(
        self,
        thread_id: str,
        *,
        conversation_id: str,
        source: str = "chat_api_thread_flush",
    ) -> Dict[str, Any]:
        """Persist an immutable runtime/materialization snapshot."""

    def stage_flush_materialization(
        self,
        flush_id: str,
        *,
        destination: str,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Persist an external materialization payload before commit."""

    def on_flush_segment(self, thread_id: str, **kwargs: Any) -> Dict[str, Any]:
        """Commit a staged runtime flush boundary idempotently."""

    def mark_flush_materialization_delivered(
        self,
        flush_id: str,
        *,
        destination: str,
        result: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Record successful materialization delivery idempotently."""

    def complete_flush_segment(self, flush_id: str) -> Dict[str, Any]:
        """Close a fully committed and materialized flush."""

    def has_pending_flush(self, thread_id: str) -> bool:
        """Return whether an unfinished flush currently fences the thread."""

    def load_conversation_seq(self, thread_id: str) -> int:
        """Load the persisted conversation segment sequence."""

    def persist_conversation_seq(self, thread_id: str, sequence: int) -> None:
        """Persist the conversation segment sequence."""

    def health(self) -> Dict[str, Any]:
        """Return a small health snapshot for observability endpoints."""

    def shutdown(self) -> None:
        """Release durable resources such as SQLite handles."""

    def set_thread_event_emitter(
        self,
        emitter: Optional[ThreadEventEmitter],
    ) -> None:
        """Optional hook for UI/runtime event streaming."""


__all__ = ["IngestResult", "Observation", "RuntimeHost", "ThreadEventEmitter"]
