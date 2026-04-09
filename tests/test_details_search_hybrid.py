from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from m_agent.memory.memory_core.workflow.search.details_search import search_details


def _write_scene(scene_file: Path, scene_id: str, facts: list[dict]) -> None:
    payload = {
        "scene_id": scene_id,
        "facts": facts,
    }
    scene_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_search_details_hybrid_keeps_output_shape_and_uses_sparse_signal(tmp_path: Path) -> None:
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir(parents=True, exist_ok=True)

    _write_scene(
        scene_dir / "00001.json",
        "scene_00001",
        [
            {
                "Atomic fact": "alpha style memory",
                "evidence": {"episode_id": "ep_001", "dialogue_id": "dlg_001"},
                "embedding": [1.0, 0.0],
            },
            {
                "Atomic fact": "beta_unique_token happened",
                "evidence": {"episode_id": "ep_001", "dialogue_id": "dlg_002"},
                "embedding": [0.0, 1.0],
            },
            {
                "Atomic fact": "gamma unrelated context",
                "evidence": {"episode_id": "ep_001", "dialogue_id": "dlg_003"},
                "embedding": [0.8, 0.2],
            },
        ],
    )

    def _embed(text: str) -> list[float]:
        normalized = str(text or "").strip().lower()
        if normalized == "alpha style memory":
            return [1.0, 0.0]
        if normalized == "beta_unique_token happened":
            return [0.0, 1.0]
        if normalized == "gamma unrelated context":
            return [0.8, 0.2]
        # Query embedding intentionally points to alpha direction.
        return [1.0, 0.0]

    result = search_details(
        detail_query="beta_unique_token question",
        scene_dir=scene_dir,
        embed_func=_embed,
        topk=1,
    )

    assert set(result.keys()) == {
        "hit",
        "topk",
        "total_scene_count",
        "total_fact_count",
        "matched_count",
        "results",
    }
    assert result["hit"] is True
    assert result["topk"] == 1
    assert result["matched_count"] == 1
    assert isinstance(result["results"], list)
    assert len(result["results"]) == 1

    top = result["results"][0]
    assert set(top.keys()) == {"scene_id", "similarity", "Atomic fact", "evidence"}
    # Sparse keyword should pull this fact to rank-1 despite dense mismatch.
    assert top["Atomic fact"] == "beta_unique_token happened"


def test_search_details_sparse_only_when_query_embedding_is_empty(tmp_path: Path) -> None:
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir(parents=True, exist_ok=True)

    _write_scene(
        scene_dir / "00001.json",
        "scene_00001",
        [
            {
                "Atomic fact": "common context",
                "evidence": {"episode_id": "ep_001", "dialogue_id": "dlg_001"},
                "embedding": [],
            },
            {
                "Atomic fact": "rare_token fact",
                "evidence": {"episode_id": "ep_001", "dialogue_id": "dlg_002"},
                "embedding": [],
            },
        ],
    )

    result = search_details(
        detail_query="rare_token",
        scene_dir=scene_dir,
        embed_func=lambda _text: [],
        topk=1,
    )

    assert result["hit"] is True
    assert result["matched_count"] == 1
    assert result["results"][0]["Atomic fact"] == "rare_token fact"
    assert set(result["results"][0].keys()) == {"scene_id", "similarity", "Atomic fact", "evidence"}
