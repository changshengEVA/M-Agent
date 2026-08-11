"""Focused contracts for stimulus-native Scene projection."""

from __future__ import annotations

import json

import pytest

from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
)
from m_agent.runtime.scene_projection import (
    build_stimulus_scene_entries,
    render_tool_action_text,
    render_tool_feedback_text,
)


def _stimulus(
    kind: StimulusKind,
    *,
    text: str,
    payload: dict | None = None,
    stimulus_id: str = "stim-1",
) -> StimulusEnvelope:
    return StimulusEnvelope(
        stimulus_id=stimulus_id,
        thread_id="thread-1",
        conversation_id="thread-1::0",
        stimulus=Stimulus(
            kind=kind,
            text=text,
            payload=dict(payload or {}),
        ),
        occurred_at="2026-08-11T08:00:00Z",
    )


def test_scene_entry_type_uses_stimulus_native_wire_values() -> None:
    assert SceneEntryType.STIMULUS_USER_MESSAGE.value == "Stimulus_USER_MESSAGE"
    assert (
        SceneEntryType.STIMULUS_EXECUTION_FEEDBACK.value
        == "Stimulus_EXECUTION_FEEDBACK"
    )
    assert SceneEntryType.STIMULUS_SCHEDULED_PLAN.value == "Stimulus_SCHEDULED_PLAN"
    assert (
        SceneEntryType.STIMULUS_OBSERVATION_TRIGGER.value
        == "Stimulus_OBSERVATION_TRIGGER"
    )
    assert SceneEntryType.THOUGHT.value == "Thought"
    assert SceneEntryType.ACTION.value == "Action"

    # Source compatibility: old callers may continue to spell a user stimulus
    # as UTTERANCE, while every newly serialized row uses the stimulus-native type.
    assert SceneEntryType.UTTERANCE is SceneEntryType.STIMULUS_USER_MESSAGE

    # Deprecated explicit construction remains available to old source code,
    # but production projection must not choose these legacy-only wire values.
    assert SceneEntryType.OUTCOME.value == "Outcome"
    assert SceneEntryType.REPLY.value == "Reply"


@pytest.mark.parametrize(
    ("legacy_type", "legacy_actor", "expected_type"),
    [
        (
            "utterance",
            "user",
            SceneEntryType.STIMULUS_USER_MESSAGE,
        ),
        ("thought", "think", SceneEntryType.THOUGHT),
        ("action", "work", SceneEntryType.ACTION),
        (
            "outcome",
            "work",
            SceneEntryType.STIMULUS_EXECUTION_FEEDBACK,
        ),
        ("reply", "assistant", SceneEntryType.ACTION),
    ],
)
def test_scene_entry_from_dict_migrates_legacy_lowercase_wire_types(
    legacy_type: str,
    legacy_actor: str,
    expected_type: SceneEntryType,
) -> None:
    entry = SceneEntry.from_dict(
        {
            "seq": 7,
            "occurred_at": "2026-07-19T16:14:13Z",
            "entry_type": legacy_type,
            "actor": legacy_actor,
            "text": "legacy row",
            "transaction_id": "txn-legacy",
        }
    )

    assert entry.entry_type is expected_type
    assert entry.text == "legacy row"
    assert entry.transaction_id == "txn-legacy"
    assert entry.to_dict()["entry_type"] == expected_type.value
    if legacy_type == "reply":
        assert entry.tool_name == "reply_to_user"


def test_scene_entry_round_trip_preserves_actor_name_and_role() -> None:
    entry = SceneEntry(
        seq=3,
        occurred_at="2026-08-11T08:00:01Z",
        entry_type=SceneEntryType.ACTION,
        actor=SceneActor.WORK,
        actor_name="Memory Assistant",
        text="Call web_search with the requested query.",
        append_id="append-action-1",
        transaction_id="txn-1",
        delegate_id="delegate-1",
        tool_name="web_search",
    )

    wire = entry.to_dict()

    assert wire["actor"] == "Memory Assistant"
    assert wire["actor_role"] == "work"

    restored = SceneEntry.from_dict(wire)
    assert restored.actor is SceneActor.WORK
    assert restored.actor_name == "Memory Assistant"
    assert restored.to_dict() == wire


def test_render_tool_action_text_includes_arguments_and_redacts_sensitive_values() -> None:
    text = render_tool_action_text(
        {
            "tool_name": "web_search",
            "params": {
                "query": "新海天 生日",
                "max_results": 5,
                "api_key": "sk-action-secret",
                "credentials": {
                    "access_token": "nested-action-secret",
                    "region": "cn",
                },
            },
        }
    )
    lowered = text.lower()

    assert "web_search" in text
    assert "arguments" in lowered
    assert "query" in lowered
    assert "新海天 生日" in text
    assert "max_results" in lowered
    assert "sk-action-secret" not in text
    assert "nested-action-secret" not in text
    assert text.strip() != "web_search"


def test_render_tool_feedback_text_includes_success_and_specific_result() -> None:
    text = render_tool_feedback_text(
        {
            "tool_name": "web_search",
            "result": {
                "success": True,
                "result_count": 3,
                "answer": "新海天的生日是 7 月 20 日。",
            },
        }
    )
    lowered = text.lower()

    assert "web_search" in text
    assert "success" in lowered
    assert "true" in lowered
    assert "result" in lowered
    assert "7 月 20 日" in text
    assert text.strip() != "web_search"


def test_render_tool_feedback_text_includes_failure_error() -> None:
    text = render_tool_feedback_text(
        {
            "tool_name": "web_search",
            "result": {
                "success": False,
                "error": "provider timeout",
            },
        }
    )
    lowered = text.lower()

    assert "web_search" in text
    assert "success" in lowered
    assert "false" in lowered
    assert "error" in lowered
    assert "provider timeout" in lowered
    assert text.strip() != "web_search"


def test_render_tool_feedback_text_tolerates_non_numeric_count_hints() -> None:
    text = render_tool_feedback_text(
        {
            "tool_name": "custom_search",
            "result": {
                "success": True,
                "count": "many",
                "content_chars": "unknown",
                "answer": "custom evidence remains available",
            },
        }
    )

    assert '"count":"many"' in text
    assert '"content_chars":"unknown"' in text
    assert "custom evidence remains available" in text


def test_render_tool_feedback_text_has_a_hard_valid_json_size_bound() -> None:
    text = render_tool_feedback_text(
        {
            "tool_name": 'custom_"\\\n' * 2_000,
            "result": {
                "success": False,
                "error": 'failed_"\\\n' * 3_000,
                "summary": 'summary_"\\\n' * 3_000,
            },
        }
    )

    assert len(text) <= 8_000
    decoded = json.loads(text)
    assert decoded["success"] is False
    assert decoded["truncated"] is True


def test_scene_tool_json_normalizes_non_finite_floats() -> None:
    action = json.loads(
        render_tool_action_text(
            {
                "tool_name": "custom_tool",
                "params": {"nan_score": float("nan")},
            }
        ),
        parse_constant=lambda value: pytest.fail(
            f"non-standard JSON constant: {value}"
        ),
    )
    feedback = json.loads(
        render_tool_feedback_text(
            {
                "tool_name": "custom_tool",
                "result": {
                    "success": True,
                    "upper_bound": float("inf"),
                },
            }
        ),
        parse_constant=lambda value: pytest.fail(
            f"non-standard JSON constant: {value}"
        ),
    )

    assert action["arguments"]["nan_score"] == "nan"
    assert feedback["result"]["upper_bound"] == "inf"


def test_feedback_keeps_transport_error_when_result_error_is_empty() -> None:
    decoded = json.loads(
        render_tool_feedback_text(
            {
                "tool_name": "custom_tool",
                "error": "transport timeout",
                "result": {"success": False, "error": ""},
            }
        )
    )

    assert decoded["success"] is False
    assert decoded["error"] == "transport timeout"


@pytest.mark.parametrize(
    (
        "stimulus",
        "agent_name",
        "expected_type",
        "expected_actor_name",
        "expected_actor_role",
    ),
    [
        (
            _stimulus(
                StimulusKind.USER_MESSAGE,
                text="请查一下新海天的生日",
                payload={"subject": "changshengeva"},
                stimulus_id="stim-user",
            ),
            "Memory Assistant",
            SceneEntryType.STIMULUS_USER_MESSAGE,
            "changshengeva",
            SceneActor.USER,
        ),
        (
            _stimulus(
                StimulusKind.SCHEDULED_PLAN,
                text="提醒用户做饭",
                payload={"schedule_id": "schedule-1"},
                stimulus_id="stim-schedule",
            ),
            "Memory Assistant",
            SceneEntryType.STIMULUS_SCHEDULED_PLAN,
            "Memory Assistant",
            SceneActor.ASSISTANT,
        ),
        (
            _stimulus(
                StimulusKind.OBSERVATION_TRIGGER,
                text="检测到一封新邮件",
                payload={
                    "monitor_id": "mailbox_observer",
                    "source": "mailbox",
                },
                stimulus_id="stim-observation",
            ),
            "Memory Assistant",
            SceneEntryType.STIMULUS_OBSERVATION_TRIGGER,
            "mailbox_observer",
            SceneActor.WORK,
        ),
    ],
)
def test_build_stimulus_scene_entries_projects_single_stimulus(
    stimulus: StimulusEnvelope,
    agent_name: str,
    expected_type: SceneEntryType,
    expected_actor_name: str,
    expected_actor_role: SceneActor,
) -> None:
    entries = build_stimulus_scene_entries(
        stimulus,
        transaction_id="txn-1",
        agent_name=agent_name,
    )

    assert len(entries) == 1
    entry = entries[0]
    assert entry.entry_type is expected_type
    assert entry.actor is expected_actor_role
    assert entry.actor_name == expected_actor_name
    assert entry.to_dict()["actor"] == expected_actor_name
    assert entry.text == stimulus.text
    assert entry.transaction_id == "txn-1"
    assert entry.append_id == stimulus.stimulus_id


def test_build_stimulus_scene_entries_projects_each_feedback_tool_result() -> None:
    stimulus = _stimulus(
        StimulusKind.EXECUTION_FEEDBACK,
        text="execution finished",
        stimulus_id="stim-feedback",
        payload={
            "tool_history": [
                {
                    "tool_name": "web_search",
                    "params": {"query": "新海天 生日"},
                    "result": {
                        "success": True,
                        "result_count": 3,
                        "answer": "搜索发现生日为 7 月 20 日。",
                    },
                },
                {
                    "tool_name": "page_reader",
                    "params": {"url": "https://example.test/character"},
                    "result": {
                        "success": False,
                        "error": "page unavailable",
                    },
                },
            ]
        },
    )

    entries = build_stimulus_scene_entries(
        stimulus,
        transaction_id="txn-feedback",
        agent_name="Memory Assistant",
    )

    assert len(entries) == 2
    assert [entry.entry_type for entry in entries] == [
        SceneEntryType.STIMULUS_EXECUTION_FEEDBACK,
        SceneEntryType.STIMULUS_EXECUTION_FEEDBACK,
    ]
    assert [entry.actor for entry in entries] == [
        SceneActor.WORK,
        SceneActor.WORK,
    ]
    assert [entry.actor_name for entry in entries] == [
        "web_search",
        "page_reader",
    ]
    assert all(entry.transaction_id == "txn-feedback" for entry in entries)
    assert len({entry.append_id for entry in entries}) == 2
    assert all(
        str(entry.append_id or "").startswith(stimulus.stimulus_id)
        for entry in entries
    )
    assert "7 月 20 日" in entries[0].text
    assert entries[0].text.strip() != "web_search"
    assert "page unavailable" in entries[1].text
    assert entries[1].text.strip() != "page_reader"
