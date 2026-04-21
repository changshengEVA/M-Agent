from __future__ import annotations

import pytest

from m_agent.agents.memory_agent.action_planner import (
    ACTION_EVENT_DETAIL_RECALL,
    ACTION_EVENT_TIME_RECALL,
    _dedup_actions,
    action_signature,
    build_tool_registry_from_config,
    tool_registry_for_prompt,
)
from m_agent.agents.memory_agent.mixins.execution import _chunk_evidence_text
from m_agent.agents.memory_agent.workspace import Workspace


def test_dedup_actions_quota_keeps_multiple_action_types() -> None:
    prev: set[str] = set()
    candidates = [
        {
            "action_id": "r1_a1",
            "action_type": ACTION_EVENT_DETAIL_RECALL,
            "query": {"detail_query": "foo", "topk": 5},
            "source_sub_question_idx": 0,
            "priority": 10,
        },
        {
            "action_id": "r1_a2",
            "action_type": ACTION_EVENT_TIME_RECALL,
            "query": {"start_time": "2024-01-01", "end_time": "2024-01-31"},
            "source_sub_question_idx": 0,
            "priority": 5,
        },
    ]
    out = _dedup_actions(candidates, max_actions=2, previous_action_signatures=prev)
    types = {a["action_type"] for a in out}
    assert types == {ACTION_EVENT_DETAIL_RECALL, ACTION_EVENT_TIME_RECALL}


def test_dedup_actions_respects_previous_signatures() -> None:
    a = {
        "action_id": "r1_a1",
        "action_type": ACTION_EVENT_DETAIL_RECALL,
        "query": {"detail_query": "foo", "topk": 5},
        "source_sub_question_idx": 0,
        "priority": 10,
    }
    prev = {action_signature(a)}
    out = _dedup_actions([a], max_actions=2, previous_action_signatures=prev)
    assert out == []


def test_tool_registry_for_prompt_requires_non_empty_registry() -> None:
    with pytest.raises(ValueError, match="tool_registry"):
        tool_registry_for_prompt(registry=None)
    with pytest.raises(ValueError, match="tool_registry"):
        tool_registry_for_prompt(registry=[])


def test_build_tool_registry_includes_search_entry_anchors() -> None:
    tool_descriptions = {
        "EVENT_DETAIL_RECALL": {
            "description": {"en": "desc"},
            "params": "{}",
            "search_entry_anchors": {"en": "anchor text", "zh": "锚点"},
        },
    }
    reg = build_tool_registry_from_config(
        tool_descriptions,
        ["EVENT_DETAIL_RECALL"],
        language="en",
    )
    assert len(reg) == 1
    assert reg[0].get("search_entry_anchors") == "anchor text"


def test_chunk_evidence_text_splits_long_body() -> None:
    body = "a" * 100
    chunks = _chunk_evidence_text(body, chunk_chars=30)
    assert len(chunks) >= 4
    assert "".join(chunks) == body


def test_workspace_prune_except() -> None:
    ws = Workspace()
    ws.upsert({"evidence_id": "a", "content": "A", "source_type": "t"})
    ws.upsert({"evidence_id": "b", "content": "B", "source_type": "t"})
    n = ws.prune_except({"a"})
    assert n == 1
    assert ws.evidence_count() == 1
    assert ws.has_evidence("a")


def test_state_machine_stagnation_stops_when_useful_set_unchanged(monkeypatch) -> None:
    """Second INSUFFICIENT round with same judge useful_evidence_ids as round 1 must break early."""
    from m_agent.agents.memory_agent.mixins import execution as exec_mod
    from m_agent.agents.memory_agent.mixins.execution import MemoryAgentExecutionMixin

    def _fake_judge(workspace, new_evidence_ids, llm_func, prompt_text):
        return {
            "status": "INSUFFICIENT",
            "useful_evidence_ids": ["ev_stable"],
            "reason": "need more",
            "next_query": "more please",
            "gap_type": "need_more_evidence",
        }

    class _Stub(MemoryAgentExecutionMixin):
        workspace_max_keep = 8
        workspace_max_actions_per_round = 2
        workspace_max_episode_candidates = 8
        workspace_remedy_recall_max_times = 1
        detail_search_defaults = {"topk": 5}
        action_planner_mode = "llm"
        tool_registry = None
        rerank_func = None
        _TRACE_PREFIX_WORKSPACE_STATE = "WS: "
        _TRACE_PREFIX_FINAL_PAYLOAD = "FINAL: "

        def _log_structured_trace(self, *args, **kwargs) -> None:
            return None

        def _safe_trace_value(self, value):
            return value

        def _consume_current_tool_calls(self):
            return []

        def _invoke_model_with_network_retry(self, prompt_text: str, call_name: str):
            raise AssertionError("model should not be called in this test")

        def _plan_actions_with_llm(self, workspace, **kwargs):
            rid = int(workspace.round_id or 1)
            return [
                {
                    "action_id": f"r{rid}_a1",
                    "action_type": ACTION_EVENT_DETAIL_RECALL,
                    "query": {"detail_query": f"round-{rid}", "topk": 5},
                    "source_sub_question_idx": 0,
                    "priority": 10,
                }
            ]

        def _generate_final_payload_from_workspace(self, **kwargs):
            return {"answer": "x", "gold_answer": None, "evidence": "e"}

        def _run_llm_judge(self, workspace, new_evidence_ids):
            return _fake_judge(workspace, new_evidence_ids, None, "")

        def _finalize_recall_payload(
            self,
            payload,
            *,
            question_plan,
            recall_rounds,
            tool_calls,
            kept_evidence_ids=None,
        ):
            payload["recall_rounds"] = recall_rounds
            return payload

        def _append_episode_refs_to_payload(self, payload, refs):
            return payload

    def _fake_execute(**kwargs):
        return {
            "evidences": [
                {
                    "evidence_id": "ev_stable",
                    "content": "body",
                    "source_type": "episode",
                    "source_action_id": "r1_a1",
                    "recall_score": 0.9,
                    "meta": {},
                }
            ],
            "episode_refs": [],
        }

    monkeypatch.setattr(exec_mod, "execute_actions", _fake_execute)

    stub = _Stub()
    out = stub._run_state_machine_recall(question_text="Q?", max_rounds=5)
    rounds = out.get("workspace_rounds") or []
    assert len(rounds) == 2
    assert rounds[-1]["workspace_status"] == "INSUFFICIENT"
    snap = rounds[-1].get("workspace_after_judge") or {}
    assert snap.get("gap_type") == "stagnant"
    assert out["recall_rounds"][-1].get("stagnant") is True


def test_llm_judge_workspace_normalizes_ref_prefix() -> None:
    from m_agent.agents.memory_agent.answerability import llm_judge_workspace

    ws = Workspace(max_keep=4)
    ws.kept_evidence_ids = ["ev_stable"]
    ws.upsert({"evidence_id": "ev_stable", "content": "body", "source_type": "episode"})

    def _fake_llm(_prompt: str) -> str:
        # Simulate the model copying `ref:` from the workspace evidence header.
        return """
        {"status":"INSUFFICIENT","useful_evidence_ids":["ref:ev_stable"],"reason":"x","next_query":"q"}
        """

    decision = llm_judge_workspace(
        workspace=ws,
        new_evidence_ids=[],
        llm_func=_fake_llm,
        prompt_text="ignored",
    )
    assert decision["useful_evidence_ids"] == ["ev_stable"]


def test_to_evidence_summary_appends_facts_when_prefer_judge_view() -> None:
    ws = Workspace(max_keep=4)
    ws.kept_evidence_ids = ["e1"]
    ws.upsert(
        {
            "evidence_id": "e1",
            "content": "FULL CONTENT WITH FACTS BLOCK",
            "judge_view": "Short narrative without the number five.",
            "source_type": "episode",
            "meta": {
                "facts": [
                    "User purchased 5 coffee mugs.",
                    "User purchased 5 coffee mugs.",
                    "",
                ],
            },
        }
    )
    summary = ws.to_evidence_summary(prefer_judge_view=True)
    assert "Short narrative" in summary
    assert "【相关事实 Related facts】" in summary
    assert "  - User purchased 5 coffee mugs." in summary
    assert summary.count("User purchased 5 coffee mugs.") == 1


def test_to_evidence_summary_no_duplicate_facts_when_content_fallback() -> None:
    """Empty judge_view falls back to full content; do not append meta facts again."""
    ws = Workspace(max_keep=2)
    ws.kept_evidence_ids = ["e1"]
    ws.upsert(
        {
            "evidence_id": "e1",
            "content": "【相关事实 Related facts】\n  - Already inlined.\n【对话内容 Dialogue】\n  User: hi",
            "judge_view": "",
            "source_type": "episode",
            "meta": {"facts": ["Already inlined."], "judge_view": ""},
        }
    )
    summary = ws.to_evidence_summary(prefer_judge_view=True)
    assert summary.count("Already inlined.") == 1
