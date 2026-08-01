from __future__ import annotations

from typing import Any

import yaml

from m_agent.layers.execution.model_provider import ModelProvider
from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.layers.thinking import (
    ConversationStateRegistry,
    ThinkingAgent,
    TransactionResolution,
)
from m_agent.paths import PROJECT_ROOT
from m_agent.prompt_utils import load_resolved_prompt_config
from m_agent.runtime.think_life.config import ThinkLifeConfig
from m_agent.runtime.think_life.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    TransactionKind,
    TransactionLifecycle,
)
from m_agent.runtime.think_life.perception.attributor import TransactionAttributor
from m_agent.runtime.think_life.perception.matcher_scene_view import (
    format_transaction_scene_view,
)
from m_agent.runtime.think_life.transaction_registry import TransactionRegistry
from m_agent.systems.episodic import DefaultEpisodeRecorder


_PROMPT_PATH = (
    PROJECT_ROOT
    / "config"
    / "agents"
    / "chat"
    / "runtime"
    / "chat_controller_runtime.yaml"
)


class _CapturingStructuredModel:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    def invoke(self, messages: list[dict[str, str]]) -> TransactionResolution:
        self.calls.append(list(messages))
        return TransactionResolution(
            action="continue",
            transaction_id="candidate_1",
        )


class _CapturingChatModel:
    def __init__(self) -> None:
        self.structured = _CapturingStructuredModel()

    def with_structured_output(
        self,
        _schema: type,
        **_kwargs: Any,
    ) -> _CapturingStructuredModel:
        return self.structured


class _NoopExecutionAgent:
    pass


def _entry(
    seq: int,
    text: str,
    *,
    transaction_id: str,
    actor: SceneActor,
    entry_type: SceneEntryType,
) -> SceneEntry:
    return SceneEntry(
        seq=seq,
        occurred_at=f"2026-08-01T10:00:{seq:02d}+08:00",
        entry_type=entry_type,
        actor=actor,
        text=text,
        transaction_id=transaction_id,
    )


def _current_stimulus() -> StimulusEnvelope:
    return StimulusEnvelope(
        stimulus_id="stim-current",
        thread_id="thread-current",
        conversation_id="conversation-current",
        stimulus=Stimulus(
            kind=StimulusKind.USER_MESSAGE,
            text="CURRENT-CONTINUATION",
        ),
        occurred_at="2026-08-01T10:01:00+08:00",
    )


def _resolver_prompt(
    language: str,
    scene_context: str,
    *,
    candidates: list[dict[str, Any]] | None = None,
    expected_selected: str | None = "candidate_1",
) -> str:
    resolved = load_resolved_prompt_config(_PROMPT_PATH, language=language)
    resolver = resolved["chat_controller"]["thinking"]["resolve_transaction"]
    model = _CapturingChatModel()
    agent = ThinkingAgent(
        execution_agent=_NoopExecutionAgent(),  # type: ignore[arg-type]
        model_provider=ModelProvider(model=model, network_retry_attempts=1),
        system_prompt="unused",
        episode_recorder=DefaultEpisodeRecorder(),
        state_registry=ConversationStateRegistry(),
        prompt_language=language,
        transaction_resolution_base_prompt=resolver["base_prompt"],
        transaction_resolution_instructions_prompt=resolver["instructions"],
    )

    selected = agent.resolve_transaction(
        _current_stimulus().stimulus,
        candidates
        if candidates is not None
        else [
            {
                "ref": "candidate_1",
                "state": "pause",
                "goal": "GOAL-ONE",
                "remaining": [],
            },
            {
                "ref": "candidate_2",
                "state": "complete",
                "goal": "GOAL-TWO",
                "remaining": [],
            },
        ],
        scene_context=scene_context,
    )

    assert selected == expected_selected
    assert len(model.structured.calls) == 1
    messages = model.structured.calls[0]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "CURRENT-CONTINUATION" not in messages[0]["content"]
    prompt = "\n\n".join(message["content"] for message in messages)
    assert resolver["base_prompt"] in prompt
    assert resolver["instructions"] in prompt
    assert prompt.count("CURRENT-CONTINUATION") == 1
    assert "GOAL-ONE" not in prompt
    assert "GOAL-TWO" not in prompt
    assert "[Dialogue History]" not in prompt
    return prompt


def test_match_candidates_are_limited_to_current_conversation() -> None:
    registry = TransactionRegistry()
    current = registry.create(
        thread_id="thread-current",
        conversation_id="conversation-current",
        kind=TransactionKind.USER_TASK,
    )
    other = registry.create(
        thread_id="thread-other",
        conversation_id="conversation-other",
        kind=TransactionKind.USER_TASK,
    )
    running = registry.create(
        thread_id="thread-current",
        conversation_id="conversation-current",
        kind=TransactionKind.USER_TASK,
    )
    registry.pause(current.transaction_id)
    registry.pause(other.transaction_id)

    attributor = TransactionAttributor(
        registry=registry,
        config=ThinkLifeConfig(),
    )

    candidates = attributor.list_match_candidates("conversation-current")

    assert [item.transaction_id for item in candidates] == [current.transaction_id]
    assert other.transaction_id not in {item.transaction_id for item in candidates}
    assert running.transaction_id not in {item.transaction_id for item in candidates}


def test_attributor_builds_the_transaction_labeled_interaction_view() -> None:
    registry = TransactionRegistry()
    first = registry.create(
        thread_id="thread-current",
        conversation_id="conversation-current",
        kind=TransactionKind.USER_TASK,
    )
    second = registry.create(
        thread_id="thread-current",
        conversation_id="conversation-current",
        kind=TransactionKind.USER_TASK,
    )
    registry.pause(first.transaction_id)
    registry.pause(second.transaction_id)
    captured: dict[str, Any] = {}

    def resolver(
        _stimulus: StimulusEnvelope,
        candidates: list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        captured["candidates"] = candidates
        captured.update(kwargs)
        return "candidate_1"

    attributor = TransactionAttributor(
        registry=registry,
        config=ThinkLifeConfig(),
        semantic_resolver=resolver,
    )
    resolved, created = attributor.resolve(
        _current_stimulus(),
        scene_entries=[
            _entry(
                1,
                "FIRST-VISIBLE-QUESTION",
                transaction_id=first.transaction_id,
                actor=SceneActor.ASSISTANT,
                entry_type=SceneEntryType.REPLY,
            ),
            _entry(
                2,
                "INTERNAL-FEEDBACK",
                transaction_id=first.transaction_id,
                actor=SceneActor.WORK,
                entry_type=SceneEntryType.OUTCOME,
            ),
            _entry(
                3,
                "SECOND-VISIBLE-REPLY",
                transaction_id=second.transaction_id,
                actor=SceneActor.ASSISTANT,
                entry_type=SceneEntryType.REPLY,
            ),
        ],
    )

    assert created is False
    assert resolved.transaction_id == first.transaction_id
    assert [item["ref"] for item in captured["candidates"]] == [
        "candidate_1",
        "candidate_2",
    ]
    timeline = str(captured["scene_context"])
    assert "FIRST-VISIBLE-QUESTION" in timeline
    assert "SECOND-VISIBLE-REPLY" in timeline
    assert "INTERNAL-FEEDBACK" not in timeline
    assert "tx=candidate_1" in timeline
    assert "tx=candidate_2" in timeline
    assert timeline.count("CURRENT-CONTINUATION") == 1
    assert first.transaction_id not in timeline
    assert second.transaction_id not in timeline


def test_deleted_scene_is_deprecated_and_deprecated_ref_cannot_be_selected() -> None:
    registry = TransactionRegistry()
    deleted = registry.create(
        thread_id="thread-current",
        conversation_id="conversation-current",
        kind=TransactionKind.USER_TASK,
    )
    deleted = registry.delete(deleted.transaction_id)
    candidate = registry.create(
        thread_id="thread-current",
        conversation_id="conversation-current",
        kind=TransactionKind.USER_TASK,
    )
    registry.pause(candidate.transaction_id)
    captured: dict[str, Any] = {}

    def resolver(
        _stimulus: StimulusEnvelope,
        candidates: list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        captured["candidates"] = candidates
        captured.update(kwargs)
        return "deprecated_1"

    attributor = TransactionAttributor(
        registry=registry,
        config=ThinkLifeConfig(),
        semantic_resolver=resolver,
    )
    resolved, created = attributor.resolve(
        _current_stimulus(),
        scene_entries=[
            _entry(
                1,
                "DELETED-HISTORICAL-QUESTION",
                transaction_id=deleted.transaction_id,
                actor=SceneActor.USER,
                entry_type=SceneEntryType.UTTERANCE,
            ),
            _entry(
                2,
                "LIVE-CANDIDATE-QUESTION",
                transaction_id=candidate.transaction_id,
                actor=SceneActor.ASSISTANT,
                entry_type=SceneEntryType.REPLY,
            ),
        ],
    )

    assert created is True
    assert resolved.transaction_id not in {
        deleted.transaction_id,
        candidate.transaction_id,
    }
    assert [item["ref"] for item in captured["candidates"]] == ["candidate_1"]
    timeline = str(captured["scene_context"])
    assert "DELETED-HISTORICAL-QUESTION" in timeline
    assert "tx=deprecated_1" in timeline
    assert "tx=candidate_1" in timeline
    assert "not selectable/restorable" in timeline
    assert deleted.transaction_id not in timeline
    persisted_deleted = registry.get(deleted.transaction_id)
    assert persisted_deleted is not None
    assert persisted_deleted.lifecycle_status == TransactionLifecycle.DELETED
    assert persisted_deleted.current_activation_id is None


def test_only_deleted_transaction_still_reaches_matcher_and_must_create() -> None:
    registry = TransactionRegistry()
    deleted = registry.create(
        thread_id="thread-current",
        conversation_id="conversation-current",
        kind=TransactionKind.USER_TASK,
    )
    deleted = registry.delete(deleted.transaction_id)
    captured: dict[str, Any] = {}
    resolver_calls = 0

    def resolver(
        _stimulus: StimulusEnvelope,
        candidates: list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        nonlocal resolver_calls
        resolver_calls += 1
        captured["candidates"] = candidates
        captured.update(kwargs)
        # Even a candidate-shaped output cannot select anything when the
        # current conversation has no live pause/complete candidate.
        return "candidate_1"

    attributor = TransactionAttributor(
        registry=registry,
        config=ThinkLifeConfig(),
        semantic_resolver=resolver,
    )
    resolved, created = attributor.resolve(
        _current_stimulus(),
        scene_entries=[
            _entry(
                1,
                "ONLY-DELETED-HISTORICAL-QUESTION",
                transaction_id=deleted.transaction_id,
                actor=SceneActor.USER,
                entry_type=SceneEntryType.UTTERANCE,
            )
        ],
    )

    assert resolver_calls == 1
    assert captured["candidates"] == []
    timeline = str(captured["scene_context"])
    assert "ONLY-DELETED-HISTORICAL-QUESTION" in timeline
    assert "tx=deprecated_1" in timeline
    assert deleted.transaction_id not in timeline
    assert created is True
    assert resolved.transaction_id != deleted.transaction_id
    assert resolved.conversation_id == deleted.conversation_id
    persisted_deleted = registry.get(deleted.transaction_id)
    assert persisted_deleted is not None
    assert persisted_deleted.lifecycle_status == TransactionLifecycle.DELETED


def test_production_matcher_builds_deprecated_prompt_without_live_candidates() -> None:
    scene_context = format_transaction_scene_view(
        [
            _entry(
                1,
                "ONLY-DEPRECATED-PROMPT-HISTORY",
                transaction_id="deleted-private-id",
                actor=SceneActor.ASSISTANT,
                entry_type=SceneEntryType.REPLY,
            )
        ],
        candidate_refs_by_transaction_id={},
        deprecated_transaction_ids={"deleted-private-id"},
        current_stimulus=_current_stimulus(),
    )

    prompt = _resolver_prompt(
        "en",
        scene_context,
        candidates=[],
        expected_selected=None,
    )

    assert "ONLY-DEPRECATED-PROMPT-HISTORY" in prompt
    assert "tx=deprecated_1" in prompt
    assert "not selectable/restorable" in prompt
    assert "deleted-private-id" not in prompt


def test_deleted_durable_id_is_rejected_and_live_candidate_remains_selectable() -> None:
    registry = TransactionRegistry()
    deleted = registry.create(
        thread_id="thread-current",
        conversation_id="conversation-current",
        kind=TransactionKind.USER_TASK,
    )
    deleted = registry.delete(deleted.transaction_id)
    candidate = registry.create(
        thread_id="thread-current",
        conversation_id="conversation-current",
        kind=TransactionKind.USER_TASK,
    )
    registry.pause(candidate.transaction_id)
    decisions = iter([deleted.transaction_id, "candidate_1"])

    def resolver(
        _stimulus: StimulusEnvelope,
        _candidates: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> str:
        return next(decisions)

    attributor = TransactionAttributor(
        registry=registry,
        config=ThinkLifeConfig(),
        semantic_resolver=resolver,
    )
    scene_entries = [
        _entry(
            1,
            "DELETED-HISTORICAL-REPLY",
            transaction_id=deleted.transaction_id,
            actor=SceneActor.ASSISTANT,
            entry_type=SceneEntryType.REPLY,
        )
    ]

    rejected, rejected_created = attributor.resolve(
        _current_stimulus(),
        scene_entries=scene_entries,
    )
    selected, selected_created = attributor.resolve(
        _current_stimulus(),
        scene_entries=scene_entries,
    )

    assert rejected_created is True
    assert rejected.transaction_id not in {
        deleted.transaction_id,
        candidate.transaction_id,
    }
    assert selected_created is False
    assert selected.transaction_id == candidate.transaction_id
    assert selected.lifecycle_status == TransactionLifecycle.ACTIVE
    persisted_deleted = registry.get(deleted.transaction_id)
    assert persisted_deleted is not None
    assert persisted_deleted.lifecycle_status == TransactionLifecycle.DELETED


def test_deprecated_ids_are_scoped_to_the_current_conversation() -> None:
    registry = TransactionRegistry()
    local_candidate = registry.create(
        thread_id="thread-current",
        conversation_id="conversation-current",
        kind=TransactionKind.USER_TASK,
    )
    registry.pause(local_candidate.transaction_id)
    remote_deleted = registry.create(
        thread_id="thread-remote",
        conversation_id="conversation-remote",
        kind=TransactionKind.USER_TASK,
    )
    remote_deleted = registry.delete(remote_deleted.transaction_id)
    captured: dict[str, Any] = {}

    def resolver(
        _stimulus: StimulusEnvelope,
        _candidates: list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        captured.update(kwargs)
        return "candidate_1"

    attributor = TransactionAttributor(
        registry=registry,
        config=ThinkLifeConfig(),
        semantic_resolver=resolver,
    )
    selected, created = attributor.resolve(
        _current_stimulus(),
        # Production readers already scope Scene to the current conversation.
        # This synthetic remote row verifies that deprecated classification
        # itself cannot cross that boundary either.
        scene_entries=[
            _entry(
                1,
                "SYNTHETIC-REMOTE-HISTORY",
                transaction_id=remote_deleted.transaction_id,
                actor=SceneActor.USER,
                entry_type=SceneEntryType.UTTERANCE,
            )
        ],
    )

    assert created is False
    assert selected.transaction_id == local_candidate.transaction_id
    timeline = str(captured["scene_context"])
    assert "SYNTHETIC-REMOTE-HISTORY" in timeline
    assert "tx=context_1" in timeline
    assert "tx=deprecated_" not in timeline
    assert remote_deleted.transaction_id not in timeline


def test_transaction_scene_timeline_is_the_prompt_context_without_internal_events() -> None:
    entries = [
        _entry(
            1,
            "VISIBLE-ONE-USER",
            transaction_id="durable-private-one",
            actor=SceneActor.USER,
            entry_type=SceneEntryType.UTTERANCE,
        ),
        _entry(
            2,
            "VISIBLE-TWO-USER",
            transaction_id="durable-private-two",
            actor=SceneActor.USER,
            entry_type=SceneEntryType.UTTERANCE,
        ),
        _entry(
            3,
            "INTERNAL-TOOL-ACTION",
            transaction_id="durable-private-one",
            actor=SceneActor.WORK,
            entry_type=SceneEntryType.ACTION,
        ),
        _entry(
            4,
            "VISIBLE-ONE-ASSISTANT-QUESTION",
            transaction_id="durable-private-one",
            actor=SceneActor.ASSISTANT,
            entry_type=SceneEntryType.REPLY,
        ),
        _entry(
            5,
            "INTERNAL-EXECUTION-FEEDBACK",
            transaction_id="durable-private-one",
            actor=SceneActor.WORK,
            entry_type=SceneEntryType.OUTCOME,
        ),
        _entry(
            6,
            "VISIBLE-TWO-ASSISTANT-REPLY",
            transaction_id="durable-private-two",
            actor=SceneActor.ASSISTANT,
            entry_type=SceneEntryType.REPLY,
        ),
    ]
    scene_context = format_transaction_scene_view(
        entries,
        candidate_refs_by_transaction_id={
            "durable-private-one": "candidate_1",
            "durable-private-two": "candidate_2",
        },
        current_stimulus=_current_stimulus(),
    )

    prompt = _resolver_prompt("en", scene_context)

    assert "[Transaction-aware user interaction" in prompt
    assert prompt.index("VISIBLE-ONE-USER") < prompt.index("VISIBLE-TWO-USER")
    assert prompt.index("VISIBLE-TWO-USER") < prompt.index(
        "VISIBLE-ONE-ASSISTANT-QUESTION"
    )
    assert prompt.index("VISIBLE-ONE-ASSISTANT-QUESTION") < prompt.index(
        "VISIBLE-TWO-ASSISTANT-REPLY"
    )
    assert "tx=candidate_1" in prompt
    assert "tx=candidate_2" in prompt
    assert "CURRENT-CONTINUATION" in prompt
    assert "INTERNAL-TOOL-ACTION" not in prompt
    assert "INTERNAL-EXECUTION-FEEDBACK" not in prompt
    assert "durable-private-one" not in prompt
    assert "durable-private-two" not in prompt


def test_transaction_resolver_prompt_is_shipped_and_used_in_both_languages() -> None:
    raw = yaml.safe_load(_PROMPT_PATH.read_text(encoding="utf-8"))
    resolver = raw["chat_controller"]["thinking"]["resolve_transaction"]
    for section in ("base_prompt", "instructions"):
        assert set(resolver[section]) >= {"zh", "en"}
        assert str(resolver[section]["zh"]).strip()
        assert str(resolver[section]["en"]).strip()

    scene_context = format_transaction_scene_view(
        [
            _entry(
                1,
                "VISIBLE-BILINGUAL-CONTEXT",
                transaction_id="durable-private-one",
                actor=SceneActor.USER,
                entry_type=SceneEntryType.UTTERANCE,
            )
        ],
        candidate_refs_by_transaction_id={
            "durable-private-one": "candidate_1",
        },
        current_stimulus=_current_stimulus(),
    )

    zh_prompt = _resolver_prompt("zh", scene_context)
    en_prompt = _resolver_prompt("en", scene_context)

    assert "事务" in zh_prompt
    assert "transaction" in en_prompt.lower()
    assert "VISIBLE-BILINGUAL-CONTEXT" in zh_prompt
    assert "VISIBLE-BILINGUAL-CONTEXT" in en_prompt
    assert "durable-private-one" not in zh_prompt
    assert "durable-private-one" not in en_prompt
