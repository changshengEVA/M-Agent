"""P6 LangGraph TX contract scenarios."""

from __future__ import annotations

from typing import Any, Callable, Dict

from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
)

from ..scenario_catalog import registered_known_gap_key
from .base import HarnessResult, RuntimeAdapterError, ScenarioExecution
from .evidence import EvidenceBuilder
from . import shared_tx
from .langgraph_harness import LangGraphV1Harness
from .transaction_fixtures import FixtureTransactionStatus as TransactionStatus


Handler = Callable[
    [LangGraphV1Harness, EvidenceBuilder, str],
    ScenarioExecution,
]


def _builder(scenario_id: str, variant_id: str) -> EvidenceBuilder:
    return EvidenceBuilder(
        scenario_id=scenario_id,
        variant_id=variant_id,
        runtime_id="langgraph_v1",
    )


def _new_conversation(
    harness: LangGraphV1Harness,
    evidence: EvidenceBuilder,
    *,
    suffix: str,
) -> str:
    thread_id = (
        f"lg-{evidence.scenario_id.lower()}-{suffix.replace('_', '-')}"
    )
    result = harness.create_conversation(thread_id=thread_id)
    evidence.fact(f"conversation.{suffix}", result.to_dict())
    evidence.check(
        f"{evidence.scenario_id.lower()}.fixture.{suffix}.conversation",
        "The Runtime creates an isolated conversation fixture.",
        actual=result.to_dict(),
        expected="supported outcome=ok with conversation_id",
        evidence="LangGraphV1Harness.create_conversation",
        predicate=lambda _actual: (
            result.supported
            and result.outcome == "ok"
            and bool(result.data.get("conversation_id"))
        ),
    )
    return str(result.data.get("conversation_id") or f"{thread_id}::0")


def _append_scene(
    harness: LangGraphV1Harness,
    *,
    conversation_id: str,
    transaction_id: str,
    text: str,
) -> None:
    harness.append_scene(
        conversation_id,
        SceneEntry(
            seq=0,
            occurred_at="2026-01-01T00:00:01Z",
            entry_type=SceneEntryType.ACTION,
            actor=SceneActor.WORK,
            text=text,
            transaction_id=transaction_id,
        ),
    )


def _transaction_data(
    harness: LangGraphV1Harness,
    transaction_id: str,
) -> Dict[str, Any]:
    loaded = harness.load_transaction(transaction_id=transaction_id)
    transaction = loaded.data.get("transaction")
    return dict(transaction) if isinstance(transaction, dict) else {}


def _run_tx_01_core(
    harness: LangGraphV1Harness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(harness, evidence, suffix="normal")
    seeded = harness.seed_attributed_transaction(
        conversation_id=conversation_id,
        wm_entries=[],
        goal="",
    )
    transaction_id = str(seeded.data.get("transaction_id", ""))
    run = harness.run_graph_script(
        conversation_id=conversation_id,
        transaction_id=transaction_id,
        script=[
            {
                "action": "record_progress",
                "transition_id": "lg-tx-01-normal-processing",
                "wm_entries": [{"fact": "normal-processing-committed"}],
                "goal": "process the TX-01 request",
            }
        ],
    )
    evidence.fact("graph_run", run.to_dict())
    _append_scene(
        harness,
        conversation_id=conversation_id,
        transaction_id=transaction_id,
        text="TX-01 processing evidence",
    )
    snapshot = _transaction_data(harness, transaction_id)
    scene = harness.read_scene(conversation_id=conversation_id)
    evidence.fact("transaction", snapshot)
    evidence.fact("scene", scene.to_dict())
    evidence.check(
        "tx_01.transaction.target_state",
        "A normally running transaction uses target state continue.",
        actual=snapshot.get("state"),
        expected="continue",
        evidence="LangGraphV1Harness.run_graph_script",
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
        evidence="LangGraphV1Harness.load_transaction",
        known_gap_key=gap_key,
        predicate=lambda value: (
            bool(value.get("activation_id"))
            and isinstance(value.get("revision"), int)
        ),
    )
    evidence.check(
        "tx_01.state.normal_processing_writes",
        "The graph command writes WM and TaskState through the Transaction Store.",
        actual={
            "wm_entries": snapshot.get("wm_entries"),
            "task_state": snapshot.get("task_state"),
        },
        expected="non-empty WM and TaskState goal",
        evidence="LangGraphV1Harness.load_transaction",
        known_gap_key=gap_key,
        predicate=lambda value: (
            bool(value.get("wm_entries"))
            and bool((value.get("task_state") or {}).get("goal"))
        ),
    )
    evidence.check(
        "tx_01.graph.checkpoint_separate_from_store",
        "GraphState snapshots do not replace Transaction Store authority.",
        actual={
            "graph_phase": run.data.get("graph_state", {}).get("graph_phase"),
            "store_revision": snapshot.get("revision"),
            "graph_revision": run.data.get("graph_state", {}).get(
                "transaction_revision"
            ),
        },
        expected="store revision matches committed graph revision",
        evidence="TransactionGraphEngine",
        predicate=lambda value: (
            value.get("graph_phase") == "step_committed"
            and value.get("store_revision")
            == value.get("graph_revision")
        ),
    )
    return evidence.build()


def _run_tx_01_uow(
    harness: LangGraphV1Harness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(harness, evidence, suffix="uow")
    seeded = harness.seed_attributed_transaction(
        conversation_id=conversation_id,
    )
    transaction_id = str(seeded.data.get("transaction_id", ""))
    fault = harness.inject_fault(
        fault_point="after_uow_commit_before_checkpoint",
    )
    evidence.check(
        "tx_01_uow.fault.checkpoint_window",
        "The Harness can arm the UoW-committed/checkpoint-not-advanced window.",
        actual=fault.to_dict(),
        expected="supported armed checkpoint fault",
        evidence="LangGraphV1Harness.inject_fault",
        known_gap_key=gap_key,
        predicate=lambda _value: (
            fault.supported and fault.data.get("armed") is True
        ),
    )
    transition_id = "lg-tx-01-uow-transition"
    first = harness.finalize_stimulus(
        stimulus_id="lg-tx-01-uow-stimulus",
        claim_token={
            "claimed_by": "lg-consumer",
            "consumer_epoch": 1,
            "claim_epoch": 1,
        },
        disposition="consumed",
        transition_id=transition_id,
        command_digest="digest-a",
    )
    replay = harness.finalize_stimulus(
        stimulus_id="lg-tx-01-uow-stimulus",
        claim_token={
            "claimed_by": "lg-consumer",
            "consumer_epoch": 1,
            "claim_epoch": 1,
        },
        disposition="consumed",
        transition_id=transition_id,
        command_digest="digest-a",
    )
    conflict = harness.finalize_stimulus(
        stimulus_id="lg-tx-01-uow-stimulus",
        claim_token={
            "claimed_by": "lg-consumer",
            "consumer_epoch": 1,
            "claim_epoch": 1,
        },
        disposition="consumed",
        transition_id=transition_id,
        command_digest="digest-b",
    )
    graph_run = harness.run_graph_script(
        conversation_id=conversation_id,
        transaction_id=transaction_id,
        script=[
            {
                "action": "record_progress",
                "transition_id": transition_id,
                "wm_entries": [{"fact": "uow-replay"}],
                "goal": "uow replay slice",
            }
        ],
    )
    evidence.fact("uow.first", first.to_dict())
    evidence.fact("uow.replay", replay.to_dict())
    evidence.fact("uow.conflict", conflict.to_dict())
    evidence.fact("graph_run", graph_run.to_dict())
    evidence.check(
        "tx_01_uow.replay.complete_result",
        "The same transition ID and digest returns the first complete committed result.",
        actual={"first": first.to_dict(), "replay": replay.to_dict()},
        expected="two supported identical committed results",
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
    evidence.check(
        "tx_01_uow.graph.no_duplicate_domain_ids",
        "Replay does not create a second activation, delegate or effect intent.",
        actual={
            "activation_id": _transaction_data(
                harness,
                transaction_id,
            ).get("current_activation_id"),
            "effects": harness.load_effects(
                transaction_id=transaction_id,
            ).data.get("effects", []),
            "checkpoint_saved": graph_run.data.get("checkpoint_saved"),
        },
        expected="single activation and zero effects for this slice",
        evidence="TransactionGraphEngine",
        predicate=lambda value: (
            bool(value.get("activation_id"))
            and value.get("effects") == []
        ),
    )
    return evidence.build()


def _run_tx_01_poc_checkpoint_resume(
    harness: LangGraphV1Harness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(harness, evidence, suffix="resume")
    seeded = harness.seed_attributed_transaction(
        conversation_id=conversation_id,
        wm_entries=[{"fact": "poc-resume-owned"}],
        goal="resume after pause",
    )
    transaction_id = str(seeded.data.get("transaction_id", ""))
    old_activation = str(seeded.data.get("activation_id", ""))
    before_pause = harness.run_graph_script(
        conversation_id=conversation_id,
        transaction_id=transaction_id,
        script=[{"action": "pause", "reason": "manual_hold"}],
    )
    paused = _transaction_data(harness, transaction_id)
    restored = harness.run_graph_script(
        conversation_id=conversation_id,
        transaction_id=transaction_id,
        script=[{"action": "restore", "source": "ui"}],
    )
    after = _transaction_data(harness, transaction_id)
    evidence.fact("before_pause", before_pause.to_dict())
    evidence.fact("restored", restored.to_dict())
    evidence.check(
        "poc_resume.pause_then_continue",
        "A locked transaction can pause and later continue via a new activation.",
        actual={
            "paused_state": paused.get("state"),
            "restored_state": after.get("state"),
            "activation_before": old_activation,
            "activation_after": after.get("current_activation_id"),
        },
        expected="pause then continue with a different activation",
        evidence="TransactionGraphEngine",
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
    harness: LangGraphV1Harness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(harness, evidence, suffix="effects")
    seeded = harness.seed_attributed_transaction(
        conversation_id=conversation_id,
        goal="sequential fake effects",
    )
    transaction_id = str(seeded.data.get("transaction_id", ""))
    thread_id = harness._thread_by_conversation.get(
        conversation_id,
        conversation_id.split("::", 1)[0],
    )
    first_delegate = harness.run_graph_script(
        conversation_id=conversation_id,
        transaction_id=transaction_id,
        script=[
            {
                "action": "delegate",
                "delegate_id": "lg-capability-delegate",
                "capability": "fake_capability",
                "effect_id": "lg-effect-capability",
                "idempotency_key": "lg-cap-key-1",
                "thread_id": thread_id,
            }
        ],
    )
    first_activation = str(
        first_delegate.data.get("graph_state", {}).get("activation_id", "")
    )
    harness.registry.consume_feedback(
        transaction_id,
        first_activation,
        "lg-capability-delegate",
    )
    between = _transaction_data(harness, transaction_id)
    second_delegate = harness.run_graph_script(
        conversation_id=conversation_id,
        transaction_id=transaction_id,
        script=[
            {
                "action": "delegate",
                "delegate_id": "lg-reply-delegate",
                "capability": "reply_to_user",
                "effect_id": "lg-effect-reply",
                "idempotency_key": "lg-reply-key-1",
                "thread_id": thread_id,
            }
        ],
    )
    effects = harness.load_effects(transaction_id=transaction_id)
    evidence.fact("first_delegate", first_delegate.to_dict())
    evidence.fact("second_delegate", second_delegate.to_dict())
    evidence.fact("effects", effects.to_dict())
    evidence.check(
        "poc_effects.separate_delegates",
        "Capability and reply each create an independent delegate and effect intent.",
        actual={
            "effect_count": len(effects.data.get("effects", [])),
            "delegate_ids": [
                item.get("delegate_id")
                for item in effects.data.get("effects", [])
            ],
            "capabilities": [
                item.get("capability")
                for item in effects.data.get("effects", [])
            ],
        },
        expected={
            "effect_count": 2,
            "delegate_ids": [
                "lg-capability-delegate",
                "lg-reply-delegate",
            ],
            "capabilities": ["fake_capability", "reply_to_user"],
        },
        evidence="FakeEffectExecutor",
        known_gap_key=gap_key,
    )
    evidence.check(
        "poc_effects.consumer_released_between_delegates",
        "The graph ends after each delegate without blocking the next effect.",
        actual={
            "first_phase": first_delegate.data.get("graph_state", {}).get(
                "graph_phase"
            ),
            "second_phase": second_delegate.data.get("graph_state", {}).get(
                "graph_phase"
            ),
            "active_delegate_between": between.get("active_delegate_id"),
        },
        expected={
            "first_phase": "awaiting_feedback",
            "second_phase": "awaiting_feedback",
            "active_delegate_between": None,
        },
        evidence="TransactionGraphEngine",
    )
    evidence.check(
        "poc_effects.one_effect_per_delegate",
        "Each delegate carries exactly one effect intent.",
        actual=[
            {
                "delegate_id": item.get("delegate_id"),
                "visible_effects": item.get("visible_effects"),
            }
            for item in effects.data.get("effects", [])
        ],
        expected=[
            {
                "delegate_id": "lg-capability-delegate",
                "visible_effects": 1,
            },
            {
                "delegate_id": "lg-reply-delegate",
                "visible_effects": 1,
            },
        ],
        evidence="FakeEffectExecutor",
    )
    return evidence.build()


def _run_tx_01_poc_stale_feedback_after_restore(
    harness: LangGraphV1Harness,
    evidence: EvidenceBuilder,
    gap_key: str,
) -> ScenarioExecution:
    conversation_id = _new_conversation(harness, evidence, suffix="stale-fb")
    seeded = harness.seed_attributed_transaction(
        conversation_id=conversation_id,
        goal="stale feedback slice",
    )
    transaction_id = str(seeded.data.get("transaction_id", ""))
    old_activation = str(seeded.data.get("activation_id", ""))
    harness.registry.begin_delegate(
        transaction_id,
        "lg-stale-delegate",
    )
    harness.run_graph_script(
        conversation_id=conversation_id,
        transaction_id=transaction_id,
        script=[{"action": "pause", "reason": "manual_hold"}],
    )
    restore = harness.run_graph_script(
        conversation_id=conversation_id,
        transaction_id=transaction_id,
        script=[{"action": "restore", "source": "ui"}],
    )
    new_activation = str(
        restore.data.get("transaction", {}).get("current_activation_id", "")
    )
    late_feedback = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind="execution_feedback",
        text="late fake result",
        payload={
            "activation_id": old_activation,
            "delegate_id": "lg-stale-delegate",
        },
        source={
            "transaction_id": transaction_id,
            "activation_id": old_activation,
            "delegate_id": "lg-stale-delegate",
        },
        ingress_key=(
            f"feedback:{transaction_id}:{old_activation}:lg-stale-delegate"
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
    ("TX-01", "TX-01/uow_replay_and_result"): _run_tx_01_uow,
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
}


def run_tx_scenario(
    harness: LangGraphV1Harness,
    scenario_id: str,
    variant_id: str,
) -> ScenarioExecution:
    scenario = str(scenario_id or "").strip().upper()
    variant = str(variant_id or "").strip()
    handler = _HANDLERS.get((scenario, variant))
    if handler is not None:
        evidence = _builder(scenario, variant)
        return handler(
            harness,
            evidence,
            registered_known_gap_key(variant),
        )
    delegated = shared_tx._HANDLERS.get((scenario, variant))
    if delegated is not None:
        evidence = _builder(scenario, variant)
        return delegated(
            harness,
            evidence,
            registered_known_gap_key(variant),
        )
    raise RuntimeAdapterError(
        "langgraph_v1 TX adapter does not implement "
        f"{scenario}/{variant}"
    )


__all__ = ["run_tx_scenario"]
