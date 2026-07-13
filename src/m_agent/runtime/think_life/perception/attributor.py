"""Map stimuli to transaction lines."""
from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

from m_agent.runtime.think_life.config import ThinkLifeConfig
from m_agent.runtime.think_life.contracts import (
    StimulusEnvelope,
    StimulusKind,
    TransactionKind,
    TransactionRecord,
    TransactionStatus,
)
from m_agent.runtime.think_life.transaction_registry import TransactionRegistry


SemanticResolver = Callable[[StimulusEnvelope, List[TransactionRecord]], Optional[str]]


class TransactionAttributor:
    def __init__(
        self,
        *,
        registry: TransactionRegistry,
        config: ThinkLifeConfig,
        semantic_resolver: Optional[SemanticResolver] = None,
    ) -> None:
        self.registry = registry
        self.config = config
        self.semantic_resolver = semantic_resolver

    def resolve(
        self,
        stimulus: StimulusEnvelope,
        *,
        dialogue_history: Optional[List[dict]] = None,
        scene_context: str = "",
    ) -> Tuple[TransactionRecord, bool]:
        """Return (transaction, created_new)."""
        if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
            return self._resolve_feedback(stimulus)
        candidates = [
            record
            for record in self.registry.list_for_conversation(stimulus.conversation_id)
            if not record.status.is_terminal() and record.status != TransactionStatus.SUSPENDED
        ]
        if candidates and self.semantic_resolver is not None:
            try:
                selected = self.semantic_resolver(
                    stimulus,
                    candidates,
                    dialogue_history=dialogue_history,
                    scene_context=scene_context,
                )
            except TypeError:
                selected = self.semantic_resolver(stimulus, candidates)
            selected_id = str(selected or "").strip()
            if selected_id:
                selected = self.registry.get(selected_id)
                if selected in candidates:
                    return selected, False
            if stimulus.kind != StimulusKind.SCHEDULED_PLAN:
                return self._resolve_user(stimulus, reuse_active=False)
        if stimulus.kind == StimulusKind.SCHEDULED_PLAN:
            return self._resolve_heartbeat(stimulus)
        return self._resolve_user(stimulus)

    def _resolve_feedback(self, stimulus: StimulusEnvelope) -> Tuple[TransactionRecord, bool]:
        delegate_id = str(stimulus.delegate_id or stimulus.payload.get("delegate_id", "") or "").strip()
        if not delegate_id:
            raise ValueError("execution_feedback requires delegate_id")
        transaction_id = str(stimulus.transaction_id or "").strip()
        if not transaction_id:
            raise ValueError("execution_feedback requires transaction_id")
        record = self.registry.get(transaction_id)
        if record is None:
            raise ValueError(f"unknown feedback transaction_id={transaction_id}")
        if record.active_delegate_id != delegate_id:
            raise ValueError(
                f"delegate_id={delegate_id} does not match active delegate for {transaction_id}"
            )
        return record, False

    def _resolve_heartbeat(self, stimulus: StimulusEnvelope) -> Tuple[TransactionRecord, bool]:
        schedule_id = str(stimulus.schedule_id or stimulus.payload.get("schedule_id", "") or "").strip()
        priority = stimulus.priority_override or self.config.scheduler.default_heartbeat_priority
        from m_agent.runtime.think_life.contracts import TransactionCorrelation

        payload = stimulus.payload if isinstance(stimulus.payload, dict) else {}
        correlation = TransactionCorrelation(
            schedule_id=schedule_id or None,
            schedule_owner_id=str(payload.get("owner_id", "") or "").strip() or None,
            schedule_run_id=str(payload.get("run_id", "") or "").strip() or None,
        )
        record = self.registry.create(
            thread_id=stimulus.thread_id,
            conversation_id=stimulus.conversation_id,
            kind=TransactionKind.SCHEDULE,
            priority=int(priority),
            correlation=correlation,
        )
        return record, True

    def _resolve_user(
        self,
        stimulus: StimulusEnvelope,
        *,
        reuse_active: bool = True,
    ) -> Tuple[TransactionRecord, bool]:
        priority = stimulus.priority_override or self.config.scheduler.default_user_priority
        active = (
            self.registry.get_active_user_transaction(stimulus.conversation_id)
            if reuse_active
            else None
        )
        if active is not None and active.status in {
            TransactionStatus.RUNNING,
            TransactionStatus.WAITING_EXECUTION,
            TransactionStatus.PENDING,
        }:
            return active, False
        record = self.registry.create(
            thread_id=stimulus.thread_id,
            conversation_id=stimulus.conversation_id,
            kind=TransactionKind.USER_TASK,
            priority=int(priority),
        )
        self.registry.set_active_user_transaction(stimulus.conversation_id, record.transaction_id)
        return record, True

    def priority_for(self, stimulus: StimulusEnvelope) -> int:
        if stimulus.priority_override is not None:
            return int(stimulus.priority_override)
        if stimulus.kind == StimulusKind.USER_MESSAGE:
            return self.config.scheduler.default_user_priority
        if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
            return self.config.scheduler.default_feedback_priority
        return self.config.scheduler.default_heartbeat_priority
