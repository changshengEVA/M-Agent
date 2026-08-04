"""Verify YAML-loaded prompts are used by ThinkingAgent / persona helpers.

These tests poke at the prompt-assembly internals directly so they don't
require a real LLM or sub-agent stack.
"""
from __future__ import annotations

from m_agent.layers.execution.model_provider import ModelProvider
from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.layers.thinking import (
    ConversationStateRegistry,
    PerceptionInput,
    ThinkingAgent,
)
from m_agent.systems.episodic import DefaultEpisodeRecorder
from m_agent.layers.thinking.persona import (
    build_capability_boundary_block,
    build_runtime_context_block,
)
from m_agent.paths import PROJECT_ROOT
from m_agent.prompt_utils import load_resolved_prompt_config


class _CapabilityCatalog:
    def describe_capabilities_block(self) -> str:
        return "[mock-caps]\n- noop"


def test_capability_boundary_block_uses_override_header() -> None:
    body = "[caps]\n- shallow_recall"
    custom = "[OVERRIDE HEADER]\nfollow these rules"
    out = build_capability_boundary_block(body, language="zh", header_template=custom)
    assert out.startswith("[OVERRIDE HEADER]")
    assert "shallow_recall" in out


def test_capability_boundary_block_falls_back_to_default_when_blank() -> None:
    body = "[caps]\n- shallow_recall"
    out_zh = build_capability_boundary_block(body, language="zh", header_template="")
    out_en = build_capability_boundary_block(body, language="en", header_template="   ")
    assert "[可委托能力]" in out_zh
    assert "[Delegable Capabilities]" in out_en


def test_runtime_context_template_placeholders_are_substituted() -> None:
    context_tpl = "HEADER\nS=<source>\nCTX=<context_json>"
    block = build_runtime_context_block(
        source="schedule",
        system_context={"schedule_id": "sch_1"},
        language="zh",
        template=context_tpl,
    )
    assert "HEADER" in block
    assert "S=schedule" in block
    assert '"schedule_id"' in block and "sch_1" in block

    # Split templates remain accepted for existing user prompt configs.
    generic_tpl = "GEN\nS=<source>"
    block2 = build_runtime_context_block(
        source="external",
        system_context={"k": "v"},
        language="zh",
        schedule_template="should-not-be-used",
        generic_template=generic_tpl,
    )
    assert block2.startswith("GEN")
    assert "S=external" in block2


def test_runtime_context_block_skipped_when_user_and_empty_context() -> None:
    assert build_runtime_context_block(source="user", system_context=None, language="zh") == ""
    assert build_runtime_context_block(source="user", system_context={}, language="en") == ""


def test_thinking_agent_uses_override_joint_plan_and_fallback_prompts() -> None:
    custom_task_base = "[CUSTOM TASK STATE BASE]"
    custom_task_instructions = "[CUSTOM TASK STATE INSTRUCTIONS]"
    custom_plan = "[CUSTOM PLAN BLOCK]"
    custom_thinking_turn = "[CUSTOM THINKING TURN BLOCK]"
    custom_fallback = "兜底 OVERRIDE"
    custom_cap_header = "[CUSTOM CAP HEADER]"

    agent = ThinkingAgent(
        execution_agent=_CapabilityCatalog(),  # type: ignore[arg-type]
        model_provider=ModelProvider(model=None),
        system_prompt="SYS",
        wm_reader=None,
        episode_recorder=DefaultEpisodeRecorder(),
        state_registry=ConversationStateRegistry(),
        prompt_language="zh",
        task_state_base_prompt=custom_task_base,
        task_state_instructions_prompt=custom_task_instructions,
        plan_instructions_prompt=custom_plan,
        thinking_turn_instructions_prompt=custom_thinking_turn,
        capability_boundary_header=custom_cap_header,
        fallback_answer_prompt=custom_fallback,
    )

    # Joint, task-state, and legacy plan instruction blocks.
    assert agent._task_state_base_block() == custom_task_base
    assert agent._task_state_instructions_block() == custom_task_instructions
    assert agent._plan_instructions_block() == custom_plan
    assert agent._thinking_turn_instructions_block() == custom_thinking_turn

    # capability boundary header propagates into the assembled plan messages
    state = agent.state_registry.get_or_create("c::0", thread_id="t1")
    perception = PerceptionInput(
        thread_id="t1",
        conversation_id="c::0",
        transaction_id="txn-1",
        stimulus=Stimulus(
            kind=StimulusKind.SCHEDULED_PLAN,
            text="你好",
            payload={"schedule_id": "abc"},
        ),
    )
    messages = agent._build_plan_messages(perception, state)
    sys_text = messages[0]["content"]
    assert custom_cap_header in sys_text
    assert "CTX-CUSTOM" not in sys_text
    assert "kind: scheduled_plan" in sys_text
    assert custom_plan in sys_text

    joint_messages = agent._build_thinking_turn_messages(perception, state)
    joint_sys_text = joint_messages[0]["content"]
    assert custom_cap_header in joint_sys_text
    assert "[Previous Task State]" in joint_sys_text
    assert custom_thinking_turn in joint_sys_text
    assert custom_plan not in joint_sys_text

    # fallback answer override
    assert agent._fallback_answer(perception) == custom_fallback


def test_chat_controller_runtime_yaml_contains_thinking_prompt_sections() -> None:
    """Smoke test: the shipped YAML exposes the thinking prompt keys."""
    path = PROJECT_ROOT / "config" / "agents" / "chat" / "runtime" / "chat_controller_runtime.yaml"
    resolved = load_resolved_prompt_config(path, language="zh")
    cc = resolved.get("chat_controller")
    assert isinstance(cc, dict)

    thinking = cc.get("thinking")
    assert isinstance(thinking, dict), "chat_controller.thinking must be defined"
    for key in (
        "persona_tone_prompt",
        "persona_merge_template",
    ):
        assert isinstance(thinking.get(key), str) and thinking[key].strip(), f"missing or empty: thinking.{key}"

    thinking_turn = thinking.get("thinking_turn")
    assert isinstance(thinking_turn, dict), "chat_controller.thinking.thinking_turn must be defined"
    for key in ("base_prompt", "instructions"):
        assert isinstance(thinking_turn.get(key), str) and thinking_turn[key].strip(), f"missing or empty: thinking_turn.{key}"
    joint_instructions = thinking_turn["instructions"]
    assert "reason → task_state → decision" in joint_instructions
    assert joint_instructions.index("reason") < joint_instructions.index("task_state") < joint_instructions.index("decision")
    assert "不要输出 request_complete" in joint_instructions
    assert "完整快照" in joint_instructions
    assert "remaining[0]" in joint_instructions
    assert "stage=param_fill" in joint_instructions
    assert "schedule_create" in joint_instructions
    assert "[输出示例：仅演示结构与状态语义]" in joint_instructions
    assert '"mode": "answer_directly"' in joint_instructions
    assert '"completion_status": "processing"' in joint_instructions
    assert '"request_complete":' not in joint_instructions

    pre_gen = thinking.get("pre_gen_task_state")
    assert isinstance(pre_gen, dict), "chat_controller.thinking.pre_gen_task_state must be defined"
    for key in ("base_prompt", "instructions"):
        assert isinstance(pre_gen.get(key), str) and pre_gen[key].strip(), f"missing or empty: pre_gen_task_state.{key}"
    assert "completion_status" in pre_gen["instructions"]

    resolver = thinking.get("resolve_transaction")
    assert isinstance(resolver, dict), "chat_controller.thinking.resolve_transaction must be defined"
    for key in ("base_prompt", "instructions"):
        assert isinstance(resolver.get(key), str) and resolver[key].strip(), f"missing or empty: resolve_transaction.{key}"

    decision = thinking.get("make_decision")
    assert isinstance(decision, dict), "chat_controller.thinking.make_decision must be defined"
    for key in (
        "base_prompt",
        "instructions",
        "capability_boundary_header",
        "fallback_answer",
    ):
        assert isinstance(decision.get(key), str) and decision[key].strip(), f"missing or empty: make_decision.{key}"
    assert "completion_status==completed" in decision["instructions"]

    assert "base_prompt" not in thinking
    assert "plan_instructions" not in thinking
    assert "summarize_instructions" not in thinking
    assert "runtime_context_schedule" not in thinking
    assert "runtime_context_generic" not in thinking

    execution = cc.get("execution")
    assert isinstance(execution, dict), "chat_controller.execution must be defined"
    for key in ("capability_block_header",):
        assert isinstance(execution.get(key), str) and execution[key].strip(), f"missing or empty: execution.{key}"
    assert set(execution) == {"capability_block_header"}
