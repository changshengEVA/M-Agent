from __future__ import annotations

import pytest

from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    TransactionKind,
)
from m_agent.runtime.turn_support.think_context import (
    build_perception_for_stimulus,
    format_scene_tail,
)
from m_agent.runtime.transaction.registry import TransactionRegistry
from m_agent.systems.scene.default import SceneReaderAdapter, SceneWriterAdapter
from m_agent.systems.scene.default.jsonl_store import SceneLogStore


def _scene() -> tuple[SceneReaderAdapter, SceneWriterAdapter]:
    store = SceneLogStore(persist_enabled=False)
    return SceneReaderAdapter(store), SceneWriterAdapter(store)


def _scheduled_stimulus(*, text: str, payload: dict) -> StimulusEnvelope:
    return StimulusEnvelope(
        stimulus_id="stim-schedule",
        thread_id="thread-1",
        conversation_id="conversation-1",
        stimulus=Stimulus(
            kind=StimulusKind.SCHEDULED_PLAN,
            text=text,
            payload=payload,
        ),
        occurred_at="2026-08-01T09:25:33Z",
        schedule_id="sch-1",
    )


def test_typed_schedule_activation_preserves_semantic_roles() -> None:
    reader, _ = _scene()
    transaction = TransactionRegistry().create(
        thread_id="thread-1",
        conversation_id="conversation-1",
        kind=TransactionKind.SCHEDULE,
    )
    stimulus = _scheduled_stimulus(
        text="schedule_due",
        payload={
            "schedule_id": "sch-1",
            "activation": {
                "schema_version": 1,
                "event": {
                    "role": "activation_event",
                    "type": "schedule_due",
                    "source": "heartbeat",
                    "facts": {"due_at_utc": "2026-08-01T09:25:29Z"},
                },
                "objective": {
                    "role": "deferred_objective",
                    "description": "Remind the user to cook",
                    "encoding": "native",
                },
                "evidence": [],
            },
        },
    )

    perception = build_perception_for_stimulus(
        transaction=transaction,
        stimulus=stimulus,
        scene_reader=reader,
        scene_context_max_entries=20,
    )

    assert perception.stimulus.text == "schedule_due"
    assert perception.activation is not None
    assert perception.activation.event.event_type == "schedule_due"
    assert perception.activation.objective is not None
    assert perception.activation.objective.description == "Remind the user to cook"
    assert perception.activation.objective.encoding == "native"
    assert perception.activation.evidence == []


@pytest.mark.parametrize(
    "legacy_text",
    [
        "Remind the user now",
        "The user has already been reminded",
    ],
)
def test_legacy_schedule_wording_is_objective_never_evidence(
    legacy_text: str,
) -> None:
    reader, _ = _scene()
    transaction = TransactionRegistry().create(
        thread_id="thread-1",
        conversation_id="conversation-1",
        kind=TransactionKind.SCHEDULE,
    )

    perception = build_perception_for_stimulus(
        transaction=transaction,
        stimulus=_scheduled_stimulus(text=legacy_text, payload={}),
        scene_reader=reader,
        scene_context_max_entries=20,
    )

    assert perception.stimulus.text == "schedule_due"
    assert perception.activation is not None
    assert perception.activation.objective is not None
    assert perception.activation.objective.description == legacy_text
    assert perception.activation.objective.encoding == "legacy_text"
    assert perception.activation.evidence == []


def test_foreign_internal_scene_entries_remain_context_not_evidence() -> None:
    reader, writer = _scene()
    registry = TransactionRegistry()
    current = registry.create(
        thread_id="thread-1",
        conversation_id="conversation-1",
        kind=TransactionKind.SCHEDULE,
    )
    foreign = registry.create(
        thread_id="thread-1",
        conversation_id="conversation-1",
        kind=TransactionKind.USER_TASK,
    )
    writer.append(
        "conversation-1",
        SceneEntry(
            seq=0,
            occurred_at="2026-08-01T09:24:30Z",
            entry_type=SceneEntryType.THOUGHT,
            actor=SceneActor.THINK,
            text="Foreign task is completed",
            transaction_id=foreign.transaction_id,
        ),
    )
    writer.append(
        "conversation-1",
        SceneEntry(
            seq=0,
            occurred_at="2026-08-01T09:24:31Z",
            entry_type=SceneEntryType.REPLY,
            actor=SceneActor.ASSISTANT,
            text="A prior visible reply",
            transaction_id=foreign.transaction_id,
        ),
    )
    writer.append(
        "conversation-1",
        SceneEntry(
            seq=0,
            occurred_at="2026-08-01T09:25:31Z",
            entry_type=SceneEntryType.THOUGHT,
            actor=SceneActor.THINK,
            text="Current reasoning context",
            transaction_id=current.transaction_id,
        ),
    )

    perception = build_perception_for_stimulus(
        transaction=current,
        stimulus=_scheduled_stimulus(
            text="schedule_due",
            payload={
                "activation": {
                    "event": {"type": "schedule_due", "source": "heartbeat"},
                    "objective": {
                        "description": "Perform the deferred work",
                        "encoding": "native",
                    },
                    "evidence": [],
                }
            },
        ),
        scene_reader=reader,
        scene_context_max_entries=20,
    )

    assert "Foreign task is completed" not in perception.scene_context
    assert "[tx=context_1 assistant/reply]" in perception.scene_context
    assert "[tx=current think/thought]" in perception.scene_context
    assert current.transaction_id not in perception.scene_context
    assert foreign.transaction_id not in perception.scene_context
    assert perception.activation is not None
    assert perception.activation.evidence == []


def test_execution_feedback_becomes_typed_evidence() -> None:
    reader, _ = _scene()
    transaction = TransactionRegistry().create(
        thread_id="thread-1",
        conversation_id="conversation-1",
        kind=TransactionKind.SCHEDULE,
    )
    transaction.task_state.goal = "Inform the user"
    stimulus = StimulusEnvelope(
        stimulus_id="stim-feedback",
        thread_id="thread-1",
        conversation_id="conversation-1",
        stimulus=Stimulus(
            kind=StimulusKind.EXECUTION_FEEDBACK,
            text="reply delivery finished",
            payload={
                "tool_history": [
                    {
                        "tool_name": "reply_to_user",
                        "result": {
                            "success": True,
                            "message": "Time to cook",
                        },
                    }
                ]
            },
        ),
        occurred_at="2026-08-01T09:25:35Z",
        transaction_id=transaction.transaction_id,
        activation_id=transaction.current_activation_id,
        delegate_id="delegate-1",
    )

    perception = build_perception_for_stimulus(
        transaction=transaction,
        stimulus=stimulus,
        scene_reader=reader,
        scene_context_max_entries=20,
    )

    assert perception.activation is not None
    assert perception.activation.objective is not None
    assert perception.activation.objective.description == "Inform the user"
    assert len(perception.activation.evidence) == 1
    evidence = perception.activation.evidence[0]
    assert evidence.evidence_type == "assistant_reply_committed"
    assert evidence.source == "reply_to_user"
    assert evidence.summary == "Time to cook"


def test_current_user_stimulus_is_not_duplicated_in_scene_context() -> None:
    reader, writer = _scene()
    transaction = TransactionRegistry().create(
        thread_id="thread-1",
        conversation_id="conversation-1",
        kind=TransactionKind.USER_TASK,
    )
    writer.append(
        "conversation-1",
        SceneEntry(
            seq=0,
            occurred_at="2026-08-01T09:25:30Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="older context",
            append_id="stim-old",
            transaction_id=transaction.transaction_id,
        ),
    )
    writer.append(
        "conversation-1",
        SceneEntry(
            seq=0,
            occurred_at="2026-08-01T09:25:31Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text="CURRENT-ONLY-ONCE",
            append_id="stim-current",
            transaction_id=transaction.transaction_id,
        ),
    )
    stimulus = StimulusEnvelope(
        stimulus_id="stim-current",
        thread_id="thread-1",
        conversation_id="conversation-1",
        stimulus=Stimulus(
            kind=StimulusKind.USER_MESSAGE,
            text="CURRENT-ONLY-ONCE",
            payload={},
        ),
        occurred_at="2026-08-01T09:25:31Z",
        transaction_id=transaction.transaction_id,
    )

    perception = build_perception_for_stimulus(
        transaction=transaction,
        stimulus=stimulus,
        scene_reader=reader,
        scene_context_max_entries=20,
    )

    assert perception.stimulus.text == "CURRENT-ONLY-ONCE"
    assert "older context" in perception.scene_context
    assert "CURRENT-ONLY-ONCE" not in perception.scene_context


def test_scene_character_budget_keeps_latest_chronological_suffix() -> None:
    entries = [
        SceneEntry(
            seq=index,
            occurred_at=f"2026-08-01T09:25:{index:02d}Z",
            entry_type=SceneEntryType.REPLY,
            actor=SceneActor.ASSISTANT,
            text=("old-entry-" + str(index)) * 8,
            transaction_id="txn-1",
        )
        for index in range(1, 4)
    ]
    entries.append(
        SceneEntry(
            seq=4,
            occurred_at="2026-08-01T09:25:04Z",
            entry_type=SceneEntryType.REPLY,
            actor=SceneActor.ASSISTANT,
            text="LATEST-MUST-SURVIVE",
            transaction_id="txn-1",
        )
    )

    rendered = format_scene_tail(
        entries,
        current_transaction_id="txn-1",
        max_chars=180,
    )

    assert "LATEST-MUST-SURVIVE" in rendered
    assert "old-entry-1" not in rendered
