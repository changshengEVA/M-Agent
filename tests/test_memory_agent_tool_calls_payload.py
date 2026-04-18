from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src" / "m_agent" / "agents" / "memory_agent.py"


def _load_memory_agent_module():
    src_dir = str(PROJECT_ROOT / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    spec = importlib.util.spec_from_file_location("memory_agent_module", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_finalize_recall_payload_keeps_tool_calls_in_result() -> None:
    module = _load_memory_agent_module()
    MemoryAgent = module.MemoryAgent
    agent = MemoryAgent.__new__(MemoryAgent)
    agent._TRACE_PREFIX_FINAL_PAYLOAD = "FINAL_PAYLOAD: "
    agent._last_tool_calls = []
    agent._log_structured_trace = lambda *args, **kwargs: None
    agent._safe_trace_value = lambda value: value
    agent._collect_episode_refs_from_tool_calls = lambda calls: []
    agent._append_episode_refs_to_payload = lambda payload, refs: payload

    payload = {
        "answer": "Jon was in Paris on January 28, 2023.",
        "gold_answer": "January 28, 2023",
        "evidence": "search_details found the event.",
    }
    tool_calls = [
        {
            "call_id": 1,
            "tool_name": "search_details",
            "params": {"detail": "Jon in Paris", "topk": 5},
            "status": "completed",
            "result": {"hit": True},
        }
    ]

    result = agent._finalize_recall_payload(
        payload,
        question_plan={"goal": "When was Jon in Paris?"},
        recall_rounds=[],
        tool_calls=tool_calls,
    )

    assert result["tool_calls"] == tool_calls
    assert result["tool_call_count"] == 1
    assert agent._last_tool_calls == tool_calls


def test_search_details_with_trace_runs_without_legacy_limits() -> None:
    module = _load_memory_agent_module()
    MemoryAgent = module.MemoryAgent
    agent = MemoryAgent.__new__(MemoryAgent)
    agent._TRACE_PREFIX_TOOL_CALL = "TOOL_CALL: "
    agent._TRACE_PREFIX_TOOL_RESULT = "TOOL_RESULT: "
    agent._safe_trace_value = lambda value: value
    agent._log_structured_trace = lambda *args, **kwargs: None
    agent._tool_call_seq = 0
    agent._current_tool_calls = []
    agent.detail_search_defaults = {"topk": 5}
    agent._resolve_topk = lambda topk: 5 if topk is None else int(topk)

    class _DummyMemorySys:
        def __init__(self) -> None:
            self.calls = 0

        def search_details(self, detail_query: str, topk: int = 5):
            self.calls += 1
            return {"hit": True, "results": [{"Atomic fact": "x"}]}

    agent.memory_sys = _DummyMemorySys()

    result = agent._search_details_with_trace(detail="find this", topk=5)

    assert result["hit"] is True
    assert agent.memory_sys.calls == 1
    assert len(agent._current_tool_calls) == 1
    assert agent._current_tool_calls[0]["tool_name"] == "search_details"
    assert agent._current_tool_calls[0]["status"] == "completed"


def test_shallow_recall_always_runs_single_round_state_machine() -> None:
    module = _load_memory_agent_module()
    MemoryAgent = module.MemoryAgent
    agent = MemoryAgent.__new__(MemoryAgent)
    agent._reset_round_state = lambda: None
    agent._consume_current_tool_calls = lambda: []
    agent._last_tool_calls = []

    captured = {}

    def _run_state_machine_recall(*, question_text: str, max_rounds: int):
        captured["question_text"] = question_text
        captured["max_rounds"] = max_rounds
        return {"answer": "ok", "gold_answer": None, "evidence": None}

    agent._run_state_machine_recall = _run_state_machine_recall

    result = agent.shallow_recall("What happened?")

    assert captured["question_text"] == "What happened?"
    assert captured["max_rounds"] == 1
    assert result["answer"] == "ok"


def test_deep_recall_uses_workspace_max_rounds() -> None:
    module = _load_memory_agent_module()
    MemoryAgent = module.MemoryAgent
    agent = MemoryAgent.__new__(MemoryAgent)
    agent.workspace_max_rounds = 3
    agent._reset_round_state = lambda: None
    agent._consume_current_tool_calls = lambda: []
    agent._last_tool_calls = []

    captured = {}

    def _run_state_machine_recall(*, question_text: str, max_rounds: int):
        captured["question_text"] = question_text
        captured["max_rounds"] = max_rounds
        return {"answer": "ok", "gold_answer": None, "evidence": None}

    agent._run_state_machine_recall = _run_state_machine_recall

    result = agent.deep_recall("When did this happen?")

    assert captured["question_text"] == "When did this happen?"
    assert captured["max_rounds"] == 3
    assert result["answer"] == "ok"


def test_finalize_recall_payload_appends_episode_refs_to_answer_and_evidence() -> None:
    module = _load_memory_agent_module()
    MemoryAgent = module.MemoryAgent
    agent = MemoryAgent.__new__(MemoryAgent)
    agent._TRACE_PREFIX_FINAL_PAYLOAD = "FINAL_PAYLOAD: "
    agent._last_tool_calls = []
    agent._log_structured_trace = lambda *args, **kwargs: None
    agent._safe_trace_value = lambda value: value
    agent.attach_episode_refs_to_answer = True
    agent.evidence_episode_ref_max_in_text = 8
    agent._is_successful_tool_call = lambda call: True
    agent._collect_episode_refs_from_tool_calls = lambda _calls: ["dlg_1:ep_1"]

    def _append_episode_refs_to_payload(payload, refs):
        payload["answer"] = f"{payload['answer']} [{refs[0]}]"
        payload["evidence"] = f"{payload['evidence']} [{refs[0]}]"
        return payload

    agent._append_episode_refs_to_payload = _append_episode_refs_to_payload

    payload = {
        "answer": "Jon closed the account for business growth.",
        "gold_answer": "for business growth",
        "evidence": "Tool evidence indicates he did this to support his business.",
    }
    tool_calls = [
        {
            "call_id": 1,
            "tool_name": "search_content",
            "params": {"dialogue_id": "dlg_1", "episode_id": "ep_1"},
            "status": "completed",
            "result": {"hit": True, "dialogue_id": "dlg_1", "episode_id": "ep_1"},
        }
    ]

    result = agent._finalize_recall_payload(
        payload,
        question_plan={"goal": "Why did Jon shut down his bank account?"},
        recall_rounds=[],
        tool_calls=tool_calls,
    )

    assert result["evidence_episode_refs"] == ["dlg_1:ep_1"]
    assert "dlg_1:ep_1" in result["answer"]
    assert "dlg_1:ep_1" in result["evidence"]
