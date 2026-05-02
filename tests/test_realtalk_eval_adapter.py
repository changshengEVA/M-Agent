from __future__ import annotations

import json

from scripts.run_realtalk._shared import resolve_target_sample_ids
from scripts.run_realtalk.run_eval_realtalk import load_realtalk_eval_samples


def test_resolve_target_sample_ids_from_chat(tmp_path) -> None:
    payload = {
        "selection": {
            "chat_ids": [1],
        }
    }
    data_file = tmp_path / "Chat_1_A_B.json"
    data_file.write_text(
        json.dumps(
            {
                "session_1": [{"clean_text": "x", "speaker": "A", "date_time": "01.01.2024, 10:00:00"}],
                "session_2": [{"clean_text": "y", "speaker": "B", "date_time": "01.01.2024, 10:01:00"}],
                "session_2_date_time": "01.01.2024, 10:01:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = resolve_target_sample_ids(payload, tmp_path)
    assert result == ["realtalk-chat-1"]


def test_load_realtalk_eval_samples_assigns_qa_to_session(tmp_path) -> None:
    data_file = tmp_path / "Chat_3_X_Y.json"
    data_file.write_text(
        json.dumps(
            {
                "session_1": [{"clean_text": "hello", "speaker": "X", "date_time": "01.01.2024, 10:00:00"}],
                "session_2": [{"clean_text": "world", "speaker": "Y", "date_time": "01.01.2024, 10:01:00"}],
                "qa": [
                    {"question": "q1", "answer": "a1", "category": 2, "evidence": ["D2:1"]},
                    {"question": "q2", "answer": "a2", "category": 2, "evidence": ["D1:1"]},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    samples = load_realtalk_eval_samples(str(tmp_path))
    by_id = {item["sample_id"]: item for item in samples}
    qa_questions = [qa["question"] for qa in by_id["realtalk-chat-3"]["qa"]]
    assert qa_questions == ["q1", "q2"]
