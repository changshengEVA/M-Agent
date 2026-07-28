"""Curated behavioral gates for the 14 runtime migration invariants.

These tests deliberately exercise public boundaries or the narrowest stable
runtime seam. Known runtime gaps call ``pytest.xfail`` only after observing the
gap, so a future fix turns the same test green without creating an XPASS trap.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from m_agent.acceptance.trace import SemanticTrace
from m_agent.api.chat_api_runtime import ChatServiceRuntime
from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS
from m_agent.layers.execution.contracts import ExecutionResult
from m_agent.layers.execution.core import ExecutionAgent
from m_agent.layers.execution.model_provider import ModelProvider
from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.runtime.think_life.config import ThinkLifeConfig, ThinkLifeSchedulerConfig
from m_agent.runtime.think_life.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    TransactionKind,
    TransactionStatus,
)
from m_agent.runtime.think_life.drainer import ThreadDrainerService
from m_agent.runtime.think_life.perception.attributor import TransactionAttributor
from m_agent.runtime.think_life.perception.gateway import PerceptionGateway
from m_agent.runtime.think_life.perception.inbox import StimulusInbox
from m_agent.runtime.think_life.scheduler.delegate import DelegateTarget
from m_agent.runtime.think_life.scheduler.cpu_state import THREAD_CPU_STATE
from m_agent.runtime.think_life.scheduler.loop import ThinkLifeLoop
from m_agent.runtime.think_life.runtime import ThinkLifeRuntime
from m_agent.runtime.think_life.transaction_registry import TransactionRegistry
from m_agent.systems.scene.default.jsonl_store import SceneLogStore


def _stimulus(
    *,
    stimulus_id: str,
    kind: StimulusKind,
    transaction_id: str | None = None,
    delegate_id: str | None = None,
    conversation_id: str = "thread-1::0",
) -> StimulusEnvelope:
    return StimulusEnvelope(
        stimulus_id=stimulus_id,
        thread_id="thread-1",
        conversation_id=conversation_id,
        stimulus=Stimulus(kind=kind, text=stimulus_id, payload={}),
        occurred_at="2026-07-26T00:00:00Z",
        transaction_id=transaction_id,
        delegate_id=delegate_id,
    )


def _minimal_loop(
    *,
    registry: TransactionRegistry,
    inbox: StimulusInbox,
    attributor: Any,
    execution_agent: Any,
    gateway: Any | None = None,
) -> ThinkLifeLoop:
    loop = ThinkLifeLoop(
        config=ThinkLifeConfig(),
        registry=registry,
        inbox=inbox,
        gateway=gateway or MagicMock(),
        attributor=attributor,
        thinking_agent=MagicMock(),
        execution_agent=execution_agent,
        wm_system=MagicMock(),
        scene_writer=MagicMock(),
        scene_reader=MagicMock(),
    )
    loop._should_yield_to_inbox = MagicMock(return_value=False)  # type: ignore[method-assign]
    loop._append_tool_scene = MagicMock()  # type: ignore[method-assign]
    return loop


def test_inv_01_all_stimuli_enter_inbox_before_processing(request: Any) -> None:
    trace = SemanticTrace(scenario_id="INV-01")

    class _RecordingInbox(StimulusInbox):
        def push(self, stimulus: StimulusEnvelope, *, priority: int) -> None:
            trace.record(
                "stimulus.queued",
                phase="ingress",
                source="StimulusInbox",
                stimulus_id=stimulus.stimulus_id,
                stimulus_kind=stimulus.kind.value,
            )
            super().push(stimulus, priority=priority)

    class _RecordingSceneWriter:
        def append(self, conversation_id: str, entry: SceneEntry) -> SceneEntry:
            trace.record(
                "scene.appended",
                phase="ingress",
                source="SceneWriter",
                conversation_id=conversation_id,
                entry_type=entry.entry_type.value,
            )
            return entry

    registry = TransactionRegistry()
    inbox = _RecordingInbox()
    gateway = PerceptionGateway(
        inbox=inbox,
        attributor=TransactionAttributor(
            registry=registry,
            config=ThinkLifeConfig(),
        ),
        scene_writer=_RecordingSceneWriter(),
    )
    user_id = gateway.submit_user_message(
        thread_id="thread-1",
        conversation_id="thread-1::0",
        text="hello",
        schedule_drainer=False,
    )
    gateway.submit_execution_feedback(
        thread_id="thread-1",
        conversation_id="thread-1::0",
        transaction_id="tx-not-resolved-yet",
        delegate_id="dlg-not-resolved-yet",
        tool_history=[],
        schedule_drainer=False,
    )
    gateway.submit_heartbeat(
        thread_id="thread-1",
        conversation_id="thread-1::0",
        schedule_id="schedule-1",
        text="tick",
    )
    trace.attach(request)

    assert inbox.pending_count("thread-1") == 3
    assert registry.count_all() == 0, "transaction attribution must happen after dequeue"
    queued_user = next(
        event
        for event in trace.events
        if event.event_type == "stimulus.queued"
        and event.data.get("stimulus_id") == "stim#1"
    )
    scene_user = next(event for event in trace.events if event.event_type == "scene.appended")
    if scene_user.seq < queued_user.seq:
        pytest.xfail(
            "Known gap: user Scene append currently occurs before inbox admission; "
            "a Scene failure can prevent the stimulus from being queued."
        )
    assert queued_user.seq < scene_user.seq
    assert user_id


def test_inv_02_only_one_drainer_runs_per_thread(request: Any) -> None:
    trace = SemanticTrace(scenario_id="INV-02")
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    pending = {"count": 1}
    pending_lock = threading.Lock()

    def get_pending(_thread_id: str) -> int:
        with pending_lock:
            return pending["count"]

    def drain_fn(_thread_id: str, **_kwargs: Any) -> Dict[str, Any]:
        trace.record("drainer.entered", source="ThreadDrainerService")
        started.set()
        assert release.wait(timeout=2)
        with pending_lock:
            pending["count"] = 0
        finished.set()
        return {}

    service = ThreadDrainerService(
        drain_fn=drain_fn,
        get_pending=get_pending,
        build_emitter=lambda _thread_id: None,
        get_history=lambda _thread_id: None,
    )
    assert service.ensure_running("thread-1") is True
    assert started.wait(timeout=2)
    second_started = service.ensure_running("thread-1")
    trace.record(
        "drainer.second_start_checked",
        source="acceptance",
        accepted=second_started,
    )
    release.set()
    assert finished.wait(timeout=2)
    deadline = time.monotonic() + 2
    while service.active_drainer_count() and time.monotonic() < deadline:
        time.sleep(0.01)
    trace.attach(request)

    assert second_started is False
    assert service.active_drainer_count() == 0
    assert trace.count("drainer.entered") == 1


def test_inv_02_drainer_does_not_lose_wakeup_at_worker_exit(request: Any) -> None:
    trace = SemanticTrace(scenario_id="INV-02-lost-wakeup")
    pending = {"count": 1}
    calls = {"get_pending": 0, "drain": 0}
    lock = threading.Lock()
    exit_window = threading.Event()
    release_exit = threading.Event()

    def get_pending(_thread_id: str) -> int:
        with lock:
            calls["get_pending"] += 1
            call_number = calls["get_pending"]
            captured = pending["count"]
        if call_number == 3:
            # Freeze the worker after its final empty check but before it removes
            # itself from the active-worker map.
            exit_window.set()
            assert release_exit.wait(timeout=2)
        return captured

    def drain_fn(_thread_id: str, **_kwargs: Any) -> Dict[str, Any]:
        with lock:
            calls["drain"] += 1
            pending["count"] = 0
        trace.record("drainer.drained", source="ThreadDrainerService")
        return {}

    service = ThreadDrainerService(
        drain_fn=drain_fn,
        get_pending=get_pending,
        build_emitter=lambda _thread_id: None,
        get_history=lambda _thread_id: None,
    )
    assert service.ensure_running("thread-lost-wakeup") is True
    assert exit_window.wait(timeout=2)
    with lock:
        pending["count"] = 1
    restart_accepted = service.ensure_running("thread-lost-wakeup")
    trace.record(
        "stimulus.arrived_during_worker_exit",
        restart_accepted=restart_accepted,
    )
    release_exit.set()
    deadline = time.monotonic() + 2
    while service.active_drainer_count() and time.monotonic() < deadline:
        time.sleep(0.01)
    with lock:
        remaining = pending["count"]
        drain_calls = calls["drain"]
    trace.record(
        "drainer.exit_snapshot",
        remaining=remaining,
        drain_calls=drain_calls,
    )
    trace.attach(request)

    if remaining:
        pytest.xfail(
            "Known gap: a stimulus arriving between the worker's empty check and "
            "active-map removal can remain queued without a replacement drainer."
        )
    assert remaining == 0


def test_inv_03_feedback_bypasses_semantic_reattribution(request: Any) -> None:
    trace = SemanticTrace(scenario_id="INV-03")
    registry = TransactionRegistry()
    transaction = registry.create(
        thread_id="thread-1",
        conversation_id="thread-1::0",
        kind=TransactionKind.USER_TASK,
    )
    registry.transition(transaction.transaction_id, TransactionStatus.RUNNING)
    registry.set_active_user_transaction(
        transaction.conversation_id,
        transaction.transaction_id,
    )
    registry.begin_delegate(transaction.transaction_id, "delegate-1")
    calls = {"semantic": 0}

    def semantic_resolver(_stimulus: StimulusEnvelope, _candidates: list[Any], **_kwargs: Any) -> str:
        calls["semantic"] += 1
        trace.record("transaction.semantic_attribution", source="semantic_resolver")
        return transaction.transaction_id

    attributor = TransactionAttributor(
        registry=registry,
        config=ThinkLifeConfig(),
        semantic_resolver=semantic_resolver,
    )
    selected, created = attributor.resolve(
        _stimulus(stimulus_id="user-follow-up", kind=StimulusKind.USER_MESSAGE)
    )
    assert selected.transaction_id == transaction.transaction_id
    assert created is False
    calls_before_feedback = calls["semantic"]

    selected_feedback, feedback_created = attributor.resolve(
        _stimulus(
            stimulus_id="feedback-1",
            kind=StimulusKind.EXECUTION_FEEDBACK,
            transaction_id=transaction.transaction_id,
            delegate_id="delegate-1",
        )
    )
    trace.record(
        "feedback.attributed",
        source="TransactionAttributor",
        transaction_id=selected_feedback.transaction_id,
        delegate_id="delegate-1",
    )
    trace.attach(request)

    assert feedback_created is False
    assert selected_feedback.transaction_id == transaction.transaction_id
    assert calls["semantic"] == calls_before_feedback


def test_inv_04_feedback_requires_transaction_and_active_delegate(request: Any) -> None:
    trace = SemanticTrace(scenario_id="INV-04")
    registry = TransactionRegistry()
    transaction = registry.create(
        thread_id="thread-1",
        conversation_id="thread-1::0",
        kind=TransactionKind.USER_TASK,
    )
    registry.transition(transaction.transaction_id, TransactionStatus.RUNNING)
    registry.begin_delegate(transaction.transaction_id, "delegate-active")
    attributor = TransactionAttributor(registry=registry, config=ThinkLifeConfig())

    valid, created = attributor.resolve(
        _stimulus(
            stimulus_id="feedback-valid",
            kind=StimulusKind.EXECUTION_FEEDBACK,
            transaction_id=transaction.transaction_id,
            delegate_id="delegate-active",
        )
    )
    trace.record(
        "feedback.accepted",
        transaction_id=valid.transaction_id,
        delegate_id="delegate-active",
    )
    assert created is False

    invalid = (
        _stimulus(
            stimulus_id="feedback-missing-tx",
            kind=StimulusKind.EXECUTION_FEEDBACK,
            delegate_id="delegate-active",
        ),
        _stimulus(
            stimulus_id="feedback-unknown-tx",
            kind=StimulusKind.EXECUTION_FEEDBACK,
            transaction_id="missing",
            delegate_id="delegate-active",
        ),
        _stimulus(
            stimulus_id="feedback-wrong-delegate",
            kind=StimulusKind.EXECUTION_FEEDBACK,
            transaction_id=transaction.transaction_id,
            delegate_id="delegate-stale",
        ),
    )
    for stimulus in invalid:
        with pytest.raises(ValueError):
            attributor.resolve(stimulus)
        trace.record(
            "feedback.rejected",
            transaction_id=stimulus.transaction_id,
            delegate_id=stimulus.delegate_id,
        )
    trace.attach(request)

    assert trace.count("feedback.accepted") == 1
    assert trace.count("feedback.rejected") == 3


def test_inv_05_transaction_runtime_state_is_isolated(request: Any) -> None:
    trace = SemanticTrace(scenario_id="INV-05")
    registry = TransactionRegistry()
    first = registry.create(
        thread_id="thread-1",
        conversation_id="thread-1::0",
        kind=TransactionKind.USER_TASK,
    )
    second = registry.create(
        thread_id="thread-1",
        conversation_id="thread-1::0",
        kind=TransactionKind.USER_TASK,
    )
    first.task_state.goal = "first goal"
    first.wm_entries.append({"tool_name": "one"})
    first.turn_count = 3
    first.episode_buffer.append({"note": "first"})
    trace.record(
        "transaction.state_mutated",
        transaction_id=first.transaction_id,
        fields=["task_state", "wm_entries", "turn_count", "episode_buffer"],
    )
    trace.attach(request)

    assert first.task_state is not second.task_state
    assert first.wm_entries is not second.wm_entries
    assert first.episode_buffer is not second.episode_buffer
    assert second.task_state.goal == ""
    assert second.wm_entries == []
    assert second.turn_count == 0
    assert second.episode_buffer == []


def test_inv_05_flush_drains_the_transaction_episode_owner(request: Any) -> None:
    trace = SemanticTrace(scenario_id="INV-05-episode-owner")
    registry = TransactionRegistry()
    record = registry.create(
        thread_id="thread-episode",
        conversation_id="thread-episode::0",
        kind=TransactionKind.USER_TASK,
    )
    registry.transition(record.transaction_id, TransactionStatus.RUNNING)
    registry.set_active_user_transaction(record.conversation_id, record.transaction_id)
    record.episode_buffer.append({"note": "transaction-owned episode"})

    runtime = ThinkLifeRuntime.__new__(ThinkLifeRuntime)
    runtime.registry = registry
    runtime.agent = SimpleNamespace(
        thinking_agent=MagicMock(on_flush=MagicMock(return_value=[]))
    )
    runtime._emit_runtime_updated = MagicMock()  # type: ignore[method-assign]
    result = runtime.on_flush_segment(
        record.thread_id,
        conversation_id=record.conversation_id,
    )
    trace.record(
        "flush.completed",
        transaction_id=record.transaction_id,
        episode_notes_reported=result["episode_notes_drained"],
        episode_notes_remaining=len(record.episode_buffer),
    )
    trace.attach(request)

    if record.episode_buffer:
        pytest.xfail(
            "Known gap: on_flush_segment drains ThinkingAgent's legacy state "
            "registry instead of TransactionRecord.episode_buffer."
        )
    assert result["episode_notes_drained"] == 1


def test_inv_06_scene_is_one_monotonic_conversation_timeline(
    tmp_path: Path,
    request: Any,
) -> None:
    trace = SemanticTrace(scenario_id="INV-06")
    store = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    conversation_id = "thread-1::0"
    entries = []
    for transaction_id, text in (("tx-a", "one"), ("tx-b", "two"), ("tx-a", "three")):
        entry = store.append(
            conversation_id,
            SceneEntry(
                seq=0,
                occurred_at="2026-07-26T00:00:00Z",
                entry_type=SceneEntryType.ACTION,
                actor=SceneActor.WORK,
                text=text,
                transaction_id=transaction_id,
            ),
        )
        entries.append(entry)
        trace.record(
            "scene.appended",
            conversation_id=conversation_id,
            transaction_id=transaction_id,
            scene_seq=entry.seq,
        )
    trace.attach(request)

    assert [entry.seq for entry in entries] == [1, 2, 3]
    assert [entry.text for entry in store.tail(conversation_id, limit=10)] == [
        "one",
        "two",
        "three",
    ]


def test_inv_08_delegate_invokes_at_most_one_capability(request: Any) -> None:
    trace = SemanticTrace(scenario_id="INV-08")
    registry = TransactionRegistry()
    inbox = StimulusInbox()
    execution_agent = MagicMock()
    # An empty registry exercises the compatibility no-args mapping for the
    # deterministic fake capability without invoking a parameter model.
    execution_agent.registry = MagicMock()
    execution_agent.registry.get.return_value = None
    execution_agent.invoke_tool_direct.return_value = ExecutionResult(
        summary="time returned",
        tool_history=[
            {
                "tool_name": "get_current_time",
                "params": {},
                "result": {"success": True, "answer": "12:00"},
            }
        ],
        success=True,
    )
    gateway = MagicMock()
    loop = _minimal_loop(
        registry=registry,
        inbox=inbox,
        attributor=MagicMock(priority_for=MagicMock(return_value=10)),
        execution_agent=execution_agent,
        gateway=gateway,
    )
    record = registry.create(thread_id="thread-1", kind=TransactionKind.USER_TASK)
    registry.transition(record.transaction_id, TransactionStatus.RUNNING)
    stimulus = _stimulus(
        stimulus_id="user-tool",
        kind=StimulusKind.USER_MESSAGE,
        conversation_id=record.conversation_id,
    )
    result = loop._delegate_and_wait(
        record,
        target=DelegateTarget(tool_name="get_current_time"),
        perception=MagicMock(),
        stimulus=stimulus,
    )
    trace.record(
        "delegate.completed",
        transaction_id=record.transaction_id,
        delegate_id=result["delegate_id"],
        invoke_count=execution_agent.invoke_tool_direct.call_count,
    )
    trace.attach(request)

    execution_agent.invoke_tool_direct.assert_called_once()
    gateway.submit_execution_feedback.assert_called_once()
    feedback = gateway.submit_execution_feedback.call_args.kwargs
    assert len(feedback["tool_history"]) == 1
    assert feedback["delegate_id"] == result["delegate_id"]


def test_inv_10_reply_uses_capability_and_audit_path(request: Any) -> None:
    trace = SemanticTrace(scenario_id="INV-10")

    class _Writer:
        def __init__(self) -> None:
            self.entries: list[SceneEntry] = []

        def append(self, conversation_id: str, entry: SceneEntry) -> SceneEntry:
            self.entries.append(entry)
            trace.record(
                "scene.appended",
                conversation_id=conversation_id,
                transaction_id=entry.transaction_id,
                delegate_id=entry.delegate_id,
                tool_name=entry.tool_name,
            )
            return entry

    writer = _Writer()
    replies: list[str] = []
    agent = ExecutionAgent(
        model_provider=ModelProvider(
            model=MagicMock(),
            network_retry_attempts=1,
            network_retry_backoff_seconds=0.0,
        ),
        enabled_capability_names=["reply_to_user"],
        capability_descriptions={"reply_to_user": "Reply through the audited capability."},
        tool_defaults={"__controller__": {"max_calls_per_turn": 1}},
    )
    result = agent.invoke_tool_direct(
        tool_name="reply_to_user",
        tool_input={"message": "hello", "finalize": True},
        thread_id="thread-1",
        correlation_id="delegate-reply",
        think_life_hooks={
            "conversation_id": "thread-1::0",
            "transaction_id": "transaction-reply",
            "delegate_id": "delegate-reply",
            "scene_writer": writer,
            "on_reply": lambda message, **_kwargs: replies.append(message),
        },
    )
    trace.record(
        "capability.finished",
        transaction_id="transaction-reply",
        delegate_id="delegate-reply",
        tool_name="reply_to_user",
        tool_history_count=len(result.tool_history),
    )
    trace.attach(request)

    assert replies == ["hello"]
    assert len(writer.entries) == 1
    if result.tool_call_count == 0:
        pytest.xfail(
            "Known gap: reply_to_user emits the reply and Scene entry but does not "
            "record itself through ControllerCapabilityContext.record_tool_use."
        )
    assert result.tool_names == ["reply_to_user"]
    assert result.tool_call_count == 1


def test_inv_12_preemption_suspends_and_requeues_at_boundary(request: Any) -> None:
    trace = SemanticTrace(scenario_id="INV-12")
    registry = TransactionRegistry()
    inbox = StimulusInbox()
    config = ThinkLifeConfig(
        scheduler=ThinkLifeSchedulerConfig(
            preempt_enabled=True,
            max_preempt_per_stimulus=2,
        )
    )
    attributor = TransactionAttributor(registry=registry, config=config)
    loop = _minimal_loop(
        registry=registry,
        inbox=inbox,
        attributor=attributor,
        execution_agent=MagicMock(),
    )
    loop.config = config
    record = registry.create(
        thread_id="thread-1",
        conversation_id="thread-1::0",
        kind=TransactionKind.USER_TASK,
        priority=50,
    )
    registry.transition(record.transaction_id, TransactionStatus.RUNNING)
    registry.set_active_user_transaction(record.conversation_id, record.transaction_id)
    stimulus = _stimulus(
        stimulus_id="preempted",
        kind=StimulusKind.USER_MESSAGE,
        conversation_id=record.conversation_id,
    )

    result = loop._handle_preempt(stimulus, record, phase="think")
    requeued = inbox.pop_next("thread-1")
    assert requeued is not None
    trace.record(
        "preempt.requeued",
        stimulus_id=requeued.stimulus_id,
        transaction_id=requeued.transaction_id,
        checkpoint=requeued.payload.get("_checkpoint"),
        preempt_count=requeued.payload.get("_preempt_count"),
    )
    resumed, created = attributor.resolve(requeued)
    trace.record(
        "transaction.resolved_after_preempt",
        transaction_id=resumed.transaction_id,
        created=created,
    )
    trace.attach(request)

    assert result["preempted"] is True
    assert registry.get(record.transaction_id).status == TransactionStatus.SUSPENDED
    assert requeued.stimulus_id == stimulus.stimulus_id
    assert requeued.payload["_preempt_count"] == 1
    if resumed.transaction_id != record.transaction_id:
        pytest.xfail(
            "Known gap: the attributor ignores the transaction_id on a requeued "
            "non-feedback stimulus and creates a new transaction."
        )
    assert created is False


def test_inv_13_duplicate_feedback_is_consumed_once(request: Any) -> None:
    trace = SemanticTrace(scenario_id="INV-13")
    registry = TransactionRegistry()
    record = registry.create(
        thread_id="thread-1",
        conversation_id="thread-1::0",
        kind=TransactionKind.USER_TASK,
    )
    registry.transition(record.transaction_id, TransactionStatus.RUNNING)
    registry.begin_delegate(record.transaction_id, "delegate-once")
    attributor = TransactionAttributor(registry=registry, config=ThinkLifeConfig())
    feedback = _stimulus(
        stimulus_id="feedback-duplicate",
        kind=StimulusKind.EXECUTION_FEEDBACK,
        transaction_id=record.transaction_id,
        delegate_id="delegate-once",
    )

    first, created = attributor.resolve(feedback)
    assert created is False
    registry.transition(first.transaction_id, TransactionStatus.RUNNING)
    trace.record(
        "feedback.consumed",
        transaction_id=record.transaction_id,
        delegate_id="delegate-once",
    )
    try:
        attributor.resolve(feedback)
    except ValueError:
        trace.record(
            "feedback.duplicate_rejected",
            transaction_id=record.transaction_id,
            delegate_id="delegate-once",
        )
        trace.attach(request)
        return
    trace.record(
        "feedback.duplicate_accepted",
        transaction_id=record.transaction_id,
        delegate_id="delegate-once",
    )
    trace.attach(request)
    pytest.xfail(
        "Known gap: feedback consumption does not clear or persist the active "
        "delegate key, so an identical feedback can be accepted twice."
    )


def test_inv_14_flush_gate_covers_every_unresolved_obligation(request: Any) -> None:
    trace = SemanticTrace(scenario_id="INV-14")
    runtime = ChatServiceRuntime.__new__(ChatServiceRuntime)
    runtime._threads_lock = threading.Lock()
    runtime._threads = {}
    runtime._think_life_pending_users = {}
    thread_id = "acceptance-flush-gate"

    try:
        THREAD_RUNTIME_STATUS.set_drainer_active(thread_id, True)
        assert runtime._flush_block_reason(thread_id) == "drainer_active"
        trace.record("flush.blocked", reason="drainer_active")
        THREAD_RUNTIME_STATUS.set_drainer_active(thread_id, False)

        THREAD_RUNTIME_STATUS.set_pending_stimuli(thread_id, 2)
        assert runtime._flush_block_reason(thread_id) == "stimuli_queued"
        trace.record("flush.blocked", reason="stimuli_queued")
        THREAD_RUNTIME_STATUS.set_pending_stimuli(thread_id, 0)

        THREAD_CPU_STATE.set_in_flight(
            thread_id,
            stimulus_id="stimulus-in-flight",
            transaction_id="transaction-in-flight",
        )
        assert runtime._flush_block_reason(thread_id) == "stimulus_in_flight"
        trace.record("flush.blocked", reason="stimulus_in_flight")
        THREAD_CPU_STATE.clear_in_flight(
            thread_id,
            stimulus_id="stimulus-in-flight",
        )

        runtime._think_life_pending_users[thread_id] = [{"text": "waiting"}]
        assert runtime._flush_block_reason(thread_id) == "reply_pending"
        trace.record("flush.blocked", reason="reply_pending")
        runtime._think_life_pending_users.clear()

        assert runtime._flush_block_reason(thread_id) is None
        trace.record("flush.allowed")
        trace.attach(request)
    finally:
        THREAD_RUNTIME_STATUS.set_drainer_active(thread_id, False)
        THREAD_RUNTIME_STATUS.set_pending_stimuli(thread_id, 0)
        THREAD_CPU_STATE.clear_in_flight(thread_id)

    assert trace.count("flush.blocked") == 4
    trace.assert_before("flush.blocked", "flush.allowed")
