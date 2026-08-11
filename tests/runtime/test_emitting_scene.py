"""Scene append notifications follow durable append creation semantics."""

from __future__ import annotations

from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
)
from m_agent.runtime.host.emitting_scene import EmittingSceneWriter
from m_agent.systems.scene.default.jsonl_store import (
    SceneLogStore,
    SceneWriterAdapter,
)


def test_idempotent_scene_replay_emits_only_when_row_is_created() -> None:
    store = SceneLogStore(persist_enabled=False)
    emitted: list[SceneEntry] = []
    writer = EmittingSceneWriter(
        SceneWriterAdapter(store),
        lambda _conversation_id, entry: emitted.append(entry),
    )
    entry = SceneEntry(
        seq=0,
        occurred_at="2026-08-11T08:00:00Z",
        entry_type=SceneEntryType.STIMULUS_EXECUTION_FEEDBACK,
        actor=SceneActor.WORK,
        actor_name="web_search",
        text='{"success":true,"tool_name":"web_search"}',
        transaction_id="txn-1",
    )

    first = writer.append("thread-1::0", entry, append_id="stim-feedback-1")
    replay = writer.append("thread-1::0", entry, append_id="stim-feedback-1")

    assert replay.to_dict() == first.to_dict()
    assert [item.append_id for item in emitted] == ["stim-feedback-1"]
    assert len(store.tail("thread-1::0")) == 1
