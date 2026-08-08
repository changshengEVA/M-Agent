"""Shared executable TX contract scenarios.

The scenarios deliberately distinguish two kinds of evidence:

* behavior that the current production components already provide is asserted
  without a known-gap key and therefore remains a hard regression check;
* target semantics that the Runtime does not yet expose are asserted with the
  variant's registered known-gap key.

Arrange code may seed fixture records. Act code goes through
``SharedRuntimeHarness`` operations or public production commands exposed by
the Harness.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    TransactionKind,
    TransactionRecord,
    TransactionState,
)

from ..scenario_catalog import registered_known_gap_key
from .base import HarnessResult, RuntimeAdapterError, ScenarioExecution
from .evidence import EvidenceBuilder
from .shared_harness import SharedRuntimeHarness
from .transaction_fixtures import (
    FixtureTransactionStatus as TransactionStatus,
    apply_fixture_status,
)


Handler = Callable[
    [SharedRuntimeHarness, EvidenceBuilder, str],
    ScenarioExecution,
]


def _builder(
    harness: SharedRuntimeHarness,
    scenario_id: str,
    variant_id: str,
) -> EvidenceBuilder:
    return EvidenceBuilder(
        scenario_id=scenario_id,
        variant_id=variant_id,
        runtime_id=harness.runtime_id,
    )


def _new_conversation(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    *,
    suffix: str,
) -> str:
    thread_id = (
        f"acceptance-{evidence.scenario_id.lower()}-"
        f"{suffix.replace('_', '-')}"
    )
    result = harness.create_conversation(thread_id=thread_id)
    evidence.fact(f"conversation.{suffix}", result.to_dict())
    evidence.check(
        f"{evidence.scenario_id.lower()}.fixture.{suffix}.conversation",
        "The Runtime creates an isolated conversation fixture.",
        actual=result.to_dict(),
        expected="supported outcome=ok with conversation_id",
        evidence="SharedRuntimeHarness.create_conversation",
        predicate=lambda _actual: (
            result.supported
            and result.outcome == "ok"
            and bool(result.data.get("conversation_id"))
        ),
    )
    return str(
        result.data.get("conversation_id")
        or f"{thread_id}::0"
    )


def _seed_transaction(
    harness: SharedRuntimeHarness,
    *,
    conversation_id: str,
    status: TransactionStatus = TransactionStatus.PENDING,
    kind: TransactionKind = TransactionKind.USER_TASK,
) -> TransactionRecord:
    """Arrange one valid fixture transaction through public Registry commands."""

    record = harness.create_runtime_transaction(
        conversation_id=conversation_id,
        kind=kind,
        status=TransactionStatus.PENDING,
    )
    return apply_fixture_status(harness.registry, record, status)


def _append_scene(
    harness: SharedRuntimeHarness,
    *,
    conversation_id: str,
    transaction_id: str | None,
    text: str,
    occurred_at: str,
    entry_type: SceneEntryType = SceneEntryType.ACTION,
    actor: SceneActor = SceneActor.WORK,
) -> SceneEntry:
    return harness.append_scene(
        conversation_id,
        SceneEntry(
            seq=0,
            occurred_at=occurred_at,
            entry_type=entry_type,
            actor=actor,
            text=text,
            transaction_id=transaction_id,
        ),
    )


def _result_check(
    evidence: EvidenceBuilder,
    *,
    check_id: str,
    description: str,
    result: HarnessResult,
    expected: str,
    predicate: Callable[[HarnessResult], bool],
    gap_key: str = "",
) -> bool:
    evidence.fact(f"{check_id}.result", result.to_dict())
    return evidence.check(
        check_id,
        description,
        actual=result.to_dict(),
        expected=expected,
        evidence=f"SharedRuntimeHarness.{result.operation}",
        known_gap_key=gap_key,
        predicate=lambda _actual: predicate(result),
    )


def _target_operation(
    evidence: EvidenceBuilder,
    *,
    check_id: str,
    description: str,
    result: HarnessResult,
    gap_key: str,
    expected_outcomes: tuple[str, ...] = ("ok",),
    data_predicate: Callable[[Dict[str, Any]], bool] | None = None,
) -> bool:
    return _result_check(
        evidence,
        check_id=check_id,
        description=description,
        result=result,
        expected=(
            "supported outcome in "
            f"{list(expected_outcomes)} with target domain result"
        ),
        gap_key=gap_key,
        predicate=lambda item: (
            item.supported
            and item.outcome in expected_outcomes
            and (
                data_predicate is None
                or data_predicate(item.data)
            )
        ),
    )


def _transaction_data(
    harness: SharedRuntimeHarness,
    transaction_id: str,
) -> Dict[str, Any]:
    loaded = harness.load_transaction(transaction_id=transaction_id)
    transaction = loaded.data.get("transaction")
    return dict(transaction) if isinstance(transaction, dict) else {}


def _run_tx_01_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="normal",
    )
    stimulus = harness.make_stimulus(
        conversation_id=conversation_id,
        kind="user_message",
        text="Start one transaction and process the request.",
        payload={"fixture": "tx-01"},
        stimulus_id="tx-01-stimulus",
    )

    # Act: attribution creates the production Registry record.
    record, created = harness.resolve(stimulus)
    created_transaction_id = record.transaction_id
    def record_progress(
        transaction: TransactionRecord | None,
    ) -> Dict[str, Any]:
        assert transaction is not None
        transaction.wm_entries.append(
            {"fact": "normal-processing-committed"}
        )
        transaction.task_state.goal = "process the TX-01 request"
        return {
            "transaction_id": transaction.transaction_id,
            "outcome": "progress_recorded",
        }

    harness.uow.apply_transition(
        "tx-01-normal-processing",
        {
            "action": "record_progress",
            "stimulus_id": stimulus.stimulus_id,
        },
        record.transaction_id,
        record.revision,
        record_progress,
    )
    persisted = harness.registry.get(record.transaction_id)
    assert persisted is not None
    record = persisted
    scene_entry = _append_scene(
        harness,
        conversation_id=conversation_id,
        transaction_id=record.transaction_id,
        text="TX-01 processing evidence",
        occurred_at="2026-01-01T00:00:01Z",
    )

    records = harness.registry.list_for_conversation(conversation_id)
    snapshot = _transaction_data(harness, record.transaction_id)
    scene = harness.read_scene(conversation_id=conversation_id)
    evidence.fact("transaction", snapshot)
    evidence.fact("scene", scene.to_dict())

    evidence.check(
        "tx_01.transaction.created_once",
        "Attribution creates exactly one transaction.",
        actual={
            "created": created,
            "transaction_count": len(records),
        },
        expected={"created": True, "transaction_count": 1},
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "tx_01.transaction.identity_stable",
        "Processing evidence remains attached to the created transaction.",
        actual={
            "created_transaction_id": created_transaction_id,
            "final_transaction_id": snapshot.get("transaction_id"),
            "scene_transaction_id": scene_entry.transaction_id,
        },
        expected={
            "created_transaction_id": created_transaction_id,
            "final_transaction_id": created_transaction_id,
            "scene_transaction_id": created_transaction_id,
        },
        evidence="TransactionRegistry+SceneLogStore",
    )
    evidence.check(
        "tx_01.transaction.target_state",
        "A normally running transaction uses target state continue.",
        actual=snapshot.get("state"),
        expected="continue",
        evidence="SharedRuntimeHarness.load_transaction",
        known_gap_key=gap_key,
    )
    evidence.check(
        "tx_01.activation.first_activation",
        "Normal processing creates the first durable activation and revision.",
        actual={
            "activation_id": snapshot.get("current_activation_id"),
            "revision": snapshot.get("revision"),
        },
        expected="non-empty activation_id and integer revision",
        evidence="SharedRuntimeHarness.load_transaction",
        known_gap_key=gap_key,
        predicate=lambda value: (
            bool(value.get("activation_id"))
            and isinstance(value.get("revision"), int)
        ),
    )
    evidence.check(
        "tx_01.state.normal_processing_writes",
        "The normal processing command writes WM and TaskState.",
        actual={
            "wm_entries": snapshot.get("wm_entries"),
            "task_state": snapshot.get("task_state"),
        },
        expected="non-empty WM and TaskState goal",
        evidence="SharedRuntimeHarness.load_transaction",
        known_gap_key=gap_key,
        predicate=lambda value: (
            bool(value.get("wm_entries"))
            and bool(
                (value.get("task_state") or {}).get("goal")
            )
        ),
    )
    evidence.check(
        "tx_01.scene.single_append",
        "The turn writes one ordered Scene entry for the same transaction.",
        actual={
            "entry_count": len(scene.data.get("entries", [])),
            "seq": scene_entry.seq,
            "transaction_id": scene_entry.transaction_id,
        },
        expected={
            "entry_count": 1,
            "seq": 1,
            "transaction_id": created_transaction_id,
        },
        evidence="SceneLogStore.append",
    )
    return evidence.build()


def _run_tx_01_uow(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="uow",
    )
    record = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    first_load = harness.load_transaction(
        transaction_id=record.transaction_id,
    )
    second_load = harness.load_transaction(
        transaction_id=record.transaction_id,
    )
    evidence.check(
        "tx_01_uow.transaction.load_identity",
        "Repeated production loads preserve the transaction identity.",
        actual=[
            first_load.data.get("transaction", {}).get(
                "transaction_id"
            ),
            second_load.data.get("transaction", {}).get(
                "transaction_id"
            ),
        ],
        expected=[record.transaction_id, record.transaction_id],
        evidence="TransactionRegistry.get",
    )

    fault = harness.inject_fault(
        fault_point="after_transaction_commit_before_response",
    )
    _target_operation(
        evidence,
        check_id="tx_01_uow.fault.commit_response_window",
        description=(
            "The Harness can stop after the Store commit and before the "
            "command response."
        ),
        result=fault,
        gap_key=gap_key,
    )

    first = harness.finalize_stimulus(
        stimulus_id="tx-01-uow-stimulus",
        claim_token={
            "claimed_by": "consumer-a",
            "consumer_epoch": 1,
            "claim_epoch": 1,
        },
        disposition="consumed",
        transition_id="tx-01-transition",
        command_digest="digest-a",
    )
    replay = harness.finalize_stimulus(
        stimulus_id="tx-01-uow-stimulus",
        claim_token={
            "claimed_by": "consumer-a",
            "consumer_epoch": 1,
            "claim_epoch": 1,
        },
        disposition="consumed",
        transition_id="tx-01-transition",
        command_digest="digest-a",
    )
    conflict = harness.finalize_stimulus(
        stimulus_id="tx-01-uow-stimulus",
        claim_token={
            "claimed_by": "consumer-a",
            "consumer_epoch": 1,
            "claim_epoch": 1,
        },
        disposition="consumed",
        transition_id="tx-01-transition",
        command_digest="digest-b",
    )
    evidence.fact("uow.first", first.to_dict())
    evidence.fact("uow.replay", replay.to_dict())
    evidence.fact("uow.conflict", conflict.to_dict())
    evidence.check(
        "tx_01_uow.replay.complete_result",
        (
            "The same transition ID and digest returns the first complete "
            "committed result."
        ),
        actual={
            "first": first.to_dict(),
            "replay": replay.to_dict(),
        },
        expected="two supported results with identical committed result",
        evidence="RuntimeUnitOfWork.apply_transition",
        known_gap_key=gap_key,
        predicate=lambda _value: (
            first.supported
            and replay.supported
            and first.outcome == "ok"
            and replay.outcome == "ok"
            and first.data == replay.data
        ),
    )
    evidence.check(
        "tx_01_uow.replay.digest_conflict",
        "The same transition ID with a different digest is rejected.",
        actual=conflict.to_dict(),
        expected="supported outcome=idempotency_conflict",
        evidence="RuntimeUnitOfWork.apply_transition",
        known_gap_key=gap_key,
        predicate=lambda _value: (
            conflict.supported
            and conflict.outcome == "idempotency_conflict"
        ),
    )
    revision = (
        first_load.data.get("transaction", {}).get("revision")
    )
    evidence.check(
        "tx_01_uow.ledger.revision",
        "The transaction exposes a CAS revision used by the UoW.",
        actual=revision,
        expected="integer revision",
        evidence="SharedRuntimeHarness.load_transaction",
        known_gap_key=gap_key,
        predicate=lambda value: isinstance(value, int),
    )
    return evidence.build()


def _run_tx_02_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="pause",
    )
    first = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    first.task_state.goal = "pause and later restore this task"
    first.wm_entries.append({"fact": "tx-02-owned"})
    harness.registry.begin_delegate(
        first.transaction_id,
        "tx-02-old-delegate",
    )
    second = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    second.task_state.goal = "unrelated task"
    second_before = harness.transaction_snapshot(second)

    latch = harness.set_worker_latch(
        conversation_id=conversation_id,
        phase="thinking",
    )
    _target_operation(
        evidence,
        check_id="tx_02.pause.thinking_latch",
        description=(
            "The scenario can deterministically hold the target stimulus "
            "inside Thinking."
        ),
        result=latch,
        gap_key=gap_key,
    )
    pause = harness.control_transaction(
        transaction_id=first.transaction_id,
        action="pause",
    )
    _target_operation(
        evidence,
        check_id="tx_02.pause.direct_control",
        description=(
            "UI Pause directly aborts the target stimulus and commits "
            "state=pause."
        ),
        result=pause,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("state") == "pause"
            and data.get("stimulus_disposition") == "aborted"
        ),
    )

    late_feedback = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind="execution_feedback",
        text="late result",
        payload={
            "delegate_id": "tx-02-old-delegate",
            "activation_id": "tx-02-old-activation",
        },
        source={
            "transaction_id": first.transaction_id,
            "activation_id": "tx-02-old-activation",
            "delegate_id": "tx-02-old-delegate",
        },
        ingress_key=(
            f"feedback:{first.transaction_id}:"
            "tx-02-old-activation:tx-02-old-delegate"
        ),
    )
    _result_check(
        evidence,
        check_id="tx_02.feedback.old_activation_discarded",
        description=(
            "Feedback from the invalidated activation is discarded at "
            "admission."
        ),
        result=late_feedback,
        expected="outcome=expected_discard stage=admission",
        gap_key=gap_key,
        predicate=lambda item: (
            item.supported
            and item.outcome == "expected_discard"
            and item.data.get("stage") == "admission"
        ),
    )

    restore = harness.restore_transaction(
        transaction_id=first.transaction_id,
        source="ui",
    )
    _target_operation(
        evidence,
        check_id="tx_02.restore.new_activation",
        description=(
            "Explicit Restore reuses the transaction and creates a new "
            "activation while preserving WM and TaskState."
        ),
        result=restore,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("transaction_id") == first.transaction_id
            and data.get("state") == "continue"
            and bool(data.get("activation_id"))
        ),
    )

    first_after = _transaction_data(
        harness,
        first.transaction_id,
    )
    second_after = _transaction_data(
        harness,
        second.transaction_id,
    )
    evidence.check(
        "tx_02.isolation.other_transaction",
        "Controlling transaction A does not mutate transaction B.",
        actual=second_after,
        expected=second_before,
        evidence="TransactionRegistry",
    )
    evidence.check(
        "tx_02.state.owned_data_preserved",
        "The original transaction still owns its WM and TaskState.",
        actual={
            "wm_entries": first_after.get("wm_entries"),
            "goal": (
                first_after.get("task_state") or {}
            ).get("goal"),
        },
        expected={
            "wm_entries": [{"fact": "tx-02-owned"}],
            "goal": "pause and later restore this task",
        },
        evidence="TransactionRegistry",
    )
    evidence.check(
        "tx_02.activation.old_delegate_invalidated",
        "Pause permanently invalidates the old activation and delegate.",
        actual={
            "current_activation_id": first_after.get(
                "current_activation_id"
            ),
            "active_delegate_id": first_after.get(
                "active_delegate_id"
            ),
        },
        expected=(
            "new non-empty activation_id and no active old delegate"
        ),
        evidence="SharedRuntimeHarness.load_transaction",
        known_gap_key=gap_key,
        predicate=lambda value: (
            bool(value.get("current_activation_id"))
            and value.get("active_delegate_id")
            != "tx-02-old-delegate"
        ),
    )
    return evidence.build()


def _run_tx_03_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="complete",
    )
    record = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.COMPLETED,
    )
    record.task_state.goal = "reactivate before flush"
    record.wm_entries.append({"fact": "complete-state"})
    before = harness.transaction_snapshot(record)

    candidates = harness.list_match_candidates(
        conversation_id=conversation_id,
    )
    evidence.check(
        "tx_03.match.complete_candidate",
        "An unflushed complete transaction remains a matcher candidate.",
        actual=candidates.data.get("candidate_ids", []),
        expected=f"contains {record.transaction_id}",
        evidence="SharedRuntimeHarness.list_match_candidates",
        known_gap_key=gap_key,
        predicate=lambda value: record.transaction_id in value,
    )

    restore = harness.restore_transaction(
        transaction_id=record.transaction_id,
        source="ui",
    )
    _target_operation(
        evidence,
        check_id="tx_03.restore.explicit",
        description=(
            "Explicit Restore bypasses matching and changes complete to "
            "continue with a new activation."
        ),
        result=restore,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("transaction_id") == record.transaction_id
            and data.get("state") == "continue"
            and bool(data.get("activation_id"))
            and data.get("matcher_called") is False
        ),
    )
    after = _transaction_data(harness, record.transaction_id)
    evidence.check(
        "tx_03.restore.identity_and_state",
        "Reactivation preserves ID, WM and TaskState.",
        actual={
            "transaction_id": after.get("transaction_id"),
            "wm_entries": after.get("wm_entries"),
            "task_state": after.get("task_state"),
        },
        expected={
            "transaction_id": record.transaction_id,
            "wm_entries": before["wm_entries"],
            "task_state": before["task_state"],
        },
        evidence="TransactionRegistry",
    )
    evidence.check(
        "tx_03.restore.target_lifecycle",
        "The restored transaction is continue with a fresh activation.",
        actual={
            "state": after.get("state"),
            "activation_id": after.get("current_activation_id"),
        },
        expected="state=continue and non-empty activation_id",
        evidence="SharedRuntimeHarness.load_transaction",
        known_gap_key=gap_key,
        predicate=lambda value: (
            value.get("state") == "continue"
            and bool(value.get("activation_id"))
        ),
    )

    flush = harness.trigger_flush(
        conversation_id=conversation_id,
        flush_id="tx-03-flush",
    )
    _target_operation(
        evidence,
        check_id="tx_03.flush.reactivated_not_archived",
        description=(
            "A later Flush observes the new revision and does not archive "
            "the reactivated transaction."
        ),
        result=flush,
        gap_key=gap_key,
        data_predicate=lambda data: (
            record.transaction_id
            not in data.get("archived_transaction_ids", [])
        ),
    )
    return evidence.build()


def _run_tx_04_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="archive",
    )
    running = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    paused = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.SUSPENDED,
    )
    complete = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.COMPLETED,
    )
    deleted_target = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    evidence.check(
        "tx_04.fixture.distinct_transactions",
        "The fixture contains distinct state-isolated transactions.",
        actual=len(
            {
                running.transaction_id,
                paused.transaction_id,
                complete.transaction_id,
                deleted_target.transaction_id,
            }
        ),
        expected=4,
        evidence="TransactionRegistry.create",
    )

    direct_archive = harness.control_transaction(
        transaction_id=running.transaction_id,
        action="archive",
    )
    _target_operation(
        evidence,
        check_id="tx_04.archive.non_flush_rejected",
        description=(
            "A non-Flush archive command is explicitly rejected without "
            "changing the transaction."
        ),
        result=direct_archive,
        gap_key=gap_key,
        expected_outcomes=("rejected",),
    )
    delete = harness.control_transaction(
        transaction_id=deleted_target.transaction_id,
        action="delete",
    )
    _target_operation(
        evidence,
        check_id="tx_04.delete.tombstone_not_archive",
        description=(
            "UI Delete writes a permanent lifecycle tombstone without "
            "creating archive or a Flush boundary."
        ),
        result=delete,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("deleted") is True
            and data.get("state") != "archive"
            and data.get("flush_boundary_created") is False
        ),
    )
    flush = harness.trigger_flush(
        conversation_id=conversation_id,
        flush_id="tx-04-flush",
    )
    _target_operation(
        evidence,
        check_id="tx_04.flush.only_complete_archived",
        description=(
            "Flush archives only the eligible complete transaction and "
            "preserves continue/pause."
        ),
        result=flush,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("archived_transaction_ids")
            == [complete.transaction_id]
            and running.transaction_id
            in data.get("unchanged_transaction_ids", [])
            and paused.transaction_id
            in data.get("unchanged_transaction_ids", [])
        ),
    )
    complete_after = _transaction_data(
        harness,
        complete.transaction_id,
    )
    deleted_after = _transaction_data(
        harness,
        deleted_target.transaction_id,
    )
    evidence.check(
        "tx_04.flush.archive_state",
        "The eligible complete transaction has target state archive.",
        actual=complete_after.get("state"),
        expected="archive",
        evidence="SharedRuntimeHarness.load_transaction",
        known_gap_key=gap_key,
    )
    evidence.check(
        "tx_04.delete.lifecycle",
        "The deleted transaction exposes a permanent tombstone.",
        actual=deleted_after.get("deleted"),
        expected=True,
        evidence="SharedRuntimeHarness.load_transaction",
        known_gap_key=gap_key,
    )
    return evidence.build()


def _run_tx_05_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="restore",
    )
    record = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.COMPLETED,
    )
    record.task_state.goal = "restore archived transaction"
    record.wm_entries.append({"fact": "archive-owned"})
    _append_scene(
        harness,
        conversation_id=conversation_id,
        transaction_id=record.transaction_id,
        text="before archive",
        occurred_at="2026-01-01T00:00:01Z",
    )

    archive_state_available = "archive" in {
        item.value
        for item in TransactionState
    }
    evidence.check(
        "tx_05.fixture.archive_state",
        "The Runtime can represent an archived transaction fixture.",
        actual=archive_state_available,
        expected=True,
        evidence="TransactionStatus",
        known_gap_key=gap_key,
    )
    restore = harness.restore_transaction(
        transaction_id=record.transaction_id,
        source="ui",
    )
    _target_operation(
        evidence,
        check_id="tx_05.restore.explicit_id",
        description=(
            "A legal explicit action restores archive by exact ID without "
            "calling the matcher or creating a transaction."
        ),
        result=restore,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("transaction_id") == record.transaction_id
            and data.get("created_new") is False
            and data.get("matcher_called") is False
            and data.get("state") == "continue"
            and bool(data.get("activation_id"))
        ),
    )
    unknown = harness.load_transaction(
        transaction_id="tx-05-missing",
    )
    evidence.check(
        "tx_05.lookup.unknown_id",
        "Unknown transaction IDs produce an explicit not-found result.",
        actual={
            "supported": unknown.supported,
            "outcome": unknown.outcome,
            "reason": unknown.reason,
        },
        expected={
            "supported": True,
            "outcome": "not_found",
            "reason": "unknown_transaction",
        },
        evidence="SharedRuntimeHarness.load_transaction",
    )
    unknown_restore = harness.restore_transaction(
        transaction_id="tx-05-missing",
        source="ui",
    )
    _target_operation(
        evidence,
        check_id="tx_05.restore.unknown_rejected",
        description="Restore explicitly rejects an ID absent from active and archive stores.",
        result=unknown_restore,
        gap_key=gap_key,
        expected_outcomes=("rejected", "not_found"),
    )

    feedback = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind="execution_feedback",
        text="old feedback",
        payload={
            "delegate_id": "tx-05-old-delegate",
            "activation_id": "tx-05-old-activation",
        },
        source={
            "transaction_id": record.transaction_id,
            "activation_id": "tx-05-old-activation",
            "delegate_id": "tx-05-old-delegate",
        },
        ingress_key=(
            f"feedback:{record.transaction_id}:"
            "tx-05-old-activation:tx-05-old-delegate"
        ),
    )
    _result_check(
        evidence,
        check_id="tx_05.feedback.cannot_restore",
        description="Old Feedback cannot restore an archived transaction.",
        result=feedback,
        expected="outcome=expected_discard",
        gap_key=gap_key,
        predicate=lambda item: (
            item.supported
            and item.outcome == "expected_discard"
        ),
    )
    scene = harness.read_scene(conversation_id=conversation_id)
    evidence.check(
        "tx_05.scene.original_conversation",
        "Pre-existing Scene history remains associated with the original transaction.",
        actual=[
            item.get("transaction_id")
            for item in scene.data.get("entries", [])
        ],
        expected=[record.transaction_id],
        evidence="SceneLogStore",
    )
    return evidence.build()


def _run_tx_06_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="schedule",
    )
    original = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    original.task_state.goal = "execute the scheduled continuation"
    original.wm_entries.append({"fact": "schedule-context"})
    original = harness.registry.save(
        original,
        expected_revision=int(original.revision),
        bump_revision=True,
    )
    registered = harness.schedule_coordinator.register(
        schedule_id="tx-06-schedule",
        transaction_id=original.transaction_id,
        due_at="2026-07-29T12:00:00Z",
        expected_transaction_revision=int(original.revision),
        schedule_run_id="tx-06-run",
        transition_id="tx-06-register",
    )
    original = (
        harness.registry.get(original.transaction_id) or original
    )

    source = {
        "transaction_id": original.transaction_id,
        "schedule_id": "tx-06-schedule",
        "schedule_run_id": "tx-06-run",
        "schedule_delivery_id": "tx-06-delivery-1",
    }
    admitted = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind="scheduled_plan",
        text="The scheduled time has arrived.",
        payload={
            "schedule_id": source["schedule_id"],
            "run_id": source["schedule_run_id"],
            "schedule_run_id": source["schedule_run_id"],
            "schedule_delivery_id": source[
                "schedule_delivery_id"
            ],
            "owner_id": "acceptance-owner",
        },
        source=source,
        ingress_key="schedule:tx-06-run:1",
    )
    evidence.check(
        "tx_06.delivery.source_ids_preserved",
        "The ingress observation preserves all supplied Schedule source IDs.",
        actual=admitted.data.get("source"),
        expected={
            "transaction_id": original.transaction_id,
            "activation_id": None,
            "delegate_id": None,
            "schedule_id": "tx-06-schedule",
            "schedule_run_id": "tx-06-run",
            "schedule_delivery_id": "tx-06-delivery-1",
        },
        evidence="SharedRuntimeHarness.submit_stimulus",
    )

    stimulus = harness.make_stimulus(
        conversation_id=conversation_id,
        kind="scheduled_plan",
        text="The scheduled time has arrived.",
        payload={
            "schedule_id": "tx-06-schedule",
            "run_id": "tx-06-run",
            "schedule_delivery_id": "tx-06-delivery-1",
            "owner_id": "acceptance-owner",
        },
        source=source,
        stimulus_id="tx-06-stimulus",
    )
    resolved, created = harness.resolve(stimulus)
    evidence.fact(
        "schedule_resolution",
        {
            "created": created,
            "registered_outcome": registered.outcome,
            "transaction": harness.transaction_snapshot(resolved),
        },
    )
    evidence.check(
        "tx_06.activation.original_transaction",
        "A due plan reuses the bound transaction and does not create a second one.",
        actual={
            "created_new": created,
            "resolved_transaction_id": resolved.transaction_id,
            "transaction_count": len(
                harness.registry.list_for_conversation(
                    conversation_id
                )
            ),
        },
        expected={
            "created_new": False,
            "resolved_transaction_id": original.transaction_id,
            "transaction_count": 1,
        },
        evidence="TransactionAttributor.resolve",
        known_gap_key=gap_key,
    )
    evidence.check(
        "tx_06.activation.new_activation",
        "Claiming the run opens one new activation on the original transaction.",
        actual=harness.transaction_snapshot(resolved),
        expected=(
            "state=continue with non-empty activation_id on original transaction"
        ),
        evidence="TransactionRegistry",
        known_gap_key=gap_key,
        predicate=lambda value: (
            value.get("transaction_id")
            == original.transaction_id
            and value.get("state") == "continue"
            and bool(value.get("current_activation_id"))
        ),
    )
    schedule = harness.load_schedule(
        schedule_run_id="tx-06-run",
    )
    _target_operation(
        evidence,
        check_id="tx_06.run.atomic_claim",
        description=(
            "Run claim, delivery claim and activation binding are observed "
            "as one committed transition."
        ),
        result=schedule,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("status") == "claimed"
            and bool(data.get("claimed_activation_id"))
            and data.get("delivery_status") in {
                "claimed",
                "consumed",
            }
        ),
    )
    original_after = _transaction_data(
        harness,
        original.transaction_id,
    )
    evidence.check(
        "tx_06.state.original_context_preserved",
        "Schedule attribution does not overwrite the original WM or TaskState.",
        actual={
            "wm_entries": original_after.get("wm_entries"),
            "goal": (
                original_after.get("task_state") or {}
            ).get("goal"),
        },
        expected={
            "wm_entries": [{"fact": "schedule-context"}],
            "goal": "execute the scheduled continuation",
        },
        evidence="TransactionRegistry",
    )
    return evidence.build()


def _run_tx_06_robustness(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="schedule-robustness",
    )
    original = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.SUSPENDED,
    )
    source = {
        "transaction_id": original.transaction_id,
        "schedule_id": "tx-06-r-schedule",
        "schedule_run_id": "tx-06-r-run",
        "schedule_delivery_id": "tx-06-r-delivery",
    }
    stimulus = harness.make_stimulus(
        conversation_id=conversation_id,
        kind="scheduled_plan",
        text="duplicate due delivery",
        payload={
            "schedule_id": source["schedule_id"],
            "run_id": source["schedule_run_id"],
            "owner_id": "acceptance-owner",
        },
        source=source,
        stimulus_id="tx-06-r-stimulus",
    )
    first, first_created = harness.resolve(stimulus)
    second, second_created = harness.resolve(stimulus)
    evidence.fact(
        "duplicate_delivery",
        {
            "first_created": first_created,
            "second_created": second_created,
            "first_transaction_id": first.transaction_id,
            "second_transaction_id": second.transaction_id,
        },
    )
    evidence.check(
        "tx_06_robustness.duplicate_generation",
        (
            "Duplicate delivery of the same run generation returns the "
            "existing result without a second transaction or activation."
        ),
        actual={
            "first_transaction_id": first.transaction_id,
            "second_transaction_id": second.transaction_id,
            "transaction_count": len(
                harness.registry.list_for_conversation(
                    conversation_id
                )
            ),
        },
        expected={
            "first_transaction_id": original.transaction_id,
            "second_transaction_id": original.transaction_id,
            "transaction_count": 1,
        },
        evidence="TransactionAttributor.resolve",
        known_gap_key=gap_key,
    )

    fault = harness.inject_fault(
        fault_point="after_schedule_claim_before_activation",
    )
    _target_operation(
        evidence,
        check_id="tx_06_robustness.atomic_fault",
        description=(
            "A half-commit fault rolls back or replays Schedule, "
            "Transaction and Inbox as one transition."
        ),
        result=fault,
        gap_key=gap_key,
    )
    restart = harness.restart_runtime()
    _target_operation(
        evidence,
        check_id="tx_06_robustness.restart",
        description=(
            "Restart preserves run, generation, delivery and claimed "
            "activation bindings."
        ),
        result=restart,
        gap_key=gap_key,
    )
    pause = harness.control_transaction(
        transaction_id=original.transaction_id,
        action="pause",
    )
    _target_operation(
        evidence,
        check_id="tx_06_robustness.pause_control",
        description=(
            "UI Pause atomically invalidates activation and moves the run "
            "to blocked_on_activation(pause)."
        ),
        result=pause,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("run_status")
            == "blocked_on_activation"
            and data.get("blocked_reason") == "pause"
        ),
    )
    schedule = harness.load_schedule(
        schedule_run_id="tx-06-r-run",
    )
    _target_operation(
        evidence,
        check_id="tx_06_robustness.run_recovery",
        description=(
            "The durable run leaves claimed on every blocked/cancelled "
            "control branch and can recover at a safe boundary."
        ),
        result=schedule,
        gap_key=gap_key,
        data_predicate=lambda data: data.get("status") in {
            "blocked_on_activation",
            "cancelled",
            "consumed",
        },
    )
    evidence.check(
        "tx_06_robustness.original_identity",
        "The original transaction remains present while duplicate attempts are observed.",
        actual=harness.registry.get(original.transaction_id) is not None,
        expected=True,
        evidence="TransactionRegistry.get",
    )
    return evidence.build()


def _run_tx_07_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="feedback",
    )
    target = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    harness.registry.begin_delegate(
        target.transaction_id,
        "tx-07-delegate",
    )
    target = harness.registry.get(target.transaction_id) or target
    activation_id = str(target.current_activation_id or "")
    other = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    other.task_state.goal = "unrelated"
    other_before = harness.transaction_snapshot(other)

    valid = harness.make_stimulus(
        conversation_id=conversation_id,
        kind="execution_feedback",
        text="tool result",
        payload={
            "activation_id": activation_id,
            "delegate_id": "tx-07-delegate",
            "tool_history": [],
        },
        source={
            "transaction_id": target.transaction_id,
            "activation_id": activation_id,
            "delegate_id": "tx-07-delegate",
        },
        stimulus_id="tx-07-feedback",
        ingress_key=(
            f"feedback:{target.transaction_id}:"
            f"{activation_id}:tx-07-delegate"
        ),
    )
    harness.admit_envelope(valid, schedule_drainer=False)
    resolved, created = harness.resolve(valid)
    evidence.check(
        "tx_07.feedback.routes_to_original",
        "Valid Feedback bypasses creation and resolves to its transaction.",
        actual={
            "created_new": created,
            "transaction_id": resolved.transaction_id,
        },
        expected={
            "created_new": False,
            "transaction_id": target.transaction_id,
        },
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "tx_07.feedback.triple_identity",
        "Feedback validates transaction, activation and delegate identity.",
        actual={
            "transaction_id": resolved.transaction_id,
            "activation_id": activation_id,
            "delegate_id": "tx-07-delegate",
            "source_activation_id": valid.activation_id,
            "source_delegate_id": valid.delegate_id,
        },
        expected={
            "transaction_id": target.transaction_id,
            "activation_id": activation_id,
            "delegate_id": "tx-07-delegate",
            "source_activation_id": activation_id,
            "source_delegate_id": "tx-07-delegate",
        },
        evidence="TransactionAttributor.resolve",
        known_gap_key=gap_key,
    )

    invalid = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind="execution_feedback",
        text="wrong delegate result",
        payload={
            "activation_id": activation_id,
            "delegate_id": "tx-07-wrong-delegate",
        },
        source={
            "transaction_id": target.transaction_id,
            "activation_id": activation_id,
            "delegate_id": "tx-07-wrong-delegate",
        },
        ingress_key=(
            f"feedback:{target.transaction_id}:"
            f"{activation_id}:tx-07-wrong-delegate"
        ),
    )
    _result_check(
        evidence,
        check_id="tx_07.feedback.invalid_admission",
        description=(
            "Invalid sourced Feedback is Expected Discarded before the "
            "ready queue."
        ),
        result=invalid,
        expected="outcome=expected_discard stage=admission",
        gap_key=gap_key,
        predicate=lambda item: (
            item.supported
            and item.outcome == "expected_discard"
            and item.data.get("stage") == "admission"
        ),
    )

    duplicate, duplicate_created = harness.resolve(valid)
    evidence.check(
        "tx_07.feedback.consumed_once",
        "A delegate is consumed once and duplicate Feedback returns canonical evidence.",
        actual={
            "duplicate_created": duplicate_created,
            "duplicate_transaction_id": duplicate.transaction_id,
            "active_delegate_id": (
                harness.registry.get(target.transaction_id)
                or target
            ).active_delegate_id,
        },
        expected={
            "duplicate_created": False,
            "duplicate_transaction_id": target.transaction_id,
            "active_delegate_id": None,
        },
        evidence="TransactionAttributor+TransactionRegistry",
        known_gap_key=gap_key,
    )
    disposition = harness.load_stimulus(
        stimulus_id="tx-07-feedback",
    )
    _target_operation(
        evidence,
        check_id="tx_07.feedback.disposition",
        description=(
            "Feedback exposes canonical admission/preconsume/final "
            "disposition."
        ),
        result=disposition,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("disposition") in {"consumed", "completed"}
            and data.get("disposition_stage") in {
                "final",
                "preconsume",
                "admission",
            }
        ),
    )
    evidence.check(
        "tx_07.isolation.other_transaction",
        "Feedback for transaction A does not mutate transaction B.",
        actual=_transaction_data(harness, other.transaction_id),
        expected=other_before,
        evidence="TransactionRegistry",
    )
    return evidence.build()


def _run_tx_07_outbox(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="outbox",
    )
    record = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    harness.registry.begin_delegate(
        record.transaction_id,
        "tx-07-outbox-delegate",
    )

    effects = harness.load_effects(
        transaction_id=record.transaction_id,
    )
    _target_operation(
        evidence,
        check_id="tx_07_outbox.effect_ledger",
        description=(
            "The transaction exposes a durable effect intent/result and "
            "Feedback ingress outbox."
        ),
        result=effects,
        gap_key=gap_key,
        data_predicate=lambda data: (
            bool(data.get("effects"))
            and bool(data.get("feedback_outbox"))
        ),
    )
    fault = harness.inject_fault(
        fault_point="after_effect_result_before_feedback_relay",
    )
    _target_operation(
        evidence,
        check_id="tx_07_outbox.result_commit",
        description=(
            "Effect result and Feedback outbox commit atomically across "
            "the relay crash window."
        ),
        result=fault,
        gap_key=gap_key,
    )
    relay = harness.control_effect_sink(
        action="relay_feedback",
        effect_id="tx-07-effect",
    )
    _target_operation(
        evidence,
        check_id="tx_07_outbox.relay_idempotent",
        description=(
            "Relay retries retain one ingress key and one canonical "
            "Feedback."
        ),
        result=relay,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("canonical_feedback_count") == 1
            and bool(data.get("ingress_key"))
        ),
    )
    terminal = harness.control_transaction(
        transaction_id=record.transaction_id,
        action="pause",
    )
    _target_operation(
        evidence,
        check_id="tx_07_outbox.expected_discard_ack",
        description=(
            "A stale Feedback Expected Discard terminally acknowledges "
            "the outbox event instead of retrying forever."
        ),
        result=terminal,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("outbox_status") in {
                "terminal",
                "delivered",
            }
            and bool(data.get("discard_evidence"))
        ),
    )
    evidence.check(
        "tx_07_outbox.transaction_identity",
        "The effect fixture remains owned by its original transaction.",
        actual=harness.registry.get(record.transaction_id) is record,
        expected=True,
        evidence="TransactionRegistry.get",
    )
    return evidence.build()


def _run_tx_07_guarantees(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    from m_agent.systems.tools.default.registry import (
        get_default_capability_registry,
    )

    registry = get_default_capability_registry()
    specs = [
        registry.get(name)
        for name in registry.names()
    ]
    guarantee_values = {
        str(
            getattr(spec, "delivery_guarantee", "")
            or ""
        )
        for spec in specs
        if spec is not None
    }
    evidence.fact(
        "capability_guarantees",
        {
            "capability_count": len(specs),
            "values": sorted(guarantee_values),
        },
    )
    evidence.check(
        "tx_07_guarantees.manifest_declaration",
        "Every capability declares one supported delivery guarantee.",
        actual=sorted(guarantee_values),
        expected=[
            "at_least_once",
            "at_most_once",
            "idempotent",
        ],
        evidence="ControllerCapabilityRegistry",
        known_gap_key=gap_key,
        predicate=lambda values: (
            bool(specs)
            and all(
                value in {
                    "idempotent",
                    "at_most_once",
                    "at_least_once",
                }
                for value in values
            )
            and "" not in values
        ),
    )

    cases = (
        (
            "idempotent",
            "tx_07_guarantees.idempotent_sink",
            lambda data: (
                data.get("attempts", 0) >= 2
                and data.get("visible_effect_count") == 1
                and bool(data.get("idempotency_key"))
            ),
        ),
        (
            "at_most_once",
            "tx_07_guarantees.at_most_once_sink",
            lambda data: (
                data.get("attempts") == 1
                and data.get("status") == "uncertain"
                and data.get("visible_effect_count") in {0, 1}
            ),
        ),
        (
            "at_least_once",
            "tx_07_guarantees.at_least_once_sink",
            lambda data: (
                data.get("attempts", 0) >= 2
                and data.get("visible_effect_count", 0) >= 1
                and bool(data.get("idempotency_key"))
            ),
        ),
    )
    for guarantee, check_id, predicate in cases:
        result = harness.control_effect_sink(
            action=f"exercise_{guarantee}",
            effect_id=f"tx-07-{guarantee}",
        )
        _target_operation(
            evidence,
            check_id=check_id,
            description=(
                f"The controllable {guarantee} sink satisfies its "
                "attempt/ack/visible-effect contract."
            ),
            result=result,
            gap_key=gap_key,
            data_predicate=predicate,
        )
    return evidence.build()


def _run_tx_08_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="isolation",
    )
    first = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    second = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    first.wm_entries.append({"owner": "A"})
    first.task_state.goal = "task A"
    second.wm_entries.append({"owner": "B"})
    second.task_state.goal = "task B"
    harness.registry.begin_delegate(
        first.transaction_id,
        "tx-08-a-delegate",
    )
    first = harness.registry.get(first.transaction_id) or first
    activation_id = str(first.current_activation_id or "")
    second_before = harness.transaction_snapshot(second)

    evidence.check(
        "tx_08.state.initial_isolation",
        "A and B have distinct WM and TaskState ownership.",
        actual={
            "same_wm_object": (
                first.wm_entries is second.wm_entries
            ),
            "same_task_object": (
                first.task_state is second.task_state
            ),
            "a_wm": list(first.wm_entries),
            "b_wm": list(second.wm_entries),
        },
        expected={
            "same_wm_object": False,
            "same_task_object": False,
            "a_wm": [{"owner": "A"}],
            "b_wm": [{"owner": "B"}],
        },
        evidence="TransactionRecord",
    )
    feedback = harness.make_stimulus(
        conversation_id=conversation_id,
        kind="execution_feedback",
        text="A result",
        payload={
            "delegate_id": "tx-08-a-delegate",
            "activation_id": activation_id,
        },
        source={
            "transaction_id": first.transaction_id,
            "activation_id": activation_id,
            "delegate_id": "tx-08-a-delegate",
        },
        stimulus_id="tx-08-a-feedback",
    )
    resolved, created = harness.resolve(feedback)
    evidence.check(
        "tx_08.feedback.no_cross_write",
        "Feedback for A resolves to A and never to B.",
        actual={
            "created_new": created,
            "resolved_transaction_id": resolved.transaction_id,
            "b_state": harness.transaction_snapshot(second),
        },
        expected={
            "created_new": False,
            "resolved_transaction_id": first.transaction_id,
            "b_state": second_before,
        },
        evidence="TransactionAttributor.resolve",
    )

    delete = harness.control_transaction(
        transaction_id=first.transaction_id,
        action="delete",
    )
    _target_operation(
        evidence,
        check_id="tx_08.delete.transaction_control",
        description=(
            "UI Delete aborts only A, writes its tombstone and invalidates "
            "only A's activation/delegates."
        ),
        result=delete,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("transaction_id") == first.transaction_id
            and data.get("deleted") is True
            and data.get("stimulus_disposition") == "aborted"
        ),
    )
    second_after = _transaction_data(
        harness,
        second.transaction_id,
    )
    evidence.check(
        "tx_08.delete.b_unchanged",
        "Delete A does not mutate B.",
        actual=second_after,
        expected=second_before,
        evidence="TransactionRegistry",
    )
    late = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind="execution_feedback",
        text="late A result",
        payload={
            "delegate_id": "tx-08-a-delegate",
            "activation_id": activation_id,
        },
        source={
            "transaction_id": first.transaction_id,
            "activation_id": activation_id,
            "delegate_id": "tx-08-a-delegate",
        },
        ingress_key=(
            f"feedback:{first.transaction_id}:"
            f"{activation_id}:tx-08-a-delegate"
        ),
    )
    _result_check(
        evidence,
        check_id="tx_08.delete.late_feedback",
        description="Feedback for deleted A is permanently Expected Discarded.",
        result=late,
        expected="outcome=expected_discard",
        gap_key=gap_key,
        predicate=lambda item: (
            item.supported
            and item.outcome == "expected_discard"
        ),
    )
    restore = harness.restore_transaction(
        transaction_id=first.transaction_id,
        source="ui",
    )
    _target_operation(
        evidence,
        check_id="tx_08.delete.restore_rejected",
        description="A deleted transaction can never be restored.",
        result=restore,
        gap_key=gap_key,
        expected_outcomes=("rejected",),
    )
    candidates = harness.list_match_candidates(
        conversation_id=conversation_id,
    )
    evidence.check(
        "tx_08.delete.not_match_candidate",
        "Deleted A is absent from future matcher candidates.",
        actual=candidates.data.get("candidate_ids", []),
        expected=f"does not contain {first.transaction_id}",
        evidence="SharedRuntimeHarness.list_match_candidates",
        known_gap_key=gap_key,
        predicate=lambda values: first.transaction_id not in values,
    )
    return evidence.build()


def _run_tx_09_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="scene",
    )
    first = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    second = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    appended = [
        _append_scene(
            harness,
            conversation_id=conversation_id,
            transaction_id=first.transaction_id,
            text="A user input",
            occurred_at="2026-01-01T00:00:01Z",
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
        ),
        _append_scene(
            harness,
            conversation_id=conversation_id,
            transaction_id=second.transaction_id,
            text="B action result arrived first",
            occurred_at="2026-01-01T00:00:03Z",
        ),
        _append_scene(
            harness,
            conversation_id=conversation_id,
            transaction_id=first.transaction_id,
            text="A action result arrived second",
            occurred_at="2026-01-01T00:00:04Z",
        ),
        _append_scene(
            harness,
            conversation_id=conversation_id,
            transaction_id=None,
            text="conversation-level event",
            occurred_at="2026-01-01T00:00:05Z",
            entry_type=SceneEntryType.OUTCOME,
        ),
    ]
    scene = harness.read_scene(conversation_id=conversation_id)
    entries = list(scene.data.get("entries", []))
    evidence.fact("scene", scene.to_dict())
    evidence.check(
        "tx_09.scene.single_timeline",
        "A and B append to one conversation Scene with strict monotonic sequence.",
        actual={
            "entry_count": len(entries),
            "seqs": [item.get("seq") for item in entries],
            "current_seq": scene.data.get("current_seq"),
        },
        expected={
            "entry_count": 4,
            "seqs": [1, 2, 3, 4],
            "current_seq": 4,
        },
        evidence="SceneLogStore",
    )
    evidence.check(
        "tx_09.scene.actual_arrival_order",
        "Asynchronous results are stored in their actual arrival order.",
        actual=[item.get("text") for item in entries[1:3]],
        expected=[
            "B action result arrived first",
            "A action result arrived second",
        ],
        evidence="SceneLogStore.append",
    )
    evidence.check(
        "tx_09.scene.transaction_association",
        "Attributed entries retain the correct transaction association.",
        actual=[
            item.get("transaction_id")
            for item in entries
        ],
        expected=[
            first.transaction_id,
            second.transaction_id,
            first.transaction_id,
            None,
        ],
        evidence="SceneLogStore",
    )
    evidence.check(
        "tx_09.scene.append_id",
        "Every Scene append exposes a stable append_id for idempotent replay.",
        actual=[
            item.get("append_id")
            for item in entries
        ],
        expected="four non-empty unique append IDs",
        evidence="SceneLogStore",
        known_gap_key=gap_key,
        predicate=lambda values: (
            len(values) == 4
            and all(values)
            and len(set(values)) == 4
        ),
    )

    pause = harness.control_transaction(
        transaction_id=first.transaction_id,
        action="pause",
    )
    _target_operation(
        evidence,
        check_id="tx_09.scene.lifecycle_control",
        description=(
            "Pause/Restore/Delete can be exercised without splitting or "
            "rewriting the Scene."
        ),
        result=pause,
        gap_key=gap_key,
    )
    _append_scene(
        harness,
        conversation_id=conversation_id,
        transaction_id=first.transaction_id,
        text="A later event remains in the same Scene",
        occurred_at="2026-01-01T00:00:06Z",
    )
    later = harness.read_scene(conversation_id=conversation_id)
    evidence.check(
        "tx_09.scene.append_only_history",
        "Later events extend the same append-only timeline without rewriting history.",
        actual={
            "original_seqs": [item.seq for item in appended],
            "all_seqs": [
                item.get("seq")
                for item in later.data.get("entries", [])
            ],
        },
        expected={
            "original_seqs": [1, 2, 3, 4],
            "all_seqs": [1, 2, 3, 4, 5],
        },
        evidence="SceneLogStore",
    )
    flush = harness.trigger_flush(
        conversation_id=conversation_id,
        flush_id="tx-09-flush",
    )
    _target_operation(
        evidence,
        check_id="tx_09.scene.flush_boundary",
        description=(
            "Flush creates one observable boundary carrying the stable "
            "flush_id without deleting history."
        ),
        result=flush,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("flush_id") == "tx-09-flush"
            and data.get("visible_boundary_count") == 1
        ),
    )
    return evidence.build()


def _run_tx_10_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="flush",
    )
    continuing = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    paused = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.SUSPENDED,
    )
    complete = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.COMPLETED,
    )
    scheduled_wait = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.SUSPENDED,
        kind=TransactionKind.SCHEDULE,
    )
    pending_delegate = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    harness.registry.begin_delegate(
        pending_delegate.transaction_id,
        "tx-10-pending-delegate",
    )
    for index, transaction_id in enumerate(
        (
            continuing.transaction_id,
            complete.transaction_id,
            None,
        ),
        start=1,
    ):
        _append_scene(
            harness,
            conversation_id=conversation_id,
            transaction_id=transaction_id,
            text=f"flush entry {index}",
            occurred_at=f"2026-01-01T00:00:0{index}Z",
        )
    def preserve_completed_state(
        transaction: TransactionRecord | None,
    ) -> Dict[str, Any]:
        assert transaction is not None
        transaction.wm_entries.append(
            {"fact": "preserve-after-flush"}
        )
        transaction.task_state.goal = "completed task"
        return {"transaction_id": transaction.transaction_id}

    harness.uow.apply_transition(
        "tx-10-preserve-completed-state",
        {"action": "persist_completed_owned_state"},
        complete.transaction_id,
        complete.revision,
        preserve_completed_state,
    )

    before = harness.read_scene(conversation_id=conversation_id)
    through_seq = int(before.data.get("current_seq", 0))
    manual = harness.trigger_flush(
        conversation_id=conversation_id,
        flush_id="tx-10-manual",
    )
    _target_operation(
        evidence,
        check_id="tx_10.flush.manual_contract",
        description=(
            "Manual Flush atomically commits payload, watermark, eligible "
            "archive and one visible boundary."
        ),
        result=manual,
        gap_key=gap_key,
        data_predicate=lambda data: (
            data.get("through_seq") == through_seq
            and data.get("watermark") == through_seq
            and data.get("visible_boundary_count") == 1
            and data.get("archived_transaction_ids")
            == [complete.transaction_id]
        ),
    )
    automatic = harness.trigger_flush(
        conversation_id=conversation_id,
        flush_id="tx-10-auto",
    )
    _target_operation(
        evidence,
        check_id="tx_10.flush.auto_contract",
        description="Automatic Flush follows the same semantic contract as manual Flush.",
        result=automatic,
        gap_key=gap_key,
    )

    after_scene = harness.read_scene(
        conversation_id=conversation_id,
    )
    evidence.check(
        "tx_10.scene.delta_order",
        "The fixed Scene delta remains in original cross-transaction order.",
        actual=[
            (item.get("seq"), item.get("transaction_id"))
            for item in after_scene.data.get("entries", [])
        ],
        expected=[
            (1, continuing.transaction_id),
            (2, complete.transaction_id),
            (3, None),
        ],
        evidence="SceneLogStore",
    )
    evidence.check(
        "tx_10.flush.watermark",
        "The durable watermark advances only through the committed Scene sequence.",
        actual=after_scene.data.get("flush_watermark"),
        expected=through_seq,
        evidence="SceneLogStore.flush_watermark",
        known_gap_key=gap_key,
    )
    complete_after = _transaction_data(
        harness,
        complete.transaction_id,
    )
    evidence.check(
        "tx_10.flush.eligible_archive",
        "Only eligible complete is archived.",
        actual=complete_after.get("state"),
        expected="archive",
        evidence="SharedRuntimeHarness.load_transaction",
        known_gap_key=gap_key,
    )
    evidence.check(
        "tx_10.flush.ineligible_unchanged",
        "Continue, pause, scheduled_wait and pending-delegate transactions are not archived.",
        actual={
            "continue": _transaction_data(
                harness,
                continuing.transaction_id,
            ).get("state"),
            "pause": _transaction_data(
                harness,
                paused.transaction_id,
            ).get("state"),
            "scheduled_wait": _transaction_data(
                harness,
                scheduled_wait.transaction_id,
            ).get("state"),
            "pending_delegate": _transaction_data(
                harness,
                pending_delegate.transaction_id,
            ).get("state"),
        },
        expected={
            "continue": TransactionState.CONTINUE.value,
            "pause": TransactionState.PAUSE.value,
            "scheduled_wait": TransactionState.PAUSE.value,
            "pending_delegate": TransactionState.CONTINUE.value,
        },
        evidence="TransactionRegistry",
    )
    evidence.check(
        "tx_10.flush.owned_state_preserved",
        "Flush does not clear transaction WM or TaskState.",
        actual={
            "wm_entries": complete_after.get("wm_entries"),
            "goal": (
                complete_after.get("task_state") or {}
            ).get("goal"),
        },
        expected={
            "wm_entries": [{"fact": "preserve-after-flush"}],
            "goal": "completed task",
        },
        evidence="TransactionRegistry",
    )
    return evidence.build()


def _run_tx_10_robustness(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="flush-robustness",
    )
    complete = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.COMPLETED,
    )
    _append_scene(
        harness,
        conversation_id=conversation_id,
        transaction_id=complete.transaction_id,
        text="flush fault payload",
        occurred_at="2026-01-01T00:00:01Z",
    )
    before = harness.read_scene(conversation_id=conversation_id)

    fault = harness.inject_fault(
        fault_point="after_flush_commit_before_materialize",
    )
    _target_operation(
        evidence,
        check_id="tx_10_robustness.fault_injection",
        description=(
            "The scenario can deterministically crash before/after the "
            "Flush semantic commit."
        ),
        result=fault,
        gap_key=gap_key,
    )
    first = harness.trigger_flush(
        conversation_id=conversation_id,
        flush_id="tx-10-stable-flush",
    )
    replay = harness.trigger_flush(
        conversation_id=conversation_id,
        flush_id="tx-10-stable-flush",
    )
    evidence.fact("flush.first", first.to_dict())
    evidence.fact("flush.replay", replay.to_dict())
    evidence.check(
        "tx_10_robustness.replay",
        (
            "Repeating a committed flush_id returns the first complete "
            "result without a second boundary, watermark advance or archive."
        ),
        actual={
            "first": first.to_dict(),
            "replay": replay.to_dict(),
        },
        expected="supported identical committed results with boundary_count=1",
        evidence="FlushJournal",
        known_gap_key=gap_key,
        predicate=lambda _value: (
            first.supported
            and replay.supported
            and first.outcome == "ok"
            and replay.outcome == "ok"
            and first.data == replay.data
            and first.data.get("visible_boundary_count") == 1
        ),
    )
    restart = harness.restart_runtime()
    _target_operation(
        evidence,
        check_id="tx_10_robustness.outbox_recovery",
        description=(
            "After commit and before materialization, restart resumes the "
            "same flush outbox without recommitting."
        ),
        result=restart,
        gap_key=gap_key,
    )
    after = harness.read_scene(conversation_id=conversation_id)
    evidence.check(
        "tx_10_robustness.scene_source_intact",
        "Fault probing does not delete the source Scene history.",
        actual=after.data.get("entries"),
        expected=before.data.get("entries"),
        evidence="SceneLogStore",
    )
    evidence.check(
        "tx_10_robustness.atomic_state",
        "The stable flush commits watermark and archive together or rolls both back.",
        actual={
            "watermark": after.data.get("flush_watermark"),
            "transaction_state": _transaction_data(
                harness,
                complete.transaction_id,
            ).get("state"),
        },
        expected=(
            "either watermark=0/state=complete or "
            "watermark=1/state=archive"
        ),
        evidence="FlushJournal",
        known_gap_key=gap_key,
        predicate=lambda value: value in (
            {
                "watermark": 0,
                "transaction_state": "complete",
            },
            {
                "watermark": 1,
                "transaction_state": "archive",
            },
        ),
    )
    return evidence.build()


def _run_tx_01_poc_checkpoint_resume(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(harness, evidence, suffix="poc-resume")
    record = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    record.wm_entries.append({"fact": "poc-resume-owned"})
    record.task_state.goal = "resume after pause"
    old_activation = str(record.current_activation_id or "")
    paused = harness.control_transaction(
        transaction_id=record.transaction_id,
        action="pause",
    )
    restored = harness.restore_transaction(
        transaction_id=record.transaction_id,
        source="ui",
    )
    after = _transaction_data(harness, record.transaction_id)
    evidence.fact("pause", paused.to_dict())
    evidence.fact("restore", restored.to_dict())
    evidence.check(
        "poc_resume.pause_then_continue",
        "A locked transaction can pause and later continue via a new activation.",
        actual={
            "paused_state": paused.data.get("state"),
            "restored_state": after.get("state"),
            "activation_before": old_activation,
            "activation_after": after.get("current_activation_id"),
        },
        expected="pause then continue with a different activation",
        evidence="TransactionRegistry",
        known_gap_key=gap_key,
        predicate=lambda value: (
            value.get("paused_state") == "pause"
            and value.get("restored_state") == "continue"
            and value.get("activation_before")
            and value.get("activation_after")
            and value.get("activation_before")
            != value.get("activation_after")
        ),
    )
    evidence.check(
        "poc_resume.wm_task_state_preserved",
        "Pause and restore preserve WM and task state in the Transaction Store.",
        actual={
            "wm_entries": after.get("wm_entries"),
            "goal": (after.get("task_state") or {}).get("goal"),
        },
        expected={
            "wm_entries": [{"fact": "poc-resume-owned"}],
            "goal": "resume after pause",
        },
        evidence="TransactionRegistry",
    )
    return evidence.build()


def _run_tx_01_poc_sequential_fake_effects(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(harness, evidence, suffix="poc-effects")
    record = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    record.task_state.goal = "sequential fake effects"
    first = harness.registry.begin_delegate(
        record.transaction_id,
        "tl-capability-delegate",
    )
    first_activation = str(first.current_activation_id or "")
    harness.registry.consume_feedback(
        record.transaction_id,
        first_activation,
        "tl-capability-delegate",
    )
    between = _transaction_data(harness, record.transaction_id)
    second = harness.registry.begin_delegate(
        record.transaction_id,
        "tl-reply-delegate",
    )
    sink = harness.control_effect_sink(action="install")
    evidence.fact("sink", sink.to_dict())
    evidence.check(
        "poc_effects.separate_delegates",
        "Capability and reply each create an independent delegate.",
        actual={
            "first_delegate": "tl-capability-delegate",
            "second_delegate": "tl-reply-delegate",
            "between_active_delegate": between.get("active_delegate_id"),
        },
        expected={
            "first_delegate": "tl-capability-delegate",
            "second_delegate": "tl-reply-delegate",
            "between_active_delegate": None,
        },
        evidence="TransactionRegistry",
        known_gap_key=gap_key,
    )
    evidence.check(
        "poc_effects.async_sink_controllable",
        "Fake effects remain controllable without blocking the next delegate.",
        actual=sink.supported,
        expected=True,
        evidence="SharedRuntimeHarness.control_effect_sink",
    )
    evidence.check(
        "poc_effects.final_state_awaiting_feedback",
        "Pending Feedback is delegate state; the transaction remains continue.",
        actual=_transaction_data(harness, record.transaction_id).get("state"),
        expected="continue",
        evidence="TransactionRegistry",
    )
    return evidence.build()


def _run_tx_01_poc_stale_feedback_after_restore(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(
        harness,
        evidence,
        suffix="poc-stale-fb",
    )
    record = _seed_transaction(
        harness,
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    old_activation = str(record.current_activation_id or "")
    harness.registry.begin_delegate(
        record.transaction_id,
        "tl-stale-delegate",
    )
    harness.control_transaction(
        transaction_id=record.transaction_id,
        action="pause",
    )
    restore = harness.restore_transaction(
        transaction_id=record.transaction_id,
        source="ui",
    )
    new_activation = str(restore.data.get("activation_id", ""))
    late_feedback = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind="execution_feedback",
        text="late fake result",
        payload={
            "activation_id": old_activation,
            "delegate_id": "tl-stale-delegate",
        },
        source={
            "transaction_id": record.transaction_id,
            "activation_id": old_activation,
            "delegate_id": "tl-stale-delegate",
        },
        ingress_key=(
            f"feedback:{record.transaction_id}:"
            f"{old_activation}:tl-stale-delegate"
        ),
    )
    evidence.fact("restore", restore.to_dict())
    evidence.fact("late_feedback", late_feedback.to_dict())
    evidence.check(
        "poc_stale_feedback.old_activation_discarded",
        "Fake Feedback from the invalidated activation is Expected Discarded.",
        actual={
            "outcome": late_feedback.outcome,
            "stage": late_feedback.data.get("stage"),
            "old_activation": old_activation,
            "new_activation": new_activation,
        },
        expected={
            "outcome": "expected_discard",
            "stage": "admission",
            "old_activation": old_activation,
            "new_activation": new_activation,
        },
        evidence="PerceptionGateway",
        known_gap_key=gap_key,
        predicate=lambda value: (
            value.get("outcome") == "expected_discard"
            and value.get("stage") == "admission"
            and value.get("old_activation")
            and value.get("new_activation")
            and value.get("old_activation") != value.get("new_activation")
        ),
    )
    return evidence.build()


_HANDLERS: Dict[tuple[str, str], Handler] = {
    ("TX-01", "TX-01/core"): _run_tx_01_core,
    (
        "TX-01",
        "TX-01/uow_replay_and_result",
    ): _run_tx_01_uow,
    (
        "TX-01",
        "TX-01/poc_checkpoint_resume",
    ): _run_tx_01_poc_checkpoint_resume,
    (
        "TX-01",
        "TX-01/poc_sequential_fake_effects",
    ): _run_tx_01_poc_sequential_fake_effects,
    (
        "TX-01",
        "TX-01/poc_stale_feedback_after_restore",
    ): _run_tx_01_poc_stale_feedback_after_restore,
    ("TX-02", "TX-02/core"): _run_tx_02_core,
    ("TX-03", "TX-03/core"): _run_tx_03_core,
    ("TX-04", "TX-04/core"): _run_tx_04_core,
    ("TX-05", "TX-05/core"): _run_tx_05_core,
    ("TX-06", "TX-06/core"): _run_tx_06_core,
    (
        "TX-06",
        "TX-06/schedule_atomicity_and_control_recovery",
    ): _run_tx_06_robustness,
    ("TX-07", "TX-07/core"): _run_tx_07_core,
    (
        "TX-07",
        "TX-07/effect_result_feedback_outbox",
    ): _run_tx_07_outbox,
    (
        "TX-07",
        "TX-07/capability_delivery_guarantees",
    ): _run_tx_07_guarantees,
    ("TX-08", "TX-08/core"): _run_tx_08_core,
    ("TX-09", "TX-09/core"): _run_tx_09_core,
    ("TX-10", "TX-10/core"): _run_tx_10_core,
    (
        "TX-10",
        "TX-10/flush_fault_recovery",
    ): _run_tx_10_robustness,
}


def run_tx_scenario(
    harness: SharedRuntimeHarness,
    scenario_id: str,
    variant_id: str,
) -> ScenarioExecution:
    """Run one TX Core/Robustness variant through normalized evidence."""

    scenario = str(scenario_id or "").strip().upper()
    variant = str(variant_id or "").strip()
    handler = _HANDLERS.get((scenario, variant))
    if handler is None:
        raise RuntimeAdapterError(
            f"{harness.runtime_id} TX adapter does not implement "
            f"{scenario}/{variant}"
        )
    evidence = _builder(harness, scenario, variant)
    return handler(
        harness,
        evidence,
        registered_known_gap_key(variant),
    )


__all__ = ["run_tx_scenario"]
