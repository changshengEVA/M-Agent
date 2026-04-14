from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from m_agent.tools.memory_baseline_explorer.app import create_app
from m_agent.tools.memory_baseline_explorer.baseline_store import (
    is_safe_scene_stem,
    is_safe_workflow_id,
    resolve_scene_file,
    strip_embeddings,
)


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def sample_memory_root(tmp_path: Path) -> Path:
    root = tmp_path / "mem"
    dlg = {
        "dialogue_id": "dlg_test_1",
        "participants": ["A", "B"],
        "turns": [
            {"turn_id": 0, "speaker": "A", "text": "hi"},
            {"turn_id": 1, "speaker": "B", "text": "yo"},
        ],
        "meta": {"language": "en"},
    }
    _write_json(root / "dialogues" / "2024-01" / "dlg_test_1.json", dlg)
    _write_json(
        root / "episodes" / "by_dialogue" / "dlg_test_1" / "episodes_v1.json",
        {
            "dialogue_id": "dlg_test_1",
            "episodes": [
                {
                    "episode_id": "ep_001",
                    "topic": "t",
                    "turn_span": [0, 1],
                    "segments": [
                        {
                            "segment_id": "seg_001",
                            "turn_span": [0, 0],
                            "topic": "greeting",
                        }
                    ],
                }
            ],
        },
    )
    _write_json(
        root / "episodes" / "by_dialogue" / "dlg_test_1" / "eligibility_v1.json",
        {"dialogue_id": "dlg_test_1", "results": [{"episode_id": "ep_001", "eligible": True}]},
    )
    scene = {
        "scene_id": "scene_00001",
        "theme": "hello",
        "theme_embedding": [0.1, 0.2],
        "diary": "Test diary line.",
        "source": {"episodes": [{"dialogue_id": "dlg_test_1", "episode_id": "ep_001"}]},
        "facts": [
            {
                "Atomic fact": "A greeted.",
                "evidence_sentence": "hi",
                "evidence": {
                    "episode_id": "ep_001",
                    "dialogue_id": "dlg_test_1",
                    "segment_id": "seg_001",
                    "segment_turn_span": [0, 0],
                },
                "embedding": [0.5],
            }
        ],
    }
    _write_json(root / "scene" / "00001.json", scene)
    _write_json(
        root / "episodes" / "episode_situation.json",
        {"statistics": {"total_episodes": 1}},
    )
    return root


def test_strip_embeddings_removes_embedding_keys() -> None:
    data = {"a": 1, "theme_embedding": [1, 2], "nested": {"x_embedding": [3]}}
    out = strip_embeddings(data)
    assert out == {"a": 1, "nested": {}}


def test_path_safety_helpers() -> None:
    assert is_safe_workflow_id("baseline")
    assert not is_safe_workflow_id("../x")
    assert not is_safe_workflow_id("")
    assert is_safe_scene_stem("00001")
    assert not is_safe_scene_stem("..")


def test_resolve_scene_file(tmp_path: Path) -> None:
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir(parents=True)
    p = scene_dir / "00005.json"
    p.write_text("{}", encoding="utf-8")
    assert resolve_scene_file(scene_dir, "5") == p
    assert resolve_scene_file(scene_dir, "scene_00005") == p


def test_api_dialogues_overview_scenes(sample_memory_root: Path) -> None:
    app = create_app(workflow_id="testwf", memory_root=sample_memory_root)
    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    r = client.get("/api/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["dialogue_file_count"] == 1
    assert body["scene_file_count"] == 1
    assert body["by_dialogue_dir_count"] == 1
    assert body["episode_situation_statistics"]["total_episodes"] == 1
    assert body["facts_in_scenes_total"] == 1
    assert body["scenes_with_fact_counts"][0]["fact_count"] == 1

    r = client.get("/api/dialogues")
    assert r.json()["count"] == 1

    r = client.get("/api/dialogues/dlg_test_1")
    assert r.json()["dialogue_id"] == "dlg_test_1"

    r = client.get("/api/dialogues/dlg_test_1/episodes")
    j = r.json()
    assert j["episodes"]["dialogue_id"] == "dlg_test_1"
    assert j["eligibility"]["dialogue_id"] == "dlg_test_1"

    r = client.get("/api/scenes")
    scenes = r.json()["scenes"]
    assert len(scenes) == 1
    assert "dlg_test_1" in r.json()["dialogue_to_scenes"]

    r = client.get("/api/scenes/00001")
    assert r.status_code == 200
    assert "theme_embedding" not in json.dumps(r.json()["data"])

    r = client.get("/api/scenes/00001?omit_embeddings=false")
    assert "theme_embedding" in r.json()["data"]

    r = client.get("/api/dialogues/dlg_test_1/narrative")
    assert r.status_code == 200
    nar = r.json()
    assert nar["dialogue_id"] == "dlg_test_1"
    assert nar["scenes"][0]["facts_total_in_scene"] == 1
    assert nar["scenes"][0]["diary"] == "Test diary line."
    segs = nar["scenes"][0]["episodes"][0]["segments"]
    assert segs[0]["segment_id"] == "seg_001"
    assert len(segs[0]["turns"]) == 1
    assert len(segs[0]["facts"]) == 1
    assert "embedding" not in json.dumps(segs[0]["facts"])


def test_api_scene_path_traversal_rejected(sample_memory_root: Path) -> None:
    app = create_app(workflow_id="testwf", memory_root=sample_memory_root)
    client = TestClient(app)
    assert client.get("/api/scenes/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_create_app_rejects_bad_workflow() -> None:
    with pytest.raises(ValueError):
        create_app(workflow_id="../evil")


def test_dialogue_not_found(sample_memory_root: Path) -> None:
    app = create_app(workflow_id="testwf", memory_root=sample_memory_root)
    client = TestClient(app)
    assert client.get("/api/dialogues/missing_id").status_code == 404
