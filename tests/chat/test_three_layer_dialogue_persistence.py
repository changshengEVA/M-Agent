from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from m_agent.chat.three_layer_chat_agent import ThreeLayerChatAgent


def test_persist_dialogue_payload_indexes_frozen_episode_notes() -> None:
    agent = ThreeLayerChatAgent.__new__(ThreeLayerChatAgent)
    archive = MagicMock()
    archive.persist_dialogue_payload.return_value = {
        "success": True,
        "dialogue_id": "dialogue-1",
    }
    backend = MagicMock()
    backend.persistence = None
    backend.persist_dialogue.return_value = {"success": True, "chunks_added": 1}
    agent.memory_persistence = archive
    agent.systems = SimpleNamespace(
        episodic=SimpleNamespace(backend=backend),
    )
    agent.user_name = "Alice"
    agent.assistant_name = "M-Agent"
    episode_notes = [
        {
            "note_id": "tx-1:episode_note",
            "note": "Alice prefers concise answers.",
            "turn_meta": {"source": "committed_scene", "scene_seq": 3},
        }
    ]
    dialogue = {
        "dialogue_id": "dialogue-1",
        "user_id": "Alice",
        "participants": ["Alice", "M-Agent"],
        "meta": {
            "thread_id": "thread-1",
            "trace_summary": {"episode_notes": episode_notes},
        },
        "turns": [
            {
                "speaker": "Alice",
                "text": "Please be brief.",
                "timestamp": "2026-08-05T00:00:00Z",
                "entry_type": "utterance",
                "actor": "user",
            },
            {
                "speaker": "M-Agent",
                "text": "Understood.",
                "timestamp": "2026-08-05T00:00:01Z",
                "entry_type": "reply",
                "actor": "assistant",
            },
        ],
    }

    result = agent.persist_dialogue_payload(
        dialogue_payload=dialogue,
        thread_id="thread-1",
    )

    assert result["success"] is True
    archive.persist_dialogue_payload.assert_called_once_with(
        dialogue_payload=dialogue,
        progress_callback=None,
    )
    indexed_rounds = backend.persist_dialogue.call_args.kwargs["rounds"]
    assert len(indexed_rounds) == 1
    assert indexed_rounds[0]["dialogue_id"] == "dialogue-1"
    assert indexed_rounds[0]["episode_notes"] == episode_notes
