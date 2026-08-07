"""Stimulus Kernel v0.3: ingest, pool/disposition split, trace, recovery, lab."""

from __future__ import annotations

from pathlib import Path

import pytest

from m_agent.lab.stimulus import StimulusLab, run_stimulus_lab
from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.runtime.clock import VirtualClock
from m_agent.runtime.domain.contracts import StimulusEnvelope
from m_agent.runtime.transaction.store import SQLiteRuntimeStore, StaleClaimError
from m_agent.sdk.stimulus import Observation
from m_agent.sdk.stimulus.contracts import Disposition, PoolState


def _envelope(
    *,
    stimulus_id: str,
    ingress_key: str | None = None,
    text: str = "hello",
) -> StimulusEnvelope:
    return StimulusEnvelope(
        stimulus_id=stimulus_id,
        thread_id="t-kernel",
        conversation_id="t-kernel::0",
        stimulus=Stimulus(
            kind=StimulusKind.USER_MESSAGE,
            text=text,
            payload={},
        ),
        occurred_at="2026-01-01T00:00:00Z",
        ingress_key=ingress_key,
    )


def test_pool_state_and_disposition_are_split(tmp_path: Path) -> None:
    clock = VirtualClock("2026-01-01T00:00:00Z")
    store = SQLiteRuntimeStore(tmp_path / "kernel.sqlite3", clock=clock)
    admitted = store.admit_stimulus(
        _envelope(stimulus_id="stim_a", ingress_key="k-a"),
        effective_priority=10,
    )
    assert admitted.pool_state == PoolState.READY.value
    assert admitted.disposition is None
    assert admitted.terminal is False

    store.acquire_consumer(
        conversation_id="t-kernel::0",
        consumer_id="c1",
        lease_seconds=30,
    )
    claimed = store.claim_next_stimulus(
        conversation_id="t-kernel::0",
        consumer_id="c1",
    )
    assert claimed is not None
    assert claimed.pool_state == PoolState.RUNNING.value
    assert claimed.disposition is None

    final = store.finalize_stimulus(
        stimulus_id=claimed.stimulus_id,
        claim_token={
            "claimed_by": claimed.claimed_by,
            "consumer_epoch": claimed.consumer_epoch,
            "claim_epoch": claimed.claim_epoch,
        },
        disposition="completed",
        transition_id="fin-a",
        command_digest="completed",
    )
    assert final["pool_state"] == PoolState.TERMINATED.value
    assert final["disposition"] == Disposition.COMPLETED.value
    stored = store.load_stimulus(claimed.stimulus_id)
    assert stored is not None
    assert stored.pool_state == PoolState.TERMINATED.value
    assert stored.disposition == Disposition.COMPLETED.value
    stages = [
        item["stage"]
        for item in store.list_stimulus_trace(stimulus_id=claimed.stimulus_id)
    ]
    assert "admit" in stages
    assert "claim" in stages
    assert "final" in stages or "finalize" in stages


def test_ingress_key_merge_is_idempotent(tmp_path: Path) -> None:
    store = SQLiteRuntimeStore(tmp_path / "merge.sqlite3")
    first = store.admit_stimulus(
        _envelope(stimulus_id="stim_1", ingress_key="same"),
        effective_priority=1,
    )
    second = store.admit_stimulus(
        _envelope(stimulus_id="stim_2", ingress_key="same", text="dup"),
        effective_priority=1,
    )
    assert first.stimulus_id == second.stimulus_id
    assert len(store.list_stimuli(conversation_id="t-kernel::0")) == 1
    traces = store.list_stimulus_trace(ingress_key="same")
    assert any(item.get("disposition") == "merged" for item in traces)


def test_claim_crash_recovery_and_stale_finalize(tmp_path: Path) -> None:
    db = tmp_path / "recover.sqlite3"
    store = SQLiteRuntimeStore(db)
    store.admit_stimulus(
        _envelope(stimulus_id="stim_r", ingress_key="recover"),
        effective_priority=5,
    )
    store.acquire_consumer(
        conversation_id="t-kernel::0",
        consumer_id="worker-a",
        lease_seconds=30,
    )
    claimed = store.claim_next_stimulus(
        conversation_id="t-kernel::0",
        consumer_id="worker-a",
    )
    assert claimed is not None
    assert claimed.pool_state == PoolState.RUNNING.value
    store.close()

    # Process restart: takeover and reclaim the running stimulus.
    recovered = SQLiteRuntimeStore(db)
    recovered.takeover_consumer(
        conversation_id="t-kernel::0",
        consumer_id="worker-b",
    )
    reclaimed = recovered.claim_next_stimulus(
        conversation_id="t-kernel::0",
        consumer_id="worker-b",
    )
    assert reclaimed is not None
    assert reclaimed.stimulus_id == claimed.stimulus_id
    assert reclaimed.pool_state == PoolState.RUNNING.value
    with pytest.raises(StaleClaimError):
        recovered.finalize_stimulus(
            stimulus_id=claimed.stimulus_id,
            claim_token={
                "claimed_by": claimed.claimed_by,
                "consumer_epoch": claimed.consumer_epoch,
                "claim_epoch": claimed.claim_epoch,
            },
            disposition="completed",
            transition_id="stale-fin",
            command_digest="completed",
        )
    recovered.finalize_stimulus(
        stimulus_id=reclaimed.stimulus_id,
        claim_token={
            "claimed_by": reclaimed.claimed_by,
            "consumer_epoch": reclaimed.consumer_epoch,
            "claim_epoch": reclaimed.claim_epoch,
        },
        disposition="completed",
        transition_id="ok-fin",
        command_digest="completed",
    )
    done = recovered.load_stimulus(reclaimed.stimulus_id)
    assert done is not None
    assert done.disposition == Disposition.COMPLETED.value


def test_observation_ingest_via_fake_lab_runtime() -> None:
    with StimulusLab() as lab:
        result = lab.runtime.ingest(
            Observation(
                source="unit",
                type="external_event",
                thread_id=lab.thread_id,
                conversation_id=lab.conversation_id,
                occurred_at="2026-01-01T10:00:00Z",
                observed_at="2026-01-01T10:00:00Z",
                text="ping",
                idempotency_key="unit:1",
            )
        )
        assert result.created is True
        assert result.pool_state == PoolState.READY.value
        assert lab.runtime.list_stimulus_trace(stimulus_id=result.stimulus_id)


def test_stimulus_lab_pack_passes() -> None:
    report = run_stimulus_lab()
    assert report["passed"] is True
    names = {item["name"] for item in report["results"]}
    assert names == {
        "duplicate",
        "out_of_order",
        "expired",
        "irrelevant",
        "valid",
        "chat_duplicate",
    }


def test_chat_source_adapter_ingest_and_idempotency() -> None:
    from m_agent.runtime.perception.chat_adapter import ChatSignal, ChatSourceAdapter

    with StimulusLab() as lab:
        adapter = ChatSourceAdapter(lab.runtime)
        first = adapter.handle_message(
            ChatSignal(
                text="hi",
                message_id="run_abc",
                occurred_at="2026-01-01T10:00:00Z",
                subject="owner1",
                payload={"user_turn": {"speaker": "owner1", "text": "hi"}},
            ),
            thread_id=lab.thread_id,
            conversation_id=lab.conversation_id,
            observed_at="2026-01-01T10:00:01Z",
            schedule_drainer=False,
        )
        assert first.created is True
        expected_key = f"chat:{lab.thread_id}:run_abc"
        stored_first = lab.runtime.store.load_stimulus(first.stimulus_id)
        assert stored_first is not None
        assert stored_first.ingress_key == expected_key
        second = adapter.handle_message(
            ChatSignal(
                text="hi again",
                message_id="run_abc",
                occurred_at="2026-01-01T10:00:02Z",
                subject="owner1",
                payload={"user_turn": {"speaker": "owner1", "text": "hi again"}},
            ),
            thread_id=lab.thread_id,
            conversation_id=lab.conversation_id,
            observed_at="2026-01-01T10:00:03Z",
            schedule_drainer=False,
        )
        assert second.merged is True
        assert second.stimulus_id == first.stimulus_id
        stored = lab.runtime.store.load_stimulus(first.stimulus_id)
        assert stored is not None
        payload = stored.stimulus.payload if stored.stimulus else {}
        assert payload.get("user_turn", {}).get("text") == "hi"
        assert payload.get("observation", {}).get("subject") == "owner1"
        assert payload.get("observation", {}).get("idempotency_key") == expected_key
