"""Strict Pydantic contracts for one joint thinking turn."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from m_agent.layers.thinking.contracts import (
    DecisionOutput,
    TaskStateOutput,
    ThinkingTurnOutput,
)


def _valid_payload(*, mode: str = "silent") -> dict[str, Any]:
    decisions = {
        "execute": {
            "mode": "execute",
            "tool_name": "deep_recall",
            "instruction": "Find the relevant memory",
            "answer": None,
            "episode_note": None,
        },
        "answer_directly": {
            "mode": "answer_directly",
            "tool_name": None,
            "instruction": None,
            "answer": "Here is the answer.",
            "episode_note": "The user asked a stable preference question.",
        },
        "silent": {
            "mode": "silent",
            "tool_name": None,
            "instruction": None,
            "answer": None,
            "episode_note": None,
        },
    }
    return {
        "reason": "The current evidence determines the next action.",
        "task_state": {
            "goal": "answer the request",
            "completion_status": "processing",
            "completed": ["understand the request"],
            "remaining": ["perform the current step", "deliver the result"],
        },
        "decision": decisions[mode],
    }


@pytest.mark.parametrize("mode", ["execute", "answer_directly", "silent"])
def test_thinking_turn_accepts_each_valid_decision_mode(mode: str) -> None:
    output = ThinkingTurnOutput.model_validate(_valid_payload(mode=mode))

    assert output.decision.mode == mode
    assert output.task_state.completion_status == "processing"
    assert output.task_state.remaining[0] == "perform the current step"


def test_joint_contract_declares_generation_fields_in_required_order() -> None:
    assert list(ThinkingTurnOutput.model_fields) == [
        "reason",
        "task_state",
        "decision",
    ]
    assert list(TaskStateOutput.model_fields) == [
        "goal",
        "completion_status",
        "completed",
        "remaining",
    ]
    assert list(DecisionOutput.model_fields) == [
        "mode",
        "tool_name",
        "instruction",
        "answer",
        "episode_note",
    ]


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("top", "reason"),
        ("top", "task_state"),
        ("top", "decision"),
        ("task_state", "goal"),
        ("task_state", "completion_status"),
        ("task_state", "completed"),
        ("task_state", "remaining"),
        ("decision", "mode"),
    ],
)
def test_joint_contract_rejects_every_missing_required_field(
    section: str,
    field: str,
) -> None:
    payload = _valid_payload(mode="execute")
    target = payload if section == "top" else payload[section]
    target.pop(field)

    with pytest.raises(ValidationError):
        ThinkingTurnOutput.model_validate(payload)


@pytest.mark.parametrize("field", ["tool_name", "instruction", "answer", "episode_note"])
def test_decision_contract_allows_irrelevant_nullable_fields_to_be_omitted(
    field: str,
) -> None:
    payload = _valid_payload(mode="silent")
    payload["decision"].pop(field)

    output = ThinkingTurnOutput.model_validate(payload)

    assert output.decision.mode == "silent"
    assert getattr(output.decision, field) is None


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("top", "unexpected"),
        ("task_state", "current_step"),
        ("decision", "request_complete"),
        ("decision", "reasoning"),
        ("decision", "capability_hint"),
    ],
)
def test_joint_contract_forbids_top_level_nested_and_legacy_fields(
    section: str,
    field: str,
) -> None:
    payload = _valid_payload()
    target = payload if section == "top" else payload[section]
    target[field] = "forbidden"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ThinkingTurnOutput.model_validate(payload)


@pytest.mark.parametrize("status", ["done", "paused", "", None])
def test_task_state_rejects_unknown_completion_status(status: Any) -> None:
    payload = _valid_payload()
    payload["task_state"]["completion_status"] = status

    with pytest.raises(ValidationError):
        ThinkingTurnOutput.model_validate(payload)


@pytest.mark.parametrize("mode", ["reply", "wait", "defer", "unknown", ""])
def test_decision_output_rejects_noncanonical_modes(mode: str) -> None:
    payload = _valid_payload()
    payload["decision"]["mode"] = mode

    with pytest.raises(ValidationError):
        ThinkingTurnOutput.model_validate(payload)


@pytest.mark.parametrize(
    "decision",
    [
        {
            "mode": "execute",
            "tool_name": None,
            "instruction": "Find it",
            "answer": None,
            "episode_note": None,
        },
        {
            "mode": "execute",
            "tool_name": "deep_recall",
            "instruction": "   ",
            "answer": None,
            "episode_note": None,
        },
        {
            "mode": "execute",
            "tool_name": "deep_recall",
            "instruction": "Find it",
            "answer": "Premature answer",
            "episode_note": None,
        },
        {
            "mode": "answer_directly",
            "tool_name": None,
            "instruction": None,
            "answer": "   ",
            "episode_note": None,
        },
        {
            "mode": "answer_directly",
            "tool_name": "deep_recall",
            "instruction": None,
            "answer": "Answer",
            "episode_note": None,
        },
        {
            "mode": "answer_directly",
            "tool_name": None,
            "instruction": "Do work",
            "answer": "Answer",
            "episode_note": None,
        },
        {
            "mode": "silent",
            "tool_name": "deep_recall",
            "instruction": None,
            "answer": None,
            "episode_note": None,
        },
        {
            "mode": "silent",
            "tool_name": None,
            "instruction": "Do work",
            "answer": None,
            "episode_note": None,
        },
        {
            "mode": "silent",
            "tool_name": None,
            "instruction": None,
            "answer": "Unexpected answer",
            "episode_note": None,
        },
    ],
    ids=[
        "execute-missing-tool",
        "execute-blank-instruction",
        "execute-with-answer",
        "answer-blank",
        "answer-with-tool",
        "answer-with-instruction",
        "silent-with-tool",
        "silent-with-instruction",
        "silent-with-answer",
    ],
)
def test_decision_output_rejects_invalid_mode_field_combinations(
    decision: dict[str, Any],
) -> None:
    payload = _valid_payload()
    payload["decision"] = deepcopy(decision)

    with pytest.raises(ValidationError):
        ThinkingTurnOutput.model_validate(payload)


@pytest.mark.parametrize(
    ("decision", "expected_code"),
    [
        (
            {"mode": "execute", "instruction": "Find it"},
            "decision_execute_fields_missing",
        ),
        (
            {
                "mode": "execute",
                "tool_name": "deep_recall",
                "instruction": "Find it",
                "answer": "too early",
            },
            "decision_execute_answer_present",
        ),
        ({"mode": "answer_directly"}, "decision_answer_missing"),
        (
            {
                "mode": "answer_directly",
                "answer": "Done",
                "tool_name": "deep_recall",
            },
            "decision_answer_fields_present",
        ),
        (
            {"mode": "silent", "answer": "unexpected"},
            "decision_silent_fields_present",
        ),
    ],
)
def test_decision_semantic_failures_expose_stable_safe_error_codes(
    decision: dict[str, Any],
    expected_code: str,
) -> None:
    payload = _valid_payload()
    payload["decision"] = decision

    with pytest.raises(ValidationError) as exc_info:
        ThinkingTurnOutput.model_validate(payload)

    assert exc_info.value.errors(include_input=False)[0]["type"] == expected_code


def test_nullable_action_fields_may_use_empty_strings_when_semantically_empty() -> None:
    direct = _valid_payload(mode="answer_directly")
    direct["decision"]["tool_name"] = ""
    direct["decision"]["instruction"] = "   "
    silent = _valid_payload(mode="silent")
    silent["decision"]["tool_name"] = "   "
    silent["decision"]["instruction"] = ""
    silent["decision"]["answer"] = "   "

    assert ThinkingTurnOutput.model_validate(direct).decision.mode == "answer_directly"
    assert ThinkingTurnOutput.model_validate(silent).decision.mode == "silent"


@pytest.mark.parametrize("reason", ["", " ", "\n\t"])
def test_reason_must_not_be_blank(reason: str) -> None:
    payload = _valid_payload()
    payload["reason"] = reason

    with pytest.raises(ValidationError):
        ThinkingTurnOutput.model_validate(payload)


def test_reason_is_trimmed_before_projection() -> None:
    payload = _valid_payload()
    payload["reason"] = "  concise rationale  "

    output = ThinkingTurnOutput.model_validate(payload)

    assert output.reason == "concise rationale"


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("top", "reason", "x" * 1001),
        ("task_state", "goal", "x" * 1001),
        ("task_state", "completed", [f"step-{n}" for n in range(33)]),
        ("task_state", "remaining", [f"step-{n}" for n in range(33)]),
    ],
)
def test_joint_contract_enforces_documented_size_limits(
    section: str,
    field: str,
    value: Any,
) -> None:
    payload = _valid_payload()
    target = payload if section == "top" else payload[section]
    target[field] = value

    with pytest.raises(ValidationError):
        ThinkingTurnOutput.model_validate(payload)
