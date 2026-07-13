"""Unit tests for Think-life contracts, Scene log, and transaction registry."""
from __future__ import annotations

import json
from pathlib import Path

from m_agent.runtime.think_life.config import ThinkLifeConfig, ThinkLifeSchedulerConfig
from m_agent.runtime.think_life.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    StimulusKind,
    TransactionKind,
    TransactionStatus,
)
from m_agent.layers.perception.contracts import Stimulus
from m_agent.runtime.think_life.perception.attributor import TransactionAttributor
from m_agent.runtime.think_life.perception.gateway import PerceptionGateway
from m_agent.runtime.think_life.perception.inbox import StimulusInbox
from m_agent.runtime.think_life.transaction_registry import TransactionRegistry
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
        priority_override=priority_override,
    )


def test_transaction_lifecycle() -> None:
    reg = TransactionRegistry()
    tx = reg.create(thread_id="t1", kind=TransactionKind.USER_TASK, priority=10)
    assert tx.status == TransactionStatus.PENDING
    reg.transition(tx.transaction_id, TransactionStatus.RUNNING)
    reg.transition(tx.transaction_id, TransactionStatus.WAITING_EXECUTION)
    reg.transition(tx.transaction_id, TransactionStatus.RUNNING)
    reg.transition(tx.transaction_id, TransactionStatus.COMPLETED)
    assert reg.get(tx.transaction_id).status == TransactionStatus.COMPLETED


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
    scoped_tid = "think_life_test::demo-thread-1"
    stem = scene_persist_file_stem(scoped_tid)
    assert ":" not in stem
    assert stem == "think_life_test__demo-thread-1"

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
    config = ThinkLifeConfig(scheduler=ThinkLifeSchedulerConfig())
    attr = TransactionAttributor(registry=reg, config=config)
    tx = reg.create(thread_id="t1", kind=TransactionKind.USER_TASK)
    reg.transition(tx.transaction_id, TransactionStatus.RUNNING)
    tx.active_delegate_id = "dlg_1"
    tx.correlation.delegate_id = "dlg_1"

    stim = _stimulus(
        stimulus_id="s1",
        thread_id="t1",
        kind=StimulusKind.EXECUTION_FEEDBACK,
        payload={"tool_history": [], "summary": "done"},
        occurred_at="2026-01-01T00:00:03Z",
        transaction_id=tx.transaction_id,
        delegate_id="dlg_1",
    )
    resolved, created = attr.resolve(stim)
    assert created is False
    assert resolved.transaction_id == tx.transaction_id


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


def test_waiting_execution_completes_via_running() -> None:
    reg = TransactionRegistry()
    tx = reg.create(thread_id="t1", kind=TransactionKind.USER_TASK)
    reg.transition(tx.transaction_id, TransactionStatus.RUNNING)
    reg.transition(tx.transaction_id, TransactionStatus.WAITING_EXECUTION)
    reg.transition(tx.transaction_id, TransactionStatus.RUNNING)
    reg.transition(tx.transaction_id, TransactionStatus.COMPLETED)
    assert reg.get(tx.transaction_id).status == TransactionStatus.COMPLETED


def test_user_messages_share_transaction_until_flush() -> None:
    reg = TransactionRegistry()
    config = ThinkLifeConfig(scheduler=ThinkLifeSchedulerConfig(preempt_enabled=False))
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
    reg.transition(first.transaction_id, TransactionStatus.RUNNING)

    stim2 = _stimulus(
        stimulus_id="s2",
        thread_id="t1",
        kind=StimulusKind.USER_MESSAGE,
        payload={"text": "follow up"},
        occurred_at="2026-01-01T00:00:01Z",
    )
    second, created2 = attr.resolve(stim2)
    assert created2 is False
    assert second.transaction_id == first.transaction_id

    closed = reg.complete_active_user_transaction("t1::0")
    assert closed == first.transaction_id
    assert reg.get(first.transaction_id).status == TransactionStatus.COMPLETED

    stim3 = _stimulus(
        stimulus_id="s3",
        thread_id="t1",
        kind=StimulusKind.USER_MESSAGE,
        payload={"text": "after flush"},
        occurred_at="2026-01-01T00:00:02Z",
    )
    third, created3 = attr.resolve(stim3)
    assert created3 is True
    assert third.transaction_id != first.transaction_id


def test_preempt_disabled_reuses_active_user_transaction() -> None:
    reg = TransactionRegistry()
    config = ThinkLifeConfig(
        scheduler=ThinkLifeSchedulerConfig(preempt_enabled=False),
    )
    attr = TransactionAttributor(registry=reg, config=config)
    first = reg.create(thread_id="t1", kind=TransactionKind.USER_TASK)
    reg.transition(first.transaction_id, TransactionStatus.RUNNING)
    reg.set_active_user_transaction("t1::0", first.transaction_id)

    stim = _stimulus(
        stimulus_id="s2",
        thread_id="t1",
        kind=StimulusKind.USER_MESSAGE,
        payload={"text": "follow-up"},
        occurred_at="2026-01-01T00:00:01Z",
    )
    second, created = attr.resolve(stim)
    assert created is False
    assert second.transaction_id == first.transaction_id
    assert reg.get(first.transaction_id).status == TransactionStatus.RUNNING


def test_semantic_attribution_can_create_new_transaction() -> None:
    reg = TransactionRegistry()
    config = ThinkLifeConfig()
    first = reg.create(
        thread_id="t1",
        conversation_id="t1::0",
        kind=TransactionKind.USER_TASK,
    )
    reg.transition(first.transaction_id, TransactionStatus.RUNNING)
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


def test_gateway_submits_user_utterance_to_scene() -> None:
    store = SceneLogStore(persist_enabled=False)
    from m_agent.systems.scene.default import SceneReaderAdapter, SceneWriterAdapter

    reg = TransactionRegistry()
    config = ThinkLifeConfig()
    attr = TransactionAttributor(registry=reg, config=config)
    inbox = StimulusInbox()
    gw = PerceptionGateway(
        inbox=inbox,
        attributor=attr,
        scene_writer=SceneWriterAdapter(store),
    )
    gw.submit_user_message(thread_id="t1", conversation_id="t1::0", text="hi there")
    tail = store.tail("t1::0", limit=5)
    assert len(tail) == 1
    assert tail[0].entry_type == SceneEntryType.UTTERANCE
    assert tail[0].text == "hi there"


def test_latest_user_utterance_from_scene() -> None:
    from m_agent.runtime.think_life.scheduler.think_context import latest_user_utterance_from_scene

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
    from m_agent.runtime.think_life.scheduler.think_context import (
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
    from m_agent.runtime.think_life.scheduler.think_context import build_perception_for_stimulus
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
    config = ThinkLifeConfig()
    attr = TransactionAttributor(registry=reg, config=config)
    store = SceneLogStore(persist_enabled=False)
    from m_agent.systems.scene.default import SceneWriterAdapter

    gw = PerceptionGateway(
        inbox=inbox,
        attributor=attr,
        scene_writer=SceneWriterAdapter(store),
        on_enqueued=_hook,
    )
    gw.submit_execution_feedback(
        thread_id="t1",
        conversation_id="t1::0",
        transaction_id="txn_1",
        delegate_id="dlg_1",
        tool_history=[],
        summary="done",
    )
    assert scheduled == [False]
