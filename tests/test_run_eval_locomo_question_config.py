from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "scripts" / "run_eval_locomo.py"


def _load_run_eval_locomo_module():
    scripts_dir = str(MODULE_PATH.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_eval_locomo_module", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_question_config_loads_and_filters_fixed_subset(tmp_path: Path) -> None:
    module = _load_run_eval_locomo_module()
    config_path = tmp_path / "subset.yaml"
    config_path.write_text(
        "\n".join(
            [
                "questions:",
                "  - sample_id: conv-1",
                "    qa_indices: [2, 0, 2]",
                "  - sample_id: conv-2",
                "    qa_index: 1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    selection = module.load_question_selection_config(str(config_path))
    assert selection == {"conv-1": [2, 0], "conv-2": [1]}

    samples = [
        {
            "sample_id": "conv-1",
            "qa": [
                {"question": "q0"},
                {"question": "q1"},
                {"question": "q2"},
            ],
        },
        {
            "sample_id": "conv-2",
            "qa": [
                {"question": "q3"},
                {"question": "q4"},
            ],
        },
    ]

    filtered = module.filter_samples_by_question_selection(samples, selection)

    assert [sample["sample_id"] for sample in filtered] == ["conv-1", "conv-2"]
    assert [qa["question"] for qa in filtered[0]["qa"]] == ["q2", "q0"]
    assert [qa["question"] for qa in filtered[1]["qa"]] == ["q4"]
    assert [qa[module.LOCOMO_SOURCE_QA_INDEX_KEY] for qa in filtered[0]["qa"]] == [2, 0]
    assert filtered[1]["qa"][0][module.LOCOMO_SOURCE_QA_INDEX_KEY] == 1


def test_question_config_rejects_unknown_sample_and_bad_index() -> None:
    module = _load_run_eval_locomo_module()
    samples = [{"sample_id": "conv-1", "qa": [{"question": "q0"}]}]

    with pytest.raises(ValueError, match="unknown sample_id"):
        module.filter_samples_by_question_selection(samples, {"conv-missing": [0]})

    with pytest.raises(ValueError, match="requested qa_index=3"):
        module.filter_samples_by_question_selection(samples, {"conv-1": [3]})


def test_eval_recall_prefers_episode_refs_with_turn_span_mapping(tmp_path: Path) -> None:
    module = _load_run_eval_locomo_module()
    dialogue_dir = tmp_path / "by_dialogue" / "dlg_locomo10_conv-1_2"
    dialogue_dir.mkdir(parents=True, exist_ok=True)
    (dialogue_dir / "episodes_v1.json").write_text(
        json.dumps(
            {
                "dialogue_id": "dlg_locomo10_conv-1_2",
                "episodes": [
                    {
                        "episode_id": "ep_001",
                        "turn_span": [2, 4],
                        "segments": [
                            {"segment_id": "seg_001", "turn_span": [2, 4]},
                            {"segment_id": "seg_002", "turn_span": [5, 7]},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    qas = [
        {
            "question": "q0",
            "answer": "A",
            "category": 2,
            "evidence": ["D2:3", "D2:6"],
            "pred": "A",
            "pred_evidence_segment_refs": ["dlg_locomo10_conv-1_2:ep_001:seg_001"],
        }
    ]

    _, _, recalls, _ = module.eval_question_answering_locomo(
        qas,
        "pred",
        episodes_root=tmp_path,
    )
    assert recalls == [0.5]


def test_eval_recall_can_recover_episode_refs_from_tool_calls(tmp_path: Path) -> None:
    module = _load_run_eval_locomo_module()
    dialogue_dir = tmp_path / "by_dialogue" / "dlg_locomo10_conv-1_2"
    dialogue_dir.mkdir(parents=True, exist_ok=True)
    (dialogue_dir / "episodes_v1.json").write_text(
        json.dumps(
            {
                "dialogue_id": "dlg_locomo10_conv-1_2",
                "episodes": [
                    {
                        "episode_id": "ep_001",
                        "turn_span": [0, 2],
                        "segments": [
                            {"segment_id": "seg_001", "turn_span": [0, 2]},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    qas = [
        {
            "question": "q0",
            "answer": "A",
            "category": 2,
            "evidence": ["D2:1"],
            "pred": "A",
            "pred_tool_calls": [
                {
                    "status": "completed",
                    "params": {
                        "dialogue_id": "dlg_locomo10_conv-1_2",
                        "episode_id": "ep_001",
                        "segment_id": "seg_001",
                    },
                    "result": {"success": True},
                }
            ],
        }
    ]

    _, _, recalls, _ = module.eval_question_answering_locomo(
        qas,
        "pred",
        episodes_root=tmp_path,
    )
    assert recalls == [1.0]


def test_eval_recall_is_zero_when_no_evidence_ids_available() -> None:
    module = _load_run_eval_locomo_module()
    qas = [
        {
            "question": "q0",
            "answer": "A",
            "category": 2,
            "evidence": ["D1:1"],
            "pred": "A",
        }
    ]

    _, _, recalls, _ = module.eval_question_answering_locomo(qas, "pred", episodes_root=None)
    assert recalls == [0.0]


def test_trace_recover_applies_episode_ref_fields() -> None:
    module = _load_run_eval_locomo_module()
    qa = {}
    applied = module._apply_trace_record_to_qa(
        qa,
        {
            "prediction": "A",
            "prediction_evidence_episode_refs": ["dlg_locomo10_conv-1_2:ep_001"],
            "prediction_evidence_episode_ref_count": 1,
        },
        "pred",
    )

    assert applied is True
    assert qa["pred"] == "A"
    assert qa["pred_evidence_episode_refs"] == ["dlg_locomo10_conv-1_2:ep_001"]
    assert qa["pred_evidence_episode_ref_count"] == 1
