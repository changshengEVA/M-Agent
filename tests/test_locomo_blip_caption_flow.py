from __future__ import annotations

import json

from m_agent.load_data.locomo_history_loader import extract_dialogues_from_locomo
import m_agent.memory.build_memory.form_scene_details as form_scene_details
from m_agent.memory.build_memory.form_scene import build_episode_with_content
from m_agent.memory.build_memory.form_scene_details import build_episode_payload


def _build_raw_locomo_sample() -> dict:
    return {
        "sample_id": "conv-1",
        "conversation": {
            "speaker_a": "Jon",
            "speaker_b": "Amy",
            "session_1_date_time": "1:56 pm on 8 May, 2023",
            "session_1": [
                {
                    "speaker": "Jon",
                    "text": "Look at this.",
                    "blip_caption": "A cat sits on a sofa.",
                },
                {
                    "speaker": "Amy",
                    "text": "Nice photo.",
                },
            ],
        },
    }


def test_locomo_import_keeps_blip_caption_per_turn() -> None:
    dialogues = extract_dialogues_from_locomo(_build_raw_locomo_sample(), source_file="locomo10.json")
    assert len(dialogues) == 1
    turns = dialogues[0]["turns"]
    assert turns[0]["blip_caption"] == "A cat sits on a sofa."
    assert "blip_caption" not in turns[1]


def test_scene_builder_uses_turn_level_blip_caption() -> None:
    dialogue = extract_dialogues_from_locomo(_build_raw_locomo_sample(), source_file="locomo10.json")[0]
    episode_meta = {
        "episode_id": "ep_001",
        "dialogue_id": dialogue["dialogue_id"],
        "turn_span": [0, 1],
    }

    payload = build_episode_with_content(episode_meta, dialogue)
    assert payload["turns"][0]["blip_caption"] == "A cat sits on a sofa."
    assert "blip_caption" not in payload["turns"][1]


def test_fact_builder_uses_turn_level_blip_caption() -> None:
    dialogue = extract_dialogues_from_locomo(_build_raw_locomo_sample(), source_file="locomo10.json")[0]
    episode_meta = {
        "episode_id": "ep_001",
        "dialogue_id": dialogue["dialogue_id"],
        "turn_span": [0, 1],
    }

    payload = build_episode_payload(
        source_ep=episode_meta,
        turns=dialogue["turns"],
        dialogue_data=dialogue,
    )

    assert payload["turns"][0]["blip_caption"] == "A cat sits on a sofa."
    assert "blip_caption" not in payload["turns"][1]


def test_fact_chunk_payload_keeps_arbitrary_turn_fields(monkeypatch, tmp_path) -> None:
    dialogue_id = "dlg_custom_1"
    dialogue_payload = {
        "dialogue_id": dialogue_id,
        "user_id": "Jon",
        "participants": ["Jon", "Amy"],
        "meta": {
            "start_time": "2023-05-08T13:56:00",
            "end_time": "2023-05-08T13:56:05",
        },
        "turns": [
            {
                "turn_id": 0,
                "speaker": "Jon",
                "text": "Look at this.",
                "timestamp": "2023-05-08T13:56:00",
                "img_url": "https://example.com/a.jpg",
                "custom_field": {"k": "v"},
            }
        ],
    }
    month_dir = tmp_path / "2023-05"
    month_dir.mkdir(parents=True, exist_ok=True)
    dialogue_file = month_dir / f"{dialogue_id}.json"
    dialogue_file.write_text(json.dumps(dialogue_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    captured = {}

    def _fake_call_fact_extraction(
        dialogue_block: str,
        episode_payload_text: str,
        start_time: str,
        prompt_template: str,
        llm_model=None,
        **kwargs,
    ):
        captured["episode_payload"] = json.loads(episode_payload_text)
        return [
            {
                "Atomic fact": "Jon shared a photo link.",
                "evidence_sentence": "Look at this.",
            }
        ]

    monkeypatch.setattr(form_scene_details, "call_fact_extraction", _fake_call_fact_extraction)

    facts = form_scene_details.build_facts_from_source_episode(
        source_ep={
            "episode_id": "ep_001",
            "dialogue_id": dialogue_id,
            "turn_span": [0, 0],
            "start_time": "2023-05-08T13:56:00",
            "segments": [
                {"segment_id": "seg_001", "turn_span": [0, 0], "topic": "test"},
            ],
        },
        dialogues_root=tmp_path,
        prompt_template="unused in test",
        embed_model=lambda _text: [],
        llm_model=lambda _prompt: "[]",
    )

    assert facts
    turn_payload = captured["episode_payload"]["turns"][0]
    assert turn_payload["img_url"] == "https://example.com/a.jpg"
    assert turn_payload["custom_field"] == {"k": "v"}


def test_segment_turns_into_fact_subchunks() -> None:
    turns = [{"turn_id": i, "speaker": "A", "text": str(i)} for i in range(10)]
    chunks = form_scene_details.segment_turns_into_fact_subchunks(turns)
    assert len(chunks) == 3
    assert [len(c) for c in chunks] == [4, 4, 2]

    short = turns[:5]
    one = form_scene_details.segment_turns_into_fact_subchunks(short)
    assert len(one) == 1 and len(one[0]) == 5
