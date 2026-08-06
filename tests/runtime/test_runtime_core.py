"""Unit tests for runtime contracts, scene logging, and transaction state."""
from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from m_agent.runtime.config import RuntimeConfig, SchedulerConfig
from m_agent.runtime.domain.contracts import (
    PauseReason,
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    StimulusKind,
    TransactionKind,
    TransactionState,
)
from m_agent.layers.perception.contracts import Stimulus
from m_agent.runtime.perception.attributor import TransactionAttributor
from m_agent.runtime.perception.gateway import PerceptionGateway
from m_agent.runtime.perception.inbox import StimulusInbox
from m_agent.runtime.transaction.registry import (
    TransactionRegistry,
    TransactionTransitionError,
)
from m_agent.systems.scene.default.jsonl_store import SceneLogStore, scene_persist_file_stem


def _stimulus(
    *,
    stimulus_id: str,
    thread_id: str,
    kind: StimulusKind,
    payload: dict,
    occurred_at: str,
    text: str = "",
    transaction_id: str | None = None,
    delegate_id: str | None = None,
    activation_id: str | None = None,
    schedule_id: str | None = None,
    schedule_run_id: str | None = None,
    schedule_delivery_id: str | None = None,
    priority_override: int | None = None,
) -> StimulusEnvelope:
    return StimulusEnvelope(
        stimulus_id=stimulus_id,
        thread_id=thread_id,
        conversation_id=f"{thread_id}::0",
        stimulus=Stimulus(
            kind=kind,
            text=text or str(payload.get("text", "") or payload.get("summary", "") or "stimulus"),
            payload=payload,
        ),
        occurred_at=occurred_at,
        transaction_id=transaction_id,
        delegate_id=delegate_id,
        activation_id=activation_id,
        schedule_id=schedule_id,
        schedule_run_id=schedule_run_id,
        schedule_delivery_id=schedule_delivery_id,
        priority_override=priority_override,
    )


def test_transaction_lifecycle() -> None:
    reg = TransactionRegistry()
    tx = reg.create(thread_id="t1", kind=TransactionKind.USER_TASK, priority=10)
    assert tx.state == TransactionState.CONTINUE
    completed = reg.complete(tx.transaction_id)
    assert completed.state == TransactionState.COMPLETE


def test_begin_delegate_is_atomic_and_rejects_terminal_transaction() -> None:
    reg = TransactionRegistry()
    tx = reg.create(thread_id="t1", kind=TransactionKind.USER_TASK)

    delegated = reg.begin_delegate(tx.transaction_id, "dlg_1")

    assert delegated.state == TransactionState.CONTINUE
    assert delegated.delegate_count == 1
    assert delegated.active_delegate_id == "dlg_1"

    # Real pending work prevents logical completion.
    with pytest.raises(TransactionTransitionError, match="pending delegates"):
        reg.complete(tx.transaction_id)
    reg.consume_feedback(
        tx.transaction_id,
        str(delegated.current_activation_id),
        "dlg_1",
    )
    reg.complete(tx.transaction_id)
    with pytest.raises(TransactionTransitionError, match="cannot begin delegate"):
        reg.begin_delegate(tx.transaction_id, "dlg_2")

    terminal = reg.get(tx.transaction_id)
    assert terminal is not None
    assert terminal.delegate_count == 1
    assert terminal.active_delegate_id is None


def test_transaction_wm_isolation() -> None:
    reg = TransactionRegistry()
    a = reg.create(thread_id="t1", kind=TransactionKind.USER_TASK)
    b = reg.create(thread_id="t1", kind=TransactionKind.SCHEDULE)
    a.wm_entries.append({"tool_name": "x", "summary": "a"})
    b.wm_entries.append({"tool_name": "y", "summary": "b"})
    assert a.wm_entries != b.wm_entries
    assert len(reg.get(a.transaction_id).wm_entries) == 1
    assert reg.get(b.transaction_id).wm_entries[0]["tool_name"] == "y"


def test_transaction_task_state_isolation() -> None:
    reg = TransactionRegistry()
    a = reg.create(thread_id="t1", conversation_id="t1::0", kind=TransactionKind.USER_TASK)
    b = reg.create(thread_id="t1", conversation_id="t1::0", kind=TransactionKind.USER_TASK)
    a.task_state.goal = "create schedules"
    b.task_state.goal = "find a restaurant"
    assert a.task_state.goal != b.task_state.goal


def test_scene_chronological_cross_transaction(tmp_path: Path) -> None:
    store = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    e1 = store.append(
        "thread-1",
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:01Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="hello",
            transaction_id="txn_a",
        ),
    )
    e2 = store.append(
        "thread-1",
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:02Z",
            entry_type=SceneEntryType.ACTION,
            actor=SceneActor.WORK,
            text="tool run",
            transaction_id="txn_b",
        ),
    )
    tail = store.tail("thread-1", limit=10)
    assert [x.seq for x in tail] == [e1.seq, e2.seq]
    assert tail[0].transaction_id == "txn_a"
    assert tail[1].transaction_id == "txn_b"
    assert (tmp_path / "thread-1.jsonl").is_file()


def test_scene_is_isolated_by_conversation() -> None:
    store = SceneLogStore(persist_enabled=False)
    entry = SceneEntry(
        seq=0,
        occurred_at="2026-01-01T00:00:01Z",
        entry_type=SceneEntryType.UTTERANCE,
        actor=SceneActor.USER,
        text="first conversation",
    )
    store.append("thread-1::0", entry)
    store.append(
        "thread-1::1",
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:02Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="second conversation",
        ),
    )
    assert [item.text for item in store.tail("thread-1::0")] == ["first conversation"]
    assert [item.text for item in store.tail("thread-1::1")] == ["second conversation"]


def test_scene_persist_scoped_thread_id_is_filesystem_safe(tmp_path: Path) -> None:
    scoped_tid = "runtime_test::demo-thread-1"
    stem = scene_persist_file_stem(scoped_tid)
    assert ":" not in stem
    assert stem == "runtime_test__demo-thread-1"

    store = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    store.append(
        scoped_tid,
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:01Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="hi",
        ),
    )
    path = tmp_path / f"{stem}.jsonl"
    assert path.is_file()

    store2 = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    store2.load_thread_from_disk(scoped_tid)
    tail = store2.tail(scoped_tid, limit=5)
    assert len(tail) == 1
    assert tail[0].text == "hi"


def test_scene_append_after_restart_continues_seq(tmp_path: Path) -> None:
    tid = "thread-restart"
    store = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    store.append(
        tid,
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:01Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="first",
        ),
    )
    store2 = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    store2.append(
        tid,
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:02Z",
            entry_type=SceneEntryType.REPLY,
            actor=SceneActor.ASSISTANT,
            text="second",
        ),
    )
    tail = store2.tail(tid, limit=10)
    assert [entry.seq for entry in tail] == [1, 2]


def test_scene_flush_watermark_persists_across_restart(tmp_path: Path) -> None:
    tid = "thread-flush-meta"
    store = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    store.append(
        tid,
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:01Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="hello",
        ),
    )
    store.mark_flushed(tid, through_seq=1)
    assert store.entries_since_flush(tid) == []

    store2 = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    store2.ensure_thread_loaded(tid)
    assert store2.flush_watermark(tid) == 1
    assert store2.entries_since_flush(tid) == []


def test_scene_conversation_sequence_persists_across_restart(tmp_path: Path) -> None:
    tid = "alice::persistent-thread"
    store = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    store.persist_conversation_seq(tid, 4)

    store2 = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    assert store2.load_conversation_seq(tid) == 4


def test_scene_conversation_sequence_is_inferred_for_existing_files(tmp_path: Path) -> None:
    tid = "alice::existing-thread"
    conversation_id = f"{tid}::2"
    store = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    store.append(
        conversation_id,
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:01Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="still pending",
        ),
    )
    store.persist_conversation_seq(tid, 2)

    store2 = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    assert store2.load_conversation_seq(tid) == 2

    store2.mark_flushed(conversation_id, through_seq=1)
    store3 = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    assert store3.load_conversation_seq(tid) == 3


def test_scene_load_normalizes_duplicate_seq_on_disk(tmp_path: Path) -> None:
    tid = "thread-dup-seq"
    stem = scene_persist_file_stem(tid)
    path = tmp_path / f"{stem}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    SceneEntry(
                        seq=1,
                        occurred_at="2026-01-01T00:00:01Z",
                        entry_type=SceneEntryType.UTTERANCE,
                        actor=SceneActor.USER,
                        text="a",
                    ).to_dict(),
                    ensure_ascii=False,
                ),
                json.dumps(
                    SceneEntry(
                        seq=1,
                        occurred_at="2026-01-01T00:00:02Z",
                        entry_type=SceneEntryType.REPLY,
                        actor=SceneActor.ASSISTANT,
                        text="b",
                    ).to_dict(),
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    store = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    store.ensure_thread_loaded(tid)
    tail = store.tail(tid, limit=10)
    assert [entry.seq for entry in tail] == [1, 2]
    next_entry = store.append(
        tid,
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:03Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="c",
        ),
    )
    assert next_entry.seq == 3


def test_execution_feedback_attribution() -> None:
    reg = TransactionRegistry()
    config = RuntimeConfig(scheduler=SchedulerConfig())
    attr = TransactionAttributor(registry=reg, config=config)
    tx = reg.create(thread_id="t1", kind=TransactionKind.USER_TASK)
    delegated = reg.begin_delegate(tx.transaction_id, "dlg_1")

    stim = _stimulus(
        stimulus_id="s1",
        thread_id="t1",
        kind=StimulusKind.EXECUTION_FEEDBACK,
        payload={"tool_history": [], "summary": "done"},
        occurred_at="2026-01-01T00:00:03Z",
        transaction_id=tx.transaction_id,
        delegate_id="dlg_1",
        activation_id=delegated.current_activation_id,
    )
    resolved, created = attr.resolve(stim)
    assert created is False
    assert resolved.transaction_id == tx.transaction_id


def test_execution_feedback_requires_full_causal_triple() -> None:
    """transaction_id + activation_id + delegate_id must all be present."""

    reg = TransactionRegistry()
    config = RuntimeConfig(scheduler=SchedulerConfig())
    attr = TransactionAttributor(registry=reg, config=config)
    tx = reg.create(thread_id="t1", kind=TransactionKind.USER_TASK)
    delegated = reg.begin_delegate(tx.transaction_id, "dlg_1")
    activation_id = delegated.current_activation_id

    incomplete = {
        "missing_activation": (tx.transaction_id, None, "dlg_1"),
        "missing_delegate": (tx.transaction_id, activation_id, None),
        "missing_transaction": (None, activation_id, "dlg_1"),
    }
    for name, (transaction_id, aid, delegate_id) in incomplete.items():
        with pytest.raises(ValueError):
            attr.resolve(
                _stimulus(
                    stimulus_id=f"s-{name}",
                    thread_id="t1",
                    kind=StimulusKind.EXECUTION_FEEDBACK,
                    payload={"tool_history": [], "summary": "done"},
                    occurred_at="2026-01-01T00:00:03Z",
                    transaction_id=transaction_id,
                    delegate_id=delegate_id,
                    activation_id=aid,
                )
            )


def test_inbox_priority_order() -> None:
    inbox = StimulusInbox()

    def _stim(sid: str, pri: int) -> StimulusEnvelope:
        return _stimulus(
            stimulus_id=sid,
            thread_id="t1",
            kind=StimulusKind.USER_MESSAGE,
            payload={"text": sid},
            occurred_at="2026-01-01T00:00:00Z",
            priority_override=pri,
        )

    inbox.push(_stim("low", 50), priority=50)
    inbox.push(_stim("high", 5), priority=5)
    first = inbox.pop_next("t1")
    assert first is not None
    assert first.stimulus_id == "high"


def test_waiting_execution_is_derived_from_delegate_facts() -> None:
    reg = TransactionRegistry()
    tx = reg.create(thread_id="t1", kind=TransactionKind.USER_TASK)
    waiting = reg.begin_delegate(tx.transaction_id, "dlg-wait")
    assert waiting.state == TransactionState.CONTINUE
    assert waiting.active_delegate_id == "dlg-wait"
    reg.consume_feedback(
        tx.transaction_id,
        str(waiting.current_activation_id),
        "dlg-wait",
    )
    completed = reg.complete(tx.transaction_id)
    assert completed.state == TransactionState.COMPLETE


def test_each_user_message_opens_its_own_transaction() -> None:
    """A running transaction is never implicitly joined by the next message.

    ``is_match_candidate`` excludes ``continue``/running records entirely, so a
    follow-up while the first task is still running opens a fresh USER_TASK and
    moves the active pointer to it. Only ``pause``/``complete`` lines can be
    re-joined, and only through the semantic resolver (see
    :func:`test_semantic_match_reuses_paused_transaction`).
    """

    reg = TransactionRegistry()
    config = RuntimeConfig(scheduler=SchedulerConfig())
    attr = TransactionAttributor(registry=reg, config=config)

    stim1 = _stimulus(
        stimulus_id="s1",
        thread_id="t1",
        kind=StimulusKind.USER_MESSAGE,
        payload={"text": "hello"},
        occurred_at="2026-01-01T00:00:00Z",
    )
    first, created1 = attr.resolve(stim1)
    assert created1 is True

    stim2 = _stimulus(
        stimulus_id="s2",
        thread_id="t1",
        kind=StimulusKind.USER_MESSAGE,
        payload={"text": "follow up"},
        occurred_at="2026-01-01T00:00:01Z",
    )
    second, created2 = attr.resolve(stim2)
    assert created2 is True
    assert second.transaction_id != first.transaction_id
    assert (
        reg.get_active_user_transaction("t1::0").transaction_id
        == second.transaction_id
    )
    assert reg.get(first.transaction_id).state == TransactionState.CONTINUE

    reg.complete(second.transaction_id)
    assert reg.get(second.transaction_id).state == TransactionState.COMPLETE
    assert reg.get_active_user_transaction("t1::0") is None


def test_sourceless_scheduled_plan_uses_persisted_run_binding(
    tmp_path: Path,
) -> None:
    from m_agent.runtime.transaction import (
        TransactionScheduleCoordinator,
    )

    reg = TransactionRegistry(
        persist_path=tmp_path / "sourceless-schedule.sqlite3"
    )
    transaction = reg.create(
        thread_id="t-schedule",
        conversation_id="t-schedule::0",
        kind=TransactionKind.USER_TASK,
    )
    coordinator = TransactionScheduleCoordinator(reg.store)
    registered = coordinator.register(
        schedule_id="schedule-bound",
        schedule_run_id="run-bound",
        transaction_id=transaction.transaction_id,
        due_at="2026-01-02T09:30:00Z",
        expected_transaction_revision=transaction.revision,
    )
    claimed = coordinator.claim(
        registered.run.schedule_run_id,
        expected_run_revision=registered.run.revision,
        expected_transaction_revision=registered.transaction.revision,
    )
    stimulus = _stimulus(
        stimulus_id="scheduled-bound",
        thread_id="t-schedule",
        kind=StimulusKind.SCHEDULED_PLAN,
        text="run the bound plan",
        payload={
            "schedule_id": registered.run.schedule_id,
            "run_id": registered.run.schedule_run_id,
            "delivery_id": claimed.delivery_id,
        },
        occurred_at="2026-01-02T09:30:00Z",
        schedule_id=registered.run.schedule_id,
        schedule_run_id=registered.run.schedule_run_id,
        schedule_delivery_id=claimed.delivery_id,
    )

    resolved, created = TransactionAttributor(
        registry=reg,
        config=RuntimeConfig(),
    ).resolve(stimulus)

    assert created is False
    assert resolved.transaction_id == transaction.transaction_id
    assert resolved.current_activation_id == claimed.activation_id


def test_registering_multiple_schedule_intents_keeps_one_dormant_task(
    tmp_path: Path,
) -> None:
    from m_agent.runtime.transaction import (
        TransactionScheduleCoordinator,
    )

    reg = TransactionRegistry(
        persist_path=tmp_path / "multiple-schedule-intents.sqlite3"
    )
    transaction = reg.create(
        thread_id="t-multiple-schedules",
        conversation_id="t-multiple-schedules::0",
        kind=TransactionKind.USER_TASK,
    )
    transaction = reg.record_schedule_intent(
        transaction.transaction_id,
        schedule_id="schedule-one",
        due_at="2026-08-03T09:00:00Z",
    )
    transaction = reg.record_schedule_intent(
        transaction.transaction_id,
        schedule_id="schedule-two",
        due_at="2026-08-04T09:00:00Z",
    )
    coordinator = TransactionScheduleCoordinator(reg.store)

    first = coordinator.register(
        schedule_id="schedule-one",
        transaction_id=transaction.transaction_id,
        due_at="2026-08-03T09:00:00Z",
        expected_transaction_revision=transaction.revision,
    )
    second = coordinator.register(
        schedule_id="schedule-two",
        transaction_id=transaction.transaction_id,
        due_at="2026-08-04T09:00:00Z",
        expected_transaction_revision=first.transaction.revision,
    )

    assert second.transaction.state == TransactionState.PAUSE
    assert second.transaction.pause_reason == PauseReason.SCHEDULED_WAIT
    assert second.transaction.pending_schedule_intents == []
    assert len(
        coordinator.list_for_transaction(transaction.transaction_id)
    ) == 2


def test_register_adopts_run_created_by_immediate_due_race(
    tmp_path: Path,
) -> None:
    from m_agent.runtime.transaction import (
        TransactionScheduleCoordinator,
        stable_schedule_run_id,
    )

    reg = TransactionRegistry(
        persist_path=tmp_path / "immediate-due-race.sqlite3"
    )
    transaction = reg.create(
        thread_id="t-immediate-due",
        conversation_id="t-immediate-due::0",
        kind=TransactionKind.USER_TASK,
    )
    transaction = reg.record_schedule_intent(
        transaction.transaction_id,
        schedule_id="schedule-immediate",
        due_at="2026-08-02T09:00:00Z",
    )
    coordinator = TransactionScheduleCoordinator(reg.store)
    run_id = stable_schedule_run_id(
        "schedule-immediate",
        transaction.transaction_id,
        "2026-08-02T09:00:00Z",
    )
    coordinator.ensure_run(
        schedule_id="schedule-immediate",
        schedule_run_id=run_id,
        transaction_id=transaction.transaction_id,
        conversation_id=transaction.conversation_id,
        due_at="2026-08-02T09:00:00Z",
    )

    adopted = coordinator.register(
        schedule_id="schedule-immediate",
        schedule_run_id=run_id,
        transaction_id=transaction.transaction_id,
        due_at="2026-08-02T09:00:00Z",
        expected_transaction_revision=transaction.revision,
    )

    assert adopted.outcome == "adopted_existing"
    assert adopted.transaction.state == TransactionState.PAUSE
    assert adopted.transaction.pause_reason == PauseReason.SCHEDULED_WAIT
    assert adopted.transaction.pending_schedule_intents == []


@pytest.mark.parametrize(
    ("delivered_due_at", "run_due_at", "error_pattern"),
    [
        pytest.param(
            "2026-08-02T09:01:00Z",
            "2026-08-02T09:00:00Z",
            "due_at binding mismatch",
            id="due-at-mismatch",
        ),
        pytest.param(
            "2026-08-02T09:00:00Z",
            "2026-08-02T09:01:00Z",
            "run_id binding mismatch",
            id="run-id-mismatch",
        ),
    ],
)
def test_pending_schedule_binding_mismatch_is_rejected_without_mutation(
    tmp_path: Path,
    delivered_due_at: str,
    run_due_at: str,
    error_pattern: str,
) -> None:
    from m_agent.runtime.transaction import (
        stable_schedule_delivery_id,
        stable_schedule_run_id,
    )

    intent_due_at = "2026-08-02T09:00:00Z"
    schedule_id = "schedule-binding-guard"
    reg = TransactionRegistry(
        persist_path=tmp_path / f"{error_pattern[:3]}-binding.sqlite3"
    )
    transaction = reg.create(
        thread_id="t-binding-guard",
        conversation_id="t-binding-guard::0",
        kind=TransactionKind.USER_TASK,
    )
    transaction = reg.record_schedule_intent(
        transaction.transaction_id,
        schedule_id=schedule_id,
        due_at=intent_due_at,
        owner_id="owner-binding-guard",
    )
    run_id = stable_schedule_run_id(
        schedule_id,
        transaction.transaction_id,
        run_due_at,
    )
    delivery_id = stable_schedule_delivery_id(run_id, 1)
    before = reg.get(transaction.transaction_id)
    assert before is not None
    assert before.state == TransactionState.CONTINUE
    assert before.current_activation_id
    assert before.pending_schedule_intents
    before_transaction = before.to_dict()
    before_activations = [
        item.to_dict()
        for item in reg.store.list_activations(transaction.transaction_id)
    ]
    before_runs = reg.store.list_schedule_runs()
    stimulus = _stimulus(
        stimulus_id=f"stim-{error_pattern[:3]}-binding",
        thread_id=transaction.thread_id,
        kind=StimulusKind.SCHEDULED_PLAN,
        text="run mismatched scheduled objective",
        payload={
            "owner_id": "owner-binding-guard",
            "schedule_id": schedule_id,
            "schedule_run_id": run_id,
            "schedule_delivery_id": delivery_id,
            "transaction_id": transaction.transaction_id,
            "due_at_utc": delivered_due_at,
        },
        occurred_at=delivered_due_at,
        transaction_id=transaction.transaction_id,
        schedule_id=schedule_id,
        schedule_run_id=run_id,
        schedule_delivery_id=delivery_id,
    )

    with pytest.raises(ValueError, match=error_pattern):
        TransactionAttributor(
            registry=reg,
            config=RuntimeConfig(),
        ).resolve(stimulus)

    after = reg.get(transaction.transaction_id)
    assert after is not None
    assert after.revision == before.revision
    assert after.state == before.state
    assert after.current_activation_id == before.current_activation_id
    assert after.pending_schedule_intents == before.pending_schedule_intents
    assert after.to_dict() == before_transaction
    assert [
        item.to_dict()
        for item in reg.store.list_activations(transaction.transaction_id)
    ] == before_activations
    assert reg.store.list_schedule_runs() == before_runs


def test_scheduled_plan_due_at_utc_is_persisted_and_reopens_origin_transaction(
    tmp_path: Path,
) -> None:
    from m_agent.runtime.transaction import (
        ScheduleRunStatus,
        stable_schedule_delivery_id,
        stable_schedule_run_id,
    )

    reg = TransactionRegistry(
        persist_path=tmp_path / "origin-schedule.sqlite3"
    )
    transaction = reg.create(
        thread_id="t-origin",
        conversation_id="t-origin::0",
        kind=TransactionKind.USER_TASK,
    )
    completed = reg.complete(transaction.transaction_id)
    due_at = "2026-01-02T09:30:00Z"
    run_id = stable_schedule_run_id(
        "schedule-origin",
        completed.transaction_id,
        due_at,
    )
    delivery_id = stable_schedule_delivery_id(run_id, 1)
    stimulus = _stimulus(
        stimulus_id="scheduled-origin",
        thread_id="t-origin",
        kind=StimulusKind.SCHEDULED_PLAN,
        text="schedule_due",
        payload={
            "schedule_id": "schedule-origin",
            "run_id": run_id,
            "schedule_delivery_id": delivery_id,
            "transaction_id": completed.transaction_id,
            "owner_id": "owner-origin",
            "due_at_utc": due_at,
        },
        occurred_at=due_at,
        transaction_id=completed.transaction_id,
        schedule_id="schedule-origin",
        schedule_run_id=run_id,
        schedule_delivery_id=delivery_id,
    )

    resolved, created = TransactionAttributor(
        registry=reg,
        config=RuntimeConfig(),
    ).resolve(stimulus)

    assert created is False
    assert resolved.transaction_id == completed.transaction_id
    assert resolved.state == TransactionState.CONTINUE
    assert resolved.correlation.schedule_owner_id == "owner-origin"
    persisted_run = reg.store.load_schedule_run(run_id)
    assert persisted_run is not None
    assert persisted_run["due_at"] == due_at
    assert persisted_run["status"] == ScheduleRunStatus.CLAIMED.value
    assert persisted_run["schedule_delivery_id"] == delivery_id


def test_sourceless_external_schedule_run_creates_schedule_transaction() -> None:
    reg = TransactionRegistry()
    stimulus = _stimulus(
        stimulus_id="scheduled-external",
        thread_id="t-external",
        kind=StimulusKind.SCHEDULED_PLAN,
        text="run external schedule",
        payload={
            "schedule_id": "schedule-external",
            "run_id": "run-external",
            "owner_id": "owner-external",
        },
        occurred_at="2026-01-02T09:30:00Z",
        schedule_id="schedule-external",
        schedule_run_id="run-external",
    )

    resolved, created = TransactionAttributor(
        registry=reg,
        config=RuntimeConfig(),
    ).resolve(stimulus)

    assert created is True
    assert resolved.kind == TransactionKind.SCHEDULE
    assert resolved.correlation.schedule_id == "schedule-external"
    assert resolved.correlation.schedule_run_id == "run-external"


def test_schedule_run_binding_rejects_conflicting_explicit_transaction(
    tmp_path: Path,
) -> None:
    from m_agent.runtime.transaction import (
        TransactionScheduleCoordinator,
    )

    reg = TransactionRegistry(
        persist_path=tmp_path / "conflicting-schedule.sqlite3"
    )
    bound = reg.create(
        thread_id="t-conflict",
        conversation_id="t-conflict::0",
        kind=TransactionKind.USER_TASK,
    )
    other = reg.create(
        thread_id="t-conflict",
        conversation_id="t-conflict::0",
        kind=TransactionKind.USER_TASK,
    )
    coordinator = TransactionScheduleCoordinator(reg.store)
    registered = coordinator.register(
        schedule_id="schedule-conflict",
        schedule_run_id="run-conflict",
        transaction_id=bound.transaction_id,
        due_at="2026-01-02T09:30:00Z",
        expected_transaction_revision=bound.revision,
    )
    claimed = coordinator.claim(
        registered.run.schedule_run_id,
        expected_run_revision=registered.run.revision,
        expected_transaction_revision=registered.transaction.revision,
    )
    stimulus = _stimulus(
        stimulus_id="scheduled-conflict",
        thread_id="t-conflict",
        kind=StimulusKind.SCHEDULED_PLAN,
        text="conflicting source",
        payload={
            "schedule_id": registered.run.schedule_id,
            "run_id": registered.run.schedule_run_id,
            "delivery_id": claimed.delivery_id,
        },
        occurred_at="2026-01-02T09:30:00Z",
        transaction_id=other.transaction_id,
        schedule_id=registered.run.schedule_id,
        schedule_run_id=registered.run.schedule_run_id,
        schedule_delivery_id=claimed.delivery_id,
    )

    with pytest.raises(
        ValueError,
        match="run is bound to another transaction",
    ):
        TransactionAttributor(
            registry=reg,
            config=RuntimeConfig(),
        ).resolve(stimulus)


def test_schedule_gateway_deduplicates_delivery_by_run_id(
    tmp_path: Path,
) -> None:
    from m_agent.systems.scene.default import SceneWriterAdapter

    reg = TransactionRegistry(
        persist_path=tmp_path / "schedule-ingress.sqlite3"
    )
    inbox = StimulusInbox(store=reg.store)
    gateway = PerceptionGateway(
        inbox=inbox,
        attributor=TransactionAttributor(
            registry=reg,
            config=RuntimeConfig(),
        ),
        scene_writer=SceneWriterAdapter(
            SceneLogStore(persist_enabled=False)
        ),
    )

    first_id = gateway.submit_heartbeat(
        thread_id="t-ingress",
        conversation_id="t-ingress::0",
        schedule_id="schedule-ingress",
        text="run once",
        payload={"run_id": "run-ingress"},
    )
    replay_id = gateway.submit_heartbeat(
        thread_id="t-ingress",
        conversation_id="t-ingress::0",
        schedule_id="schedule-ingress",
        text="run once",
        payload={"run_id": "run-ingress"},
    )

    assert replay_id == first_id
    assert inbox.pending_count("t-ingress") == 1


def test_semantic_match_reuses_paused_transaction() -> None:
    """A paused line is re-joined only when the semantic resolver picks it."""

    reg = TransactionRegistry()
    config = RuntimeConfig(scheduler=SchedulerConfig())
    first = reg.create(
        thread_id="t1",
        conversation_id="t1::0",
        kind=TransactionKind.USER_TASK,
    )
    reg.pause(first.transaction_id)
    reg.set_active_user_transaction("t1::0", first.transaction_id)

    stim = _stimulus(
        stimulus_id="s2",
        thread_id="t1",
        kind=StimulusKind.USER_MESSAGE,
        payload={"text": "follow-up on the same task"},
        occurred_at="2026-01-01T00:00:01Z",
    )

    declining = TransactionAttributor(
        registry=reg,
        config=config,
        semantic_resolver=lambda _stimulus, _candidates: None,
    )
    fresh, created_fresh = declining.resolve(stim)
    assert created_fresh is True
    assert fresh.transaction_id != first.transaction_id

    matching = TransactionAttributor(
        registry=reg,
        config=config,
        semantic_resolver=lambda _stimulus, _candidates: first.transaction_id,
    )
    matched, created_matched = matching.resolve(stim)
    assert created_matched is False
    assert matched.transaction_id == first.transaction_id
    assert matched.state == TransactionState.CONTINUE


def test_semantic_match_revalidates_candidate_after_concurrent_delete() -> None:
    reg = TransactionRegistry()
    candidate = reg.create(
        thread_id="t-match-delete",
        conversation_id="t-match-delete::0",
        kind=TransactionKind.USER_TASK,
    )
    candidate = reg.pause(candidate.transaction_id)
    resolver_entered = threading.Event()
    release_resolver = threading.Event()

    def _resolver(_stimulus, _candidates, **_kwargs):
        resolver_entered.set()
        assert release_resolver.wait(timeout=2)
        return "candidate_1"

    attributor = TransactionAttributor(
        registry=reg,
        config=RuntimeConfig(),
        semantic_resolver=_resolver,
    )
    stimulus = _stimulus(
        stimulus_id="stim-match-delete",
        thread_id="t-match-delete",
        kind=StimulusKind.USER_MESSAGE,
        text="continue the old task",
        payload={},
        occurred_at="2026-01-01T00:00:00Z",
    )
    outcome: dict = {}

    def _resolve() -> None:
        try:
            record, created = attributor.resolve(stimulus)
            outcome.update(record=record, created=created)
        except Exception as exc:  # pragma: no cover - asserted below
            outcome["error"] = exc

    worker = threading.Thread(target=_resolve)
    worker.start()
    assert resolver_entered.wait(timeout=2)
    reg.delete_with_cleanup(
        candidate.transaction_id,
        expected_revision=candidate.revision,
        transition_id="delete-during-semantic-match",
        idempotency_key="delete-during-semantic-match",
    )
    release_resolver.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert "error" not in outcome
    assert outcome["created"] is True
    assert outcome["record"].transaction_id != candidate.transaction_id
    deleted = reg.store.load_transaction(candidate.transaction_id)
    assert deleted is not None and deleted.deleted


def test_semantic_attribution_can_create_new_transaction() -> None:
    reg = TransactionRegistry()
    config = RuntimeConfig()
    first = reg.create(
        thread_id="t1",
        conversation_id="t1::0",
        kind=TransactionKind.USER_TASK,
    )
    reg.set_active_user_transaction("t1::0", first.transaction_id)
    attr = TransactionAttributor(
        registry=reg,
        config=config,
        semantic_resolver=lambda _stimulus, _candidates: None,
    )
    second, created = attr.resolve(
        _stimulus(
            stimulus_id="s-new",
            thread_id="t1",
            kind=StimulusKind.USER_MESSAGE,
            text="start an unrelated task",
            payload={},
            occurred_at="2026-01-01T00:00:04Z",
        )
    )
    assert created is True
    assert second.transaction_id != first.transaction_id


def test_gateway_defers_sourceless_user_utterance_to_the_scheduler() -> None:
    """A sourceless utterance has no transaction yet, so Scene waits for AT.

    Writing at ingress would orphan the entry under a null ``transaction_id``,
    and ``append_id`` replay is idempotent so it could never be repaired. The
    scheduler loop appends it once attribution has bound a transaction.
    """

    store = SceneLogStore(persist_enabled=False)
    from m_agent.systems.scene.default import SceneWriterAdapter

    reg = TransactionRegistry()
    config = RuntimeConfig()
    attr = TransactionAttributor(registry=reg, config=config)
    inbox = StimulusInbox()
    gw = PerceptionGateway(
        inbox=inbox,
        attributor=attr,
        scene_writer=SceneWriterAdapter(store),
    )
    gw.submit_user_message(thread_id="t1", conversation_id="t1::0", text="hi there")
    assert store.tail("t1::0", limit=5) == []
    assert inbox.pending_count("t1") == 1


def test_gateway_writes_scene_for_already_attributed_utterance() -> None:
    store = SceneLogStore(persist_enabled=False)
    from m_agent.systems.scene.default import SceneWriterAdapter

    reg = TransactionRegistry()
    config = RuntimeConfig()
    attr = TransactionAttributor(registry=reg, config=config)
    tx = reg.create(
        thread_id="t1",
        conversation_id="t1::0",
        kind=TransactionKind.USER_TASK,
    )
    gw = PerceptionGateway(
        inbox=StimulusInbox(),
        attributor=attr,
        scene_writer=SceneWriterAdapter(store),
    )
    gw.submit_user_message(
        thread_id="t1",
        conversation_id="t1::0",
        text="hi there",
        payload={},
    )
    assert store.tail("t1::0", limit=5) == []

    gw.submit(
        _stimulus(
            stimulus_id="s-attributed",
            thread_id="t1",
            kind=StimulusKind.USER_MESSAGE,
            payload={"text": "hi there"},
            occurred_at="2026-01-01T00:00:00Z",
            transaction_id=tx.transaction_id,
        )
    )
    tail = store.tail("t1::0", limit=5)
    assert len(tail) == 1
    assert tail[0].entry_type == SceneEntryType.UTTERANCE
    assert tail[0].text == "hi there"
    assert tail[0].transaction_id == tx.transaction_id


def test_latest_user_utterance_from_scene() -> None:
    from m_agent.runtime.turn_support.think_context import latest_user_utterance_from_scene

    entries = [
        SceneEntry(
            seq=1,
            occurred_at="2026-01-01T00:00:00Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="晚上好啊",
        ),
        SceneEntry(
            seq=2,
            occurred_at="2026-01-01T00:00:01Z",
            entry_type=SceneEntryType.REPLY,
            actor=SceneActor.ASSISTANT,
            text="晚上好呀",
        ),
        SceneEntry(
            seq=3,
            occurred_at="2026-01-01T00:00:02Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="今天有什么安排吗",
        ),
    ]
    assert latest_user_utterance_from_scene(entries) == "今天有什么安排吗"


def test_read_scene_segment_excludes_flushed_entries(tmp_path: Path) -> None:
    from m_agent.runtime.turn_support.think_context import (
        format_scene_tail,
        read_scene_segment,
    )
    from m_agent.systems.scene.default import SceneReaderAdapter, SceneWriterAdapter

    tid = "seg-thread"
    store = SceneLogStore(persist_dir=tmp_path, persist_enabled=True)
    writer = SceneWriterAdapter(store)
    reader = SceneReaderAdapter(store)
    writer.append(
        tid,
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:01Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="old segment",
        ),
    )
    store.mark_flushed(tid, through_seq=1)
    writer.append(
        tid,
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:02Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="new segment",
        ),
    )
    segment = read_scene_segment(reader, tid, max_entries=40)
    assert len(segment) == 1
    assert segment[0].text == "new segment"
    assert "new segment" in format_scene_tail(segment)
    assert "old segment" not in format_scene_tail(segment)


def test_execution_feedback_perception_includes_pending_user_request() -> None:
    from m_agent.runtime.turn_support.think_context import build_perception_for_stimulus
    from m_agent.systems.scene.default import SceneReaderAdapter, SceneWriterAdapter
    from m_agent.systems.scene.default.jsonl_store import SceneLogStore

    store = SceneLogStore(persist_enabled=False)
    writer = SceneWriterAdapter(store)
    reader = SceneReaderAdapter(store)
    writer.append(
        "t1::0",
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:00Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="今天有什么安排吗",
        ),
    )
    reg = TransactionRegistry()
    tx = reg.create(thread_id="t1", kind=TransactionKind.USER_TASK)
    stim = _stimulus(
        stimulus_id="fb1",
        thread_id="t1",
        kind=StimulusKind.EXECUTION_FEEDBACK,
        payload={"summary": "no schedules found", "delegate_id": "dlg_x"},
        occurred_at="2026-01-01T00:00:03Z",
        transaction_id=tx.transaction_id,
        delegate_id="dlg_x",
    )
    perception = build_perception_for_stimulus(
        transaction=tx,
        stimulus=stim,
        scene_reader=reader,
        scene_context_max_entries=20,
    )
    assert perception.stimulus.payload.get("pending_user_request") == "今天有什么安排吗"
    assert "今天有什么安排吗" in perception.stimulus.text
    assert "no schedules found" in perception.stimulus.text or "Execution note" in perception.stimulus.text


def test_gateway_execution_feedback_does_not_schedule_drainer_by_default() -> None:
    scheduled: list[bool] = []

    def _hook(_stimulus: StimulusEnvelope, *, schedule_drainer: bool = True) -> None:
        scheduled.append(schedule_drainer)

    inbox = StimulusInbox()
    reg = TransactionRegistry()
    config = RuntimeConfig()
    attr = TransactionAttributor(registry=reg, config=config)
    store = SceneLogStore(persist_enabled=False)
    from m_agent.systems.scene.default import SceneWriterAdapter

    tx = reg.create(
        thread_id="t1",
        conversation_id="t1::0",
        kind=TransactionKind.USER_TASK,
    )
    delegated = reg.begin_delegate(tx.transaction_id, "dlg_1")

    gw = PerceptionGateway(
        inbox=inbox,
        attributor=attr,
        scene_writer=SceneWriterAdapter(store),
        on_enqueued=_hook,
    )
    gw.submit_execution_feedback(
        thread_id="t1",
        conversation_id="t1::0",
        transaction_id=tx.transaction_id,
        delegate_id="dlg_1",
        activation_id=delegated.current_activation_id or "",
        tool_history=[],
        summary="done",
    )
    assert scheduled == [False]


def test_gateway_discards_feedback_without_a_causal_source() -> None:
    """An unattributable feedback is refused at admission, not queued."""

    enqueued: list[str] = []
    inbox = StimulusInbox()
    reg = TransactionRegistry()
    attr = TransactionAttributor(registry=reg, config=RuntimeConfig())
    from m_agent.systems.scene.default import SceneWriterAdapter

    gw = PerceptionGateway(
        inbox=inbox,
        attributor=attr,
        scene_writer=SceneWriterAdapter(SceneLogStore(persist_enabled=False)),
        on_enqueued=lambda stimulus, **_kwargs: enqueued.append(
            stimulus.stimulus_id
        ),
    )
    gw.submit_execution_feedback(
        thread_id="t1",
        conversation_id="t1::0",
        transaction_id="txn_never_created",
        delegate_id="dlg_1",
        tool_history=[],
        summary="done",
    )
    assert enqueued == []
    assert inbox.pending_count("t1") == 0


def test_feedback_admission_linearizes_before_delete_cleanup() -> None:
    from m_agent.systems.scene.default import SceneWriterAdapter

    reg = TransactionRegistry()
    inbox = StimulusInbox(store=reg.store)
    attr = TransactionAttributor(registry=reg, config=RuntimeConfig())
    tx = reg.create(
        thread_id="t-feedback-delete",
        conversation_id="t-feedback-delete::0",
        kind=TransactionKind.USER_TASK,
    )
    delegated = reg.begin_delegate(tx.transaction_id, "delegate-delete")
    gateway = PerceptionGateway(
        inbox=inbox,
        attributor=attr,
        scene_writer=SceneWriterAdapter(
            SceneLogStore(persist_enabled=False)
        ),
    )
    original_validate = gateway._feedback_source_is_admissible
    validation_entered = threading.Event()
    release_validation = threading.Event()

    def _blocking_validate(stimulus: StimulusEnvelope) -> bool:
        validation_entered.set()
        assert release_validation.wait(timeout=2)
        return original_validate(stimulus)

    gateway._feedback_source_is_admissible = _blocking_validate  # type: ignore[method-assign]
    admission: dict = {}
    deletion: dict = {}
    feedback_thread = threading.Thread(
        target=lambda: admission.update(
            stimulus_id=gateway.submit_execution_feedback(
                thread_id=tx.thread_id,
                conversation_id=tx.conversation_id,
                transaction_id=tx.transaction_id,
                delegate_id="delegate-delete",
                activation_id=str(delegated.current_activation_id or ""),
                tool_history=[],
                summary="late feedback",
            )
        )
    )
    feedback_thread.start()
    assert validation_entered.wait(timeout=2)

    def _delete() -> None:
        current = reg.store.load_transaction(tx.transaction_id)
        assert current is not None
        deletion.update(
            reg.delete_with_cleanup(
                tx.transaction_id,
                expected_revision=current.revision,
                transition_id="delete-during-feedback-admission",
                idempotency_key="delete-during-feedback-admission",
            )
        )

    delete_thread = threading.Thread(target=_delete)
    delete_thread.start()
    release_validation.set()
    feedback_thread.join(timeout=2)
    delete_thread.join(timeout=2)

    assert not feedback_thread.is_alive()
    assert not delete_thread.is_alive()
    stored = reg.store.load_stimulus(admission["stimulus_id"])
    assert stored is not None
    assert stored.disposition == "aborted"
    assert stored.disposition_stage == "transaction_delete"
    assert admission["stimulus_id"] in deletion["cleanup"]["aborted_stimulus_ids"]
    assert inbox.pending_count(tx.thread_id) == 0


def test_targeted_schedule_after_delete_is_expected_discard() -> None:
    from m_agent.systems.scene.default import SceneWriterAdapter

    reg = TransactionRegistry()
    inbox = StimulusInbox(store=reg.store)
    gateway = PerceptionGateway(
        inbox=inbox,
        attributor=TransactionAttributor(
            registry=reg,
            config=RuntimeConfig(),
        ),
        scene_writer=SceneWriterAdapter(
            SceneLogStore(persist_enabled=False)
        ),
    )
    tx = reg.create(
        thread_id="t-schedule-after-delete",
        conversation_id="t-schedule-after-delete::0",
        kind=TransactionKind.USER_TASK,
    )
    reg.delete_with_cleanup(
        tx.transaction_id,
        expected_revision=tx.revision,
        transition_id="delete-before-schedule-admission",
        idempotency_key="delete-before-schedule-admission",
    )

    stimulus_id = gateway.submit_heartbeat(
        thread_id=tx.thread_id,
        conversation_id=tx.conversation_id,
        schedule_id="schedule-after-delete",
        text="must not run",
        payload={
            "transaction_id": tx.transaction_id,
            "run_id": "run-after-delete",
        },
    )

    stored = reg.store.load_stimulus(stimulus_id)
    assert stored is not None
    assert stored.disposition == "rejected"
    assert stored.pool_state == "terminated"
    assert stored.disposition_stage == "admission"
    assert stored.disposition_reason == "invalid_schedule_source"
    assert inbox.pending_count(tx.thread_id) == 0
