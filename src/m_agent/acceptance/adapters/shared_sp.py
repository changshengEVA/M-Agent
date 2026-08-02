"""Shared executable SP contract scenarios."""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List

from m_agent.layers.perception.contracts import StimulusKind
from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
)
from m_agent.runtime.dispatch.drainer import ThreadDrainerService

from ..scenario_catalog import registered_known_gap_key
from .base import RuntimeAdapterError, ScenarioExecution
from .evidence import EvidenceBuilder
from .shared_harness import SharedRuntimeHarness
from .transaction_fixtures import FixtureTransactionStatus as TransactionStatus


_SP_VARIANTS = {
    ("SP-01", "sp-01/core"),
    ("SP-02", "sp-02/core"),
    ("SP-03", "sp-03/core"),
    ("SP-04", "sp-04/core"),
    ("SP-05", "sp-05/core"),
    ("SP-06", "sp-06/core"),
    ("SP-07", "sp-07/core"),
    ("SP-08", "sp-08/core"),
    ("SP-01", "sp-01/durable_ingress_restart"),
    ("SP-01", "sp-01/lease_takeover_fencing"),
}


def _create_conversation(
    harness: SharedRuntimeHarness,
    *,
    thread_id: str,
) -> str:
    result = harness.create_conversation(thread_id=thread_id)
    if not result.supported or result.outcome != "ok":
        raise RuntimeAdapterError(
            f"cannot create SP conversation: {result.to_dict()}"
        )
    return str(result.data["conversation_id"])


def _pop_all(
    harness: SharedRuntimeHarness,
    *,
    thread_id: str,
) -> List[StimulusEnvelope]:
    items: List[StimulusEnvelope] = []
    while True:
        item = harness.inbox.pop_next(thread_id)
        if item is None:
            return items
        items.append(item)


def _submit_envelopes(
    harness: SharedRuntimeHarness,
    stimuli: List[StimulusEnvelope],
) -> None:
    for stimulus in stimuli:
        harness.gateway.submit(stimulus, schedule_drainer=False)


def _run_sp_01_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap: str,
) -> ScenarioExecution:
    thread_id = "acceptance-sp-01"
    conversation_id = _create_conversation(
        harness,
        thread_id=thread_id,
    )
    transaction = harness.create_runtime_transaction(
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    harness.registry.begin_delegate(
        transaction.transaction_id,
        "delegate-valid",
    )
    transaction = harness.registry.get(transaction.transaction_id) or transaction
    activation_id = str(transaction.current_activation_id or "")

    user = harness.make_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.USER_MESSAGE.value,
        text="user input",
        payload={"fixture": "user"},
        stimulus_id="sp-01-user",
    )
    feedback = harness.make_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.EXECUTION_FEEDBACK.value,
        text="tool completed",
        payload={"fixture": "feedback"},
        source={
            "transaction_id": transaction.transaction_id,
            "activation_id": activation_id,
            "delegate_id": "delegate-valid",
        },
        stimulus_id="sp-01-feedback",
    )
    scheduled = harness.make_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.SCHEDULED_PLAN.value,
        text="scheduled work is due",
        payload={
            "fixture": "schedule",
            "schedule_run_id": "run-valid",
            "schedule_delivery_id": "delivery-valid",
        },
        source={
            "transaction_id": transaction.transaction_id,
            "schedule_id": "schedule-valid",
            "schedule_run_id": "run-valid",
            "schedule_delivery_id": "delivery-valid",
        },
        priority_override=30,
        stimulus_id="sp-01-schedule",
    )
    observation = harness.make_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="environment changed",
        payload={"fixture": "observation"},
        stimulus_id="sp-01-observation",
    )
    accepted = [user, feedback, scheduled, observation]
    _submit_envelopes(harness, accepted)
    evidence.event(
        "stimuli_submitted",
        phase="act",
        source="PerceptionGateway",
        conversation_id=conversation_id,
        stimulus_count=len(accepted),
    )

    popped = _pop_all(harness, thread_id=thread_id)
    evidence.fact(
        "positive_ingress_order",
        [item.stimulus_id for item in popped],
    )
    evidence.check(
        "sp01.positive_ingress_count",
        "All four built-in target stimulus kinds enter the production Inbox.",
        actual=len(popped),
        expected=4,
        evidence="StimulusInbox",
    )
    evidence.check(
        "sp01.positive_identity_preserved",
        "Inbox preserves each accepted stimulus identity.",
        actual=sorted(item.stimulus_id for item in popped),
        expected=sorted(item.stimulus_id for item in accepted),
        evidence="StimulusInbox",
    )
    evidence.check(
        "sp01.no_attribution_during_ingress",
        "Ingress itself does not create a second transaction.",
        actual=harness.registry.count_all(),
        expected=1,
        evidence="TransactionRegistry",
    )
    resolved, created = harness.resolve(feedback)
    evidence.check(
        "sp01.valid_feedback_routes_to_source",
        "Valid Feedback is resolved to its source transaction.",
        actual={
            "transaction_id": resolved.transaction_id,
            "created": created,
        },
        expected={
            "transaction_id": transaction.transaction_id,
            "created": False,
        },
        evidence="TransactionAttributor._resolve_feedback",
    )
    evidence.check(
        "sp01.feedback_activation_preserved",
        "Feedback preserves transaction/activation/delegate identity.",
        actual={
            "transaction_id": feedback.transaction_id,
            "activation_id": getattr(feedback, "activation_id", None),
            "delegate_id": feedback.delegate_id,
        },
        expected={
            "transaction_id": transaction.transaction_id,
            "activation_id": activation_id,
            "delegate_id": "delegate-valid",
        },
        evidence="StimulusEnvelope",
        known_gap_key=gap,
    )
    evidence.check(
        "sp01.schedule_source_preserved",
        "Scheduled Plan preserves transaction/run/delivery source identity.",
        actual={
            "transaction_id": scheduled.transaction_id,
            "schedule_id": scheduled.schedule_id,
            "schedule_run_id": getattr(
                scheduled,
                "schedule_run_id",
                None,
            ),
            "schedule_delivery_id": getattr(
                scheduled,
                "schedule_delivery_id",
                None,
            ),
        },
        expected={
            "transaction_id": transaction.transaction_id,
            "schedule_id": "schedule-valid",
            "schedule_run_id": "run-valid",
            "schedule_delivery_id": "delivery-valid",
        },
        evidence="StimulusEnvelope",
        known_gap_key=gap,
    )
    evidence.check(
        "sp01.accepted_seq_assigned",
        "Every accepted stimulus receives a stable accepted_seq.",
        actual=[
            getattr(item, "accepted_seq", None)
            for item in accepted
        ],
        expected=[1, 2, 3, 4],
        evidence="StimulusInbox",
        known_gap_key=gap,
    )

    extension = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind="registered_extension_fixture",
        text="extension",
    )
    evidence.check(
        "sp01.registered_extension_admitted",
        "A registered extension kind with an explicit default priority is admitted.",
        actual={
            "outcome": extension.outcome,
            "reason": extension.reason,
        },
        expected={"outcome": "ok", "reason": ""},
        evidence="SharedRuntimeHarness.submit_stimulus",
        known_gap_key=gap,
    )

    duplicate_thread = "acceptance-sp-01-duplicate"
    duplicate_conversation = _create_conversation(
        harness,
        thread_id=duplicate_thread,
    )
    first_duplicate = harness.submit_stimulus(
        conversation_id=duplicate_conversation,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="same event",
        ingress_key="event:stable-key",
    )
    second_duplicate = harness.submit_stimulus(
        conversation_id=duplicate_conversation,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="same event",
        ingress_key="event:stable-key",
    )
    evidence.check(
        "sp01.duplicate_ingress_is_canonical",
        "The same ingress key maps to one canonical stimulus and queue slot.",
        actual={
            "same_stimulus_id": (
                first_duplicate.data.get("stimulus_id")
                == second_duplicate.data.get("stimulus_id")
            ),
            "ready_count": harness.inbox.pending_count(
                duplicate_thread
            ),
            "ingress_key_persisted": bool(
                first_duplicate.data.get("ingress_key_persisted")
            ),
        },
        expected={
            "same_stimulus_id": True,
            "ready_count": 1,
            "ingress_key_persisted": True,
        },
        evidence="PerceptionGateway/StimulusInbox",
        known_gap_key=gap,
    )

    invalid_thread = "acceptance-sp-01-invalid"
    invalid_conversation = _create_conversation(
        harness,
        thread_id=invalid_thread,
    )
    invalid = harness.make_stimulus(
        conversation_id=invalid_conversation,
        kind=StimulusKind.EXECUTION_FEEDBACK.value,
        text="stale result",
        source={
            "transaction_id": "transaction-missing",
            "activation_id": "activation-missing",
            "delegate_id": "delegate-missing",
        },
        stimulus_id="sp-01-invalid-feedback",
    )
    harness.gateway.submit(invalid, schedule_drainer=False)
    evidence.check(
        "sp01.invalid_feedback_discarded_at_admission",
        "Invalid Feedback is discarded before it acquires a queue position.",
        actual={
            "ready_count": harness.inbox.pending_count(invalid_thread),
            "accepted_seq": getattr(invalid, "accepted_seq", None),
        },
        expected={"ready_count": 0, "accepted_seq": None},
        evidence="PerceptionGateway",
        known_gap_key=gap,
    )
    invalid_popped = harness.inbox.pop_next(invalid_thread)
    late_rejected = invalid_popped is None
    if invalid_popped is not None:
        try:
            harness.resolve(invalid_popped)
        except ValueError:
            late_rejected = True
    evidence.check(
        "sp01.late_feedback_check",
        "Unknown Feedback is rejected at admission or before attribution.",
        actual=late_rejected,
        expected=True,
        evidence="PerceptionGateway/TransactionAttributor._resolve_feedback",
    )

    race_thread = "acceptance-sp-01-preconsume"
    race_conversation = _create_conversation(
        harness,
        thread_id=race_thread,
    )
    race_transaction = harness.create_runtime_transaction(
        conversation_id=race_conversation,
        status=TransactionStatus.RUNNING,
    )
    delegated = harness.registry.begin_delegate(
        race_transaction.transaction_id,
        "delegate-old",
    )
    old_activation_id = str(delegated.current_activation_id or "")
    race_feedback = harness.make_stimulus(
        conversation_id=race_conversation,
        kind=StimulusKind.EXECUTION_FEEDBACK.value,
        text="old result",
        source={
            "transaction_id": race_transaction.transaction_id,
            "activation_id": old_activation_id,
            "delegate_id": "delegate-old",
        },
        stimulus_id="sp-01-preconsume-feedback",
    )
    harness.gateway.submit(race_feedback, schedule_drainer=False)
    harness.registry.pause(race_transaction.transaction_id)
    harness.registry.restore(
        race_transaction.transaction_id,
        source="sp-01-preconsume-fixture",
    )
    harness.registry.begin_delegate(
        race_transaction.transaction_id,
        "delegate-new",
    )
    before_transactions = harness.registry.count_all()
    before_scene = len(
        harness.scene_store.tail(race_conversation)
    )
    selected = harness.inbox.pop_next(race_thread)
    preconsume_rejected = False
    if selected is not None:
        try:
            harness.resolve(selected)
        except ValueError:
            preconsume_rejected = True
    evidence.check(
        "sp01.preconsume_source_rechecked",
        "A source invalidated after enqueue is checked again before attribution.",
        actual=preconsume_rejected,
        expected=True,
        evidence="TransactionAttributor._resolve_feedback",
    )
    evidence.check(
        "sp01.preconsume_has_no_transaction_or_scene_side_effect",
        "The late rejection creates no transaction or Scene entry.",
        actual={
            "transaction_count": harness.registry.count_all(),
            "scene_count": len(
                harness.scene_store.tail(race_conversation)
            ),
        },
        expected={
            "transaction_count": before_transactions,
            "scene_count": before_scene,
        },
        evidence="TransactionRegistry/SceneLogStore",
    )
    disposition = harness.load_stimulus(
        stimulus_id=race_feedback.stimulus_id
    )
    evidence.check(
        "sp01.preconsume_expected_discard_persisted",
        "The preconsume rejection is atomically persisted as Expected Discard.",
        actual={
            "supported": disposition.supported,
            "outcome": disposition.outcome,
            "status": disposition.data.get("status"),
            "stage": disposition.data.get("disposition_stage"),
        },
        expected={
            "supported": True,
            "outcome": "ok",
            "status": "expected_discard",
            "stage": "preconsume",
        },
        evidence="StimulusInboxStore",
        known_gap_key=gap,
    )
    return evidence.build()


def _run_sp_02_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap: str,
) -> ScenarioExecution:
    thread_id = "acceptance-sp-02"
    conversation_id = _create_conversation(
        harness,
        thread_id=thread_id,
    )
    low = harness.make_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.SCHEDULED_PLAN.value,
        text="low",
        priority_override=50,
        stimulus_id="sp-02-low",
    )
    high = harness.make_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.USER_MESSAGE.value,
        text="high",
        priority_override=5,
        stimulus_id="sp-02-high",
    )
    middle = harness.make_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.EXECUTION_FEEDBACK.value,
        text="middle",
        source={
            "transaction_id": "priority-fixture",
            "delegate_id": "priority-delegate",
        },
        priority_override=20,
        stimulus_id="sp-02-middle",
    )
    background = harness.make_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="background",
        priority_override=30,
        stimulus_id="sp-02-background",
    )
    _submit_envelopes(
        harness,
        [low, high, middle, background],
    )
    order = [
        item.stimulus_id
        for item in _pop_all(harness, thread_id=thread_id)
    ]
    evidence.fact("override_priority_order", order)
    evidence.check(
        "sp02.raw_heap_priority_order",
        "Accepted stimuli are ordered by numeric priority.",
        actual=order,
        expected=[
            "sp-02-high",
            "sp-02-background",
            "sp-02-low",
        ],
        evidence="PerceptionGateway/StimulusInbox",
    )

    default_conversation = _create_conversation(
        harness,
        thread_id="acceptance-sp-02-defaults",
    )
    defaults = {
        kind.value: harness.attributor.priority_for(
            harness.make_stimulus(
                conversation_id=default_conversation,
                kind=kind.value,
                text=kind.value,
            )
        )
        for kind in StimulusKind
    }
    evidence.fact("default_priorities", defaults)
    for kind, expected in (
        (StimulusKind.USER_MESSAGE.value, 10),
        (StimulusKind.EXECUTION_FEEDBACK.value, 20),
        (StimulusKind.OBSERVATION_TRIGGER.value, 40),
    ):
        evidence.check(
            f"sp02.default_priority.{kind}",
            f"{kind} retains its target default priority.",
            actual=defaults[kind],
            expected=expected,
            evidence="TransactionAttributor.priority_for",
        )
    evidence.check(
        "sp02.default_priority.scheduled_plan",
        "Scheduled Plan has a distinct default priority of 30.",
        actual=defaults[StimulusKind.SCHEDULED_PLAN.value],
        expected=30,
        evidence="TransactionAttributor.priority_for",
        known_gap_key=gap,
    )
    zero_override = harness.make_stimulus(
        conversation_id=default_conversation,
        kind=StimulusKind.USER_MESSAGE.value,
        text="trusted urgent event",
        priority_override=0,
    )
    evidence.check(
        "sp02.zero_priority_override",
        "A trusted priority override of zero remains valid.",
        actual=harness.attributor.priority_for(zero_override),
        expected=0,
        evidence="TransactionAttributor.priority_for",
    )
    invalid_override = harness.submit_stimulus(
        conversation_id=default_conversation,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="invalid priority",
        priority_override=-1,
    )
    evidence.check(
        "sp02.override_range_validated",
        "Priority overrides outside 0..100 are rejected.",
        actual=invalid_override.outcome,
        expected="rejected",
        evidence="PerceptionGateway",
        known_gap_key=gap,
    )
    ready = harness.list_ready_stimuli(
        conversation_id=default_conversation
    )
    evidence.check(
        "sp02.conversation_ready_order_observable",
        "Ready ordering is queryable at the conversation partition boundary.",
        actual=ready.supported,
        expected=True,
        evidence="StimulusInboxStore",
        known_gap_key=gap,
    )
    evidence.check(
        "sp02.invalid_feedback_has_no_sorting_position",
        "Feedback rejected at admission never participates in priority ordering.",
        actual=middle.stimulus_id in order,
        expected=False,
        evidence="PerceptionGateway/StimulusInbox",
        known_gap_key=gap,
    )
    return evidence.build()


def _run_sp_03_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap: str,
) -> ScenarioExecution:
    thread_id = "acceptance-sp-03"
    conversation_id = _create_conversation(
        harness,
        thread_id=thread_id,
    )
    stimuli = [
        harness.make_stimulus(
            conversation_id=conversation_id,
            kind=StimulusKind.OBSERVATION_TRIGGER.value,
            text=name,
            priority_override=25,
            stimulus_id=f"sp-03-{name.lower()}",
            occurred_at=occurred_at,
        )
        for name, occurred_at in (
            ("A", "2026-01-01T00:00:03Z"),
            ("B", "2026-01-01T00:00:02Z"),
            ("C", "2026-01-01T00:00:01Z"),
        )
    ]
    _submit_envelopes(harness, stimuli)
    popped = _pop_all(harness, thread_id=thread_id)
    actual_order = [item.text for item in popped]
    evidence.fact("submitted_order", ["A", "B", "C"])
    evidence.fact("consumed_order", actual_order)
    evidence.check(
        "sp03.identity_and_count_preserved",
        "All same-priority stimuli remain present exactly once.",
        actual={
            "count": len(popped),
            "ids": sorted(
                item.stimulus_id
                for item in popped
            ),
        },
        expected={
            "count": 3,
            "ids": sorted(
                item.stimulus_id
                for item in stimuli
            ),
        },
        evidence="StimulusInbox",
    )
    evidence.check(
        "sp03.acceptance_fifo",
        "Same-priority stimuli are consumed by admission order A, B, C.",
        actual=actual_order,
        expected=["A", "B", "C"],
        evidence="StimulusInbox",
        known_gap_key=gap,
    )
    evidence.check(
        "sp03.accepted_seq_atomic",
        "Admission assigns strictly increasing accepted_seq values.",
        actual=[
            getattr(item, "accepted_seq", None)
            for item in stimuli
        ],
        expected=[1, 2, 3],
        evidence="StimulusInbox",
        known_gap_key=gap,
    )
    evidence.check(
        "sp03.occurred_at_is_not_scheduler_key",
        "Reverse occurred_at values do not reverse equal-priority FIFO.",
        actual=actual_order,
        expected=["A", "B", "C"],
        evidence="_QueuedItem",
        known_gap_key=gap,
    )
    return evidence.build()


def _run_sp_04_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap: str,
) -> ScenarioExecution:
    thread_id = "acceptance-sp-04"
    conversation_id = _create_conversation(
        harness,
        thread_id=thread_id,
    )
    stimuli = [
        harness.make_stimulus(
            conversation_id=conversation_id,
            kind=StimulusKind.OBSERVATION_TRIGGER.value,
            text=f"serial-{index}",
            stimulus_id=f"sp-04-{index}",
        )
        for index in (1, 2)
    ]
    _submit_envelopes(harness, stimuli)

    entered = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()
    state = {"active": 0, "max_active": 0}
    processed: List[str] = []

    def drain_one(active_thread: str, **_kwargs: object) -> Dict[str, object]:
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(
                state["max_active"],
                state["active"],
            )
        entered.set()
        if not release.wait(timeout=3):
            raise RuntimeError("SP-04 deterministic release timed out")
        item = harness.inbox.pop_next(active_thread)
        if item is not None:
            processed.append(item.stimulus_id)
        with state_lock:
            state["active"] -= 1
        return {"success": True}

    drainer = ThreadDrainerService(
        drain_fn=drain_one,
        get_pending=lambda active_thread: harness.inbox.pending_count(
            active_thread
        ),
        build_emitter=lambda _active_thread: None,
        get_history=lambda _active_thread: None,
    )
    first_started = drainer.ensure_running(thread_id)
    entered.wait(timeout=3)
    second_started = drainer.ensure_running(thread_id)
    release.set()
    deadline = time.monotonic() + 3
    while (
        drainer.active_drainer_count()
        and time.monotonic() < deadline
    ):
        time.sleep(0.005)
    evidence.fact("serial_processed_ids", processed)
    evidence.check(
        "sp04.single_drainer",
        "Only one production drainer owns a thread at a time.",
        actual={
            "first_started": first_started,
            "second_started": second_started,
            "max_active": state["max_active"],
        },
        expected={
            "first_started": True,
            "second_started": False,
            "max_active": 1,
        },
        evidence="ThreadDrainerService",
    )
    evidence.check(
        "sp04_each_stimulus_delivered_once",
        "The serial drainer removes both fixtures once.",
        actual={
            "processed": sorted(processed),
            "remaining": harness.inbox.pending_count(thread_id),
        },
        expected={
            "processed": sorted(item.stimulus_id for item in stimuli),
            "remaining": 0,
        },
        evidence="ThreadDrainerService/StimulusInbox",
    )

    feedback_transaction = harness.create_runtime_transaction(
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    harness.registry.begin_delegate(
        feedback_transaction.transaction_id,
        "sp-04-delegate",
    )
    feedback_transaction = (
        harness.registry.get(feedback_transaction.transaction_id)
        or feedback_transaction
    )
    feedback_id = harness.gateway.submit_execution_feedback(
        thread_id=thread_id,
        conversation_id=conversation_id,
        transaction_id=feedback_transaction.transaction_id,
        delegate_id="sp-04-delegate",
        activation_id=str(
            feedback_transaction.current_activation_id or ""
        ),
        tool_history=[],
        summary="fake tool completed",
        schedule_drainer=False,
    )
    feedback = harness.inbox.pop_next(thread_id)
    evidence.check(
        "sp04_feedback_uses_inbox",
        "A produced Feedback re-enters the same production Inbox.",
        actual={
            "stimulus_id": (
                feedback.stimulus_id
                if feedback is not None
                else None
            ),
            "kind": (
                feedback.kind.value
                if feedback is not None
                else None
            ),
        },
        expected={
            "stimulus_id": feedback_id,
            "kind": StimulusKind.EXECUTION_FEEDBACK.value,
        },
        evidence="PerceptionGateway/StimulusInbox",
    )
    effect_sink = harness.control_effect_sink(action="install")
    evidence.check(
        "sp04_async_effect_sink_available",
        "Thinking can commit a delegate and release before the fake effect completes.",
        actual=effect_sink.supported,
        expected=True,
        evidence="SharedRuntimeHarness.control_effect_sink",
        known_gap_key=gap,
    )
    latch = harness.set_worker_latch(
        conversation_id=conversation_id,
        phase="after_thinking_commit",
    )
    evidence.check(
        "sp04_thinking_effect_boundary_observable",
        "The Harness can hold an effect after Thinking commits and observe B completing.",
        actual=latch.supported,
        expected=True,
        evidence="SharedRuntimeHarness.set_worker_latch",
        known_gap_key=gap,
    )
    return evidence.build()


def _run_sp_05_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap: str,
) -> ScenarioExecution:
    thread_id = "acceptance-sp-05"
    conversation_id = _create_conversation(
        harness,
        thread_id=thread_id,
    )
    transaction = harness.create_runtime_transaction(
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    harness.registry.begin_delegate(
        transaction.transaction_id,
        "sp-05-valid-delegate",
    )
    transaction = harness.registry.get(transaction.transaction_id) or transaction
    activation_id = str(transaction.current_activation_id or "")
    stimuli = [
        harness.make_stimulus(
            conversation_id=conversation_id,
            kind=StimulusKind.USER_MESSAGE.value,
            text="user",
            payload={"producer": "user"},
            priority_override=10,
            stimulus_id="sp-05-user",
        ),
        harness.make_stimulus(
            conversation_id=conversation_id,
            kind=StimulusKind.EXECUTION_FEEDBACK.value,
            text="valid feedback",
            payload={"producer": "feedback-valid"},
            source={
                "transaction_id": transaction.transaction_id,
                "activation_id": activation_id,
                "delegate_id": "sp-05-valid-delegate",
            },
            priority_override=20,
            stimulus_id="sp-05-feedback-valid",
        ),
        harness.make_stimulus(
            conversation_id=conversation_id,
            kind=StimulusKind.SCHEDULED_PLAN.value,
            text="schedule",
            payload={"producer": "schedule"},
            source={"schedule_id": "sp-05-schedule"},
            priority_override=30,
            stimulus_id="sp-05-schedule",
        ),
        harness.make_stimulus(
            conversation_id=conversation_id,
            kind=StimulusKind.OBSERVATION_TRIGGER.value,
            text="observation",
            payload={"producer": "observation"},
            priority_override=40,
            stimulus_id="sp-05-observation",
        ),
        harness.make_stimulus(
            conversation_id=conversation_id,
            kind=StimulusKind.EXECUTION_FEEDBACK.value,
            text="invalid feedback",
            payload={"producer": "feedback-invalid"},
            source={
                "transaction_id": "missing-transaction",
                "activation_id": "missing-activation",
                "delegate_id": "missing-delegate",
            },
            priority_override=20,
            stimulus_id="sp-05-feedback-invalid",
        ),
    ]
    barrier = threading.Barrier(len(stimuli) + 1)
    result_lock = threading.Lock()
    submitted: List[str] = []
    errors: List[str] = []

    def producer(stimulus: StimulusEnvelope) -> None:
        try:
            barrier.wait(timeout=3)
            stimulus_id = harness.gateway.submit(
                stimulus,
                schedule_drainer=False,
            )
            with result_lock:
                submitted.append(stimulus_id)
        except Exception as exc:
            with result_lock:
                errors.append(str(exc))

    workers = [
        threading.Thread(
            target=producer,
            args=(stimulus,),
            name=f"sp-05-producer-{index}",
            daemon=True,
        )
        for index, stimulus in enumerate(stimuli, start=1)
    ]
    for worker in workers:
        worker.start()
    barrier.wait(timeout=3)
    for worker in workers:
        worker.join(timeout=3)
    alive = [worker.name for worker in workers if worker.is_alive()]
    popped = _pop_all(harness, thread_id=thread_id)
    popped_priorities = [
        harness.attributor.priority_for(item)
        for item in popped
    ]
    admissible_stimuli = [
        item
        for item in stimuli
        if item.stimulus_id != "sp-05-feedback-invalid"
    ]
    evidence.fact("producer_completion_ids", submitted)
    evidence.fact(
        "consumed_ids",
        [item.stimulus_id for item in popped],
    )
    evidence.check(
        "sp05_producers_finish",
        "All barrier-released producer threads finish without infrastructure errors.",
        actual={"alive": alive, "errors": errors},
        expected={"alive": [], "errors": []},
        evidence="threading.Barrier/PerceptionGateway",
    )
    evidence.check(
        "sp05_raw_inbox_no_loss",
        "The thread-safe production pool retains every admitted envelope exactly once.",
        actual={
            "submitted_ids": sorted(submitted),
            "popped_ids": sorted(
                item.stimulus_id
                for item in popped
            ),
            "popped_count": len(popped),
        },
        expected={
            "submitted_ids": sorted(
                item.stimulus_id
                for item in stimuli
            ),
            "popped_ids": sorted(
                item.stimulus_id
                for item in admissible_stimuli
            ),
            "popped_count": len(admissible_stimuli),
        },
        evidence="PerceptionGateway/StimulusInbox",
    )
    evidence.check(
        "sp05_raw_priority_order",
        "Concurrent raw pushes still leave the heap ordered by numeric priority.",
        actual=popped_priorities,
        expected=sorted(popped_priorities),
        evidence="StimulusInbox",
    )
    valid_feedback = next(
        item
        for item in popped
        if item.stimulus_id == "sp-05-feedback-valid"
    )
    evidence.check(
        "sp05_source_fields_not_overwritten",
        "Concurrent producers do not overwrite transaction/delegate fields.",
        actual={
            "transaction_id": valid_feedback.transaction_id,
            "delegate_id": valid_feedback.delegate_id,
        },
        expected={
            "transaction_id": transaction.transaction_id,
            "delegate_id": "sp-05-valid-delegate",
        },
        evidence="StimulusEnvelope",
    )
    evidence.check(
        "sp05_feedback_activation_preserved",
        "Concurrent valid Feedback retains its activation identity.",
        actual=getattr(valid_feedback, "activation_id", None),
        expected=activation_id,
        evidence="StimulusEnvelope",
        known_gap_key=gap,
    )
    evidence.check(
        "sp05_invalid_feedback_not_admitted",
        "Concurrent invalid Feedback receives no queue position.",
        actual=any(
            item.stimulus_id == "sp-05-feedback-invalid"
            for item in popped
        ),
        expected=False,
        evidence="PerceptionGateway",
        known_gap_key=gap,
    )
    evidence.check(
        "sp05_atomic_accepted_seq",
        "Successful concurrent admission assigns one unique accepted_seq per item.",
        actual=[
            getattr(item, "accepted_seq", None)
            for item in popped
        ],
        expected=[1, 2, 3, 4],
        evidence="StimulusInbox",
        known_gap_key=gap,
        predicate=lambda values: (
            len(values) == 4
            and len(set(values)) == 4
            and all(value is not None for value in values)
        ),
    )
    return evidence.build()


def _run_sp_06_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap: str,
) -> ScenarioExecution:
    thread_id = "acceptance-sp-06"
    conversation_id = _create_conversation(
        harness,
        thread_id=thread_id,
    )
    initial = harness.make_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="initial",
        stimulus_id="sp-06-initial",
    )
    harness.gateway.submit(initial, schedule_drainer=False)

    calls = {"get_pending": 0, "drain": 0}
    calls_lock = threading.Lock()
    exit_window = threading.Event()
    release_exit = threading.Event()
    drained_ids: List[str] = []

    def get_pending(active_thread: str) -> int:
        with calls_lock:
            calls["get_pending"] += 1
            call_number = calls["get_pending"]
            captured = harness.inbox.pending_count(active_thread)
        if call_number == 3:
            exit_window.set()
            if not release_exit.wait(timeout=3):
                raise RuntimeError("SP-06 exit-window release timed out")
        return captured

    def drain_one(active_thread: str, **_kwargs: object) -> Dict[str, object]:
        item = harness.inbox.pop_next(active_thread)
        with calls_lock:
            calls["drain"] += 1
        if item is not None:
            drained_ids.append(item.stimulus_id)
        return {"success": True}

    drainer = ThreadDrainerService(
        drain_fn=drain_one,
        get_pending=get_pending,
        build_emitter=lambda _active_thread: None,
        get_history=lambda _active_thread: None,
    )
    first_started = drainer.ensure_running(thread_id)
    window_observed = exit_window.wait(timeout=3)

    feedback_transaction = harness.create_runtime_transaction(
        conversation_id=conversation_id,
        status=TransactionStatus.RUNNING,
    )
    harness.registry.begin_delegate(
        feedback_transaction.transaction_id,
        "sp-06-delegate",
    )
    feedback_transaction = (
        harness.registry.get(feedback_transaction.transaction_id)
        or feedback_transaction
    )
    feedback_id = harness.gateway.submit_execution_feedback(
        thread_id=thread_id,
        conversation_id=conversation_id,
        transaction_id=feedback_transaction.transaction_id,
        delegate_id="sp-06-delegate",
        activation_id=str(
            feedback_transaction.current_activation_id or ""
        ),
        tool_history=[],
        summary="async completion",
        schedule_drainer=False,
    )
    replacement_started = drainer.ensure_running(thread_id)
    release_exit.set()
    deadline = time.monotonic() + 3
    while (
        drainer.active_drainer_count()
        and time.monotonic() < deadline
    ):
        time.sleep(0.005)
    remaining = harness.inbox.pending_count(thread_id)
    evidence.fact("drained_ids", drained_ids)
    evidence.fact("late_feedback_id", feedback_id)
    evidence.check(
        "sp06_deterministic_exit_window_reached",
        "The production worker is held after its final empty check.",
        actual={
            "first_started": first_started,
            "window_observed": window_observed,
        },
        expected={
            "first_started": True,
            "window_observed": True,
        },
        evidence="ThreadDrainerService",
    )
    evidence.check(
        "sp06_single_worker_guard",
        "A second worker cannot start while the exiting worker remains registered.",
        actual=replacement_started,
        expected=False,
        evidence="ThreadDrainerService.ensure_running",
    )
    evidence.check(
        "sp06_initial_consumed_once",
        "The initial item was consumed once before the exit window.",
        actual=drained_ids.count(initial.stimulus_id),
        expected=1,
        evidence="ThreadDrainerService/StimulusInbox",
    )
    evidence.check(
        "sp06_late_feedback_wakes_consumer",
        "Feedback arriving in the exit window is eventually consumed exactly once.",
        actual={
            "remaining": remaining,
            "feedback_consumptions": drained_ids.count(feedback_id),
            "drained_after_initial": drained_ids[1:] == [feedback_id],
        },
        expected={
            "remaining": 0,
            "feedback_consumptions": 1,
            "drained_after_initial": True,
        },
        evidence="ThreadDrainerService",
        known_gap_key=gap,
    )
    return evidence.build()


def _run_sp_07_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap: str,
) -> ScenarioExecution:
    thread_id = "acceptance-sp-07"
    conversation_id = _create_conversation(
        harness,
        thread_id=thread_id,
    )
    current = harness.make_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="current",
        priority_override=50,
        stimulus_id="sp-07-current",
    )
    harness.gateway.submit(current, schedule_drainer=False)
    selected_current = harness.inbox.pop_next(thread_id)
    middle = harness.make_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.SCHEDULED_PLAN.value,
        text="middle",
        priority_override=30,
        stimulus_id="sp-07-middle",
    )
    high = harness.make_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.USER_MESSAGE.value,
        text="high",
        priority_override=5,
        stimulus_id="sp-07-high",
    )
    _submit_envelopes(harness, [middle, high])
    selected_next = harness.inbox.pop_next(thread_id)
    evidence.fact(
        "selection_snapshot",
        {
            "current_stimulus_id": (
                selected_current.stimulus_id
                if selected_current is not None
                else None
            ),
            "next_stimulus_id": (
                selected_next.stimulus_id
                if selected_next is not None
                else None
            ),
            "expected_high_priority_id": high.stimulus_id,
        },
    )
    evidence.check(
        "sp07_current_selection_stays_stable",
        "A newly arrived item does not replace the already selected stimulus.",
        actual=(
            selected_current.stimulus_id
            if selected_current is not None
            else None
        ),
        expected=current.stimulus_id,
        evidence="StimulusInbox",
    )
    evidence.check(
        "sp07_next_selection_uses_priority",
        "The next selection chooses the newly arrived higher-priority item.",
        actual=(
            selected_next.stimulus_id
            if selected_next is not None
            else None
        ),
        expected=high.stimulus_id,
        evidence="StimulusInbox",
    )

    retry_thread = "acceptance-sp-07-retry"
    retry_conversation = _create_conversation(
        harness,
        thread_id=retry_thread,
    )
    original_transaction = harness.create_runtime_transaction(
        conversation_id=retry_conversation,
        status=TransactionStatus.RUNNING,
    )
    retry = harness.make_stimulus(
        conversation_id=retry_conversation,
        kind=StimulusKind.USER_MESSAGE.value,
        text="resume assigned work",
        source={"transaction_id": original_transaction.transaction_id},
        priority_override=25,
        stimulus_id="sp-07-retry",
    )
    harness.gateway.submit(retry, schedule_drainer=False)
    reselected = harness.inbox.pop_next(retry_thread)
    evidence.check(
        "sp07_requeue_preserves_envelope_identity",
        "The raw requeue path preserves stimulus and transaction IDs.",
        actual={
            "stimulus_id": (
                reselected.stimulus_id
                if reselected is not None
                else None
            ),
            "transaction_id": (
                reselected.transaction_id
                if reselected is not None
                else None
            ),
            "priority_override": (
                reselected.priority_override
                if reselected is not None
                else None
            ),
        },
        expected={
            "stimulus_id": retry.stimulus_id,
            "transaction_id": original_transaction.transaction_id,
            "priority_override": 25,
        },
        evidence="StimulusInbox",
    )
    if reselected is None:
        raise RuntimeAdapterError("SP-07 retry stimulus disappeared")
    resolved, created = harness.resolve(reselected)
    evidence.check(
        "sp07_requeue_skips_reattribution",
        "An already attributed requeue retains the original transaction.",
        actual={
            "transaction_id": resolved.transaction_id,
            "created": created,
        },
        expected={
            "transaction_id": original_transaction.transaction_id,
            "created": False,
        },
        evidence="TransactionAttributor.resolve",
        known_gap_key=gap,
    )
    evidence.check(
        "sp07_requeue_preserves_accepted_seq",
        "A recoverable requeue retains its original accepted_seq.",
        actual=getattr(reselected, "accepted_seq", None),
        expected=1,
        evidence="StimulusInbox",
        known_gap_key=gap,
    )
    pause = harness.control_transaction(
        transaction_id=original_transaction.transaction_id,
        action="pause",
    )
    evidence.check(
        "sp07_ui_pause_aborts_without_requeue",
        "UI Pause can atomically abort the current stimulus without requeue.",
        actual=pause.supported,
        expected=True,
        evidence="SharedRuntimeHarness.control_transaction",
        known_gap_key=gap,
    )
    effect = harness.control_effect_sink(action="install")
    evidence.check(
        "sp07_async_effect_does_not_block_next_selection",
        "An unfinished asynchronous effect does not occupy the stimulus consumer.",
        actual=effect.supported,
        expected=True,
        evidence="SharedRuntimeHarness.control_effect_sink",
        known_gap_key=gap,
    )
    return evidence.build()


def _run_sp_08_core(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap: str,
) -> ScenarioExecution:
    thread_id = "acceptance-sp-08-shared-thread"
    conversation_a = _create_conversation(
        harness,
        thread_id=thread_id,
    )
    conversation_b = _create_conversation(
        harness,
        thread_id=thread_id,
    )
    evidence.check(
        "sp08_distinct_conversations",
        "The Harness can create two conversation identities under one thread.",
        actual=conversation_a != conversation_b,
        expected=True,
        evidence="SharedRuntimeHarness.create_conversation",
    )
    stimulus_a = harness.make_stimulus(
        conversation_id=conversation_a,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="conversation A",
        priority_override=50,
        stimulus_id="sp-08-a",
    )
    stimulus_b = harness.make_stimulus(
        conversation_id=conversation_b,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="conversation B",
        priority_override=5,
        stimulus_id="sp-08-b",
    )
    _submit_envelopes(harness, [stimulus_a, stimulus_b])
    claimed_for_a = harness.claim_next_stimulus(
        conversation_id=conversation_a,
        consumer_id="consumer-a",
    )
    evidence.fact("claim_requested_for_a", claimed_for_a.to_dict())
    evidence.check(
        "sp08_claim_is_conversation_partitioned",
        "A consumer for conversation A cannot claim B's stimulus.",
        actual=claimed_for_a.data.get("actual_conversation_id"),
        expected=conversation_a,
        evidence="StimulusInbox",
        known_gap_key=gap,
    )

    transaction_a = harness.create_runtime_transaction(
        conversation_id=conversation_a,
        status=TransactionStatus.RUNNING,
    )
    transaction_b = harness.create_runtime_transaction(
        conversation_id=conversation_b,
        status=TransactionStatus.RUNNING,
    )
    evidence.check(
        "sp08_transaction_queries_are_isolated",
        "TransactionRegistry conversation queries do not cross partitions.",
        actual={
            "a": [
                item.transaction_id
                for item in harness.registry.list_for_conversation(
                    conversation_a
                )
            ],
            "b": [
                item.transaction_id
                for item in harness.registry.list_for_conversation(
                    conversation_b
                )
            ],
        },
        expected={
            "a": [transaction_a.transaction_id],
            "b": [transaction_b.transaction_id],
        },
        evidence="TransactionRegistry",
    )
    harness.append_scene(
        conversation_a,
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:00Z",
            entry_type=SceneEntryType.OUTCOME,
            actor=SceneActor.THINK,
            text="A only",
        ),
    )
    harness.append_scene(
        conversation_b,
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:01Z",
            entry_type=SceneEntryType.OUTCOME,
            actor=SceneActor.THINK,
            text="B only",
        ),
    )
    evidence.check(
        "sp08_scene_queries_are_isolated",
        "Scene entries remain scoped to their conversation.",
        actual={
            "a": [
                item.text
                for item in harness.scene_store.tail(conversation_a)
            ],
            "b": [
                item.text
                for item in harness.scene_store.tail(conversation_b)
            ],
        },
        expected={"a": ["A only"], "b": ["B only"]},
        evidence="SceneLogStore",
    )

    harness.registry.begin_delegate(
        transaction_a.transaction_id,
        "sp-08-delegate-a",
    )
    cross_feedback = harness.make_stimulus(
        conversation_id=conversation_b,
        kind=StimulusKind.EXECUTION_FEEDBACK.value,
        text="cross-conversation result",
        source={
            "transaction_id": transaction_a.transaction_id,
            "activation_id": "sp-08-activation-a",
            "delegate_id": "sp-08-delegate-a",
        },
        stimulus_id="sp-08-cross-feedback",
    )
    cross_rejected = False
    try:
        harness.resolve(cross_feedback)
    except ValueError:
        cross_rejected = True
    evidence.check(
        "sp08_cross_conversation_feedback_rejected",
        "Feedback cannot cross a conversation boundary even with valid causal IDs.",
        actual=cross_rejected,
        expected=True,
        evidence="TransactionAttributor._resolve_feedback",
        known_gap_key=gap,
    )
    partition_a = harness.load_partition_state(
        conversation_id=conversation_a
    )
    partition_b = harness.load_partition_state(
        conversation_id=conversation_b
    )
    evidence.check(
        "sp08_independent_consumer_ownership",
        "Each conversation exposes independent consumer lease and epoch state.",
        actual={
            "a_supported": partition_a.supported,
            "b_supported": partition_b.supported,
        },
        expected={
            "a_supported": True,
            "b_supported": True,
        },
        evidence="StimulusInboxStore",
        known_gap_key=gap,
    )
    effect = harness.control_effect_sink(action="install")
    evidence.check(
        "sp08_async_effect_does_not_block_other_conversation",
        "An unfinished effect in A does not block B's Thinking consumer.",
        actual=effect.supported,
        expected=True,
        evidence="SharedRuntimeHarness.control_effect_sink",
        known_gap_key=gap,
    )
    return evidence.build()


def _run_sp_01_durable_ingress_restart(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap: str,
) -> ScenarioExecution:
    thread_id = "acceptance-sp-01-restart"
    conversation_id = _create_conversation(
        harness,
        thread_id=thread_id,
    )
    first = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="ready before restart",
        ingress_key="restart:ready",
    )
    second = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="claimed before restart",
        ingress_key="restart:claimed",
    )
    claimed = harness.claim_next_stimulus(
        conversation_id=conversation_id,
        consumer_id="consumer-before-restart",
    )
    evidence.check(
        "sp01_restart_claim_preserves_stimulus_id_in_process",
        "A destructive pop still reports the original stimulus ID.",
        actual=claimed.data.get("stimulus_id"),
        expected=first.data.get("stimulus_id"),
        evidence="StimulusInbox",
    )
    evidence.fact(
        "pending_before_restart",
        harness.inbox.pending_count(thread_id),
    )
    restart = harness.restart_runtime()
    evidence.check(
        "sp01_restart_operation_supported",
        "The Harness can reopen the same durable Runtime state.",
        actual=restart.supported,
        expected=True,
        evidence="SharedRuntimeHarness.restart_runtime",
        known_gap_key=gap,
    )

    restarted = type(harness)()
    restarted_conversation = _create_conversation(
        restarted,
        thread_id=thread_id,
    )
    evidence.check(
        "sp01_restart_preserves_ready_and_claimed_records",
        "Ready and unfinished claimed records survive process reconstruction.",
        actual={
            "conversation_id_same": (
                restarted_conversation == conversation_id
            ),
            "pending_after_restart": restarted.inbox.pending_count(
                thread_id
            ),
            "expected_ready_id": second.data.get("stimulus_id"),
            "claimed_recoverable": restarted.load_stimulus(
                stimulus_id=str(
                    claimed.data.get("stimulus_id", "")
                )
            ).supported,
        },
        expected={
            "conversation_id_same": True,
            "pending_after_restart": 1,
            "expected_ready_id": second.data.get("stimulus_id"),
            "claimed_recoverable": True,
        },
        evidence="StimulusInboxStore",
        known_gap_key=gap,
    )
    evidence.check(
        "sp01_restart_preserves_accepted_seq",
        "accepted_seq is durable across restart.",
        actual=[
            first.data.get("accepted_seq"),
            second.data.get("accepted_seq"),
        ],
        expected=[1, 2],
        evidence="StimulusInboxStore",
        known_gap_key=gap,
    )
    disposition = harness.wait_for_disposition(
        stimulus_id=str(claimed.data.get("stimulus_id", "")),
        timeout_seconds=0,
    )
    evidence.check(
        "sp01_restart_recovers_final_disposition",
        "A claimed item can recover or expose its final disposition after restart.",
        actual=disposition.supported,
        expected=True,
        evidence="StimulusInboxStore",
        known_gap_key=gap,
    )
    restarted.close()
    return evidence.build()


def _run_sp_01_lease_takeover_fencing(
    harness: SharedRuntimeHarness,
    evidence: EvidenceBuilder,
    gap: str,
) -> ScenarioExecution:
    thread_id = "acceptance-sp-01-lease"
    conversation_id = _create_conversation(
        harness,
        thread_id=thread_id,
    )
    committed = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="commit before takeover",
        ingress_key="lease:committed",
    )
    unfinished = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind=StimulusKind.OBSERVATION_TRIGGER.value,
        text="reclaim after takeover",
        ingress_key="lease:unfinished",
    )
    acquired = harness.acquire_consumer(
        conversation_id=conversation_id,
        consumer_id="consumer-old",
        lease_seconds=1,
    )
    first_claim = harness.claim_next_stimulus(
        conversation_id=conversation_id,
        consumer_id="consumer-old",
    )
    claim_token = {
        "claimed_by": first_claim.data.get("consumer_id"),
        "consumer_epoch": first_claim.data.get("consumer_epoch"),
        "claim_epoch": first_claim.data.get("claim_epoch"),
    }
    evidence.check(
        "sp01_lease_claim_keeps_stimulus_id",
        "The current destructive claim returns the submitted stimulus ID.",
        actual=first_claim.data.get("stimulus_id"),
        expected=committed.data.get("stimulus_id"),
        evidence="StimulusInbox",
    )
    evidence.check(
        "sp01_lease_owner_and_epoch_assigned",
        "Acquisition and claim return a durable owner plus fencing epochs.",
        actual={
            "acquire_supported": acquired.supported,
            "consumer_epoch": claim_token["consumer_epoch"],
            "claim_epoch": claim_token["claim_epoch"],
        },
        expected={
            "acquire_supported": True,
            "consumer_epoch": 1,
            "claim_epoch": 1,
        },
        evidence="StimulusInboxStore",
        known_gap_key=gap,
    )
    committed_finalize = harness.finalize_stimulus(
        stimulus_id=str(committed.data.get("stimulus_id", "")),
        claim_token=claim_token,
        disposition="consumed",
        transition_id="lease-transition-committed",
        command_digest="digest-committed",
    )
    evidence.check(
        "sp01_pre_takeover_commit_succeeds",
        "The old owner commits one transition before lease takeover.",
        actual={
            "outcome": committed_finalize.outcome,
            "replayed": committed_finalize.data.get("replayed"),
        },
        expected={"outcome": "ok", "replayed": False},
        evidence="RuntimeUnitOfWork",
        known_gap_key=gap,
    )
    second_claim = harness.claim_next_stimulus(
        conversation_id=conversation_id,
        consumer_id="consumer-old",
    )
    unfinished_token = {
        "claimed_by": second_claim.data.get("consumer_id"),
        "consumer_epoch": second_claim.data.get("consumer_epoch"),
        "claim_epoch": second_claim.data.get("claim_epoch"),
    }
    takeover = harness.takeover_consumer(
        conversation_id=conversation_id,
        consumer_id="consumer-new",
    )
    evidence.fact(
        "lease_claim_snapshot",
        {
            "conversation_id": conversation_id,
            "committed_stimulus_id": committed.data.get("stimulus_id"),
            "unfinished_stimulus_id": unfinished.data.get(
                "stimulus_id"
            ),
            "old_consumer": claim_token["claimed_by"],
            "consumer_epoch": claim_token["consumer_epoch"],
            "claim_epoch": unfinished_token["claim_epoch"],
            "takeover": takeover.to_dict(),
        },
    )
    evidence.check(
        "sp01_lease_takeover_advances_epoch",
        "A new owner takes over with a strictly higher consumer epoch.",
        actual={
            "supported": takeover.supported,
            "consumer_epoch": takeover.data.get("consumer_epoch"),
        },
        expected={"supported": True, "consumer_epoch": 2},
        evidence="StimulusInboxStore",
        known_gap_key=gap,
    )

    transactions_before = harness.registry.count_all()
    scene_before = len(harness.scene_store.tail(conversation_id))
    stale_commit = harness.finalize_stimulus(
        stimulus_id=str(unfinished.data.get("stimulus_id", "")),
        claim_token=unfinished_token,
        disposition="consumed",
        transition_id="lease-transition-new",
        command_digest="digest-new",
    )
    evidence.check(
        "sp01_stale_claim_is_fenced",
        "The old token receives StaleClaim rather than committing a new transition.",
        actual=stale_commit.outcome,
        expected="stale_claim",
        evidence="RuntimeUnitOfWork",
        known_gap_key=gap,
    )
    evidence.check(
        "sp01_unsupported_commit_has_no_side_effect",
        "A fenced stale finalize changes no transaction or Scene.",
        actual={
            "transaction_count": harness.registry.count_all(),
            "scene_count": len(
                harness.scene_store.tail(conversation_id)
            ),
        },
        expected={
            "transaction_count": transactions_before,
            "scene_count": scene_before,
        },
        evidence="TransactionRegistry/SceneLogStore",
    )
    replay = harness.finalize_stimulus(
        stimulus_id=str(committed.data.get("stimulus_id", "")),
        claim_token=claim_token,
        disposition="consumed",
        transition_id="lease-transition-committed",
        command_digest="digest-committed",
    )
    evidence.check(
        "sp01_committed_transition_replays_before_fencing",
        "A previously committed ID/digest replays its first result after takeover.",
        actual={
            "outcome": replay.outcome,
            "replayed": replay.data.get("replayed"),
        },
        expected={"outcome": "ok", "replayed": True},
        evidence="RuntimeUnitOfWork",
        known_gap_key=gap,
    )
    reclaimed = harness.claim_next_stimulus(
        conversation_id=conversation_id,
        consumer_id="consumer-new",
    )
    evidence.check(
        "sp01_takeover_reclaims_same_stimulus",
        "The new claimant receives the same unfinished stimulus with a higher claim epoch.",
        actual={
            "outcome": reclaimed.outcome,
            "stimulus_id": reclaimed.data.get("stimulus_id"),
            "claim_epoch": reclaimed.data.get("claim_epoch"),
        },
        expected={
            "outcome": "ok",
            "stimulus_id": unfinished.data.get("stimulus_id"),
            "claim_epoch": 3,
        },
        evidence="StimulusInboxStore",
        known_gap_key=gap,
    )
    return evidence.build()


_HANDLERS: Dict[
    tuple[str, str],
    Callable[
        [SharedRuntimeHarness, EvidenceBuilder, str],
        ScenarioExecution,
    ],
] = {
    ("SP-01", "sp-01/core"): _run_sp_01_core,
    ("SP-02", "sp-02/core"): _run_sp_02_core,
    ("SP-03", "sp-03/core"): _run_sp_03_core,
    ("SP-04", "sp-04/core"): _run_sp_04_core,
    ("SP-05", "sp-05/core"): _run_sp_05_core,
    ("SP-06", "sp-06/core"): _run_sp_06_core,
    ("SP-07", "sp-07/core"): _run_sp_07_core,
    ("SP-08", "sp-08/core"): _run_sp_08_core,
    (
        "SP-01",
        "sp-01/durable_ingress_restart",
    ): _run_sp_01_durable_ingress_restart,
    (
        "SP-01",
        "sp-01/lease_takeover_fencing",
    ): _run_sp_01_lease_takeover_fencing,
}


def run_sp_scenario(
    harness: SharedRuntimeHarness,
    scenario_id: str,
    variant_id: str,
) -> ScenarioExecution:
    """Drive one SP variant through shared runtime components."""

    scenario = str(scenario_id or "").strip().upper()
    variant = str(variant_id or "").strip().lower()
    key = (scenario, variant)
    if key not in _SP_VARIANTS:
        raise RuntimeAdapterError(
            f"{harness.runtime_id} does not implement {scenario}/{variant}"
        )
    evidence = EvidenceBuilder(
        scenario_id=scenario,
        variant_id=str(variant_id),
        runtime_id=harness.runtime_id,
    )
    return _HANDLERS[key](
        harness,
        evidence,
        registered_known_gap_key(str(variant_id)),
    )
