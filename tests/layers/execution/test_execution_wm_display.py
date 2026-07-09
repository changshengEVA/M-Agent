"""Execution layer can opt into WM display for custom/debug prompts."""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

from m_agent.chat.working_memory import WorkingMemoryConfig
from m_agent.layers.execution.contracts import ExecutionRequest
from m_agent.layers.execution.core import ExecutionAgent
from m_agent.layers.execution.model_provider import ModelProvider
from m_agent.systems.wm import DefaultWMDisplay


def _minimal_execution_agent(*, wm_display: DefaultWMDisplay | None = None) -> ExecutionAgent:
    provider = ModelProvider(
        model=MagicMock(),
        network_retry_attempts=1,
        network_retry_backoff_seconds=0.0,
    )
    return ExecutionAgent(
        model_provider=provider,
        enabled_capability_names=[],
        capability_descriptions={},
        tool_defaults={},
        wm_display=wm_display,
        system_prompt_base="[role]",
        tool_policy_prompt="",
    )


def test_execution_system_prompt_includes_wm_display_tail() -> None:
    wm_cfg = WorkingMemoryConfig(enable=True, inject_max_entries=1)
    agent = _minimal_execution_agent(wm_display=DefaultWMDisplay(wm_cfg))
    entries: List[Dict[str, Any]] = [
        {"kind": "time", "tool": "get_current_time", "summary": "2026-05-24"},
        {"kind": "recall", "tool": "shallow_recall", "question": "q1", "answer": "a1"},
        {"kind": "recall", "tool": "shallow_recall", "question": "q2", "answer": "a2"},
    ]
    prompt = agent._build_execution_system_prompt(wm_entries=entries)
    assert "[工作记忆]" in prompt
    assert "2026-05-24" not in prompt
    assert "q1" not in prompt
    assert "q2" in prompt


def test_execution_execute_honors_explicit_wm_entries_for_debug_prompt() -> None:
    wm_cfg = WorkingMemoryConfig(enable=True)
    agent = _minimal_execution_agent(wm_display=DefaultWMDisplay(wm_cfg))
    captured: Dict[str, Any] = {}

    def _fake_create_agent(*, system_prompt: str, **kwargs: Any) -> MagicMock:
        captured["system_prompt"] = system_prompt
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"structured_response": MagicMock(answer="done")}
        return mock_agent

    wm_entries = [{"kind": "time", "tool": "get_current_time", "summary": "now"}]
    with (
        patch("m_agent.layers.execution.core.build_controller_tools", return_value=[MagicMock()]),
        patch("m_agent.layers.execution.core.create_agent", side_effect=_fake_create_agent),
    ):
        agent.execute(
            ExecutionRequest(instruction="check time", thread_id="t1"),
            wm_entries=wm_entries,
        )

    assert "[工作记忆]" in str(captured.get("system_prompt", ""))
    assert "now" in str(captured.get("system_prompt", ""))
