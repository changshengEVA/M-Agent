"""Unified perception entry point."""
from __future__ import annotations

import logging
import uuid
from typing import Callable, Optional

from m_agent.api.chat_api_shared import _now_iso
from m_agent.runtime.think_life.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    StimulusKind,
)
from m_agent.layers.perception.contracts import Stimulus
from m_agent.runtime.think_life.perception.attributor import TransactionAttributor
from m_agent.runtime.think_life.perception.inbox import StimulusInbox
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
        self._maybe_scene_on_ingress(stimulus)
        self.inbox.push(stimulus, priority=priority)
        if self._on_enqueued is not None:
            try:
                self._on_enqueued(stimulus, schedule_drainer=schedule_drainer)
            except TypeError:
                self._on_enqueued(stimulus)
            except Exception:
                logger.exception("on_enqueued hook failed")
        return stimulus.stimulus_id

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
                    "delegate_id": delegate_id,
                    "tool_history": tool_history,
                    "summary": str(summary or "").strip(),
                },
            ),
            occurred_at=_now_iso(),
            transaction_id=transaction_id,
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
            schedule_id=schedule_id,
        )
        return self.submit(stimulus)

    def _maybe_scene_on_ingress(self, stimulus: StimulusEnvelope) -> None:
        if stimulus.kind == StimulusKind.USER_MESSAGE:
            text = str(stimulus.text or "").strip()
            if text:
                self.scene_writer.append(
                    stimulus.conversation_id,
                    SceneEntry(
                        seq=0,
                        occurred_at=stimulus.occurred_at,
                        entry_type=SceneEntryType.UTTERANCE,
                        actor=SceneActor.USER,
                        text=text,
                        transaction_id=stimulus.transaction_id,
                    ),
                )
