from __future__ import annotations

import json
from pathlib import Path

import pytest

from m_agent.api.chat_dialogue_store import get_dialogue_detail, list_dialogues


def _write_dialogue(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _sample_payload(
    *,
    dialogue_id: str,
    thread_id: str,
    start_time: str,
    text: str,
) -> dict:
    return {
        "dialogue_id": dialogue_id,
        "user_id": "User",
        "participants": ["User", "Assistant"],
        "meta": {
            "start_time": start_time,
            "end_time": start_time,
            "thread_id": thread_id,
            "source": "chat_api_thread_flush",
            "round_count": 1,
        },
        "turns": [
            {
                "turn_id": 0,
                "speaker": "User",
                "text": text,
                "timestamp": start_time,
            },
            {
                "turn_id": 1,
                "speaker": "Assistant",
                "text": f"answer: {text}",
                "timestamp": start_time,
            },
        ],
    }


def test_list_dialogues_applies_visibility_and_thread_mapping(tmp_path: Path) -> None:
    dialogues_dir = tmp_path / "dialogues"
    _write_dialogue(
        dialogues_dir / "2026-04" / "chat_alice_older.json",
        _sample_payload(
            dialogue_id="chat_alice_older",
            thread_id="alice::thread-a",
            start_time="2026-04-01T01:00:00Z",
            text="older",
        ),
    )
    _write_dialogue(
        dialogues_dir / "2026-04" / "chat_alice_newer.json",
        _sample_payload(
            dialogue_id="chat_alice_newer",
            thread_id="alice::thread-a",
            start_time="2026-04-01T02:00:00Z",
            text="newer",
        ),
    )
    _write_dialogue(
        dialogues_dir / "2026-04" / "chat_bob.json",
        _sample_payload(
            dialogue_id="chat_bob",
            thread_id="bob::thread-b",
            start_time="2026-04-01T03:00:00Z",
            text="bob",
        ),
    )

    payload = list_dialogues(dialogues_dir=dialogues_dir, username="alice", limit=10, offset=0)
    ids = [item["dialogue_id"] for item in payload["items"]]
    assert ids == ["chat_alice_newer", "chat_alice_older"]
    assert payload["total"] == 2
    assert payload["items"][0]["thread_id"] == "thread-a"
    assert payload["has_more"] is False

    filtered = list_dialogues(
        dialogues_dir=dialogues_dir,
        username="alice",
        internal_thread_id="alice::thread-a",
        limit=1,
        offset=0,
    )
    assert filtered["total"] == 2
    assert len(filtered["items"]) == 1
    assert filtered["has_more"] is True
    assert filtered["next_offset"] == 1


def test_get_dialogue_detail_respects_user_visibility(tmp_path: Path) -> None:
    dialogues_dir = tmp_path / "dialogues"
    _write_dialogue(
        dialogues_dir / "2026-04" / "chat_alice.json",
        _sample_payload(
            dialogue_id="chat_alice",
            thread_id="alice::thread-a",
            start_time="2026-04-01T01:00:00Z",
            text="hello",
        ),
    )
    _write_dialogue(
        dialogues_dir / "2026-04" / "chat_bob.json",
        _sample_payload(
            dialogue_id="chat_bob",
            thread_id="bob::thread-b",
            start_time="2026-04-01T01:00:00Z",
            text="hi",
        ),
    )

    detail = get_dialogue_detail(
        dialogues_dir=dialogues_dir,
        dialogue_id="chat_alice",
        username="alice",
    )
    assert detail["dialogue_id"] == "chat_alice"
    assert detail["thread_id"] == "thread-a"
    assert detail["turn_count"] == 2

    with pytest.raises(FileNotFoundError):
        get_dialogue_detail(
            dialogues_dir=dialogues_dir,
            dialogue_id="chat_bob",
            username="alice",
        )
