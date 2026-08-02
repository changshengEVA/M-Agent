"""Transaction deletion control and durable cleanup regression tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS
from m_agent.layers.perception.contracts import Stimulus, StimulusKind
from m_agent.runtime.host.product_views import list_transactions
from m_agent.runtime.domain.contracts import (
    ActivationStatus,
    DelegateStatus,
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    TransactionKind,
    TransactionLifecycle,
)
from m_agent.runtime.dispatch.cpu_state import THREAD_CPU_STATE
from m_agent.runtime.transaction.effects import EffectCoordinator
from m_agent.runtime.transaction.schedule import (
    OneShotScheduleRun,
    ScheduleDeliveryStatus,
    ScheduleRunStatus,
)
from m_agent.runtime.transaction.store import (
    IdempotencyConflictError,
    RevisionConflictError,
    RuntimeStoreUnitOfWork,
)
from m_agent.runtime.transaction.uow import RuntimeUnitOfWork
from m_agent.runtime.transaction.registry import (
    TransactionRegistry,
    TransactionTransitionError,
)
from m_agent.runtime.transaction_control import (
    TransactionFencedSceneWriter,
    delete_runtime_transaction,
)
from m_agent.systems.scene.default.jsonl_store import SceneLogStore


pytestmark = pytest.mark.unit


def _new_registry(tmp_path: Path, name: str = "delete.sqlite3") -> TransactionRegistry:
    return TransactionRegistry(persist_path=tmp_path / name)


def _create_transaction(
    registry: TransactionRegistry,
    *,
    thread_id: str = "delete-thread",
    conversation_id: str | None = None,
):
    return registry.create(
        thread_id=thread_id,
        conversation_id=conversation_id or f"{thread_id}::0",
        kind=TransactionKind.USER_TASK,
    )


def _stimulus(
    transaction: Any,
    stimulus_id: str,
    **bindings: Any,
) -> StimulusEnvelope:
    return StimulusEnvelope(
        stimulus_id=stimulus_id,
        thread_id=transaction.thread_id,
        conversation_id=transaction.conversation_id,
        stimulus=Stimulus(
            kind=StimulusKind.OBSERVATION_TRIGGER,
            text=stimulus_id,
            payload={},
        ),
        occurred_at="2026-08-01T00:00:00Z",
        transaction_id=bindings.get("transaction_id"),
        activation_id=bindings.get("activation_id"),
        delegate_id=bindings.get("delegate_id"),
        schedule_run_id=bindings.get("schedule_run_id"),
        schedule_delivery_id=bindings.get("schedule_delivery_id"),
    )


def _seed_cleanup_graph(registry: TransactionRegistry) -> dict[str, Any]:
    transaction = _create_transaction(registry)
    activation_id = str(transaction.current_activation_id)
    delegated = registry.begin_delegate(
        transaction.transaction_id,
        "delegate-delete",
        expected_revision=transaction.revision,
    )
    run = OneShotScheduleRun(
        schedule_id="schedule-delete",
        schedule_run_id="schedule-run-delete",
        transaction_id=delegated.transaction_id,
        conversation_id=delegated.conversation_id,
        due_at="2026-08-02T00:00:00Z",
        status=ScheduleRunStatus.DUE,
        delivery_generation=1,
        schedule_delivery_id="schedule-delivery-delete",
        delivery_status=ScheduleDeliveryStatus.CLAIMED,
        created_at="2026-08-01T00:00:00Z",
        updated_at="2026-08-01T00:00:00Z",
    )
    registry.store.create_schedule_run(run.schedule_run_id, run.to_dict())

    admitted = {
        "transaction": registry.store.admit_stimulus(
            _stimulus(
                delegated,
                "stimulus-by-transaction",
                transaction_id=delegated.transaction_id,
            ),
            effective_priority=10,
        ),
        "activation": registry.store.admit_stimulus(
            _stimulus(
                delegated,
                "stimulus-by-activation",
                activation_id=activation_id,
            ),
            effective_priority=20,
        ),
        "delegate": registry.store.admit_stimulus(
            _stimulus(
                delegated,
                "stimulus-by-delegate",
                delegate_id="delegate-delete",
            ),
            effective_priority=30,
            disposition="claimed",
        ),
        "schedule": registry.store.admit_stimulus(
            _stimulus(
                delegated,
                "stimulus-by-schedule",
                schedule_delivery_id=run.schedule_delivery_id,
            ),
            effective_priority=40,
        ),
        "unrelated": registry.store.admit_stimulus(
            _stimulus(delegated, "stimulus-unrelated"),
            effective_priority=50,
        ),
    }
    effects = EffectCoordinator(store=registry.store, registry=registry)
    effects.record_intent_for_delegate(
        transaction_id=delegated.transaction_id,
        activation_id=activation_id,
        delegate_id="delegate-delete",
        effect_id="effect-delete",
    )
    return {
        "transaction": delegated,
        "activation_id": activation_id,
        "run": run,
        "stimuli": admitted,
        "effects": effects,
    }


def test_delete_with_cleanup_atomically_terminalizes_owned_work(
    tmp_path: Path,
) -> None:
    registry = _new_registry(tmp_path)
    seeded = _seed_cleanup_graph(registry)
    transaction = seeded["transaction"]

    result = registry.delete_with_cleanup(
        transaction.transaction_id,
        expected_revision=transaction.revision,
        transition_id="delete-cleanup-success",
        idempotency_key="delete-cleanup-key",
    )

    persisted = registry.store.load_transaction(transaction.transaction_id)
    assert persisted is not None
    assert persisted.lifecycle_status == TransactionLifecycle.DELETED
    assert persisted.deleted_at
    assert persisted.current_activation_id is None
    assert (
        registry.store.load_activation(seeded["activation_id"]).status
        == ActivationStatus.INVALIDATED
    )
    assert (
        registry.store.load_delegate("delegate-delete").status
        == DelegateStatus.INVALIDATED
    )

    run = OneShotScheduleRun.from_dict(
        registry.store.load_schedule_run(seeded["run"].schedule_run_id)
    )
    assert run.status == ScheduleRunStatus.CANCELLED
    assert run.delivery_status == ScheduleDeliveryStatus.ABORTED
    assert run.blocked_reason == "transaction_deleted"

    target_ids = {
        "stimulus-by-transaction",
        "stimulus-by-activation",
        "stimulus-by-delegate",
        "stimulus-by-schedule",
    }
    for stimulus_id in target_ids:
        stimulus = registry.store.load_stimulus(stimulus_id)
        assert stimulus is not None
        assert stimulus.disposition == "aborted"
        assert stimulus.disposition_stage == "transaction_delete"
        assert stimulus.disposition_reason == "transaction_deleted"
    unrelated = registry.store.load_stimulus("stimulus-unrelated")
    assert unrelated is not None
    assert unrelated.disposition == "ready"

    outbox = registry.store.list_feedback_outbox(
        transaction_id=transaction.transaction_id
    )
    assert len(outbox) == 1
    assert outbox[0]["status"] == "terminal"
    assert (
        outbox[0]["discard_evidence"]
        == "expected_discard:transaction_deleted"
    )
    cleanup = result["cleanup"]
    assert cleanup["cancelled_schedule_run_ids"] == [
        seeded["run"].schedule_run_id
    ]
    assert set(cleanup["aborted_stimulus_ids"]) == target_ids
    assert cleanup["terminal_feedback_outbox_ids"] == [
        outbox[0]["outbox_id"]
    ]

    # A terminal deletion disposition cannot later be overwritten by a
    # worker finishing through an exception or success path.
    unchanged = registry.store.set_stimulus_disposition(
        "stimulus-by-transaction",
        disposition="failed",
        stage="late_worker",
        reason="late failure",
    )
    assert unchanged is not None
    assert unchanged.disposition == "aborted"
    assert unchanged.disposition_stage == "transaction_delete"

    # A result that lands after deletion may finish its effect ledger entry,
    # but cannot make Feedback relayable again.
    committed = seeded["effects"].commit_result_with_outbox(
        effect_id="effect-delete"
    )
    assert committed["transaction_deleted"] is True
    assert committed["feedback_outbox"]["status"] == "terminal"
    relayed = seeded["effects"].relay_feedback(effect_id="effect-delete")
    assert relayed["canonical_feedback_count"] == 0
    assert relayed["outbox_status"] == "terminal"
    registry.close()


def test_sourceless_claim_binding_survives_delete_and_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sourceless-claim.sqlite3"
    registry = TransactionRegistry(persist_path=path)
    transaction = _create_transaction(
        registry,
        thread_id="sourceless-delete-thread",
    )
    admitted = registry.store.admit_stimulus(
        _stimulus(transaction, "sourceless-claimed"),
        effective_priority=10,
    )
    assert admitted.transaction_id is None
    claimed = registry.store.pop_next_stimulus(transaction.thread_id)
    assert claimed is not None
    assert claimed.disposition == "claimed"
    assert claimed.transaction_id is None

    bound = registry.store.bind_stimulus_target(
        claimed.stimulus_id,
        transaction_id=transaction.transaction_id,
    )
    assert bound.disposition == "claimed"
    assert bound.transaction_id == transaction.transaction_id

    deleted = registry.delete_with_cleanup(
        transaction.transaction_id,
        expected_revision=transaction.revision,
        transition_id="delete-bound-sourceless-claim",
        idempotency_key="delete-bound-sourceless-claim",
    )
    assert claimed.stimulus_id in deleted["cleanup"]["aborted_stimulus_ids"]
    registry.close()

    recovered = TransactionRegistry(persist_path=path)
    durable = recovered.store.load_stimulus(claimed.stimulus_id)
    assert durable is not None
    assert durable.transaction_id == transaction.transaction_id
    assert durable.disposition == "aborted"
    assert durable.disposition_stage == "transaction_delete"
    assert recovered.store.pop_next_stimulus(transaction.thread_id) is None
    rebound = recovered.store.bind_stimulus_target(
        claimed.stimulus_id,
        transaction_id=transaction.transaction_id,
    )
    assert rebound.disposition == "aborted"
    recovered.close()


def test_delete_cleanup_rolls_back_every_domain_when_a_late_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _new_registry(tmp_path)
    seeded = _seed_cleanup_graph(registry)
    transaction = seeded["transaction"]

    def fail_outbox_update(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("injected terminal outbox failure")

    monkeypatch.setattr(
        RuntimeStoreUnitOfWork,
        "update_feedback_outbox",
        fail_outbox_update,
    )
    with pytest.raises(RuntimeError, match="injected terminal outbox failure"):
        registry.delete_with_cleanup(
            transaction.transaction_id,
            expected_revision=transaction.revision,
            transition_id="delete-cleanup-rollback",
            idempotency_key="delete-cleanup-rollback-key",
        )

    persisted = registry.store.load_transaction(transaction.transaction_id)
    assert persisted is not None
    assert persisted.lifecycle_status == TransactionLifecycle.ACTIVE
    assert persisted.revision == transaction.revision
    assert persisted.current_activation_id == seeded["activation_id"]
    assert (
        registry.store.load_activation(seeded["activation_id"]).status
        == ActivationStatus.ACTIVE
    )
    assert (
        registry.store.load_delegate("delegate-delete").status
        == DelegateStatus.PENDING
    )
    run = OneShotScheduleRun.from_dict(
        registry.store.load_schedule_run(seeded["run"].schedule_run_id)
    )
    assert run.status == ScheduleRunStatus.DUE
    assert run.revision == 1
    for stimulus in seeded["stimuli"].values():
        restored = registry.store.load_stimulus(stimulus.stimulus_id)
        assert restored is not None
        assert restored.disposition == stimulus.disposition
    outbox = registry.store.list_feedback_outbox(
        transaction_id=transaction.transaction_id
    )
    assert outbox[0]["status"] == "pending"
    registry.close()


def test_delete_revision_idempotency_replay_and_already_deleted(
    tmp_path: Path,
) -> None:
    registry = _new_registry(tmp_path)
    transaction = _create_transaction(registry)
    original_revision = transaction.revision

    deleted = registry.delete_with_cleanup(
        transaction.transaction_id,
        expected_revision=original_revision,
        transition_id="delete-idempotent",
        idempotency_key="key-one",
    )
    assert deleted["outcome"] == "deleted"
    assert deleted["already_deleted"] is False
    assert deleted["replayed"] is False
    deleted_revision = int(deleted["transaction"]["revision"])

    replay = registry.delete_with_cleanup(
        transaction.transaction_id,
        expected_revision=original_revision,
        transition_id="delete-idempotent",
        idempotency_key="key-one",
    )
    assert replay["outcome"] == "deleted"
    assert replay["already_deleted"] is False
    assert replay["replayed"] is True
    assert replay["transaction"]["revision"] == deleted_revision

    with pytest.raises(IdempotencyConflictError):
        registry.delete_with_cleanup(
            transaction.transaction_id,
            expected_revision=original_revision,
            transition_id="delete-idempotent",
            idempotency_key="different-command",
        )

    already_deleted = registry.delete_with_cleanup(
        transaction.transaction_id,
        expected_revision=deleted_revision,
        transition_id="delete-fresh-noop",
        idempotency_key="key-two",
    )
    assert already_deleted["outcome"] == "already_deleted"
    assert already_deleted["already_deleted"] is True
    assert already_deleted["replayed"] is False
    assert already_deleted["transaction"]["revision"] == deleted_revision

    with pytest.raises(RevisionConflictError):
        registry.delete_with_cleanup(
            transaction.transaction_id,
            expected_revision=original_revision,
            transition_id="delete-stale-revision",
            idempotency_key="key-three",
        )
    registry.close()


class _Inbox:
    def __init__(self, pending: int = 0) -> None:
        self.pending = pending

    def pending_count(self, _thread_id: str) -> int:
        return self.pending


def test_runtime_delete_cancels_only_matching_cpu_and_hides_deleted_holder(
    tmp_path: Path,
) -> None:
    registry = _new_registry(tmp_path)
    thread_id = "delete-cpu-thread"
    events: list[tuple[str, str, dict[str, Any]]] = []
    runtime = SimpleNamespace(
        registry=registry,
        inbox=_Inbox(pending=2),
        _emit_thread_event=lambda tid, event, payload: events.append(
            (tid, event, payload)
        ),
        _emit_runtime_updated=lambda _tid: None,
    )
    target = _create_transaction(registry, thread_id=thread_id)
    target_cancel = THREAD_CPU_STATE.set_in_flight(
        thread_id,
        stimulus_id="cpu-target-stimulus",
        transaction_id=target.transaction_id,
    )
    try:
        result = delete_runtime_transaction(
            runtime,
            thread_id=thread_id,
            conversation_id=target.conversation_id,
            transaction_id=target.transaction_id,
            expected_revision=target.revision,
            idempotency_key="delete-cpu-target",
        )
        assert target_cancel.is_set()
        assert getattr(target_cancel, "cancel_reason") == "transaction_deleted"
        assert not hasattr(target_cancel, "force_stop")
        assert result["cleanup"]["cancelled_in_flight"] is True
        assert result["cpu_transaction_id"] is None
        assert result["transaction"]["is_cpu_holder"] is False
        assert events[0][1] == "transaction_deleted"
    finally:
        THREAD_CPU_STATE.clear_in_flight(thread_id)

    cpu_owner = _create_transaction(registry, thread_id=thread_id)
    victim = _create_transaction(registry, thread_id=thread_id)
    owner_cancel = THREAD_CPU_STATE.set_in_flight(
        thread_id,
        stimulus_id="cpu-owner-stimulus",
        transaction_id=cpu_owner.transaction_id,
    )
    try:
        result = delete_runtime_transaction(
            runtime,
            thread_id=thread_id,
            conversation_id=victim.conversation_id,
            transaction_id=victim.transaction_id,
            expected_revision=victim.revision,
            idempotency_key="delete-cpu-wrong-target",
        )
        assert owner_cancel.is_set() is False
        assert result["cleanup"]["cancelled_in_flight"] is False
        assert result["cpu_transaction_id"] == cpu_owner.transaction_id
        projection = list_transactions(
            runtime,
            thread_id,
            conversation_id=victim.conversation_id,
        )
        assert projection["cpu_transaction_id"] == cpu_owner.transaction_id
    finally:
        THREAD_CPU_STATE.clear_in_flight(thread_id)
        THREAD_RUNTIME_STATUS.set_pending_stimuli(thread_id, 0)
        THREAD_RUNTIME_STATUS.set_cpu_holder(thread_id, None)
    registry.close()


def test_deleted_transaction_rejects_generic_transition_and_final_reply(
    tmp_path: Path,
) -> None:
    registry = _new_registry(tmp_path)
    transaction = _create_transaction(registry)
    deleted = registry.delete_with_cleanup(
        transaction.transaction_id,
        expected_revision=transaction.revision,
        transition_id="delete-write-fence",
        idempotency_key="delete-write-fence-key",
    )
    deleted_revision = int(deleted["transaction"]["revision"])

    with pytest.raises(TransactionTransitionError, match="write-fenced"):
        RuntimeUnitOfWork(registry).apply_transition(
            "late-generic-transition",
            {"action": "late_write"},
            transaction.transaction_id,
            deleted_revision,
            lambda _record: {"written": True},
        )
    with pytest.raises(TransactionTransitionError, match="write-fenced"):
        registry.mark_reply_finalized(
            transaction.transaction_id,
            expected_revision=deleted_revision,
        )
    assert registry.store.load_transaction(transaction.transaction_id).revision == (
        deleted_revision
    )
    registry.close()


def test_registry_get_self_heals_a_stale_live_reference_after_external_delete(
    tmp_path: Path,
) -> None:
    persist_path = tmp_path / "cache-self-heal.sqlite3"
    first = TransactionRegistry(persist_path=persist_path)
    transaction = _create_transaction(first)
    original_revision = transaction.revision
    stale_reference = first.get(transaction.transaction_id)
    assert stale_reference is not None

    second = TransactionRegistry(persist_path=persist_path)
    second.delete_with_cleanup(
        transaction.transaction_id,
        expected_revision=original_revision,
        transition_id="external-delete",
        idempotency_key="external-delete-key",
    )
    second.close()

    healed = first.get(transaction.transaction_id)
    assert healed is stale_reference
    assert healed is not None
    assert healed.deleted is True
    assert healed.deleted_at
    assert healed.revision == original_revision + 1
    first.close()


def test_transaction_fenced_scene_writer_suppresses_post_delete_append(
    tmp_path: Path,
) -> None:
    registry = _new_registry(tmp_path)
    transaction = _create_transaction(registry)
    inner = SceneLogStore(persist_enabled=False)
    writer = TransactionFencedSceneWriter(registry, inner)

    writer.append(
        transaction.conversation_id,
        SceneEntry(
            seq=0,
            occurred_at="2026-08-01T00:00:00Z",
            entry_type=SceneEntryType.ACTION,
            actor=SceneActor.WORK,
            text="before delete",
            transaction_id=transaction.transaction_id,
        ),
    )
    registry.delete_with_cleanup(
        transaction.transaction_id,
        expected_revision=transaction.revision,
        transition_id="delete-scene-fence",
        idempotency_key="delete-scene-fence-key",
    )
    suppressed = SceneEntry(
        seq=0,
        occurred_at="2026-08-01T00:00:01Z",
        entry_type=SceneEntryType.OUTCOME,
        actor=SceneActor.WORK,
        text="after delete",
        transaction_id=transaction.transaction_id,
    )
    assert writer.append(transaction.conversation_id, suppressed) is suppressed
    writer.append(
        transaction.conversation_id,
        SceneEntry(
            seq=0,
            occurred_at="2026-08-01T00:00:02Z",
            entry_type=SceneEntryType.OUTCOME,
            actor=SceneActor.WORK,
            text="unscoped runtime event",
        ),
    )

    assert [
        entry.text
        for entry in inner.tail(transaction.conversation_id, limit=10)
    ] == ["before delete", "unscoped runtime event"]
    registry.close()
