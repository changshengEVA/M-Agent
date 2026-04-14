from __future__ import annotations

from typing import Any, Dict, List

from m_agent.agents.memory_agent.action_executor import execute_actions
from m_agent.agents.memory_agent.action_planner import ACTION_EVENT_DETAIL_RECALL


def test_execute_actions_passes_dict_refs_to_content_lookup() -> None:
    captured_refs: List[Dict[str, str]] = []

    def _search_details(detail: str, topk: int) -> Dict[str, Any]:
        return {
            "hit": True,
            "results": [
                {
                    "similarity": 0.9,
                    "Atomic fact": "Emi likes painting.",
                    "evidence": {"dialogue_id": "dlg_1", "episode_id": "ep_001"},
                }
            ],
        }

    def _search_details_multi_route(detail: str, topk: int) -> Dict[str, Any]:
        return {"hit": False, "results": []}

    def _search_events_by_time_range(start_time: str, end_time: str) -> List[Dict[str, Any]]:
        return []

    def _search_contents_by_episode_refs(refs: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        captured_refs.extend(refs)
        if not refs:
            return []
        ref0 = refs[0]
        return [
            {
                "hit": True,
                "dialogue_id": ref0.get("dialogue_id", ""),
                "episode_id": ref0.get("episode_id", ""),
                "turn_span": [0, 1],
                "turns": [
                    {"turn_id": 0, "speaker": "Emi", "text": "I like painting."},
                    {"turn_id": 1, "speaker": "Jon", "text": "Nice!"},
                ],
            }
        ]

    report = execute_actions(
        actions=[
            {
                "action_id": "r1_a1",
                "action_type": ACTION_EVENT_DETAIL_RECALL,
                "query": {"detail_query": "Emi hobby", "topk": 5},
                "source_sub_question_idx": 0,
                "priority": 10,
            }
        ],
        round_id=1,
        search_details=_search_details,
        search_details_multi_route=_search_details_multi_route,
        search_events_by_time_range=_search_events_by_time_range,
        search_contents_by_episode_refs=_search_contents_by_episode_refs,
        max_episode_candidates=8,
    )

    assert captured_refs == [{"dialogue_id": "dlg_1", "episode_id": "ep_001"}]
    assert len(report["evidences"]) == 1
    ev = report["evidences"][0]
    assert ev["turn_span"] == [0, 1]
    assert len(ev["turns"]) == 2
    assert ev["facts"] == ["Emi likes painting."]
    assert ev["segment_id"] is None


def test_execute_actions_segment_level_recall() -> None:
    """When facts carry segment_id, the ref and content lookup should use
    segment-level granularity."""
    captured_refs: List[Dict[str, str]] = []

    def _search_details(detail: str, topk: int) -> Dict[str, Any]:
        return {
            "hit": True,
            "results": [
                {
                    "similarity": 0.85,
                    "Atomic fact": "Jon lost his banking job.",
                    "evidence": {
                        "dialogue_id": "dlg_1",
                        "episode_id": "ep_001",
                        "segment_id": "seg_001",
                        "segment_turn_span": [0, 1],
                    },
                }
            ],
        }

    def _search_details_multi_route(detail: str, topk: int) -> Dict[str, Any]:
        return {"hit": False, "results": []}

    def _search_events_by_time_range(start_time: str, end_time: str) -> List[Dict[str, Any]]:
        return []

    def _search_contents_by_episode_refs(refs: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        captured_refs.extend(refs)
        if not refs:
            return []
        ref0 = refs[0]
        return [
            {
                "hit": True,
                "dialogue_id": ref0.get("dialogue_id", ""),
                "episode_id": ref0.get("episode_id", ""),
                "segment_id": ref0.get("segment_id", ""),
                "turn_span": [0, 1],
                "turns": [
                    {"turn_id": 0, "speaker": "Jon", "text": "I lost my banking job."},
                    {"turn_id": 1, "speaker": "Gina", "text": "Oh no, what happened?"},
                ],
            }
        ]

    report = execute_actions(
        actions=[
            {
                "action_id": "r1_a1",
                "action_type": ACTION_EVENT_DETAIL_RECALL,
                "query": {"detail_query": "Jon job loss", "topk": 5},
                "source_sub_question_idx": 0,
                "priority": 10,
            }
        ],
        round_id=1,
        search_details=_search_details,
        search_details_multi_route=_search_details_multi_route,
        search_events_by_time_range=_search_events_by_time_range,
        search_contents_by_episode_refs=_search_contents_by_episode_refs,
        max_episode_candidates=8,
    )

    assert captured_refs == [
        {"dialogue_id": "dlg_1", "episode_id": "ep_001", "segment_id": "seg_001"}
    ]
    assert len(report["evidences"]) == 1
    ev = report["evidences"][0]
    assert ev["evidence_id"] == "dlg_1:ep_001:seg_001"
    assert ev["segment_id"] == "seg_001"
    assert ev["turn_span"] == [0, 1]
    assert len(ev["turns"]) == 2
    assert ev["facts"] == ["Jon lost his banking job."]
