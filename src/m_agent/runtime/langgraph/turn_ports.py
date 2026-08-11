"""Ports the LangGraph turn graph uses for thinking, tools and effects.

The graph itself owns ordering and durability; everything that reaches outside
the Transaction Store goes through one of these ports so the same graph can run
against a real ``ThinkingAgent``/``ExecutionAgent`` pair or against the offline
fake capability used by the R2 internal smoke.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from m_agent.api.chat_api_shared import _now_iso
from m_agent.layers.thinking.contracts import ThinkingDecision, is_reply_mode
from m_agent.runtime.langgraph.config import DEFAULT_DELIVERY_GUARANTEE
from m_agent.runtime.langgraph.fake_effects import FakeEffectExecutor
from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    StimulusKind,
    TransactionRecord,
)
from m_agent.runtime.turn_support.delegate import (
    DelegateTarget,
    resolve_delegate_tool_input,
)
from m_agent.runtime.turn_support.execution_feedback import (
    augment_perception_with_nudge,
    build_completion_nudge_message,
    build_param_gap_tool_history,
    feedback_summary_from_tool_history,
    param_gap_summary,
    premature_reply_block_reason,
)
from m_agent.runtime.turn_support.think_context import (
    latest_user_utterance_from_scene,
)
from m_agent.runtime.turn_support.tool_runner import REPLY_TOOL_NAME
from m_agent.runtime.transaction.effects import EffectCoordinator
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
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> ThinkingDecision:
        ...


@dataclass
class ThinkingAgentPlanner:
    """Drive the production thinking layer, including the completion gate.

    The gate is the same one the Runtime loop applies: an ``answer_directly``
    right after execution feedback is re-planned with a nudge when the user
    request still looks unfinished, so both engines refuse to close a
    multi-step request on a single tool step.
    """

    thinking_agent: Any
    max_gate_nudges: int = MAX_COMPLETION_GATE_NUDGES
    event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None
    _invocation_emitter: ContextVar[
        Optional[Callable[[str, Dict[str, Any]], None]]
    ] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # A planner instance is shared by all thread drainers.  ContextVar keeps
        # an invocation override local to the current worker/task rather than
        # mutating ``event_emitter`` around a graph run.
        self._invocation_emitter = ContextVar(
            f"langgraph_planner_emitter_{id(self)}",
            default=None,
        )

    def plan(
        self,
        *,
        record: TransactionRecord,
        stimulus: StimulusEnvelope,
        perception: Any,
        scene_tail: List[SceneEntry],
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> ThinkingDecision:
        token = self._invocation_emitter.set(event_emitter)
        try:
            return self._plan(
                record=record,
                stimulus=stimulus,
                perception=perception,
                scene_tail=scene_tail,
            )
        finally:
            self._invocation_emitter.reset(token)

    def _plan(
        self,
        *,
        record: TransactionRecord,
        stimulus: StimulusEnvelope,
        perception: Any,
        scene_tail: List[SceneEntry],
    ) -> ThinkingDecision:
        perception_plan = perception
        decision: Optional[ThinkingDecision] = None
        emitter = self._invocation_emitter.get() or self.event_emitter
        for nudge_index in range(self.max_gate_nudges + 1):
            decision = self.thinking_agent.handle(
                perception_plan,
                transaction_state=record,
                event_emitter=emitter,
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
    success: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_history": list(self.tool_history),
            "summary": str(self.summary or ""),
            "replies": list(self.replies),
            "reply_finalized": bool(self.reply_finalized),
            "needs_clarification": bool(self.needs_clarification),
            "visible_effects": int(self.visible_effects),
            "attempts": int(self.attempts),
            "success": bool(self.success),
        }

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "DelegateOutcome":
        payload = dict(data or {})
        return cls(
            tool_history=list(payload.get("tool_history") or []),
            summary=str(payload.get("summary", "") or ""),
            replies=[str(item) for item in list(payload.get("replies") or [])],
            reply_finalized=bool(payload.get("reply_finalized")),
            needs_clarification=bool(payload.get("needs_clarification")),
            visible_effects=int(payload.get("visible_effects", 0) or 0),
            attempts=int(payload.get("attempts", 0) or 0),
            success=bool(payload.get("success", True)),
        )


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
        effect_context: Optional[Dict[str, Any]] = None,
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
    agent_name: str = "Agent"

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
        effect_context: Optional[Dict[str, Any]] = None,
    ) -> DelegateOutcome:
        del pending_user_request
        context = dict(effect_context or {})
        intent = self.effects.dispatch(
            effect_id=effect_id,
            capability=target.tool_name,
            delegate_id=delegate_id,
            transaction_id=record.transaction_id,
            activation_id=str(record.current_activation_id or ""),
            conversation_id=record.conversation_id,
            thread_id=record.thread_id,
            idempotency_key=str(context.get("idempotency_key") or effect_id),
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
                    occurred_at=_now_iso(),
                    entry_type=SceneEntryType.ACTION,
                    actor=SceneActor.ASSISTANT,
                    actor_name=str(self.agent_name or "Agent").strip() or "Agent",
                    text=message,
                    append_id=f"{delegate_id}:action:1",
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
    on_schedule_created: Optional[Callable[..., Any]] = None
    agent_name: str = "Agent"

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
        effect_context: Optional[Dict[str, Any]] = None,
    ) -> DelegateOutcome:
        context = dict(effect_context or {})
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
            runtime_hooks={
                "delegate_id": delegate_id,
                "effect_id": effect_id,
                "idempotency_key": str(
                    context.get("idempotency_key") or effect_id
                ),
                "delivery_guarantee": str(
                    context.get("delivery_guarantee") or ""
                ),
                "transaction_id": record.transaction_id,
                "conversation_id": record.conversation_id,
                "agent_name": self.agent_name,
                "on_reply": on_reply,
                **(
                    {"on_schedule_created": self.on_schedule_created}
                    if self.on_schedule_created is not None
                    else {}
                ),
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
            success=bool(exec_result.success),
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
    capability_registry: Any = None

    @staticmethod
    def effect_id_for(delegate_id: str) -> str:
        return f"{str(delegate_id or '').strip()}-effect"

    def delivery_guarantee_for(self, capability: str) -> str:
        registry = self.capability_registry
        get_spec = getattr(registry, "get", None)
        if callable(get_spec):
            spec = get_spec(str(capability or "").strip())
            declared = str(
                getattr(spec, "delivery_guarantee", "") or ""
            ).strip().lower()
            if declared in {"idempotent", "at_most_once", "at_least_once"}:
                return declared
        fallback = str(self.delivery_guarantee or "").strip().lower()
        return fallback or DEFAULT_DELIVERY_GUARANTEE

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
        guarantee = self.delivery_guarantee_for(capability)
        if self.coordinator is None:
            return {
                "effect_id": effect_id,
                "idempotency_key": effect_id,
                "delivery_guarantee": guarantee,
                "recorded": False,
            }
        result = self.coordinator.record_intent_for_delegate(
            transaction_id=record.transaction_id,
            activation_id=str(record.current_activation_id or ""),
            delegate_id=delegate_id,
            effect_id=effect_id,
            capability=capability,
            delivery_guarantee=guarantee,
        )
        stored = dict(result.get("effect") or {})
        return {
            "effect_id": effect_id,
            "idempotency_key": str(
                stored.get("idempotency_key") or effect_id
            ),
            "delivery_guarantee": str(
                stored.get("delivery_guarantee") or guarantee
            ),
            "status": str(stored.get("status") or "pending"),
            "outcome": self.coordinator.outcome_for_effect(stored),
            "recorded": True,
            "replayed": bool(result.get("replayed")),
        }

    def prepare_execution(self, *, effect_id: str) -> Dict[str, Any]:
        if self.coordinator is None:
            return {
                "effect_id": effect_id,
                "recorded": False,
                "should_execute": True,
            }
        return self.coordinator.begin_execution(effect_id=effect_id)

    def commit_result(
        self,
        *,
        effect_id: str,
        outcome: Optional[DelegateOutcome] = None,
    ) -> Dict[str, Any]:
        if self.coordinator is None:
            return {"effect_id": effect_id, "recorded": False}
        result = self.coordinator.commit_result_with_outbox(
            effect_id=effect_id,
            outcome=outcome.to_dict() if outcome is not None else None,
        )
        effect = dict(result.get("effect") or {})
        outbox = dict(result.get("feedback_outbox") or {})
        return {
            "effect_id": effect_id,
            "recorded": True,
            "status": effect.get("status"),
            "visible_effects": int(effect.get("visible_effects", 0) or 0),
            "attempts": int(effect.get("attempts", 0) or 0),
            "outbox_status": outbox.get("status"),
            "outcome": self.coordinator.outcome_for_effect(effect),
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
            "canonical_feedback_count": int(
                result.get("canonical_feedback_count", 0) or 0
            ),
            "relay_result": result.get("relay_result"),
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
