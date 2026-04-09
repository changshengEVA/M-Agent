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
        sub_question_results=[],
        tool_calls=tool_calls,
    )

    assert result["tool_calls"] == tool_calls
    assert result["tool_call_count"] == 1
    assert agent._last_tool_calls == tool_calls


def test_search_details_blocks_when_consecutive_limit_reached() -> None:
    module = _load_memory_agent_module()
    MemoryAgent = module.MemoryAgent
    agent = MemoryAgent.__new__(MemoryAgent)
    agent._TRACE_PREFIX_TOOL_CALL = "TOOL_CALL: "
    agent._TRACE_PREFIX_TOOL_RESULT = "TOOL_RESULT: "
    agent._safe_trace_value = lambda value: value
    agent._log_structured_trace = lambda *args, **kwargs: None
    agent._tool_call_seq = 3
    agent.detail_search_defaults = {"topk": 5}
    agent.max_consecutive_search_details_calls = 3
    agent.max_search_details_calls_per_scope = 3
    agent.max_search_details_calls_per_round = 20
    agent._active_search_scope = "subq:1"
    agent._search_details_scope_counts = {"subq:1": 3}
    agent._search_details_round_count = 3
    agent._current_tool_calls = [
        {"tool_name": "search_details", "status": "completed"},
        {"tool_name": "search_details", "status": "completed"},
        {"tool_name": "search_details", "status": "completed"},
    ]

    class _DummyMemorySys:
        def __init__(self) -> None:
            self.calls = 0

        def search_details(self, detail_query: str, topk: int = 5):
            self.calls += 1
            return {"hit": True, "results": []}

    dummy = _DummyMemorySys()
    agent.memory_sys = dummy

    result = agent._search_details_with_trace(detail="find this", topk=5)

    assert result["blocked"] is True
    assert result["error"] == "search_details_consecutive_limit_reached"
    assert dummy.calls == 0
    assert agent._current_tool_calls[-1]["status"] == "completed"
    assert agent._current_tool_calls[-1]["result"]["blocked"] is True


def test_search_details_allows_and_counts_when_under_limit() -> None:
    module = _load_memory_agent_module()
    MemoryAgent = module.MemoryAgent
    agent = MemoryAgent.__new__(MemoryAgent)
    agent._TRACE_PREFIX_TOOL_CALL = "TOOL_CALL: "
    agent._TRACE_PREFIX_TOOL_RESULT = "TOOL_RESULT: "
    agent._safe_trace_value = lambda value: value
    agent._log_structured_trace = lambda *args, **kwargs: None
    agent._tool_call_seq = 0
    agent.detail_search_defaults = {"topk": 5}
    agent.max_consecutive_search_details_calls = 3
    agent.max_search_details_calls_per_scope = 3
    agent.max_search_details_calls_per_round = 20
    agent._active_search_scope = "subq:2"
    agent._search_details_scope_counts = {}
    agent._search_details_round_count = 0
    agent._current_tool_calls = []

    class _DummyMemorySys:
        def __init__(self) -> None:
            self.calls = 0

        def search_details(self, detail_query: str, topk: int = 5):
            self.calls += 1
            return {"hit": True, "results": [{"evidence": {"dialogue_id": "dlg_x", "episode_id": "ep_001"}}]}

    dummy = _DummyMemorySys()
    agent.memory_sys = dummy

    result = agent._search_details_with_trace(detail="find this", topk=5)

    assert result["hit"] is True
    assert dummy.calls == 1
    assert agent._search_details_scope_counts["subq:2"] == 1
    assert agent._search_details_round_count == 1


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

    payload = {
        "answer": "Jon closed the account for business growth.",
        "gold_answer": "for business growth",
        "evidence": "Tool evidence indicates he did this to support his business.",
    }
    tool_calls = [
        {
            "call_id": 1,
            "tool_name": "search_content",
            "params": {"dialogue_id": "dlg_locomo10_conv-30_8", "episode_id": "ep_001"},
            "status": "completed",
            "result": {"success": True, "dialogue_id": "dlg_locomo10_conv-30_8", "episode_id": "ep_001"},
        }
    ]

    result = agent._finalize_recall_payload(
        payload,
        question_plan={"goal": "Why did Jon shut down his bank account?"},
        sub_question_results=[],
        tool_calls=tool_calls,
    )

    assert result["evidence_episode_refs"] == ["dlg_locomo10_conv-30_8:ep_001"]
    assert "dlg_locomo10_conv-30_8:ep_001" in result["answer"]
    assert "dlg_locomo10_conv-30_8:ep_001" in result["evidence"]


def test_detect_strategy_llm_gate_blocks_serial_dependency() -> None:
    module = _load_memory_agent_module()
    MemoryAgent = module.MemoryAgent
    agent = MemoryAgent.__new__(MemoryAgent)
    agent._invoke_model_with_network_retry = lambda **kwargs: (
        '{"decompose_first": true, "parallelizable": false, "reason": "串行依赖"}'
    )

    decompose_first, reason = agent._detect_direct_answer_strategy("去过巴黎的那个人喜欢什么？")

    assert decompose_first is False
    assert reason == "串行依赖"


def test_detect_strategy_llm_gate_allows_parallel_decomposition() -> None:
    module = _load_memory_agent_module()
    MemoryAgent = module.MemoryAgent
    agent = MemoryAgent.__new__(MemoryAgent)
    agent._invoke_model_with_network_retry = lambda **kwargs: (
        '{"decompose_first": true, "parallelizable": true, "reason": "并行多目标"}'
    )

    decompose_first, reason = agent._detect_direct_answer_strategy("王强和李雷谁更喜欢巴黎？")

    assert decompose_first is True
    assert reason == "并行多目标"


def test_deep_recall_direct_path_does_not_fallback_to_decompose() -> None:
    module = _load_memory_agent_module()
    MemoryAgent = module.MemoryAgent
    agent = MemoryAgent.__new__(MemoryAgent)
    agent.thread_id = "test-thread"
    agent._reset_round_state = lambda: None
    agent._detect_direct_answer_strategy = lambda _: (False, "DIRECT")
    agent._build_direct_question_plan = lambda q, r: {"goal": q, "decomposition_reason": r, "sub_questions": []}
    agent._answer_directly = lambda **kwargs: {"answer": "信息不足", "gold_answer": None, "evidence": None}
    agent._consume_current_tool_calls = lambda: []
    agent._log_structured_trace = lambda *args, **kwargs: None
    agent._last_tool_calls = []
    agent._last_question_plan = None

    called = {"decompose": 0}

    def _decompose_question(_: str):
        called["decompose"] += 1
        return {"goal": "should_not_happen", "sub_questions": []}

    agent._decompose_question = _decompose_question
    agent._solve_sub_questions = lambda **kwargs: []
    agent._synthesize_final_answer = lambda **kwargs: {"answer": "N/A"}
    agent._finalize_recall_payload = lambda payload, **kwargs: {
        "answer": payload.get("answer"),
        "question_plan": kwargs.get("question_plan"),
        "sub_question_results": kwargs.get("sub_question_results"),
        "tool_calls": kwargs.get("tool_calls"),
    }

    result = agent.deep_recall("去过巴黎的那个人喜欢什么？")

    assert called["decompose"] == 0
    assert result["question_plan"]["goal"] == "去过巴黎的那个人喜欢什么？"
    assert result["sub_question_results"] == []
