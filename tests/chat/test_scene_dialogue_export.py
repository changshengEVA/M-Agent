"""Scene → Dialogue export keeps v1 turn shape with real chronological timestamps."""
from __future__ import annotations

from datetime import datetime, timezone

from m_agent.chat.chat_memory_persistence import (
    build_dialogue_payload_from_scene_entries,
    scene_entry_to_dialogue_turn,
)
from m_agent.chat.dialogue_import import turns_to_rounds
from m_agent.chat.dialogue_validation import validate_dialogue_payload
from m_agent.runtime.domain.contracts import SceneActor, SceneEntry, SceneEntryType


def _entry(
    *,
    seq: int,
    actor: SceneActor,
    entry_type: SceneEntryType,
    text: str,
    occurred_at: str,
) -> SceneEntry:
    return SceneEntry(
        seq=seq,
        transaction_id="txn-1",
        actor=actor,
        entry_type=entry_type,
        text=text,
        occurred_at=occurred_at,
    )


def test_scene_export_v1_shape_chronological_user_assistant_only() -> None:
    entries = [
        _entry(
            seq=1,
            actor=SceneActor.USER,
            entry_type=SceneEntryType.UTTERANCE,
            text="你好",
            occurred_at="2026-05-29T14:14:03.063233Z",
        ),
        _entry(
            seq=2,
            actor=SceneActor.THINK,
            entry_type=SceneEntryType.THOUGHT,
            text="用户在打招呼",
            occurred_at="2026-05-29T14:14:05.000000Z",
        ),
        _entry(
            seq=3,
            actor=SceneActor.USER,
            entry_type=SceneEntryType.UTTERANCE,
            text="补充一句",
            occurred_at="2026-05-29T14:14:09.000000Z",
        ),
        _entry(
            seq=4,
            actor=SceneActor.ASSISTANT,
            entry_type=SceneEntryType.REPLY,
            text="你好，有什么可以帮你？",
            occurred_at="2026-05-29T14:14:13.000000Z",
        ),
    ]

    payload = build_dialogue_payload_from_scene_entries(
        dialogue_id="chat_demo-thread-1_20260529_141403_063233",
        thread_id="demo-thread-1",
        entries=entries,
        source="chat_api_thread_flush",
        user_name="runtime_test",
        assistant_name="Memory Assistant",
    )

    assert payload["meta"]["version"] == 1
    assert len(payload["turns"]) == 3
    assert set(payload["turns"][0].keys()) <= {"speaker", "text", "turn_id", "timestamp"}
    assert payload["turns"][0]["speaker"] == "runtime_test"
    assert payload["turns"][0]["text"] == "你好"
    assert payload["turns"][0]["timestamp"] == "2026-05-29T14:14:03.063233Z"
    assert payload["turns"][1]["text"] == "补充一句"
    assert payload["turns"][1]["timestamp"].startswith("2026-05-29T14:14:09")
    assert payload["turns"][2]["speaker"] == "Memory Assistant"
    assert payload["turns"][2]["timestamp"].startswith("2026-05-29T14:14:13")

    rounds = turns_to_rounds(
        payload["turns"],
        user_speaker="runtime_test",
        assistant_speaker="Memory Assistant",
    )
    assert len(rounds) == 1
    assert rounds[0]["user_at"] == datetime(2026, 5, 29, 14, 14, 9, tzinfo=timezone.utc)
    assert rounds[0]["assistant_at"] == datetime(2026, 5, 29, 14, 14, 13, tzinfo=timezone.utc)

    ok, summary, errors = validate_dialogue_payload(payload)
    assert ok is True
    assert not errors
    assert summary["round_count"] == 1


def test_scene_entry_to_dialogue_turn_skips_internal_scene_events() -> None:
    entry = _entry(
        seq=7,
        actor=SceneActor.WORK,
        entry_type=SceneEntryType.ACTION,
        text="get_current_time",
        occurred_at="2026-05-29T14:14:06.000000Z",
    )
    assert scene_entry_to_dialogue_turn(entry, user_name="alice", assistant_name="Memory Assistant") is None
