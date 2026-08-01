"""Ports the LangGraph turn graph uses for thinking, tools and effects.

The graph itself owns ordering and durability; everything that reaches outside
the Transaction Store goes through one of these ports so the same graph can run
against a real ``ThinkingAgent``/``ExecutionAgent`` pair or against the offline
fake capability used by the R2 internal smoke.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from m_agent.layers.thinking.contracts import ThinkingDecision, is_reply_mode
from m_agent.runtime.langgraph.config import DEFAULT_DELIVERY_GUARANTEE
from m_agent.runtime.langgraph.fake_effects import FakeEffectExecutor
from m_agent.runtime.think_life.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    StimulusKind,
    TransactionRecord,
)
from m_agent.runtime.think_life.scheduler.delegate import (
    DelegateTarget,
    resolve_delegate_tool_input,
)
from m_agent.runtime.think_life.scheduler.execution_feedback import (
    augment_perception_with_nudge,
    build_completion_nudge_message,
    build_param_gap_tool_history,
    feedback_summary_from_tool_history,
    param_gap_summary,
    premature_reply_block_reason,
)
from m_agent.runtime.think_life.scheduler.think_context import (
    latest_user_utterance_from_scene,
)
from m_agent.runtime.think_life.scheduler.tool_runner import REPLY_TOOL_NAME
from m_agent.runtime.think_life.transaction.effects import EffectCoordinator
from m_agent.systems.scene.protocols import SceneWriter

logger = logging.getLogger(__name__)

MAX_COMPLETION_GATE_NUDGES = 2
TransactionDeletedPredicate = Callable[[str], bool]


class _TransactionFencedSceneWriter:
    def __init__(
        self,
        inner: SceneWriter,
        transaction_id: str,
        is_deleted: Optional[TransactionDeletedPredicate],
    ) -> None:
        self._inner = inner
        self._transaction_id = str(transaction_id or "").strip()
        self._is_deleted = is_deleted

    def append(
        self,
        conversation_id: str,
        entry: SceneEntry,
        *,
        append_id: Optional[str] = None,
    ) -> SceneEntry:
        if (
            self._is_deleted is not None
            and self._is_deleted(self._transaction_id)
        ):
            return entry
        if append_id is None:
            return self._inner.append(conversation_id, entry)
        return self._inner.append(
            conversation_id,
            entry,
            append_id=append_id,
        )


class TurnPlanner(Protocol):
    """Produce the next :class:`ThinkingDecision` for one transaction turn."""

    def plan(
        self,
        *,
        record: TransactionRecord,
        stimulus: StimulusEnvelope,
        perception: Any,
        scene_tail: List[SceneEntry],
    ) -> ThinkingDecision:
        ...


@dataclass
class ThinkingAgentPlanner:
    """Drive the production thinking layer, including the completion gate.

    The gate is the same one the ThinkLife loop applies: an ``answer_directly``
    right after execution feedback is re-planned with a nudge when the user
    request still looks unfinished, so both engines refuse to close a
    multi-step request on a single tool step.
    """

    thinking_agent: Any
    max_gate_nudges: int = MAX_COMPLETION_GATE_NUDGES
    event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None

    def plan(
        self,
        *,
        record: TransactionRecord,
        stimulus: StimulusEnvelope,
        perception: Any,
        scene_tail: List[SceneEntry],
    ) -> ThinkingDecision:
        perception_plan = perception
        decision: Optional[ThinkingDecision] = None
        for nudge_index in range(self.max_gate_nudges + 1):
            decision = self.thinking_agent.handle(
                perception_plan,
                transaction_state=record,
                event_emitter=self.event_emitter,
            )
            if not is_reply_mode(decision.mode):
                break
            if stimulus.kind != StimulusKind.EXECUTION_FEEDBACK:
                break
            block = premature_reply_block_reason(
                pending_user_request=latest_user_utterance_from_scene(scene_tail),
                stimulus=stimulus,
            )
            if not block:
                break
            if nudge_index >= self.max_gate_nudges:
                logger.warning(
                    "completion gate exhausted txn=%s block=%s; allowing reply",
                    record.transaction_id,
                    block,
                )
                break
            perception_plan = augment_perception_with_nudge(
                perception,
                build_completion_nudge_message(block),
            )
        assert decision is not None
        return decision


@dataclass
class DelegateOutcome:
    """Result of running one delegate; feeds WM, Scene and Feedback."""

    tool_history: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    replies: List[str] = field(default_factory=list)
    reply_finalized: bool = False
    needs_clarification: bool = False
    visible_effects: int = 0
    attempts: int = 0


class DelegateExecutor(Protocol):
    """Run exactly one capability for one delegate."""

    @property
    def enabled_tools(self) -> Sequence[str]:
        ...

    def run(
        self,
        *,
        record: TransactionRecord,
        target: DelegateTarget,
        delegate_id: str,
        effect_id: str,
        pending_user_request: str = "",
    ) -> DelegateOutcome:
        ...


ReplyCallback = Callable[[str, str, str, bool], None]


@dataclass
class FakeToolDelegateExecutor:
    """Deterministic offline capability runner for the R2 internal loop.

    Side-effect free, but it still goes through :class:`FakeEffectExecutor` so
    repeated dispatches under one idempotency key collapse to a single visible
    effect, which is what the capability delivery guarantee asserts.
    """

    scene_writer: SceneWriter
    capabilities: Tuple[str, ...] = ("fake_capability",)
    effects: FakeEffectExecutor = field(default_factory=FakeEffectExecutor)
    on_reply: Optional[ReplyCallback] = None
    transaction_is_deleted: Optional[
        TransactionDeletedPredicate
    ] = None

    def __post_init__(self) -> None:
        # The graph relays Feedback itself, after the delegate result is durable.
        self.effects.set_auto_complete(False)

    @property
    def enabled_tools(self) -> Sequence[str]:
        return (REPLY_TOOL_NAME, *self.capabilities)

    def run(
        self,
        *,
        record: TransactionRecord,
        target: DelegateTarget,
        delegate_id: str,
        effect_id: str,
        pending_user_request: str = "",
    ) -> DelegateOutcome:
        del pending_user_request
        intent = self.effects.dispatch(
            effect_id=effect_id,
            capability=target.tool_name,
            delegate_id=delegate_id,
            transaction_id=record.transaction_id,
            activation_id=str(record.current_activation_id or ""),
            conversation_id=record.conversation_id,
            thread_id=record.thread_id,
            idempotency_key=f"{target.tool_name}:{delegate_id}",
        )
        completed = self.effects.complete(intent.effect_id)
        if target.for_user_reply:
            return self._reply_outcome(record, target, delegate_id, completed)
        # Delegate-id free so the same input yields byte-identical evidence
        # across engines and across runs.
        answer = f"{target.tool_name} completed"
        tool_history = [
            {
                "tool_name": target.tool_name,
                "params": (
                    {"instruction": target.instruction}
                    if target.instruction
                    else {}
                ),
                "result": {
                    "success": True,
                    "action": "fake_execute",
                    "tool_invoked": True,
                    "count": completed.visible_effects,
                    "answer": answer,
                    "message": answer,
                },
            }
        ]
        return DelegateOutcome(
            tool_history=tool_history,
            summary=feedback_summary_from_tool_history(tool_history),
            visible_effects=completed.visible_effects,
            attempts=completed.attempts,
        )

    def _reply_outcome(
        self,
        record: TransactionRecord,
        target: DelegateTarget,
        delegate_id: str,
        completed: Any,
    ) -> DelegateOutcome:
        message = str(
            target.user_reply_text or target.instruction or ""
        ).strip() or "Acknowledge the user briefly."
        deleted = bool(
            self.transaction_is_deleted is not None
            and self.transaction_is_deleted(record.transaction_id)
        )
        if self.on_reply is not None and not deleted:
            self.on_reply(
                record.thread_id,
                record.transaction_id,
                message,
                True,
            )
        if not deleted:
            self.scene_writer.append(
                record.conversation_id,
                SceneEntry(
                    seq=0,
                    occurred_at="",
                    entry_type=SceneEntryType.REPLY,
                    actor=SceneActor.ASSISTANT,
                    text=message,
                    transaction_id=record.transaction_id,
                    delegate_id=delegate_id,
                    tool_name=REPLY_TOOL_NAME,
                ),
            )
        tool_history = [
            {
                "tool_name": REPLY_TOOL_NAME,
                "params": {"message": message, "finalize": True},
                "result": {
                    "success": True,
                    "action": "reply",
                    "tool_invoked": True,
                    "finalize": True,
                    "answer": message,
                    "message": message,
                },
            }
        ]
        return DelegateOutcome(
            tool_history=tool_history,
            summary=feedback_summary_from_tool_history(tool_history),
            replies=[message],
            reply_finalized=True,
            visible_effects=completed.visible_effects,
            attempts=completed.attempts,
        )


@dataclass
class ExecutionAgentDelegateExecutor:
    """Run one real capability through the production execution layer."""

    execution_agent: Any
    scene_writer: SceneWriter
    on_reply: Optional[ReplyCallback] = None
    transaction_is_deleted: Optional[
        TransactionDeletedPredicate
    ] = None

    @property
    def enabled_tools(self) -> Sequence[str]:
        return list(self.execution_agent.enabled_capability_names)

    def run(
        self,
        *,
        record: TransactionRecord,
        target: DelegateTarget,
        delegate_id: str,
        effect_id: str,
        pending_user_request: str = "",
    ) -> DelegateOutcome:
        del effect_id
        if (
            self.transaction_is_deleted is not None
            and self.transaction_is_deleted(record.transaction_id)
        ):
            return DelegateOutcome(
                tool_history=[],
                summary="transaction deleted before delegate dispatch",
            )
        fill_result = resolve_delegate_tool_input(
            self.execution_agent,
            target,
            thread_id=record.thread_id,
            correlation_id=delegate_id,
            pending_user_request=pending_user_request,
        )
        if (
            self.transaction_is_deleted is not None
            and self.transaction_is_deleted(record.transaction_id)
        ):
            return DelegateOutcome(
                tool_history=[],
                summary="transaction deleted before delegate dispatch",
            )
        if fill_result.needs_clarification:
            tool_history = build_param_gap_tool_history(
                fill_result,
                instruction=target.instruction,
            )
            return DelegateOutcome(
                tool_history=tool_history,
                summary=param_gap_summary(fill_result),
                needs_clarification=True,
            )
        replies: List[str] = []
        finalized = {"value": False}

        def on_reply(message: str, *, finalize: bool) -> None:
            if (
                self.transaction_is_deleted is not None
                and self.transaction_is_deleted(record.transaction_id)
            ):
                return
            replies.append(str(message or "").strip())
            if finalize:
                finalized["value"] = True
            if self.on_reply is not None:
                self.on_reply(
                    record.thread_id,
                    record.transaction_id,
                    message,
                    finalize,
                )

        exec_result = self.execution_agent.invoke_tool_direct(
            tool_name=target.tool_name,
            tool_input=dict(fill_result.args or {}),
            thread_id=record.thread_id,
            correlation_id=delegate_id,
            think_life_hooks={
                "delegate_id": delegate_id,
                "transaction_id": record.transaction_id,
                "conversation_id": record.conversation_id,
                "on_reply": on_reply,
                "scene_writer": _TransactionFencedSceneWriter(
                    self.scene_writer,
                    record.transaction_id,
                    self.transaction_is_deleted,
                ),
            },
        )
        tool_history = list(exec_result.tool_history or [])
        return DelegateOutcome(
            tool_history=tool_history,
            summary=feedback_summary_from_tool_history(tool_history)
            or str(exec_result.summary or ""),
            replies=replies,
            reply_finalized=bool(finalized["value"]),
            visible_effects=1 if exec_result.success else 0,
            attempts=1,
        )


@dataclass
class DelegateEffectLedger:
    """P7 effect ledger and Feedback outbox around one graph delegate.

    ``open_intent`` -> ``commit_result`` -> ``mark_relayed`` is the same
    ordering acceptance TX-07 asserts: the effect result and the Feedback
    outbox row commit atomically, and the relay is replay-safe.
    """

    coordinator: Optional[EffectCoordinator] = None
    delivery_guarantee: str = DEFAULT_DELIVERY_GUARANTEE

    @staticmethod
    def effect_id_for(delegate_id: str) -> str:
        return f"{str(delegate_id or '').strip()}-effect"

    @property
    def enabled(self) -> bool:
        return self.coordinator is not None

    def open_intent(
        self,
        *,
        record: TransactionRecord,
        delegate_id: str,
        capability: str,
    ) -> Dict[str, Any]:
        effect_id = self.effect_id_for(delegate_id)
        if self.coordinator is None:
            return {"effect_id": effect_id, "recorded": False}
        result = self.coordinator.record_intent_for_delegate(
            transaction_id=record.transaction_id,
            activation_id=str(record.current_activation_id or ""),
            delegate_id=delegate_id,
            effect_id=effect_id,
            capability=capability,
            delivery_guarantee=self.delivery_guarantee,
        )
        return {
            "effect_id": effect_id,
            "recorded": True,
            "replayed": bool(result.get("replayed")),
        }

    def commit_result(self, *, effect_id: str) -> Dict[str, Any]:
        if self.coordinator is None:
            return {"effect_id": effect_id, "recorded": False}
        result = self.coordinator.commit_result_with_outbox(effect_id=effect_id)
        effect = dict(result.get("effect") or {})
        outbox = dict(result.get("feedback_outbox") or {})
        return {
            "effect_id": effect_id,
            "recorded": True,
            "status": effect.get("status"),
            "visible_effects": int(effect.get("visible_effects", 0) or 0),
            "attempts": int(effect.get("attempts", 0) or 0),
            "outbox_status": outbox.get("status"),
        }

    def mark_relayed(self, *, effect_id: str) -> Dict[str, Any]:
        if self.coordinator is None:
            return {"effect_id": effect_id, "recorded": False}
        result = self.coordinator.relay_feedback(effect_id=effect_id)
        return {
            "effect_id": effect_id,
            "recorded": True,
            "ingress_key": result.get("ingress_key"),
            "outbox_status": result.get("outbox_status"),
            "replayed": bool(result.get("replayed")),
        }

    def snapshot(self, *, transaction_id: str) -> Dict[str, Any]:
        """Effect + outbox rows for one transaction (guarantee evidence)."""

        if self.coordinator is None:
            return {"effects": [], "feedback_outbox": []}
        return self.coordinator.load_for_transaction(transaction_id)


__all__ = [
    "DelegateEffectLedger",
    "DelegateExecutor",
    "DelegateOutcome",
    "ExecutionAgentDelegateExecutor",
    "FakeToolDelegateExecutor",
    "MAX_COMPLETION_GATE_NUDGES",
    "ThinkingAgentPlanner",
    "TurnPlanner",
]
