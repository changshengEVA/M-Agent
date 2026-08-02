from __future__ import annotations

from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
)
from m_agent.runtime.perception.matcher_scene_view import (
    format_transaction_scene_view,
    is_user_visible_scene_interaction,
)
from m_agent.runtime.turn_support.think_context import read_scene_segment


def _entry(
    seq: int,
    text: str,
    *,
    transaction_id: str | None,
    actor: SceneActor = SceneActor.USER,
    entry_type: SceneEntryType = SceneEntryType.UTTERANCE,
) -> SceneEntry:
    return SceneEntry(
        seq=seq,
        occurred_at=f"2026-08-01T10:00:{seq:02d}+08:00",
        entry_type=entry_type,
        actor=actor,
        text=text,
        transaction_id=transaction_id,
    )


def _current(text: str) -> StimulusEnvelope:
    return StimulusEnvelope(
        stimulus_id="stim-current",
        thread_id="thread-1",
        conversation_id="conversation-1",
        stimulus=Stimulus(kind=StimulusKind.USER_MESSAGE, text=text),
        occurred_at="2026-08-01T10:01:00+08:00",
    )


def test_transaction_scene_view_keeps_one_interleaved_timeline() -> None:
    entries = [
        _entry(1, "安排和张三开会", transaction_id="durable-a"),
        _entry(2, "精简季度报告", transaction_id="durable-b"),
        _entry(
            3,
            "什么时候方便？",
            transaction_id="durable-a",
            actor=SceneActor.ASSISTANT,
            entry_type=SceneEntryType.REPLY,
        ),
        _entry(
            4,
            "旧事务中用户可见的回复",
            transaction_id="durable-context",
            actor=SceneActor.ASSISTANT,
            entry_type=SceneEntryType.REPLY,
        ),
        _entry(
            5,
            "未绑定的历史用户消息",
            transaction_id=None,
        ),
    ]

    view = format_transaction_scene_view(
        entries,
        candidate_refs_by_transaction_id={
            "durable-a": "candidate_1",
            "durable-b": "candidate_2",
        },
        current_stimulus=_current("周五下午"),
    )

    assert view.index("安排和张三开会") < view.index("精简季度报告")
    assert view.index("精简季度报告") < view.index("什么时候方便？")
    assert view.index("什么时候方便？") < view.index("旧事务中用户可见的回复")
    assert view.index("旧事务中用户可见的回复") < view.index("未绑定的历史用户消息")
    assert view.index("未绑定的历史用户消息") < view.index("CURRENT")
    assert "#00001 |" in view and "tx=candidate_1" in view
    assert "#00002 |" in view and "tx=candidate_2" in view
    assert "#00004 |" in view and "tx=context_1" in view
    assert "#00005 |" in view and "tx=unbound" in view
    assert "CURRENT |" in view and "tx=?" in view
    assert "durable-a" not in view
    assert "durable-b" not in view
    assert "durable-context" not in view


def test_deleted_scene_entries_use_stable_deprecated_refs() -> None:
    entries = [
        _entry(1, "deleted first turn", transaction_id="deleted-a"),
        _entry(2, "ordinary historical turn", transaction_id="context-a"),
        _entry(
            3,
            "deleted first reply",
            transaction_id="deleted-a",
            actor=SceneActor.ASSISTANT,
            entry_type=SceneEntryType.REPLY,
        ),
        _entry(4, "deleted second turn", transaction_id="deleted-b"),
    ]

    view = format_transaction_scene_view(
        entries,
        candidate_refs_by_transaction_id={"live": "candidate_1"},
        deprecated_transaction_ids={"deleted-a", "deleted-b"},
    )

    assert "#00001 |" in view and "tx=deprecated_1" in view
    assert "#00002 |" in view and "tx=context_1" in view
    assert "#00003 |" in view and "tx=deprecated_1" in view
    assert "#00004 |" in view and "tx=deprecated_2" in view
    assert "deprecated_N=deleted historical context" in view
    assert "not selectable/restorable" in view
    assert "deleted-a" not in view
    assert "deleted-b" not in view


def test_transaction_scene_view_excludes_runtime_process_events() -> None:
    entries = [
        _entry(1, "visible user message", transaction_id="durable-a"),
        _entry(
            2,
            "internal reasoning",
            transaction_id="durable-a",
            actor=SceneActor.THINK,
            entry_type=SceneEntryType.THOUGHT,
        ),
        _entry(
            3,
            "internal tool action",
            transaction_id="durable-a",
            actor=SceneActor.WORK,
            entry_type=SceneEntryType.ACTION,
        ),
        _entry(
            4,
            "execution feedback outcome",
            transaction_id="durable-a",
            actor=SceneActor.WORK,
            entry_type=SceneEntryType.OUTCOME,
        ),
        _entry(
            5,
            "visible reply",
            transaction_id="durable-a",
            actor=SceneActor.ASSISTANT,
            entry_type=SceneEntryType.REPLY,
        ),
    ]

    view = format_transaction_scene_view(
        entries,
        candidate_refs_by_transaction_id={"durable-a": "candidate_1"},
    )

    assert "visible user message" in view
    assert "visible reply" in view
    assert "internal reasoning" not in view
    assert "internal tool action" not in view
    assert "execution feedback outcome" not in view


def test_transaction_scene_view_truncation_keeps_latest_complete_rows() -> None:
    entries = [
        _entry(1, "old-" + "x" * 120, transaction_id="durable-a"),
        _entry(2, "middle-" + "y" * 120, transaction_id="durable-a"),
        _entry(3, "latest", transaction_id="durable-a"),
    ]

    view = format_transaction_scene_view(
        entries,
        candidate_refs_by_transaction_id={"durable-a": "candidate_1"},
        current_stimulus=_current("current"),
        max_chars=430,
    )

    assert "latest" in view
    assert "current" in view
    assert "old-" not in view
    assert "older Scene entries omitted" in view


def test_matcher_scene_cap_is_applied_after_internal_events_are_filtered() -> None:
    visible_question = _entry(
        1,
        "visible question before internal work",
        transaction_id="durable-a",
        actor=SceneActor.ASSISTANT,
        entry_type=SceneEntryType.REPLY,
    )
    internal = [
        _entry(
            seq,
            f"internal-{seq}",
            transaction_id="durable-a",
            actor=SceneActor.WORK,
            entry_type=SceneEntryType.OUTCOME,
        )
        for seq in range(2, 52)
    ]
    visible_answer = _entry(52, "visible answer", transaction_id="durable-a")

    class _Reader:
        def entries_since_flush(self, _conversation_id: str) -> list[SceneEntry]:
            return [visible_question, *internal, visible_answer]

    segment = read_scene_segment(
        _Reader(),  # type: ignore[arg-type]
        "conversation-1",
        max_entries=2,
        entry_filter=is_user_visible_scene_interaction,
    )

    assert [entry.text for entry in segment] == [
        "visible question before internal work",
        "visible answer",
    ]
