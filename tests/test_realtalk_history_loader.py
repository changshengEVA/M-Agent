from __future__ import annotations

from m_agent.load_data.realtalk_history_loader import extract_dialogues_from_realtalk


def _build_realtalk_payload() -> dict:
    return {
        "name": {"speaker_1": "Alice", "speaker_2": "Bob"},
        "session_1": [
            {
                "clean_text": "Hello",
                "speaker": "Alice",
                "date_time": "01.01.2024, 10:00:00",
                "img_file": ["img1.jpg"],
                "img_url": ["http://example.com/img1.jpg"],
                "blip_caption": "A beach photo",
            },
            {
                "text": "Hi back",
                "speaker": "Bob",
                "date_time": "01.01.2024, 10:00:05",
            },
        ],
        "session_1_date_time": "01.01.2024, 10:00:00",
        "session_1_observation": "metadata only",
    }


def test_extract_realtalk_dialogues_ignores_session_metadata_keys() -> None:
    dialogues = extract_dialogues_from_realtalk(
        _build_realtalk_payload(),
        source_file="data/REALTALK/data/Chat_1_Emi_Elise.json",
    )
    assert len(dialogues) == 1
    assert dialogues[0]["meta"]["session_num"] == 1


def test_extract_realtalk_dialogues_generates_stable_sample_id() -> None:
    dialogues = extract_dialogues_from_realtalk(
        _build_realtalk_payload(),
        source_file="data/REALTALK/data/Chat_7_Nebraas_Vanessa.json",
    )
    assert len(dialogues) == 1
    assert dialogues[0]["meta"]["sample_id"] == "realtalk-chat-7"
    assert dialogues[0]["meta"]["session_sample_id"] == "realtalk-chat-7-s1"
    assert dialogues[0]["dialogue_id"] == "dlg_Chat_7_Nebraas_Vanessa_1"


def test_extract_realtalk_dialogues_keeps_image_fields_and_text_fallback() -> None:
    dialogues = extract_dialogues_from_realtalk(
        _build_realtalk_payload(),
        source_file="data/REALTALK/data/Chat_2_Kevin_Elise.json",
    )
    turns = dialogues[0]["turns"]
    assert turns[0]["blip_caption"] == "A beach photo"
    assert turns[0]["img_file"] == ["img1.jpg"]
    assert turns[0]["img_url"] == ["http://example.com/img1.jpg"]
    assert turns[1]["text"] == "Hi back"

