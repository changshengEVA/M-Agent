"""R2: thinking → delegate → Feedback inside the LangGraph host."""

from __future__ import annotations

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
    turn_thread_id,
)
from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE
from m_agent.runtime.think_life.contracts import (
    DelegateStatus,
    PauseReason,
    SceneActor,
    SceneEntryType,
    StimulusEnvelope,
    TransactionKind,
    TransactionState,
)

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
        config={"runtime": {"think_life": {}, "langgraph": langgraph}},
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
    agent.config["runtime"]["think_life"] = {"max_delegates_per_transaction": 2}
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

    class _StubExecutionAgent:
        enabled_capability_names = ("schedule_query",)
        registry = None

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
            think_life_hooks: Optional[Dict[str, Any]] = None,
        ) -> ExecutionResult:
            invocations.append(
                {
                    "tool_name": tool_name,
                    "tool_input": dict(tool_input),
                    "thread_id": thread_id,
                    "correlation_id": correlation_id,
                }
            )
            assert think_life_hooks is not None
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
        feedback_call = runtime.agent.thinking_agent.calls[1]
        assert "count=2" in (
            feedback_call["payload"]["execution_feedback"]["structured_summary"]
        )
    finally:
        runtime.shutdown()


def test_turn_thread_id_is_namespaced_away_from_script_graph() -> None:
    assert turn_thread_id("tx-1") == "turn:tx-1"


def test_resolve_turn_loop_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TURN_LOOP_ENV, raising=False)
    assert resolve_turn_loop_enabled() is True

    monkeypatch.setenv(TURN_LOOP_ENV, "0")
    assert resolve_turn_loop_enabled() is False
    assert load_langgraph_config({}).turn_loop_enabled is False
    # An explicit config value still wins, matching resolve_runtime_engine.
    assert resolve_turn_loop_enabled(explicit=True) is True
    assert load_langgraph_config({"turn_loop": True}).turn_loop_enabled is True

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
