"""R2: thinking → delegate → Feedback inside the LangGraph host."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from m_agent.layers.execution.contracts import ExecutionResult, ParamFillResult
from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.layers.thinking.contracts import ThinkingDecision
from m_agent.runtime.langgraph.config import (
    EXECUTION_AGENT_DELEGATE_EXECUTOR,
    TURN_LOOP_ENV,
    load_langgraph_config,
    resolve_turn_loop_enabled,
)
from m_agent.runtime.langgraph.runtime import LangGraphRuntime
from m_agent.runtime.langgraph.turn_graph import (
    PHASE_AWAITING_FEEDBACK,
    PHASE_COMPLETED,
    PHASE_ERROR,
    PHASE_TURN_IDLE,
    delegate_id_for_turn,
    turn_thread_id,
)
from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE
from m_agent.runtime.domain.contracts import (
    ActivationStatus,
    DelegateStatus,
    PauseReason,
    SceneActor,
    SceneEntryType,
    StimulusEnvelope,
    TransactionKind,
    TransactionState,
)
from m_agent.runtime.transaction.effects import EffectCoordinator
from m_agent.runtime.transaction.schedule import (
    ScheduleRunStatus,
    TransactionScheduleCoordinator,
    stable_schedule_delivery_id,
    stable_schedule_run_id,
)
from m_agent.runtime.transaction.registry import TransactionRegistry

FAKE_CAPABILITY = "fake_capability"


class _ScriptedThinkingAgent:
    """Offline stand-in for the production thinking layer.

    Mirrors the real agent's contract: it mutates ``task_state`` on the live
    transaction record the way the task-state pass does, then returns one
    planning decision per call.
    """

    def __init__(self, decisions: List[ThinkingDecision]) -> None:
        self._decisions = list(decisions)
        self.calls: List[Dict[str, Any]] = []

    def resolve_transaction(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def handle(
        self,
        perception: Any,
        *,
        transaction_state: Any = None,
        event_emitter: Any = None,
    ) -> ThinkingDecision:
        del event_emitter
        self.calls.append(
            {
                "kind": perception.stimulus.kind.value,
                "text": perception.stimulus.text,
                "payload": dict(perception.stimulus.payload or {}),
            }
        )
        if transaction_state is not None and not transaction_state.task_state.goal:
            transaction_state.task_state.goal = str(
                perception.stimulus.text or ""
            )[:240]
        if not self._decisions:
            return ThinkingDecision(mode="silent", request_complete=True)
        return self._decisions.pop(0)


def _agent(
    decisions: List[ThinkingDecision],
    *,
    turn_loop: bool = True,
    execution_agent: Any = None,
    delegate_executor: Optional[str] = None,
) -> SimpleNamespace:
    langgraph: Dict[str, Any] = {
        "turn_loop": turn_loop,
        "fake_capabilities": [FAKE_CAPABILITY],
    }
    if delegate_executor is not None:
        langgraph["delegate_executor"] = delegate_executor
    return SimpleNamespace(
        thinking_agent=_ScriptedThinkingAgent(decisions),
        execution_agent=execution_agent,
        config={"runtime": {"common": {}, "langgraph": langgraph}},
        user_name="User",
        assistant_name="Assistant",
        default_thread_id="turn-thread",
        persist_memory=False,
    )


def _runtime(
    tmp_path: Path,
    decisions: List[ThinkingDecision],
    *,
    name: str = "turn",
    **agent_kwargs: Any,
) -> LangGraphRuntime:
    return LangGraphRuntime(
        _agent(decisions, **agent_kwargs),  # type: ignore[arg-type]
        owner_id=f"turn-user-{name}",
        persist_root=tmp_path / name,
    )


def _drive(
    runtime: LangGraphRuntime,
    *,
    thread_id: str,
    text: str,
) -> List[Dict[str, Any]]:
    runtime.submit_user_message(
        thread_id=thread_id,
        conversation_id=f"{thread_id}::0",
        text=text,
        schedule_drainer=False,
    )
    return list(runtime.run_thread(thread_id)["results"])


def test_turn_loop_runs_think_delegate_feedback_reply(tmp_path: Path) -> None:
    """One user message drives tool → structured Feedback → reply → Scene."""

    runtime = _runtime(
        tmp_path,
        [
            ThinkingDecision(
                mode="execute",
                tool_name=FAKE_CAPABILITY,
                instruction="look it up",
                reasoning="need a tool first",
            ),
            ThinkingDecision(
                mode="answer_directly",
                answer="here is the answer",
                reasoning="tool evidence is enough",
            ),
            ThinkingDecision(mode="silent", request_complete=True),
        ],
        name="full-loop",
    )
    try:
        results = _drive(runtime, thread_id="t1", text="please look something up")

        # user message -> execute, tool feedback -> reply, reply feedback -> settle
        assert [item["turn_kind"] for item in results] == [
            "user_message",
            "execution_feedback",
            "execution_feedback",
        ]
        assert [item["graph_phase"] for item in results] == [
            PHASE_AWAITING_FEEDBACK,
            PHASE_AWAITING_FEEDBACK,
            PHASE_COMPLETED,
        ]
        assert all(item["success"] for item in results)
        assert [item["decision_mode"] for item in results] == [
            "execute",
            "answer_directly",
            "silent",
        ]
        assert runtime.inbox.pending_count("t1") == 0

        transaction_id = results[0]["transaction_id"]
        record = runtime.registry.get(transaction_id)
        assert record is not None
        assert record.runtime_engine == LANGGRAPH_RUNTIME_ENGINE
        assert record.state == TransactionState.COMPLETE
        assert record.active_delegate_id is None
        assert record.delegate_count == 2
        assert record.think_rounds == 3
        assert record.wm_entries

        # The thinking layer saw structured tool evidence, not raw text.
        feedback_calls = [
            call
            for call in runtime.agent.thinking_agent.calls
            if call["kind"] == "execution_feedback"
        ]
        assert len(feedback_calls) == 2
        evidence = feedback_calls[0]["payload"]["execution_feedback"]
        assert evidence["structured_summary"].startswith(
            f"tool={FAKE_CAPABILITY}"
        )
        assert evidence["last_tool_step"]["tool_name"] == FAKE_CAPABILITY

        entries = runtime.scene_system.reader.tail("t1::0", limit=100)
        actors = [(item.actor, item.entry_type) for item in entries]
        assert (SceneActor.USER, SceneEntryType.UTTERANCE) in actors
        assert (SceneActor.THINK, SceneEntryType.THOUGHT) in actors
        assert (SceneActor.WORK, SceneEntryType.ACTION) in actors
        assert (SceneActor.ASSISTANT, SceneEntryType.REPLY) in actors
        seqs = [int(item.seq) for item in entries]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    finally:
        runtime.shutdown()


def test_turn_loop_records_effect_ledger_and_outbox(tmp_path: Path) -> None:
    """Each delegate leaves one durable effect and one delivered outbox row."""

    runtime = _runtime(
        tmp_path,
        [
            ThinkingDecision(
                mode="execute",
                tool_name=FAKE_CAPABILITY,
                instruction="do the thing",
            ),
            ThinkingDecision(mode="silent", request_complete=False),
        ],
        name="effects",
    )
    try:
        results = _drive(runtime, thread_id="t2", text="do the thing")
        transaction_id = results[0]["transaction_id"]
        delegate_id = results[0]["delegate_id"]
        assert delegate_id

        ledger = runtime.effect_ledger_snapshot(transaction_id)
        effects = ledger["effects"]
        outbox = ledger["feedback_outbox"]
        assert len(effects) == 1
        assert effects[0]["delegate_id"] == delegate_id
        assert effects[0]["status"] == "completed"
        assert effects[0]["delivery_guarantee"] == "idempotent"
        # Capability guarantee: retries stay at a single visible effect.
        assert int(effects[0]["visible_effects"]) == 1
        assert len(outbox) == 1
        assert outbox[0]["status"] == "delivered"
        assert outbox[0]["ingress_key"] == (
            f"feedback:{transaction_id}:{effects[0]['activation_id']}:{delegate_id}"
        )

        # The delegate was consumed by the Feedback turn, not left dangling.
        delegate = runtime.runtime_store.load_delegate(delegate_id)
        assert delegate is not None
        assert delegate.status == DelegateStatus.CONSUMED
    finally:
        runtime.shutdown()


def test_turn_loop_resumes_checkpoint_across_stimuli(tmp_path: Path) -> None:
    """The Feedback turn resumes the same graph thread and clears the intent."""

    runtime = _runtime(
        tmp_path,
        [
            ThinkingDecision(mode="execute", tool_name=FAKE_CAPABILITY),
            ThinkingDecision(mode="silent", request_complete=False),
        ],
        name="resume",
    )
    try:
        results = _drive(runtime, thread_id="t3", text="one step please")
        transaction_id = results[0]["transaction_id"]
        assert runtime.turn_engine is not None
        state = runtime.turn_engine.get_checkpoint_state(
            transaction_id=transaction_id,
        )
        assert state["turn_index"] == 2
        assert state["graph_phase"] == PHASE_TURN_IDLE
        assert state["pending_delegate_intent"] is None
        assert state["feedback_matched_pending"] is True
        assert state["delegate_chain"] == 1

        record = runtime.registry.get(transaction_id)
        assert record is not None
        assert int(state["transaction_revision"]) == int(record.revision)
        assert state["latest_feedback_ref"]
    finally:
        runtime.shutdown()


def test_turn_loop_survives_host_restart(tmp_path: Path) -> None:
    """Store, Scene and turn checkpoint agree again after a cold restart."""

    decisions = [
        ThinkingDecision(mode="execute", tool_name=FAKE_CAPABILITY),
        ThinkingDecision(mode="silent", request_complete=False),
    ]
    runtime = _runtime(tmp_path, list(decisions), name="restart")
    try:
        results = _drive(runtime, thread_id="t4", text="persist me")
        transaction_id = results[0]["transaction_id"]
        before = runtime.registry.get(transaction_id)
        assert before is not None
        scene_before = len(runtime.scene_system.reader.tail("t4::0", limit=100))
    finally:
        runtime.shutdown()

    restarted = _runtime(tmp_path, list(decisions), name="restart")
    try:
        after = restarted.registry.get(transaction_id)
        assert after is not None
        assert after.to_dict() == before.to_dict()
        assert (
            len(restarted.scene_system.reader.tail("t4::0", limit=100))
            == scene_before
        )
        assert restarted.turn_engine is not None
        state = restarted.turn_engine.get_checkpoint_state(
            transaction_id=transaction_id,
        )
        assert int(state["transaction_revision"]) == int(after.revision)
        assert state["graph_phase"] == PHASE_TURN_IDLE
    finally:
        restarted.shutdown()


def test_silent_turn_with_request_complete_closes_user_task(
    tmp_path: Path,
) -> None:
    """``request_complete`` is the authoritative logical completion command."""

    runtime = _runtime(
        tmp_path,
        [ThinkingDecision(mode="silent", request_complete=True)],
        name="silent",
    )
    try:
        results = _drive(runtime, thread_id="t5", text="nothing to do")
        assert len(results) == 1
        assert results[0]["graph_phase"] == PHASE_COMPLETED
        assert results[0]["completed"] is True
        record = runtime.registry.get(results[0]["transaction_id"])
        assert record is not None
        assert record.state == TransactionState.COMPLETE
        assert record.delegate_count == 0
    finally:
        runtime.shutdown()


def test_execute_without_tool_name_fails_transaction(tmp_path: Path) -> None:
    """An unresolvable execute decision fails the line instead of spinning."""

    runtime = _runtime(
        tmp_path,
        [ThinkingDecision(mode="execute", tool_name="not_enabled")],
        name="bad-tool",
    )
    try:
        results = _drive(runtime, thread_id="t6", text="use a missing tool")
        assert results[0]["success"] is False
        assert results[0]["graph_phase"] == PHASE_ERROR
        assert "tool_name" in results[0]["error"]
        record = runtime.registry.get(results[0]["transaction_id"])
        assert record is not None
        assert record.state == TransactionState.PAUSE
        assert record.pause_reason == PauseReason.RUNTIME_ERROR
        assert runtime.inbox.pending_count("t6") == 0
        failed_stimulus = runtime.runtime_store.load_stimulus(
            results[0]["stimulus_id"]
        )
        assert failed_stimulus is not None
        assert failed_stimulus.disposition == "failed"
        assert failed_stimulus.disposition_stage == "final"
    finally:
        runtime.shutdown()


def test_reply_without_answer_settles_silently(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        [ThinkingDecision(mode="answer_directly", answer="   ")],
        name="empty-reply",
    )
    try:
        results = _drive(runtime, thread_id="t7", text="say something")
        assert results[0]["graph_phase"] == PHASE_TURN_IDLE
        assert results[0]["delegate_id"] == ""
        record = runtime.registry.get(results[0]["transaction_id"])
        assert record is not None
        assert record.delegate_count == 0
    finally:
        runtime.shutdown()


def test_max_delegates_per_transaction_stops_the_chain(tmp_path: Path) -> None:
    agent = _agent(
        [
            ThinkingDecision(mode="execute", tool_name=FAKE_CAPABILITY),
            ThinkingDecision(mode="execute", tool_name=FAKE_CAPABILITY),
            ThinkingDecision(mode="execute", tool_name=FAKE_CAPABILITY),
        ]
    )
    agent.config["runtime"]["common"] = {"max_delegates_per_transaction": 2}
    runtime = LangGraphRuntime(
        agent,  # type: ignore[arg-type]
        owner_id="turn-user-limit",
        persist_root=tmp_path / "limit",
    )
    try:
        results = _drive(runtime, thread_id="t8", text="keep going")
        assert [item["graph_phase"] for item in results] == [
            PHASE_AWAITING_FEEDBACK,
            PHASE_AWAITING_FEEDBACK,
            PHASE_ERROR,
        ]
        assert "max_delegates_per_transaction" in results[-1]["error"]
        record = runtime.registry.get(results[0]["transaction_id"])
        assert record is not None
        assert record.delegate_count == 2
        assert record.state == TransactionState.PAUSE
        assert record.pause_reason == PauseReason.RUNTIME_ERROR
    finally:
        runtime.shutdown()


def test_turn_loop_rollback_falls_back_to_mvp_step(tmp_path: Path) -> None:
    """Rollback flag restores the R1 ``record_progress`` drain."""

    runtime = _runtime(
        tmp_path,
        [ThinkingDecision(mode="execute", tool_name=FAKE_CAPABILITY)],
        name="rollback",
        turn_loop=False,
    )
    try:
        assert runtime.turn_loop_enabled is False
        assert runtime.turn_engine is None
        results = _drive(runtime, thread_id="t9", text="mvp only")
        assert len(results) == 1
        assert results[0]["graph_phase"] == "step_committed"
        assert runtime.agent.thinking_agent.calls == []
        record = runtime.registry.get(results[0]["transaction_id"])
        assert record is not None
        assert record.delegate_count == 0
        assert runtime.health()["turn_loop"] is False
    finally:
        runtime.shutdown()


def test_execution_agent_delegate_executor_invokes_real_tool(
    tmp_path: Path,
) -> None:
    """The same graph can drive the production execution layer."""

    invocations: List[Dict[str, Any]] = []

    class _CapabilityRegistry:
        @staticmethod
        def get(name: str) -> Any:
            assert name == "schedule_query"
            return SimpleNamespace(delivery_guarantee="at_most_once")

    class _StubExecutionAgent:
        enabled_capability_names = ("schedule_query",)
        registry = _CapabilityRegistry()

        def fill_tool_args(
            self,
            *,
            tool_name: str,
            instruction: str,
            thread_id: str,
            pending_user_request: str = "",
            correlation_id: str = "",
        ) -> ParamFillResult:
            del thread_id, pending_user_request, correlation_id
            return ParamFillResult(
                tool_name=tool_name,
                status="ready",
                args={"keyword": instruction},
            )

        def invoke_tool_direct(
            self,
            *,
            tool_name: str,
            tool_input: Dict[str, Any],
            thread_id: str,
            correlation_id: str = "",
            runtime_hooks: Optional[Dict[str, Any]] = None,
        ) -> ExecutionResult:
            invocations.append(
                {
                    "tool_name": tool_name,
                    "tool_input": dict(tool_input),
                    "thread_id": thread_id,
                    "correlation_id": correlation_id,
                    "hooks": dict(runtime_hooks or {}),
                }
            )
            assert runtime_hooks is not None
            return ExecutionResult(
                summary="two schedules found",
                tool_history=[
                    {
                        "tool_name": tool_name,
                        "params": dict(tool_input),
                        "result": {
                            "success": True,
                            "action": "query",
                            "count": 2,
                            "answer": "two schedules found",
                        },
                    }
                ],
            )

    runtime = _runtime(
        tmp_path,
        [
            ThinkingDecision(
                mode="execute",
                tool_name="schedule_query",
                instruction="tomorrow",
            ),
            ThinkingDecision(mode="silent", request_complete=False),
        ],
        name="real-executor",
        execution_agent=_StubExecutionAgent(),
        delegate_executor=EXECUTION_AGENT_DELEGATE_EXECUTOR,
    )
    try:
        results = _drive(runtime, thread_id="t10", text="what is scheduled?")
        assert results[0]["graph_phase"] == PHASE_AWAITING_FEEDBACK
        assert len(invocations) == 1
        assert invocations[0]["tool_name"] == "schedule_query"
        assert invocations[0]["correlation_id"] == results[0]["delegate_id"]
        hooks = invocations[0]["hooks"]
        assert hooks["effect_id"].endswith("-effect")
        assert hooks["idempotency_key"]
        assert hooks["delivery_guarantee"] == "at_most_once"
        effect = runtime.effect_ledger_snapshot(
            results[0]["transaction_id"]
        )["effects"][0]
        assert effect["delivery_guarantee"] == "at_most_once"
        persisted_result = json.loads(effect["result_json"])
        assert "two schedules found" in persisted_result["outcome"]["summary"]
        assert persisted_result["outcome"]["tool_history"][0]["result"][
            "count"
        ] == 2
        feedback_call = runtime.agent.thinking_agent.calls[1]
        assert "count=2" in (
            feedback_call["payload"]["execution_feedback"]["structured_summary"]
        )
    finally:
        runtime.shutdown()


def test_schedule_create_binds_and_pauses_original_langgraph_transaction(
    tmp_path: Path,
) -> None:
    class _CapabilityRegistry:
        @staticmethod
        def get(_name: str) -> Any:
            return SimpleNamespace(delivery_guarantee="at_most_once")

    class _ScheduleExecutionAgent:
        enabled_capability_names = ("schedule_create",)
        registry = _CapabilityRegistry()

        def fill_tool_args(self, *, tool_name: str, **_kwargs: Any) -> ParamFillResult:
            return ParamFillResult(tool_name=tool_name, status="ready", args={})

        def invoke_tool_direct(
            self,
            *,
            tool_name: str,
            runtime_hooks: Optional[Dict[str, Any]] = None,
            **_kwargs: Any,
        ) -> ExecutionResult:
            assert runtime_hooks is not None
            created = runtime_hooks.get("on_schedule_created")
            assert callable(created)
            result = {
                "success": True,
                "schedule_id": "schedule-created-by-graph",
            }
            created(
                transaction_id=runtime_hooks["transaction_id"],
                schedule_id="schedule-created-by-graph",
                due_at="2026-08-04T09:00:00Z",
                owner_id="schedule-owner",
                result=result,
            )
            return ExecutionResult(
                summary="schedule created",
                tool_history=[
                    {
                        "tool_name": tool_name,
                        "params": {},
                        "result": result,
                    }
                ],
            )

    runtime = _runtime(
        tmp_path,
        [
            ThinkingDecision(
                mode="execute",
                tool_name="schedule_create",
                instruction="schedule it",
            ),
            ThinkingDecision(mode="silent", request_complete=True),
        ],
        name="schedule-create-binding",
        execution_agent=_ScheduleExecutionAgent(),
        delegate_executor=EXECUTION_AGENT_DELEGATE_EXECUTOR,
    )
    try:
        results = _drive(runtime, thread_id="schedule-create", text="do it later")
        assert results[-1]["graph_phase"] == PHASE_COMPLETED
        transaction_id = results[0]["transaction_id"]
        record = runtime.registry.get(transaction_id)
        assert record is not None
        assert record.state == TransactionState.PAUSE
        assert record.pause_reason == PauseReason.SCHEDULED_WAIT
        assert record.pending_schedule_intents == []
        runs = TransactionScheduleCoordinator(
            runtime.runtime_store
        ).list_for_transaction(transaction_id)
        assert len(runs) == 1
        assert runs[0].schedule_id == "schedule-created-by-graph"
        assert runs[0].status == ScheduleRunStatus.SCHEDULED
    finally:
        runtime.shutdown()


def test_turn_thread_id_is_namespaced_away_from_script_graph() -> None:
    assert turn_thread_id("tx-1") == "turn:tx-1"


def test_delegate_identity_is_stable_for_graph_replay() -> None:
    first = delegate_id_for_turn(
        transaction_id="tx-1",
        activation_id="act-1",
        stimulus_id="stim-1",
        turn_index=3,
        capability="schedule_query",
    )
    replay = delegate_id_for_turn(
        transaction_id="tx-1",
        activation_id="act-1",
        stimulus_id="stim-1",
        turn_index=3,
        capability="schedule_query",
    )
    other = delegate_id_for_turn(
        transaction_id="tx-1",
        activation_id="act-1",
        stimulus_id="stim-2",
        turn_index=4,
        capability="schedule_query",
    )
    assert replay == first
    assert other != first


def test_pending_effect_feedback_recovers_with_real_outcome(
    tmp_path: Path,
) -> None:
    database = tmp_path / "effect-recovery.sqlite3"
    registry = TransactionRegistry(
        persist_path=database,
        default_runtime_engine=LANGGRAPH_RUNTIME_ENGINE,
    )
    record = registry.create(
        thread_id="effect-recovery-thread",
        conversation_id="effect-recovery-thread::0",
        kind=TransactionKind.USER_TASK,
    )
    delegated = registry.begin_delegate(
        record.transaction_id,
        "delegate-recovery",
        expected_revision=record.revision,
    )
    coordinator = EffectCoordinator(store=registry.store, registry=registry)
    intent = coordinator.record_intent_for_delegate(
        transaction_id=delegated.transaction_id,
        activation_id=str(delegated.current_activation_id or ""),
        delegate_id="delegate-recovery",
        effect_id="effect-recovery",
        capability="schedule_query",
        delivery_guarantee="idempotent",
    )
    prepared = coordinator.begin_execution(effect_id="effect-recovery")
    assert prepared["should_execute"] is True
    coordinator.commit_result_with_outbox(
        effect_id="effect-recovery",
        outcome={
            "success": True,
            "summary": "durable real result",
            "tool_history": [
                {
                    "tool_name": "schedule_query",
                    "params": {},
                    "result": {"success": True, "count": 3},
                }
            ],
            "visible_effects": 1,
            "attempts": 1,
        },
    )
    assert intent["feedback_outbox"]["status"] == "pending"
    registry.close()

    deliveries: List[Dict[str, Any]] = []
    reopened = TransactionRegistry(
        persist_path=database,
        default_runtime_engine=LANGGRAPH_RUNTIME_ENGINE,
    )
    recovered_coordinator = EffectCoordinator(
        store=reopened.store,
        registry=reopened,
        feedback_relay=lambda delivery: deliveries.append(delivery)
        or {"stimulus_id": "stim-recovered"},
    )
    recovery = recovered_coordinator.recover_pending_relays()
    assert recovery["recovered_count"] == 1
    assert recovery["recovery_failure_count"] == 0
    assert deliveries[0]["outcome"]["summary"] == "durable real result"
    assert deliveries[0]["outcome"]["tool_history"][0]["result"][
        "count"
    ] == 3
    snapshot = recovered_coordinator.load_for_transaction(
        record.transaction_id
    )
    assert snapshot["feedback_outbox"][0]["status"] == "delivered"
    reopened.close()


def test_at_most_once_interrupted_attempt_becomes_uncertain(
    tmp_path: Path,
) -> None:
    registry = TransactionRegistry(
        persist_path=tmp_path / "effect-at-most-once.sqlite3",
        default_runtime_engine=LANGGRAPH_RUNTIME_ENGINE,
    )
    record = registry.create(
        thread_id="at-most-once-thread",
        conversation_id="at-most-once-thread::0",
        kind=TransactionKind.USER_TASK,
    )
    delegated = registry.begin_delegate(
        record.transaction_id,
        "delegate-at-most-once",
        expected_revision=record.revision,
    )
    coordinator = EffectCoordinator(store=registry.store, registry=registry)
    coordinator.record_intent_for_delegate(
        transaction_id=delegated.transaction_id,
        activation_id=str(delegated.current_activation_id or ""),
        delegate_id="delegate-at-most-once",
        effect_id="effect-at-most-once",
        capability="email_send",
        delivery_guarantee="at_most_once",
    )
    assert coordinator.begin_execution(
        effect_id="effect-at-most-once"
    )["should_execute"] is True
    replay = coordinator.begin_execution(effect_id="effect-at-most-once")
    assert replay["should_execute"] is False
    assert replay["uncertain"] is True
    assert replay["outcome"]["status"] == "uncertain"
    registry.close()


def test_concurrent_threads_keep_history_and_event_emitters_isolated(
    tmp_path: Path,
) -> None:
    barrier = threading.Barrier(2)
    observed: List[Dict[str, Any]] = []
    observed_lock = threading.Lock()

    class _ConcurrentThinkingAgent:
        def resolve_transaction(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def handle(
            self,
            perception: Any,
            *,
            transaction_state: Any = None,
            event_emitter: Any = None,
        ) -> ThinkingDecision:
            del transaction_state
            barrier.wait(timeout=5)
            text = str(perception.stimulus.text)
            if event_emitter is not None:
                event_emitter("planner_observed", {"text": text})
            with observed_lock:
                observed.append(
                    {
                        "text": text,
                        "history": list(perception.dialogue_history or []),
                    }
                )
            return ThinkingDecision(mode="silent", request_complete=False)

    agent = SimpleNamespace(
        thinking_agent=_ConcurrentThinkingAgent(),
        execution_agent=None,
        config={
            "runtime": {
                "common": {},
                "langgraph": {
                    "turn_loop": True,
                    "fake_capabilities": [FAKE_CAPABILITY],
                },
            }
        },
        user_name="User",
        assistant_name="Assistant",
        default_thread_id="concurrent-thread",
        persist_memory=False,
    )
    runtime = LangGraphRuntime(
        agent,  # type: ignore[arg-type]
        owner_id="concurrent-user",
        persist_root=tmp_path / "concurrent",
    )
    events: Dict[str, List[Dict[str, Any]]] = {"a": [], "b": []}
    results: Dict[str, Dict[str, Any]] = {}
    try:
        for key in ("a", "b"):
            runtime.submit_user_message(
                thread_id=f"thread-{key}",
                conversation_id=f"thread-{key}::0",
                text=f"message-{key}",
                schedule_drainer=False,
            )

        def run(key: str) -> None:
            results[key] = runtime.run_thread(
                f"thread-{key}",
                history_messages=[{"role": "user", "content": f"history-{key}"}],
                event_emitter=lambda event, payload: events[key].append(
                    {"event": event, **payload}
                ),
            )

        workers = [threading.Thread(target=run, args=(key,)) for key in ("a", "b")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            assert not worker.is_alive()

        assert results["a"]["success"] is True
        assert results["b"]["success"] is True
        assert events["a"] == [
            {"event": "planner_observed", "text": "message-a"}
        ]
        assert events["b"] == [
            {"event": "planner_observed", "text": "message-b"}
        ]
        by_text = {item["text"]: item["history"] for item in observed}
        assert "history-a" in str(by_text["message-a"])
        assert "history-b" not in str(by_text["message-a"])
        assert "history-b" in str(by_text["message-b"])
        assert "history-a" not in str(by_text["message-b"])
    finally:
        runtime.shutdown()


def test_schedule_completion_consumes_claimed_run_on_original_transaction(
    tmp_path: Path,
) -> None:
    runtime = _runtime(
        tmp_path,
        [
            ThinkingDecision(mode="silent", request_complete=False),
            ThinkingDecision(mode="silent", request_complete=True),
        ],
        name="schedule-original-transaction",
    )

    class _Lifecycle:
        def __init__(self) -> None:
            self.started: List[Dict[str, Any]] = []
            self.finished: List[Dict[str, Any]] = []

        def on_schedule_processing_started(self, **kwargs: Any) -> None:
            self.started.append(dict(kwargs))

        def on_schedule_processing_finished(self, **kwargs: Any) -> None:
            self.finished.append(dict(kwargs))

    lifecycle = _Lifecycle()
    runtime.loop._schedule_lifecycle = lifecycle
    try:
        initial = _drive(runtime, thread_id="schedule-thread", text="later")
        transaction_id = initial[0]["transaction_id"]
        record = runtime.registry.get(transaction_id)
        assert record is not None
        coordinator = TransactionScheduleCoordinator(runtime.runtime_store)
        registered = coordinator.register(
            schedule_id="schedule-original",
            transaction_id=transaction_id,
            due_at="2026-08-03T08:00:00Z",
            expected_transaction_revision=record.revision,
            schedule_owner_id="owner-original",
        )
        claimed = coordinator.claim(
            registered.run.schedule_run_id,
            expected_run_revision=registered.run.revision,
            expected_transaction_revision=registered.transaction.revision,
        )
        runtime.registry.refresh_from_store(transaction_id)
        stimulus = StimulusEnvelope(
            stimulus_id="stim-schedule-original",
            ingress_key=f"schedule_delivery:{claimed.delivery_id}",
            thread_id="schedule-thread",
            conversation_id="schedule-thread::0",
            stimulus=Stimulus(
                kind=StimulusKind.SCHEDULED_PLAN,
                text="run scheduled objective",
                payload={
                    "owner_id": "owner-original",
                    "schedule_id": "schedule-original",
                    "schedule_run_id": registered.run.schedule_run_id,
                    "schedule_delivery_id": claimed.delivery_id,
                    "transaction_id": transaction_id,
                },
            ),
            occurred_at="2026-08-03T08:00:00Z",
            transaction_id=transaction_id,
            schedule_id="schedule-original",
            schedule_run_id=registered.run.schedule_run_id,
            schedule_delivery_id=claimed.delivery_id,
        )
        runtime.gateway.submit(stimulus, schedule_drainer=False)
        result = runtime.run_thread("schedule-thread")
        assert result["success"] is True
        persisted_run = coordinator.load(registered.run.schedule_run_id)
        assert persisted_run is not None
        assert persisted_run.status == ScheduleRunStatus.CONSUMED
        completed = runtime.registry.get(transaction_id)
        assert completed is not None
        assert completed.state == TransactionState.COMPLETE
        assert len(lifecycle.started) == 1
        assert lifecycle.started[0]["run_id"] == registered.run.schedule_run_id
        assert len(lifecycle.finished) == 1
        assert lifecycle.finished[0]["success"] is True
    finally:
        runtime.shutdown()


def test_early_scheduled_heartbeat_binds_pending_intent_before_claim(
    tmp_path: Path,
) -> None:
    due_at = "2026-08-03T08:00:00Z"
    schedule_id = "schedule-early-delivery"
    runtime = _runtime(
        tmp_path,
        [ThinkingDecision(mode="silent", request_complete=True)],
        name="schedule-early-delivery",
    )
    try:
        record = runtime.registry.create(
            thread_id="schedule-early-thread",
            conversation_id="schedule-early-thread::0",
            kind=TransactionKind.USER_TASK,
        )
        old_activation_id = str(record.current_activation_id or "")
        runtime.registry.record_schedule_intent(
            record.transaction_id,
            schedule_id=schedule_id,
            due_at=due_at,
            owner_id="owner-early",
        )
        pending = runtime.registry.get(record.transaction_id)
        assert pending is not None
        assert pending.current_activation_id == old_activation_id
        assert pending.pending_schedule_intents

        run_id = stable_schedule_run_id(
            schedule_id,
            record.transaction_id,
            due_at,
        )
        delivery_id = stable_schedule_delivery_id(run_id, 1)
        stimulus = StimulusEnvelope(
            stimulus_id="stim-schedule-early-delivery",
            ingress_key=f"schedule_delivery:{delivery_id}",
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            stimulus=Stimulus(
                kind=StimulusKind.SCHEDULED_PLAN,
                text="run the early scheduled objective",
                payload={
                    "owner_id": "owner-early",
                    "schedule_id": schedule_id,
                    "schedule_run_id": run_id,
                    "schedule_delivery_id": delivery_id,
                    "transaction_id": record.transaction_id,
                    "due_at_utc": due_at,
                },
            ),
            occurred_at=due_at,
            transaction_id=record.transaction_id,
            schedule_id=schedule_id,
            schedule_run_id=run_id,
            schedule_delivery_id=delivery_id,
        )

        runtime.gateway.submit(stimulus, schedule_drainer=False)
        result = runtime.run_thread(record.thread_id)

        assert result["success"] is True
        coordinator = TransactionScheduleCoordinator(runtime.runtime_store)
        consumed_run = coordinator.load(run_id)
        assert consumed_run is not None
        assert consumed_run.due_at == due_at
        assert consumed_run.status == ScheduleRunStatus.CONSUMED
        assert consumed_run.claimed_activation_id
        assert consumed_run.claimed_activation_id != old_activation_id

        old_activation = runtime.runtime_store.load_activation(
            old_activation_id
        )
        claimed_activation = runtime.runtime_store.load_activation(
            consumed_run.claimed_activation_id
        )
        assert old_activation is not None
        assert old_activation.status == ActivationStatus.COMPLETED
        assert claimed_activation is not None
        assert claimed_activation.status == ActivationStatus.COMPLETED

        completed = runtime.registry.get(record.transaction_id)
        assert completed is not None
        assert completed.state == TransactionState.COMPLETE
        assert completed.current_activation_id is None
        assert completed.pending_schedule_intents == []
        assert completed.last_error is None
    finally:
        runtime.shutdown()


def test_early_heartbeat_registers_all_pending_intents_before_claim(
    tmp_path: Path,
) -> None:
    due_at = "2026-08-03T08:00:00Z"
    later_due_at = "2026-08-04T08:00:00Z"
    due_schedule_id = "schedule-early-first"
    later_schedule_id = "schedule-early-second"
    runtime = _runtime(
        tmp_path,
        [ThinkingDecision(mode="silent", request_complete=True)],
        name="schedule-early-multiple",
    )
    try:
        record = runtime.registry.create(
            thread_id="schedule-early-multiple-thread",
            conversation_id="schedule-early-multiple-thread::0",
            kind=TransactionKind.USER_TASK,
        )
        runtime.registry.record_schedule_intent(
            record.transaction_id,
            schedule_id=due_schedule_id,
            due_at=due_at,
            owner_id="owner-early",
        )
        runtime.registry.record_schedule_intent(
            record.transaction_id,
            schedule_id=later_schedule_id,
            due_at=later_due_at,
            owner_id="owner-early",
        )
        due_run_id = stable_schedule_run_id(
            due_schedule_id,
            record.transaction_id,
            due_at,
        )
        later_run_id = stable_schedule_run_id(
            later_schedule_id,
            record.transaction_id,
            later_due_at,
        )
        delivery_id = stable_schedule_delivery_id(due_run_id, 1)
        stimulus = StimulusEnvelope(
            stimulus_id="stim-schedule-early-multiple",
            ingress_key=f"schedule_delivery:{delivery_id}",
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            stimulus=Stimulus(
                kind=StimulusKind.SCHEDULED_PLAN,
                text="run the first scheduled objective",
                payload={
                    "owner_id": "owner-early",
                    "schedule_id": due_schedule_id,
                    "schedule_run_id": due_run_id,
                    "schedule_delivery_id": delivery_id,
                    "transaction_id": record.transaction_id,
                    "due_at_utc": due_at,
                },
            ),
            occurred_at=due_at,
            transaction_id=record.transaction_id,
            schedule_id=due_schedule_id,
            schedule_run_id=due_run_id,
            schedule_delivery_id=delivery_id,
        )

        runtime.gateway.submit(stimulus, schedule_drainer=False)
        result = runtime.run_thread(record.thread_id)

        assert result["success"] is True
        coordinator = TransactionScheduleCoordinator(runtime.runtime_store)
        due_run = coordinator.load(due_run_id)
        later_run = coordinator.load(later_run_id)
        assert due_run is not None
        assert due_run.due_at == due_at
        assert due_run.status == ScheduleRunStatus.CONSUMED
        assert later_run is not None
        assert later_run.due_at == later_due_at
        assert later_run.status == ScheduleRunStatus.SCHEDULED
        assert later_run.claimed_activation_id is None

        completed = runtime.registry.get(record.transaction_id)
        assert completed is not None
        assert completed.state == TransactionState.COMPLETE
        assert completed.current_activation_id is None
        assert completed.pending_schedule_intents == []
        assert completed.last_error is None
    finally:
        runtime.shutdown()


def test_pending_schedule_intent_enters_scheduled_wait_while_task_is_processing(
    tmp_path: Path,
) -> None:
    runtime = _runtime(
        tmp_path,
        [ThinkingDecision(mode="silent", request_complete=False)],
        name="schedule-registration",
    )
    try:
        record = runtime.registry.create(
            thread_id="schedule-register-thread",
            conversation_id="schedule-register-thread::0",
            kind=TransactionKind.USER_TASK,
        )
        runtime.registry.record_schedule_intent(
            record.transaction_id,
            schedule_id="schedule-created",
            due_at="2026-08-03T08:00:00Z",
            owner_id="owner-created",
        )
        pending = runtime.registry.get(record.transaction_id)
        assert pending is not None
        assert pending.task_state.completion_status == "processing"
        stimulus = StimulusEnvelope(
            stimulus_id="stim-schedule-registration",
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            stimulus=Stimulus(
                kind=StimulusKind.USER_MESSAGE,
                text="finish schedule registration",
                payload={},
            ),
            occurred_at="2026-08-02T08:00:00Z",
            transaction_id=record.transaction_id,
        )
        runtime.gateway.submit(stimulus, schedule_drainer=False)

        result = runtime.run_thread(record.thread_id)

        assert result["success"] is True
        paused = runtime.registry.get(record.transaction_id)
        assert paused is not None
        assert paused.state == TransactionState.PAUSE
        assert paused.pause_reason == PauseReason.SCHEDULED_WAIT
        assert paused.pending_schedule_intents == []
        runs = TransactionScheduleCoordinator(
            runtime.runtime_store
        ).list_for_transaction(record.transaction_id)
        assert len(runs) == 1
        assert runs[0].status == ScheduleRunStatus.SCHEDULED
    finally:
        runtime.shutdown()


def test_resolve_turn_loop_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TURN_LOOP_ENV, raising=False)
    assert resolve_turn_loop_enabled() is True

    monkeypatch.setenv(TURN_LOOP_ENV, "0")
    assert resolve_turn_loop_enabled() is False
    assert load_langgraph_config({}).turn_loop_enabled is False
    # A direct programmatic argument remains highest priority.
    assert resolve_turn_loop_enabled(explicit=True) is True
    # YAML is a base setting, so the deployment environment can roll it back.
    assert load_langgraph_config({"turn_loop": True}).turn_loop_enabled is False

    monkeypatch.setenv(TURN_LOOP_ENV, "not-a-bool")
    assert resolve_turn_loop_enabled() is True


def test_load_langgraph_config_rejects_unknown_executor() -> None:
    with pytest.raises(ValueError, match="unsupported delegate executor"):
        load_langgraph_config({"delegate_executor": "nope"})


def test_completed_non_user_task_closes_after_turn(tmp_path: Path) -> None:
    """Non-USER_TASK lines close on ``request_complete``."""

    runtime = _runtime(
        tmp_path,
        [ThinkingDecision(mode="silent", request_complete=True)],
        name="system-kind",
    )
    try:
        record = runtime.registry.create(
            thread_id="t11",
            conversation_id="t11::0",
            kind=TransactionKind.SYSTEM,
        )
        runtime.gateway.submit(
            StimulusEnvelope(
                stimulus_id="stim-system-close",
                thread_id="t11",
                conversation_id="t11::0",
                stimulus=Stimulus(
                    kind=StimulusKind.USER_MESSAGE,
                    text="close me",
                ),
                occurred_at="2026-07-30T00:00:00Z",
                transaction_id=record.transaction_id,
            ),
            schedule_drainer=False,
        )
        results = list(runtime.run_thread("t11")["results"])
        assert results[0]["graph_phase"] == PHASE_COMPLETED
        assert results[0]["completed"] is True
        reloaded = runtime.registry.get(record.transaction_id)
        assert reloaded is not None
        assert reloaded.state == TransactionState.COMPLETE
    finally:
        runtime.shutdown()
