from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from m_agent.memory.memory_core.workflow.search.multi_route_details_search import search_details_multi_route


def _write_scene(scene_file: Path, scene_id: str, facts: list[dict]) -> None:
    payload = {
        "scene_id": scene_id,
        "facts": facts,
    }
    scene_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_search_details_multi_route_returns_fused_result_and_diagnostics(tmp_path: Path) -> None:
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir(parents=True, exist_ok=True)
    _write_scene(
        scene_dir / "00001.json",
        "scene_00001",
        [
            {
                "Atomic fact": "beta_unique_token happened",
                "evidence": {"episode_id": "ep_001", "dialogue_id": "dlg_002"},
                "embedding": [0.0, 1.0],
            },
            {
                "Atomic fact": "alpha context",
                "evidence": {"episode_id": "ep_001", "dialogue_id": "dlg_001"},
                "embedding": [1.0, 0.0],
            },
        ],
    )

    def _embed(text: str) -> list[float]:
        normalized = str(text or "").strip().lower()
        if "alpha context" in normalized:
            return [1.0, 0.0]
        if "beta_unique_token happened" in normalized:
            return [0.0, 1.0]
        return [1.0, 0.0]

    result = search_details_multi_route(
        detail_query="beta_unique_token question",
        scene_dir=scene_dir,
        embed_func=_embed,
        topk=1,
        route_config={
            "route_count": 4,
            "route_types": ["entity", "action", "time"],
            "per_route_topk": 3,
            "fusion": "rrf",
            "max_workers": 2,
        },
    )

    assert result["hit"] is True
    assert result["matched_count"] == 1
    assert result["results"][0]["Atomic fact"] == "beta_unique_token happened"
    assert isinstance(result.get("route_diagnostics"), list)
    assert len(result["route_diagnostics"]) >= 1


def test_search_details_multi_route_llm_query_generation(tmp_path: Path) -> None:
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir(parents=True, exist_ok=True)
    _write_scene(
        scene_dir / "00001.json",
        "scene_00001",
        [
            {
                "Atomic fact": "beta_unique_token happened",
                "evidence": {"episode_id": "ep_001", "dialogue_id": "dlg_002"},
                "embedding": [0.0, 1.0],
            },
            {
                "Atomic fact": "alpha context",
                "evidence": {"episode_id": "ep_001", "dialogue_id": "dlg_001"},
                "embedding": [1.0, 0.0],
            },
        ],
    )

    def _embed(text: str) -> list[float]:
        normalized = str(text or "").strip().lower()
        if "beta_unique_token" in normalized:
            return [0.0, 1.0]
        if "alpha context" in normalized:
            return [1.0, 0.0]
        return [1.0, 0.0]

    def _llm(prompt: str) -> str:
        assert "Original query" in prompt
        return json.dumps({"queries": ["beta_unique_token related clue"]}, ensure_ascii=False)

    result = search_details_multi_route(
        detail_query="general detail question",
        scene_dir=scene_dir,
        embed_func=_embed,
        topk=1,
        route_config={
            "route_count": 2,
            "query_generator": "llm",
            "per_route_topk": 1,
            "fusion": "weighted",
            "route_weights": {"base": 0.1, "llm": 2.0, "default": 1.0},
            "max_workers": 2,
        },
        llm_func=_llm,
    )

    assert result["hit"] is True
    assert result["matched_count"] == 1
    assert result["results"][0]["Atomic fact"] == "beta_unique_token happened"
    diagnostics = result.get("route_diagnostics", [])
    assert isinstance(diagnostics, list)
    assert any(str(item.get("route_type")) == "llm" for item in diagnostics if isinstance(item, dict))
    assert any(
        "beta_unique_token" in str(item.get("query", ""))
        for item in diagnostics
        if isinstance(item, dict) and str(item.get("route_type")) == "llm"
    )


def test_search_details_multi_route_llm_failure_does_not_use_template_routes(tmp_path: Path) -> None:
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir(parents=True, exist_ok=True)
    _write_scene(
        scene_dir / "00001.json",
        "scene_00001",
        [
            {
                "Atomic fact": "beta_unique_token happened",
                "evidence": {"episode_id": "ep_001", "dialogue_id": "dlg_002"},
                "embedding": [0.0, 1.0],
            },
            {
                "Atomic fact": "alpha context",
                "evidence": {"episode_id": "ep_001", "dialogue_id": "dlg_001"},
                "embedding": [1.0, 0.0],
            },
        ],
    )

    def _embed(text: str) -> list[float]:
        normalized = str(text or "").strip().lower()
        if "beta_unique_token" in normalized:
            return [0.0, 1.0]
        if "alpha context" in normalized:
            return [1.0, 0.0]
        return [1.0, 0.0]

    def _llm(_prompt: str) -> str:
        raise RuntimeError("mock llm failure")

    result = search_details_multi_route(
        detail_query="beta_unique_token question",
        scene_dir=scene_dir,
        embed_func=_embed,
        topk=1,
        route_config={
            "route_count": 3,
            "route_types": ["entity", "action"],
            "query_generator": "llm",
            "per_route_topk": 2,
            "fusion": "rrf",
            "max_workers": 2,
        },
        llm_func=_llm,
    )

    assert result["hit"] is True
    diagnostics = result.get("route_diagnostics", [])
    assert isinstance(diagnostics, list)
    route_types = [str(item.get("route_type")) for item in diagnostics if isinstance(item, dict)]
    assert "llm" not in route_types
    assert route_types == ["base"]
