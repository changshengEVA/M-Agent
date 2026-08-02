"""Internal smoke check for the LangGraph in-graph transaction turn loop.

One user message drives
   ``thinking -> delegate -> fake tool -> structured Feedback -> Scene`` inside
   the LangGraph host, and the Transaction Store, Scene log, P7 effect ledger and
   LangGraph turn checkpoint are cross-checked. The round also restarts the host
   on the same persistence root and re-runs the same input with the turn loop
   rolled back, which must fall back to the R1 ``record_progress`` drain.

Both agents are stubs with no model provider, so the run is offline and
deterministic.

Usage:
    python scripts/smoke_langgraph_turn_loop.py --rounds 3
    python scripts/smoke_langgraph_turn_loop.py --rounds 3 --verbose
    python scripts/smoke_langgraph_turn_loop.py --json-out .tmp/r2.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if _SRC_ROOT.is_dir() and str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from m_agent.layers.execution.contracts import (  # noqa: E402
    ExecutionResult,
    ParamFillResult,
)
from m_agent.layers.thinking.contracts import ThinkingDecision  # noqa: E402
from m_agent.runtime.langgraph.runtime import LangGraphRuntime  # noqa: E402
from m_agent.runtime.langgraph.turn_graph import (  # noqa: E402
    PHASE_AWAITING_FEEDBACK,
    PHASE_TURN_IDLE,
)
from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE  # noqa: E402
from m_agent.runtime.domain.contracts import (  # noqa: E402
    DelegateStatus,
    SceneActor,
    SceneEntry,
    SceneEntryType,
    TransactionState,
)
from m_agent.runtime.transaction.predicates import (  # noqa: E402
    project_compat_status,
)
from m_agent.runtime.turn_support.tool_runner import (  # noqa: E402
    REPLY_TOOL_NAME,
)
from m_agent.runtime.transaction.store import (  # noqa: E402
    SQLiteRuntimeStore,
)
from m_agent.runtime.transaction.registry import (  # noqa: E402
    TransactionRegistry,
)
from m_agent.systems.wm import build_default_wm_system  # noqa: E402

FAKE_CAPABILITY = "fake_capability"
USER_TEXT = "please look something up and tell me the result"
REPLY_TEXT = "here is what I found"


class SmokeError(RuntimeError):
    """Raised when the smoke run cannot be performed at all."""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class CheckLog:
    checks: List[Check] = field(default_factory=list)

    def expect(self, name: str, ok: Any, detail: str = "") -> bool:
        passed = bool(ok)
        self.checks.append(Check(name=name, ok=passed, detail=detail))
        return passed

    def expect_equal(self, name: str, actual: Any, expected: Any) -> bool:
        return self.expect(
            name,
            actual == expected,
            f"expected={expected!r} actual={actual!r}",
        )

    @property
    def failures(self) -> List[Check]:
        return [item for item in self.checks if not item.ok]


# ---------------------------------------------------------------------------
# Offline stubs shared by both engines
# ---------------------------------------------------------------------------


def turn_decisions() -> List[ThinkingDecision]:
    """One tool step, then a user-visible reply, then settle."""

    return [
        ThinkingDecision(
            mode="execute",
            tool_name=FAKE_CAPABILITY,
            instruction="look it up",
            reasoning="I need tool evidence before answering",
        ),
        ThinkingDecision(
            mode="answer_directly",
            answer=REPLY_TEXT,
            reasoning="the tool returned enough evidence",
            episode_note="answered from one tool step",
        ),
        ThinkingDecision(mode="silent", request_complete=False),
    ]


class ScriptedThinkingAgent:
    """Deterministic stand-in for the production thinking layer."""

    def __init__(self, decisions: Sequence[ThinkingDecision]) -> None:
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
        payload = dict(perception.stimulus.payload or {})
        self.calls.append(
            {
                "kind": perception.stimulus.kind.value,
                "execution_feedback": dict(payload.get("execution_feedback") or {}),
            }
        )
        if transaction_state is not None and not transaction_state.task_state.goal:
            transaction_state.task_state.goal = str(
                perception.stimulus.text or ""
            )[:240]
        if not self._decisions:
            return ThinkingDecision(mode="silent", request_complete=False)
        return self._decisions.pop(0)


class ScriptedExecutionAgent:
    """Deterministic execution stub for the LangGraph turn loop."""

    enabled_capability_names = (REPLY_TOOL_NAME, FAKE_CAPABILITY)
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
            args={"instruction": instruction} if instruction else {},
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
        del thread_id
        hooks = dict(runtime_hooks or {})
        if tool_name == REPLY_TOOL_NAME:
            message = str(tool_input.get("message", "") or "")
            on_reply = hooks.get("on_reply")
            if callable(on_reply):
                on_reply(message, finalize=True)
            scene_writer = hooks.get("scene_writer")
            if scene_writer is not None:
                scene_writer.append(
                    str(hooks.get("conversation_id", "") or ""),
                    SceneEntry(
                        seq=0,
                        occurred_at="",
                        entry_type=SceneEntryType.REPLY,
                        actor=SceneActor.ASSISTANT,
                        text=message,
                        transaction_id=str(hooks.get("transaction_id", "") or ""),
                        delegate_id=correlation_id or None,
                        tool_name=REPLY_TOOL_NAME,
                    ),
                )
            return ExecutionResult(
                summary=message,
                tool_history=[
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
                ],
            )
        answer = f"{tool_name} completed"
        return ExecutionResult(
            summary=answer,
            tool_history=[
                {
                    "tool_name": tool_name,
                    "params": dict(tool_input),
                    "result": {
                        "success": True,
                        "action": "fake_execute",
                        "tool_invoked": True,
                        "count": 1,
                        "answer": answer,
                        "message": answer,
                    },
                }
            ],
        )


def _stub_agent(
    *,
    thread_id: str,
    decisions: Sequence[ThinkingDecision],
    turn_loop: bool,
) -> SimpleNamespace:
    return SimpleNamespace(
        thinking_agent=ScriptedThinkingAgent(decisions),
        execution_agent=ScriptedExecutionAgent(),
        systems=SimpleNamespace(wm=build_default_wm_system()),
        config={
            "runtime": {
                "common": {},
                "langgraph": {
                    "turn_loop": turn_loop,
                    "fake_capabilities": [FAKE_CAPABILITY],
                },
            }
        },
        user_name="User",
        assistant_name="Assistant",
        default_thread_id=thread_id,
        persist_memory=False,
    )


def _open_langgraph_runtime(
    *,
    persist_root: Path,
    thread_id: str,
    owner_id: str,
    turn_loop: bool = True,
) -> LangGraphRuntime:
    return LangGraphRuntime(
        _stub_agent(
            thread_id=thread_id,
            decisions=turn_decisions(),
            turn_loop=turn_loop,
        ),  # type: ignore[arg-type]
        owner_id=owner_id,
        persist_root=persist_root,
    )


# ---------------------------------------------------------------------------
# Domain observation surface
# ---------------------------------------------------------------------------


def _transaction_surface(record: Any) -> Dict[str, Any]:
    return {
        "kind": record.kind.value,
        "state": record.state.value,
        "status": project_compat_status(record),
        "lifecycle_status": record.lifecycle_status.value,
        "delegate_count": int(record.delegate_count),
        "think_rounds": int(record.think_rounds),
        "wm_entries": len(record.wm_entries),
        "goal": str(record.task_state.goal or ""),
        "completion_status": record.task_state.completion_status,
        "has_active_delegate": bool(record.active_delegate_id),
        "last_error": record.last_error,
    }


def domain_surface(
    *,
    registry: TransactionRegistry,
    store: SQLiteRuntimeStore,
    scene_reader: Any,
    thread_id: str,
    conversation_id: str,
) -> Dict[str, Any]:
    """The engine-neutral slice both runtimes must agree on."""

    records = sorted(
        registry.list(thread_id=thread_id),
        key=lambda item: item.created_at,
    )
    delegates: List[str] = []
    for record in records:
        for item in store.list_delegates(transaction_id=record.transaction_id):
            delegates.append(item.status.value)
    stimuli = [
        (item.kind.value, item.disposition, item.disposition_stage)
        for item in sorted(
            store.list_stimuli(thread_id=thread_id),
            key=lambda entry: int(entry.accepted_seq or 0),
        )
    ]
    scene = [
        (item.actor.value, item.entry_type.value, str(item.text or ""))
        for item in scene_reader.tail(conversation_id, limit=200)
    ]
    return {
        "transactions": [_transaction_surface(item) for item in records],
        "delegates": sorted(delegates),
        "stimuli": stimuli,
        "scene": scene,
    }


# ---------------------------------------------------------------------------
# Gate 1: LangGraph turn loop
# ---------------------------------------------------------------------------


def _drive_turn_loop(
    log: CheckLog,
    runtime: LangGraphRuntime,
    *,
    thread_id: str,
    conversation_id: str,
    text: str,
    tag: str,
) -> List[Dict[str, Any]]:
    stimulus_id = runtime.submit_user_message(
        thread_id=thread_id,
        conversation_id=conversation_id,
        text=text,
        schedule_drainer=False,
    )
    log.expect(f"{tag}.stimulus_id", bool(stimulus_id), f"id={stimulus_id!r}")
    outcome = runtime.run_thread(thread_id)
    log.expect(
        f"{tag}.run_success",
        outcome.get("success") is True,
        json.dumps(outcome, default=str)[:800],
    )
    log.expect_equal(f"{tag}.turn_loop", outcome.get("turn_loop"), True)
    log.expect_equal(
        f"{tag}.runtime_engine_id",
        outcome.get("runtime_engine_id"),
        LANGGRAPH_RUNTIME_ENGINE,
    )
    log.expect_equal(
        f"{tag}.pending_after_drain",
        runtime.inbox.pending_count(thread_id),
        0,
    )
    return list(outcome.get("results") or [])


def _check_turn_sequence(
    log: CheckLog,
    results: List[Dict[str, Any]],
    *,
    tag: str,
) -> None:
    """user message -> tool delegate -> reply delegate -> settle."""

    log.expect_equal(
        f"{tag}.turn_kinds",
        [item.get("turn_kind") for item in results],
        ["user_message", "execution_feedback", "execution_feedback"],
    )
    log.expect_equal(
        f"{tag}.graph_phases",
        [item.get("graph_phase") for item in results],
        [PHASE_AWAITING_FEEDBACK, PHASE_AWAITING_FEEDBACK, PHASE_TURN_IDLE],
    )
    log.expect_equal(
        f"{tag}.decision_modes",
        [item.get("decision_mode") for item in results],
        ["execute", "answer_directly", "silent"],
    )
    log.expect_equal(
        f"{tag}.turn_indexes",
        [item.get("turn_index") for item in results],
        [1, 2, 3],
    )
    log.expect(
        f"{tag}.delegates_are_distinct",
        len({item.get("delegate_id") for item in results if item.get("delegate_id")})
        == 2,
        f"delegate_ids={[item.get('delegate_id') for item in results]}",
    )


def _check_store(
    log: CheckLog,
    runtime: LangGraphRuntime,
    *,
    transaction_id: str,
    tag: str,
) -> None:
    record = runtime.registry.get(transaction_id)
    if record is None:
        raise SmokeError(f"transaction vanished from Store: {transaction_id}")
    log.expect_equal(
        f"{tag}.store.runtime_engine",
        record.runtime_engine,
        LANGGRAPH_RUNTIME_ENGINE,
    )
    log.expect_equal(
        f"{tag}.store.state",
        record.state.value,
        TransactionState.CONTINUE.value,
    )
    log.expect_equal(
        f"{tag}.store.status",
        project_compat_status(record),
        "running",
    )
    log.expect(
        f"{tag}.store.no_delegate_in_flight",
        record.active_delegate_id is None,
        f"active_delegate_id={record.active_delegate_id!r}",
    )
    log.expect_equal(f"{tag}.store.delegate_count", int(record.delegate_count), 2)
    log.expect_equal(f"{tag}.store.think_rounds", int(record.think_rounds), 3)
    log.expect(
        f"{tag}.store.wm_not_empty",
        len(record.wm_entries) > 0,
        f"wm_entries={len(record.wm_entries)}",
    )
    log.expect(f"{tag}.store.goal_recorded", bool(record.task_state.goal))
    log.expect(
        f"{tag}.store.no_last_error",
        not record.last_error,
        f"last_error={record.last_error!r}",
    )
    delegates = runtime.runtime_store.list_delegates(transaction_id=transaction_id)
    log.expect_equal(f"{tag}.store.delegate_rows", len(delegates), 2)
    log.expect_equal(
        f"{tag}.store.delegates_consumed",
        sorted(item.status.value for item in delegates),
        [DelegateStatus.CONSUMED.value] * 2,
    )


def _check_structured_feedback(
    log: CheckLog,
    runtime: LangGraphRuntime,
    *,
    tag: str,
) -> None:
    """Thinking must see structured tool evidence, not just prose."""

    calls = runtime.agent.thinking_agent.calls
    feedback_calls = [item for item in calls if item["kind"] == "execution_feedback"]
    log.expect_equal(f"{tag}.feedback.think_calls", len(feedback_calls), 2)
    if len(feedback_calls) < 2:
        return
    tool_step = feedback_calls[0]["execution_feedback"]
    log.expect_equal(
        f"{tag}.feedback.structured_summary",
        tool_step.get("structured_summary"),
        f"tool={FAKE_CAPABILITY}; action=fake_execute; count=1; "
        f"result={FAKE_CAPABILITY} completed",
    )
    log.expect_equal(
        f"{tag}.feedback.last_tool_step",
        (tool_step.get("last_tool_step") or {}).get("tool_name"),
        FAKE_CAPABILITY,
    )
    reply_step = feedback_calls[1]["execution_feedback"]
    log.expect_equal(
        f"{tag}.feedback.reply_tool",
        (reply_step.get("last_tool_step") or {}).get("tool_name"),
        REPLY_TOOL_NAME,
    )
    log.expect_equal(
        f"{tag}.feedback.reply_finalized",
        (reply_step.get("last_tool_step") or {}).get("finalize"),
        True,
    )


def _check_effect_ledger(
    log: CheckLog,
    runtime: LangGraphRuntime,
    *,
    transaction_id: str,
    tag: str,
) -> None:
    """P7 reuse: durable effect intents plus a delivered Feedback outbox."""

    ledger = runtime.effect_ledger_snapshot(transaction_id)
    effects = list(ledger.get("effects") or [])
    outbox = list(ledger.get("feedback_outbox") or [])
    log.expect_equal(f"{tag}.effects.count", len(effects), 2)
    log.expect_equal(
        f"{tag}.effects.completed",
        sorted(str(item.get("status")) for item in effects),
        ["completed", "completed"],
    )
    log.expect_equal(
        f"{tag}.effects.single_visible_effect",
        sorted(int(item.get("visible_effects", 0) or 0) for item in effects),
        [1, 1],
    )
    log.expect_equal(
        f"{tag}.effects.delivery_guarantee",
        sorted({str(item.get("delivery_guarantee")) for item in effects}),
        ["idempotent"],
    )
    log.expect_equal(f"{tag}.outbox.count", len(outbox), 2)
    log.expect_equal(
        f"{tag}.outbox.delivered",
        sorted(str(item.get("status")) for item in outbox),
        ["delivered", "delivered"],
    )
    ingress_keys = {str(item.get("ingress_key")) for item in outbox}
    log.expect_equal(f"{tag}.outbox.canonical_ingress_keys", len(ingress_keys), 2)
    log.expect(
        f"{tag}.outbox.ingress_key_shape",
        all(key.startswith(f"feedback:{transaction_id}:") for key in ingress_keys),
        f"ingress_keys={sorted(ingress_keys)}",
    )


def _check_scene(
    log: CheckLog,
    runtime: LangGraphRuntime,
    *,
    conversation_id: str,
    tag: str,
) -> int:
    entries = runtime.scene_system.reader.tail(conversation_id, limit=200)
    seqs = [int(item.seq) for item in entries]
    log.expect(
        f"{tag}.scene.seq_strictly_increasing",
        all(later > earlier for earlier, later in zip(seqs, seqs[1:])),
        f"seqs={seqs}",
    )
    shape = [(item.actor.value, item.entry_type.value) for item in entries]
    log.expect_equal(
        f"{tag}.scene.shape",
        shape,
        [
            (SceneActor.USER.value, SceneEntryType.UTTERANCE.value),
            (SceneActor.THINK.value, SceneEntryType.THOUGHT.value),
            (SceneActor.WORK.value, SceneEntryType.ACTION.value),
            (SceneActor.THINK.value, SceneEntryType.THOUGHT.value),
            (SceneActor.THINK.value, SceneEntryType.THOUGHT.value),
            (SceneActor.ASSISTANT.value, SceneEntryType.REPLY.value),
        ],
    )
    replies = [
        str(item.text or "")
        for item in entries
        if item.entry_type == SceneEntryType.REPLY
    ]
    log.expect_equal(f"{tag}.scene.reply_text", replies, [REPLY_TEXT])
    log.expect(
        f"{tag}.scene.entries_bound_to_transaction",
        all(str(item.transaction_id or "").strip() for item in entries),
        "some Scene entries have no transaction_id",
    )
    return len(entries)


def _check_dispositions(
    log: CheckLog,
    runtime: LangGraphRuntime,
    *,
    thread_id: str,
    tag: str,
) -> None:
    """TX-07 slice: every admitted stimulus ends consumed, none discarded."""

    stimuli = runtime.runtime_store.list_stimuli(thread_id=thread_id)
    log.expect_equal(f"{tag}.stimuli.count", len(stimuli), 3)
    log.expect_equal(
        f"{tag}.stimuli.dispositions",
        sorted(item.disposition for item in stimuli),
        ["consumed", "consumed", "consumed"],
    )
    log.expect_equal(
        f"{tag}.stimuli.kinds",
        sorted(item.kind.value for item in stimuli),
        ["execution_feedback", "execution_feedback", "user_message"],
    )


def _check_checkpoint(
    log: CheckLog,
    runtime: LangGraphRuntime,
    *,
    transaction_id: str,
    tag: str,
) -> None:
    if runtime.turn_engine is None:
        log.expect(f"{tag}.checkpoint.engine_present", False, "turn engine missing")
        return
    state = runtime.turn_engine.get_checkpoint_state(transaction_id=transaction_id)
    record = runtime.registry.get(transaction_id)
    if record is None:
        raise SmokeError(f"transaction vanished from Store: {transaction_id}")
    log.expect_equal(
        f"{tag}.checkpoint.graph_phase",
        state.get("graph_phase"),
        PHASE_TURN_IDLE,
    )
    log.expect_equal(f"{tag}.checkpoint.turn_index", state.get("turn_index"), 3)
    log.expect_equal(
        f"{tag}.checkpoint.delegate_chain",
        state.get("delegate_chain"),
        2,
    )
    log.expect_equal(
        f"{tag}.checkpoint.revision_matches_store",
        int(state.get("transaction_revision", -1)),
        int(record.revision),
    )
    log.expect_equal(
        f"{tag}.checkpoint.wm_matches_store",
        len(state.get("wm_snapshot") or []),
        len(record.wm_entries),
    )
    log.expect(
        f"{tag}.checkpoint.no_pending_delegate",
        state.get("pending_delegate_intent") in (None, {}),
        f"pending={state.get('pending_delegate_intent')!r}",
    )
    log.expect_equal(
        f"{tag}.checkpoint.feedback_matched_pending",
        state.get("feedback_matched_pending"),
        True,
    )
    log.expect(
        f"{tag}.checkpoint.latest_feedback_ref",
        bool(state.get("latest_feedback_ref")),
        f"latest_feedback_ref={state.get('latest_feedback_ref')!r}",
    )


def _check_persistence_files(log: CheckLog, persist_root: Path) -> None:
    store_db = persist_root / "runtime" / "langgraph.sqlite3"
    turn_db = persist_root / "runtime" / "langgraph-turn-checkpoints.sqlite3"
    log.expect("persist.store_db_exists", store_db.is_file(), str(store_db))
    log.expect(
        "persist.turn_checkpoint_db_persistent",
        turn_db.is_file() and turn_db.stat().st_size > 0,
        f"{turn_db} missing or empty; install the checkpoint extra: "
        'pip install -e ".[langgraph]"',
    )


def _check_rollback(log: CheckLog, persist_root: Path, *, round_index: int) -> None:
    """Turn loop off must restore the R1 ``record_progress`` drain."""

    runtime = _open_langgraph_runtime(
        persist_root=persist_root,
        thread_id="r2-rollback",
        owner_id=f"r2-rollback-{round_index}",
        turn_loop=False,
    )
    try:
        log.expect_equal(
            "rollback.turn_loop_enabled",
            runtime.turn_loop_enabled,
            False,
        )
        log.expect_equal(
            "rollback.health_turn_loop",
            runtime.health().get("turn_loop"),
            False,
        )
        runtime.submit_user_message(
            thread_id="r2-rollback",
            conversation_id="r2-rollback::0",
            text=USER_TEXT,
            schedule_drainer=False,
        )
        results = list(runtime.run_thread("r2-rollback").get("results") or [])
        log.expect_equal("rollback.result_count", len(results), 1)
        if not results:
            return
        log.expect_equal(
            "rollback.graph_phase",
            results[0].get("graph_phase"),
            "step_committed",
        )
        log.expect_equal(
            "rollback.thinking_never_called",
            runtime.agent.thinking_agent.calls,
            [],
        )
        record = runtime.registry.get(str(results[0].get("transaction_id") or ""))
        log.expect(
            "rollback.no_delegates",
            record is not None and int(record.delegate_count) == 0,
            f"delegate_count={getattr(record, 'delegate_count', None)}",
        )
    finally:
        runtime.shutdown()


# ---------------------------------------------------------------------------
# Rounds
# ---------------------------------------------------------------------------


def run_round(
    *,
    round_index: int,
    persist_root: Path,
    thread_id: str,
) -> Dict[str, Any]:
    """Run one cold-start turn-loop round, restart, and rollback check."""

    conversation_id = f"{thread_id}::0"
    log = CheckLog()
    started = time.monotonic()
    turn_root = persist_root / "turn-loop"
    runtime = _open_langgraph_runtime(
        persist_root=turn_root,
        thread_id=thread_id,
        owner_id=f"r2-user-{round_index}",
    )
    restarted: Optional[LangGraphRuntime] = None
    surface: Dict[str, Any] = {}
    try:
        log.expect_equal(
            "host.runtime_engine_id",
            runtime.runtime_engine_id,
            LANGGRAPH_RUNTIME_ENGINE,
        )
        log.expect_equal("host.turn_loop_enabled", runtime.turn_loop_enabled, True)

        results = _drive_turn_loop(
            log,
            runtime,
            thread_id=thread_id,
            conversation_id=conversation_id,
            text=USER_TEXT,
            tag="cold",
        )
        if not log.expect_equal("cold.result_count", len(results), 3):
            raise SmokeError(
                "turn loop did not produce the expected three turns: "
                + json.dumps(results, default=str)[:800]
            )
        _check_turn_sequence(log, results, tag="cold")
        transaction_id = str(results[0].get("transaction_id") or "")
        log.expect_equal(
            "cold.single_transaction",
            len({str(item.get("transaction_id") or "") for item in results}),
            1,
        )
        _check_store(log, runtime, transaction_id=transaction_id, tag="cold")
        _check_structured_feedback(log, runtime, tag="cold")
        _check_effect_ledger(log, runtime, transaction_id=transaction_id, tag="cold")
        scene_entries = _check_scene(
            log,
            runtime,
            conversation_id=conversation_id,
            tag="cold",
        )
        _check_dispositions(log, runtime, thread_id=thread_id, tag="cold")
        _check_checkpoint(log, runtime, transaction_id=transaction_id, tag="cold")
        _check_persistence_files(log, turn_root)

        before_restart = domain_surface(
            registry=runtime.registry,
            store=runtime.runtime_store,
            scene_reader=runtime.scene_system.reader,
            thread_id=thread_id,
            conversation_id=conversation_id,
        )
        runtime.shutdown()

        restarted = _open_langgraph_runtime(
            persist_root=turn_root,
            thread_id=thread_id,
            owner_id=f"r2-user-{round_index}",
        )
        surface = domain_surface(
            registry=restarted.registry,
            store=restarted.runtime_store,
            scene_reader=restarted.scene_system.reader,
            thread_id=thread_id,
            conversation_id=conversation_id,
        )
        log.expect_equal("restart.surface_identical", surface, before_restart)
        log.expect_equal(
            "restart.scene_entries",
            len(surface["scene"]),
            scene_entries,
        )
        _check_checkpoint(
            log,
            restarted,
            transaction_id=transaction_id,
            tag="restart",
        )

        _check_rollback(log, persist_root / "rollback", round_index=round_index)
        return {
            "round": round_index,
            "ok": not log.failures,
            "persist_root": str(persist_root),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks_total": len(log.checks),
            "checks_failed": len(log.failures),
            "failures": [item.to_dict() for item in log.failures],
            "checks": [item.to_dict() for item in log.checks],
            "surface": surface,
        }
    except Exception as exc:
        return {
            "round": round_index,
            "ok": False,
            "persist_root": str(persist_root),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks_total": len(log.checks),
            "checks_failed": len(log.failures) + 1,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "failures": [item.to_dict() for item in log.failures],
            "checks": [item.to_dict() for item in log.checks],
            "surface": surface,
        }
    finally:
        for host in (restarted, runtime):
            if host is None:
                continue
            try:
                host.shutdown()
            except Exception:
                pass


def run_smoke(
    *,
    rounds: int,
    persist_root: Path,
    thread_id: str,
    progress: bool = True,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for index in range(1, rounds + 1):
        result = run_round(
            round_index=index,
            persist_root=persist_root / f"round-{index:02d}",
            thread_id=thread_id,
        )
        results.append(result)
        if progress:
            status = "PASS" if result["ok"] else "FAIL"
            print(
                f"[round {index}/{rounds}] {status} "
                f"checks={result['checks_total']} "
                f"failed={result['checks_failed']} "
                f"{result['duration_ms']}ms",
                file=sys.stderr,
            )
    passed = sum(1 for item in results if item["ok"])
    return {
        "gate": "r2_turn_loop",
        "runtime_engine": LANGGRAPH_RUNTIME_ENGINE,
        "rounds": rounds,
        "passed": passed,
        "failed": rounds - passed,
        "thread_id": thread_id,
        "persist_root": str(persist_root),
        "ok": passed == rounds,
        "results": results,
    }


def _summarize(report: Dict[str, Any], *, verbose: bool) -> Dict[str, Any]:
    if verbose:
        return report
    trimmed = dict(report)
    trimmed["results"] = [
        {key: value for key, value in item.items() if key not in {"checks", "surface"}}
        for item in report["results"]
    ]
    return trimmed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="Number of independent repetitions (R2 exit criterion is 3).",
    )
    parser.add_argument(
        "--thread-id",
        default="smoke-langgraph-turn",
        help="Thread id used for the internal conversation.",
    )
    parser.add_argument(
        "--persist-root",
        type=Path,
        help="Persistence root for runtime artifacts. Defaults to a temp dir.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the temp persistence root instead of deleting it.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Also write the full report (including every check) to this path.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Include every individual check in the stdout report.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-round progress lines on stderr.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.rounds < 1:
        print("--rounds must be >= 1", file=sys.stderr)
        return 2

    temp_dir: Optional[tempfile.TemporaryDirectory[str]] = None
    try:
        if args.persist_root is not None:
            persist_root = args.persist_root.resolve()
            persist_root.mkdir(parents=True, exist_ok=True)
        elif args.keep:
            persist_root = Path(tempfile.mkdtemp(prefix="m-agent-lg-turn-")).resolve()
        else:
            temp_dir = tempfile.TemporaryDirectory(
                prefix="m-agent-lg-turn-",
                ignore_cleanup_errors=True,
            )
            persist_root = Path(temp_dir.name).resolve()

        try:
            report = run_smoke(
                rounds=args.rounds,
                persist_root=persist_root,
                thread_id=args.thread_id,
                progress=not args.quiet,
            )
        except SmokeError as exc:
            print(f"smoke setup error: {exc}", file=sys.stderr)
            return 2

        if args.json_out is not None:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        print(
            json.dumps(
                _summarize(report, verbose=args.verbose),
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if report["ok"] else 1
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
