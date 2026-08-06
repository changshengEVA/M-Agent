"""Unified perception entry point."""
from __future__ import annotations

import logging
import uuid
from typing import Callable, Optional

from m_agent.api.chat_api_shared import _now_iso
from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    StimulusKind,
)
from m_agent.layers.perception.contracts import Stimulus
from m_agent.runtime.perception.attributor import TransactionAttributor
from m_agent.runtime.perception.inbox import (
    StimulusInbox,
    _copy_runtime_fields,
)
from m_agent.systems.scene.protocols import SceneWriter

logger = logging.getLogger(__name__)

StimulusHook = Callable[..., None]


class PerceptionGateway:
    def __init__(
        self,
        *,
        inbox: StimulusInbox,
        attributor: TransactionAttributor,
        scene_writer: SceneWriter,
        on_enqueued: Optional[StimulusHook] = None,
    ) -> None:
        self.inbox = inbox
        self.attributor = attributor
        self.scene_writer = scene_writer
        self._on_enqueued = on_enqueued

    def submit(self, stimulus: StimulusEnvelope, *, schedule_drainer: bool = True) -> str:
        priority = self.attributor.priority_for(stimulus)
        if priority < 0 or priority > 100:
            raise ValueError("stimulus priority must be within 0..100")
        registry = self.attributor.registry
        targeted_schedule = bool(
            stimulus.kind == StimulusKind.SCHEDULED_PLAN
            and str(stimulus.transaction_id or "").strip()
        )
        needs_transaction_fence = bool(
            stimulus.kind == StimulusKind.EXECUTION_FEEDBACK
            or targeted_schedule
        )

        if needs_transaction_fence:
            # Delete uses this same registry lock.  If admission wins, the
            # durable stimulus exists before delete's cleanup scan and is
            # aborted there.  If delete wins, validation observes the
            # tombstone and records discarded/rejected instead of leaving new
            # ready work targeting a deleted transaction.
            with registry._lock:
                if (
                    stimulus.kind == StimulusKind.EXECUTION_FEEDBACK
                    and not self._feedback_source_is_admissible(stimulus)
                ):
                    return self._discard_at_admission(
                        stimulus,
                        priority=priority,
                        reason="invalid_feedback_source",
                        reason_code="invalid_feedback_source",
                    )
                if (
                    targeted_schedule
                    and not self._target_transaction_is_admissible(stimulus)
                ):
                    return self._discard_at_admission(
                        stimulus,
                        priority=priority,
                        reason="invalid_schedule_source",
                        reason_code="invalid_schedule_source",
                    )
                stored = self.inbox.push(stimulus, priority=priority)
        else:
            self._maybe_scene_on_ingress(stimulus)
            stored = self.inbox.push(stimulus, priority=priority)
        _copy_runtime_fields(stimulus, stored)
        if self._on_enqueued is not None:
            try:
                self._on_enqueued(stimulus, schedule_drainer=schedule_drainer)
            except TypeError:
                self._on_enqueued(stimulus)
            except Exception:
                logger.exception("on_enqueued hook failed")
        return stored.stimulus_id

    def _discard_at_admission(
        self,
        stimulus: StimulusEnvelope,
        *,
        priority: int,
        reason: str,
        reason_code: str = "invalid_observation",
    ) -> str:
        store = getattr(self.inbox, "store", None)
        if store is not None:
            stored = store.admit_stimulus(
                stimulus,
                effective_priority=int(priority),
                pool_state="terminated",
                disposition="rejected",
                disposition_stage="admission",
                disposition_reason=reason,
                reason_code=reason_code,
            )
            _copy_runtime_fields(stimulus, stored)
            return stored.stimulus_id
        object.__setattr__(stimulus, "pool_state", "terminated")
        object.__setattr__(stimulus, "disposition", "rejected")
        object.__setattr__(stimulus, "disposition_stage", "admission")
        object.__setattr__(stimulus, "disposition_reason", reason)
        object.__setattr__(stimulus, "reason_code", reason_code)
        object.__setattr__(stimulus, "terminal", True)
        return stimulus.stimulus_id

    def _feedback_source_is_admissible(
        self,
        stimulus: StimulusEnvelope,
    ) -> bool:
        transaction_id = str(stimulus.transaction_id or "").strip()
        delegate_id = str(
            stimulus.delegate_id
            or stimulus.payload.get("delegate_id", "")
            or ""
        ).strip()
        activation_id = str(
            stimulus.activation_id
            or stimulus.payload.get("activation_id", "")
            or ""
        ).strip()
        if not transaction_id or not delegate_id or not activation_id:
            return False
        registry = self.attributor.registry
        record = registry.get(transaction_id)
        if record is None:
            return False
        if record.conversation_id != stimulus.conversation_id:
            return False
        return registry.validate_feedback_source(
            transaction_id,
            activation_id,
            delegate_id,
        )

    def _target_transaction_is_admissible(
        self,
        stimulus: StimulusEnvelope,
    ) -> bool:
        transaction_id = str(stimulus.transaction_id or "").strip()
        if not transaction_id:
            return False
        record = self.attributor.registry.get(transaction_id)
        return bool(
            record is not None
            and record.thread_id == stimulus.thread_id
            and record.conversation_id == stimulus.conversation_id
            and not record.deleted
            and record.deleted_at is None
        )

    def submit_user_message(
        self,
        *,
        thread_id: str,
        conversation_id: str,
        text: str,
        payload: Optional[dict] = None,
        schedule_drainer: bool = True,
    ) -> str:
        stimulus = StimulusEnvelope(
            stimulus_id=f"stim_{uuid.uuid4().hex}",
            thread_id=thread_id,
            conversation_id=conversation_id,
            stimulus=Stimulus(
                kind=StimulusKind.USER_MESSAGE,
                text=str(text or "").strip(),
                payload=dict(payload or {}),
            ),
            occurred_at=_now_iso(),
        )
        return self.submit(stimulus, schedule_drainer=schedule_drainer)

    def submit_execution_feedback(
        self,
        *,
        thread_id: str,
        conversation_id: str,
        transaction_id: str,
        delegate_id: str,
        activation_id: str = "",
        tool_history: list,
        summary: str = "",
        schedule_drainer: bool = False,
    ) -> str:
        readable = str(summary or "").strip() or "Execution finished and returned tool evidence."
        stimulus = StimulusEnvelope(
            stimulus_id=f"stim_{uuid.uuid4().hex}",
            thread_id=thread_id,
            conversation_id=conversation_id,
            stimulus=Stimulus(
                kind=StimulusKind.EXECUTION_FEEDBACK,
                text=readable,
                payload={
                    "activation_id": str(activation_id or "").strip(),
                    "delegate_id": delegate_id,
                    "tool_history": tool_history,
                    "summary": str(summary or "").strip(),
                },
            ),
            occurred_at=_now_iso(),
            transaction_id=transaction_id,
            activation_id=str(activation_id or "").strip() or None,
            delegate_id=delegate_id,
        )
        return self.submit(stimulus, schedule_drainer=schedule_drainer)

    def submit_heartbeat(
        self,
        *,
        thread_id: str,
        conversation_id: str,
        schedule_id: str,
        text: str,
        payload: Optional[dict] = None,
    ) -> str:
        body = dict(payload or {})
        body.setdefault("schedule_id", schedule_id)
        schedule_run_id = str(
            body.get("schedule_run_id", body.get("run_id", "")) or ""
        ).strip()
        schedule_delivery_id = str(
            body.get("schedule_delivery_id", "") or ""
        ).strip()
        source_transaction_id = str(
            body.get("transaction_id", "") or ""
        ).strip()
        schedule_ingress_identity = (
            schedule_delivery_id or schedule_run_id
        )
        stimulus = StimulusEnvelope(
            stimulus_id=f"stim_{uuid.uuid4().hex}",
            thread_id=thread_id,
            conversation_id=conversation_id,
            stimulus=Stimulus(
                kind=StimulusKind.SCHEDULED_PLAN,
                text=str(text or "").strip(),
                payload=body,
            ),
            occurred_at=_now_iso(),
            transaction_id=source_transaction_id or None,
            schedule_id=schedule_id,
            schedule_run_id=schedule_run_id or None,
            schedule_delivery_id=schedule_delivery_id or None,
            ingress_key=(
                f"schedule_delivery:{schedule_ingress_identity}"
                if schedule_ingress_identity
                else None
            ),
        )
        return self.submit(stimulus)

    def _maybe_scene_on_ingress(self, stimulus: StimulusEnvelope) -> None:
        if stimulus.kind != StimulusKind.USER_MESSAGE:
            return
        text = str(stimulus.text or "").strip()
        if not text:
            return
        # Sourceless user utterances are bound to a transaction only after AT
        # attribution. Writing here with a null transaction_id would permanently
        # orphan the Scene entry because append_id replay is idempotent.
        if not str(stimulus.transaction_id or "").strip():
            return
        self.scene_writer.append(
            stimulus.conversation_id,
            SceneEntry(
                seq=0,
                occurred_at=stimulus.occurred_at,
                entry_type=SceneEntryType.UTTERANCE,
                actor=SceneActor.USER,
                text=text,
                append_id=str(stimulus.stimulus_id or "").strip() or None,
                transaction_id=stimulus.transaction_id,
            ),
        )
