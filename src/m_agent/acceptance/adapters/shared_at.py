"""Shared evidence drivers for the P1 AT semantic scenarios."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Dict, Optional, Sequence

from m_agent.runtime.domain.contracts import (
    StimulusEnvelope,
    TransactionKind,
    TransactionRecord,
)

from ..matcher_evaluation import (
    MATCHER_CATEGORY_COUNTS,
    MATCHER_DOMAINS,
    load_matcher_dataset,
    normalize_harness_matcher_result,
    validate_matcher_dataset,
)
from ..scenario_catalog import registered_known_gap_key
from .base import RuntimeAdapterError, ScenarioExecution
from .evidence import EvidenceBuilder
from .shared_harness import SharedRuntimeHarness
from .transaction_fixtures import (
    FixtureTransactionStatus as TransactionStatus,
    apply_fixture_status,
)


_KNOWN_GAP_CHECK_IDS = frozenset(
    {
        "invalid_feedback_discarded_at_admission",
        "scheduled_source_validated_without_matcher",
        "explicit_restore_source_supported",
        "attributed_reentry_stays_locked",
        "feedback_identity_includes_activation",
        "candidate_set_is_exact",
        "continue_is_not_candidate",
        "all_pause_transactions_are_candidates",
        "unflushed_complete_is_candidate",
        "target_candidate_filter_is_available",
        "paused_transaction_reaches_matcher",
        "pause_match_reuses_identity",
        "pause_state_is_restored",
        "pause_restore_opens_new_activation",
        "unflushed_complete_reaches_matcher",
        "complete_match_reuses_identity",
        "complete_reactivation_enters_continue",
        "complete_reactivation_opens_activation",
        "new_transaction_starts_continue",
        "new_transaction_has_activation",
        "ambiguous_creation_starts_continue",
        "ambiguous_creation_has_activation",
        "cross_conversation_creation_starts_continue",
        "cross_conversation_creation_has_activation",
        "reentry_does_not_reattribute",
        "reentry_keeps_stimulus_and_transaction",
        "scene_uses_attributed_transaction",
        "attribution_has_activation_identity",
        "fake_matcher_exception_creates",
        "matcher_contract_uses_turn_local_refs",
        "runtime_creates_activation_after_match",
        "real_matcher_evaluator_is_supported",
        "matcher_runs_three_times",
        "structured_output_valid_rate",
        "external_candidate_reference_count",
        "wrong_candidate_count",
        "false_reuse_count",
        "matcher_total_accuracy",
        "matcher_reuse_recall",
        "matcher_stability_is_reported",
    }
)


class _GapEvidenceBuilder(EvidenceBuilder):
    """Attach a gap key only to explicitly allowlisted target checks."""

    def __init__(
        self,
        *,
        scenario_id: str,
        variant_id: str,
        runtime_id: str,
    ) -> None:
        super().__init__(
            scenario_id=scenario_id,
            variant_id=variant_id,
            runtime_id=runtime_id,
        )
        self._variant_gap_key = registered_known_gap_key(variant_id)

    def check(
        self,
        check_id: str,
        description: str,
        *,
        known_gap_key: str = "",
        **kwargs: Any,
    ) -> bool:
        return super().check(
            check_id,
            description,
            known_gap_key=(
                known_gap_key
                or (
                    self._variant_gap_key
                    if check_id in _KNOWN_GAP_CHECK_IDS
                    else ""
                )
            ),
            **kwargs,
        )


def _builder(
    harness: SharedRuntimeHarness,
    scenario_id: str,
    variant_id: str,
) -> _GapEvidenceBuilder:
    return _GapEvidenceBuilder(
        scenario_id=scenario_id,
        variant_id=variant_id,
        runtime_id=harness.runtime_id,
    )


def _conversation(
    harness: SharedRuntimeHarness,
    thread_id: str,
) -> str:
    result = harness.create_conversation(thread_id=thread_id)
    if not result.supported or result.outcome != "ok":
        raise RuntimeAdapterError(
            f"cannot create conversation for {thread_id}: {result.reason}"
        )
    return str(result.data["conversation_id"])


def _seed_transaction(
    harness: SharedRuntimeHarness,
    conversation_id: str,
    *,
    status: TransactionStatus,
    goal: str,
    wm_note: str = "",
) -> TransactionRecord:
    
    record = harness.create_runtime_transaction(
        conversation_id=conversation_id,
        kind=TransactionKind.USER_TASK,
    )
    record = apply_fixture_status(harness.registry, record, status)
    record = harness.registry.get(record.transaction_id) or record
    record.task_state.goal = goal
    record.task_state.remaining = [f"继续处理：{goal}"]
    if wm_note:
        record.wm_entries.append(
            {
                "kind": "at_acceptance",
                "content": wm_note,
            }
        )
    return record


def _user_stimulus(
    harness: SharedRuntimeHarness,
    conversation_id: str,
    text: str,
    *,
    stimulus_id: str = "",
    transaction_id: Optional[str] = None,
) -> StimulusEnvelope:
    source = (
        {"transaction_id": transaction_id}
        if transaction_id
        else None
    )
    return harness.make_stimulus(
        conversation_id=conversation_id,
        kind="user_message",
        text=text,
        source=source,
        stimulus_id=stimulus_id,
    )


def _install_recording_matcher(
    harness: SharedRuntimeHarness,
    resolver: Callable[..., Optional[str]],
) -> list[Dict[str, Any]]:
    calls: list[Dict[str, Any]] = []

    def recording(
        stimulus: StimulusEnvelope,
        candidates: Sequence[Any],
        **_kwargs: Any,
    ) -> Optional[str]:
        if candidates and isinstance(candidates[0], dict):
            candidate_ids = [str(item.get("ref") or "") for item in candidates]
            candidate_states = [
                str(item.get("state") or item.get("status") or "")
                for item in candidates
            ]
            turn_local = all(
                cid.startswith("candidate_")
                for cid in candidate_ids
                if cid
            )
        else:
            candidate_ids = [item.transaction_id for item in candidates]
            candidate_states = [item.state.value for item in candidates]
            turn_local = False
        calls.append(
            {
                "stimulus_id": stimulus.stimulus_id,
                "kind": stimulus.kind.value,
                "candidate_ids": candidate_ids,
                "candidate_states": candidate_states,
                "turn_local_refs": turn_local,
            }
        )
        return resolver(stimulus, candidates)

    harness.attributor.semantic_resolver = recording
    return calls


def _candidate_ref_at(index: int) -> str:
    return f"candidate_{index}"


def _at_01(
    harness: SharedRuntimeHarness,
    variant_id: str,
) -> ScenarioExecution:
    evidence = _builder(harness, "AT-01", variant_id)
    conversation_id = _conversation(harness, "at-01")
    transaction = _seed_transaction(
        harness,
        conversation_id,
        status=TransactionStatus.WAITING_EXECUTION,
        goal="执行一次有因果归属的工具调用",
    )
    delegate_id = str(transaction.active_delegate_id)
    activation_id = str(transaction.current_activation_id or "")
    matcher_calls = _install_recording_matcher(
        harness,
        lambda _stimulus, _candidates: None,
    )

    valid_feedback = harness.make_stimulus(
        conversation_id=conversation_id,
        kind="execution_feedback",
        text="工具执行完成",
        payload={
            "delegate_id": delegate_id,
            "tool_history": [],
        },
        source={
            "transaction_id": transaction.transaction_id,
            "activation_id": activation_id,
            "delegate_id": delegate_id,
        },
    )
    selected, created = harness.resolve(valid_feedback)
    evidence.check(
        "valid_feedback_bypasses_matcher",
        "合法 Feedback 不调用无来源 matcher。",
        actual=len(matcher_calls),
        expected=0,
        evidence="TransactionAttributor._resolve_feedback",
    )
    evidence.check(
        "valid_feedback_locks_transaction",
        "合法 Feedback 锁定原 transaction 且不新建。",
        actual={
            "transaction_matches": (
                selected.transaction_id == transaction.transaction_id
            ),
            "created": created,
        },
        expected={
            "transaction_matches": True,
            "created": False,
        },
        evidence="TransactionAttributor._resolve_feedback",
    )
    evidence.check(
        "feedback_identity_includes_activation",
        "Feedback 来源身份包含当前 activation。",
        actual=activation_id,
        expected=activation_id,
        evidence="TransactionRecord",
        predicate=lambda value: bool(value),
    )

    invalid_feedback = harness.make_stimulus(
        conversation_id=conversation_id,
        kind="execution_feedback",
        text="陈旧 Feedback",
        source={
            "transaction_id": transaction.transaction_id,
            "activation_id": "activation-stale",
            "delegate_id": "delegate-stale",
        },
    )
    rejected = False
    try:
        harness.resolve(invalid_feedback)
    except ValueError:
        rejected = True
    evidence.check(
        "invalid_feedback_rejected_without_matcher",
        "无效 Feedback 明确拒绝且不退化为 matcher。",
        actual={
            "rejected": rejected,
            "matcher_calls": len(matcher_calls),
        },
        expected={
            "rejected": True,
            "matcher_calls": 0,
        },
        evidence="TransactionAttributor._resolve_feedback",
    )

    admission = harness.submit_stimulus(
        conversation_id=conversation_id,
        kind="execution_feedback",
        text="入口处应拒绝的陈旧 Feedback",
        source={
            "transaction_id": transaction.transaction_id,
            "activation_id": "activation-stale",
            "delegate_id": "delegate-stale",
        },
        ingress_key="feedback-stale",
    )
    evidence.check(
        "invalid_feedback_discarded_at_admission",
        "陈旧 Feedback 在取得 accepted_seq 前产生 admission discard。",
        actual={
            "outcome": admission.outcome,
            "accepted_seq": admission.data.get("accepted_seq"),
            "ingress_key_persisted": admission.data.get(
                "ingress_key_persisted"
            ),
        },
        expected={
            "outcome": "expected_discard",
            "accepted_seq": None,
            "ingress_key_persisted": True,
        },
        evidence="PerceptionGateway.submit",
    )

    before_schedule_calls = len(matcher_calls)
    scheduled = harness.make_stimulus(
        conversation_id=conversation_id,
        kind="scheduled_plan",
        text="执行已到期计划",
        payload={
            "run_id": "run-1",
            "delivery_id": "delivery-1",
        },
        source={
            "transaction_id": transaction.transaction_id,
            "schedule_id": "schedule-1",
            "schedule_run_id": "run-1",
            "schedule_delivery_id": "delivery-1",
        },
    )
    scheduled_selected, scheduled_created = harness.resolve(scheduled)
    evidence.check(
        "scheduled_source_validated_without_matcher",
        "有效 Scheduled Plan 绕过 matcher 并锁定保存的 transaction。",
        actual={
            "matcher_calls_added": (
                len(matcher_calls) - before_schedule_calls
            ),
            "transaction_matches": (
                scheduled_selected.transaction_id
                == transaction.transaction_id
            ),
            "created": scheduled_created,
        },
        expected={
            "matcher_calls_added": 0,
            "transaction_matches": True,
            "created": False,
        },
        evidence="TransactionAttributor.resolve",
    )

    restore = harness.restore_transaction(
        transaction_id=transaction.transaction_id,
        source="explicit_ui_restore",
    )
    evidence.check(
        "explicit_restore_source_supported",
        "合法显式 Restore 校验控制来源后恢复原 transaction。",
        actual=restore.supported,
        expected=True,
        evidence="SharedRuntimeHarness.restore_transaction",
    )

    reentry = _user_stimulus(
        harness,
        conversation_id,
        "重试同一个已归因刺激",
        stimulus_id="at-01-reentry",
        transaction_id=transaction.transaction_id,
    )
    before_reentry_calls = len(matcher_calls)
    reentry_selected, reentry_created = harness.resolve(reentry)
    evidence.check(
        "attributed_reentry_stays_locked",
        "已归因重入保留原 stimulus/transaction 且不重新匹配。",
        actual={
            "matcher_calls_added": (
                len(matcher_calls) - before_reentry_calls
            ),
            "transaction_matches": (
                reentry_selected.transaction_id
                == transaction.transaction_id
            ),
            "created": reentry_created,
        },
        expected={
            "matcher_calls_added": 0,
            "transaction_matches": True,
            "created": False,
        },
        evidence="TransactionAttributor.resolve",
    )
    return evidence.build(
        matcher_calls=matcher_calls,
        admission=admission.to_dict(),
        transaction=harness.transaction_snapshot(transaction),
    )


def _at_02(
    harness: SharedRuntimeHarness,
    variant_id: str,
) -> ScenarioExecution:
    evidence = _builder(harness, "AT-02", variant_id)
    conversation_id = _conversation(harness, "at-02")
    remote_conversation = _conversation(harness, "at-02-remote")
    running = _seed_transaction(
        harness,
        conversation_id,
        status=TransactionStatus.RUNNING,
        goal="当前正在执行的任务",
    )
    awaiting_feedback = _seed_transaction(
        harness,
        conversation_id,
        status=TransactionStatus.WAITING_EXECUTION,
        goal="等待工具反馈的暂停任务",
    )
    manual_pause = _seed_transaction(
        harness,
        conversation_id,
        status=TransactionStatus.SUSPENDED,
        goal="人工暂停任务",
    )
    unflushed_complete = _seed_transaction(
        harness,
        conversation_id,
        status=TransactionStatus.COMPLETED,
        goal="已经完成但尚未 flush 的任务",
    )
    deleted = _seed_transaction(
        harness,
        conversation_id,
        status=TransactionStatus.CANCELLED,
        goal="已删除任务",
    )
    remote_pause = _seed_transaction(
        harness,
        remote_conversation,
        status=TransactionStatus.SUSPENDED,
        goal="其他 conversation 的暂停任务",
    )

    result = harness.list_match_candidates(
        conversation_id=conversation_id
    )
    actual_ids = set(result.data.get("candidate_ids", []))
    # Match candidates are pause + unflushed complete only. continue with a
    # A transaction with an active delegate is not a match candidate.
    expected_ids = {
        manual_pause.transaction_id,
        unflushed_complete.transaction_id,
    }
    evidence.check(
        "candidate_set_is_exact",
        "候选精确等于当前 conversation 的 pause 与未 flush complete。",
        actual=sorted(actual_ids),
        expected=sorted(expected_ids),
        evidence="SharedRuntimeHarness.list_match_candidates",
    )
    evidence.check(
        "continue_is_not_candidate",
        "continue/running transaction 不进入候选。",
        actual=running.transaction_id in actual_ids,
        expected=False,
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "all_pause_transactions_are_candidates",
        "pause transaction 进入候选；continue+pending delegate 不进入。",
        actual={
            "pending_delegate_continue": (
                awaiting_feedback.transaction_id in actual_ids
            ),
            "paused": manual_pause.transaction_id in actual_ids,
        },
        expected={
            "pending_delegate_continue": False,
            "paused": True,
        },
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "unflushed_complete_is_candidate",
        "未 flush complete transaction 进入候选。",
        actual=unflushed_complete.transaction_id in actual_ids,
        expected=True,
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "deleted_and_remote_are_excluded",
        "deleted 与其他 conversation transaction 不进入候选。",
        actual={
            "deleted": deleted.transaction_id in actual_ids,
            "remote": remote_pause.transaction_id in actual_ids,
        },
        expected={
            "deleted": False,
            "remote": False,
        },
        evidence="TransactionRegistry.list_for_conversation",
    )
    evidence.check(
        "target_candidate_filter_is_available",
        "候选查询能按目标 state/lifecycle/flush 语义过滤。",
        actual=result.data.get("target_state_filter_available"),
        expected=True,
        evidence="SharedRuntimeHarness.list_match_candidates",
    )
    return evidence.build(
        candidate_query=result.to_dict(),
        target_candidate_count=len(expected_ids),
    )


def _at_03(
    harness: SharedRuntimeHarness,
    variant_id: str,
) -> ScenarioExecution:
    evidence = _builder(harness, "AT-03", variant_id)
    conversation_id = _conversation(harness, "at-03")
    paused = _seed_transaction(
        harness,
        conversation_id,
        status=TransactionStatus.SUSPENDED,
        goal="继续编辑项目计划",
        wm_note="已完成项目背景整理",
    )
    before = harness.transaction_snapshot(paused)
    matcher_calls = _install_recording_matcher(
        harness,
        lambda _stimulus, _candidates: _candidate_ref_at(1),
    )
    selected, created = harness.resolve(
        _user_stimulus(
            harness,
            conversation_id,
            "继续刚才的项目计划",
        )
    )
    selected_snapshot = harness.transaction_snapshot(selected)
    after = harness.transaction_snapshot(paused)
    evidence.check(
        "paused_transaction_reaches_matcher",
        "pause transaction 进入 matcher 合法候选。",
        actual=any(
            call.get("turn_local_refs")
            and bool(call.get("candidate_ids"))
            for call in matcher_calls
        ),
        expected=True,
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "pause_match_reuses_identity",
        "明确匹配 pause 后复用原 transaction，不新建。",
        actual={
            "transaction_matches": (
                selected.transaction_id == paused.transaction_id
            ),
            "created": created,
        },
        expected={
            "transaction_matches": True,
            "created": False,
        },
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "pause_state_is_restored",
        "匹配后 pause 转为 continue。",
        actual=selected_snapshot["state"],
        expected="continue",
        evidence="TransactionRegistry.transition",
    )
    evidence.check(
        "pause_restore_opens_new_activation",
        "恢复 pause 时开启新的 activation。",
        actual=selected_snapshot["current_activation_id"],
        expected="non-empty",
        predicate=lambda value: bool(value),
        evidence="TransactionRecord",
    )
    evidence.check(
        "pause_restore_preserves_task_state",
        "恢复保留原 WM 与任务状态。",
        actual={
            "goal": after["task_state"]["goal"],
            "wm_entries": after["wm_entries"],
        },
        expected={
            "goal": before["task_state"]["goal"],
            "wm_entries": before["wm_entries"],
        },
        evidence="TransactionRecord",
    )
    return evidence.build(
        matcher_calls=matcher_calls,
        selected_transaction=selected_snapshot,
        original_transaction=after,
    )


def _at_04(
    harness: SharedRuntimeHarness,
    variant_id: str,
) -> ScenarioExecution:
    evidence = _builder(harness, "AT-04", variant_id)
    conversation_id = _conversation(harness, "at-04")
    complete = _seed_transaction(
        harness,
        conversation_id,
        status=TransactionStatus.COMPLETED,
        goal="完成差旅方案",
        wm_note="已比较三条路线",
    )
    before = harness.transaction_snapshot(complete)
    matcher_calls = _install_recording_matcher(
        harness,
        lambda _stimulus, _candidates: _candidate_ref_at(1),
    )
    selected, created = harness.resolve(
        _user_stimulus(
            harness,
            conversation_id,
            "把刚才的差旅方案再加上预算",
        )
    )
    selected_snapshot = harness.transaction_snapshot(selected)
    evidence.check(
        "unflushed_complete_reaches_matcher",
        "未 flush complete 进入 matcher 候选。",
        actual=any(
            call.get("turn_local_refs")
            and bool(call.get("candidate_ids"))
            for call in matcher_calls
        ),
        expected=True,
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "complete_match_reuses_identity",
        "明确匹配 complete 后复用原 transaction。",
        actual={
            "transaction_matches": (
                selected.transaction_id == complete.transaction_id
            ),
            "created": created,
        },
        expected={
            "transaction_matches": True,
            "created": False,
        },
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "complete_reactivation_enters_continue",
        "未 flush complete 重新激活为 continue。",
        actual=selected_snapshot["state"],
        expected="continue",
        evidence="TransactionRegistry.transition",
    )
    evidence.check(
        "complete_reactivation_opens_activation",
        "重新激活 complete 时创建新 activation。",
        actual=selected_snapshot["current_activation_id"],
        expected="non-empty",
        predicate=lambda value: bool(value),
        evidence="TransactionRecord",
    )
    evidence.check(
        "complete_reactivation_preserves_state",
        "重新激活保留原 WM 和任务状态。",
        actual={
            "goal": harness.transaction_snapshot(complete)[
                "task_state"
            ]["goal"],
            "wm_entries": harness.transaction_snapshot(complete)[
                "wm_entries"
            ],
        },
        expected={
            "goal": before["task_state"]["goal"],
            "wm_entries": before["wm_entries"],
        },
        evidence="TransactionRecord",
    )
    return evidence.build(
        matcher_calls=matcher_calls,
        selected_transaction=selected_snapshot,
        original_transaction=harness.transaction_snapshot(complete),
    )


def _at_05(
    harness: SharedRuntimeHarness,
    variant_id: str,
) -> ScenarioExecution:
    evidence = _builder(harness, "AT-05", variant_id)
    conversation_id = _conversation(harness, "at-05")
    candidate = _seed_transaction(
        harness,
        conversation_id,
        status=TransactionStatus.RUNNING,
        goal="整理会议纪要",
        wm_note="会议纪要已有三段",
    )
    before = harness.transaction_snapshot(candidate)
    count_before = harness.registry.count_all()
    matcher_calls = _install_recording_matcher(
        harness,
        lambda _stimulus, _candidates: None,
    )
    selected, created = harness.resolve(
        _user_stimulus(
            harness,
            conversation_id,
            "查询明天上海的天气",
        )
    )
    after = harness.transaction_snapshot(candidate)
    selected_snapshot = harness.transaction_snapshot(selected)
    evidence.check(
        "no_match_creates_new_transaction",
        "matcher 无匹配时新建 transaction。",
        actual={
            "created": created,
            "new_identity": (
                selected.transaction_id != candidate.transaction_id
            ),
            "count_delta": (
                harness.registry.count_all() - count_before
            ),
        },
        expected={
            "created": True,
            "new_identity": True,
            "count_delta": 1,
        },
        evidence="TransactionAttributor._resolve_user",
    )
    evidence.check(
        "no_match_does_not_mutate_candidate",
        "无匹配新建不修改原候选 WM/任务状态。",
        actual={
            "state": after["state"],
            "task_state": after["task_state"],
            "wm_entries": after["wm_entries"],
        },
        expected={
            "state": before["state"],
            "task_state": before["task_state"],
            "wm_entries": before["wm_entries"],
        },
        evidence="TransactionRecord",
    )
    evidence.check(
        "new_transaction_starts_continue",
        "新 transaction 初始目标状态为 continue。",
        actual=selected_snapshot["state"],
        expected="continue",
        evidence="TransactionRegistry.create",
    )
    evidence.check(
        "new_transaction_has_activation",
        "新 transaction 创建首个 activation。",
        actual=selected_snapshot["current_activation_id"],
        expected="non-empty",
        predicate=lambda value: bool(value),
        evidence="TransactionRecord",
    )
    return evidence.build(
        matcher_calls=matcher_calls,
        original_transaction=after,
        created_transaction=selected_snapshot,
    )


def _at_06(
    harness: SharedRuntimeHarness,
    variant_id: str,
) -> ScenarioExecution:
    evidence = _builder(harness, "AT-06", variant_id)
    conversation_id = _conversation(harness, "at-06")
    first = _seed_transaction(
        harness,
        conversation_id,
        status=TransactionStatus.RUNNING,
        goal="预订北京出差机票",
        wm_note="候选航班 A",
    )
    second = _seed_transaction(
        harness,
        conversation_id,
        status=TransactionStatus.RUNNING,
        goal="预订北京出差酒店",
        wm_note="候选酒店 B",
    )
    before = {
        first.transaction_id: harness.transaction_snapshot(first),
        second.transaction_id: harness.transaction_snapshot(second),
    }
    matcher_calls = _install_recording_matcher(
        harness,
        lambda _stimulus, _candidates: None,
    )
    selected, created = harness.resolve(
        _user_stimulus(
            harness,
            conversation_id,
            "继续刚才那个",
        )
    )
    unchanged = {
        transaction_id: harness.transaction_snapshot(
            harness.registry.get(transaction_id)  # type: ignore[arg-type]
        )
        for transaction_id in before
    }
    selected_snapshot = harness.transaction_snapshot(selected)
    evidence.check(
        "ambiguous_match_creates_new_transaction",
        "多候选语义模糊时不随意选择并新建。",
        actual={
            "created": created,
            "selected_existing": (
                selected.transaction_id in before
            ),
        },
        expected={
            "created": True,
            "selected_existing": False,
        },
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "ambiguous_match_keeps_candidates_unchanged",
        "模糊新建不污染任何候选。",
        actual=unchanged,
        expected=before,
        evidence="TransactionRecord",
    )
    evidence.check(
        "ambiguous_creation_starts_continue",
        "模糊时新建 transaction 初始状态为 continue。",
        actual=selected_snapshot["state"],
        expected="continue",
        evidence="TransactionRegistry.create",
    )
    evidence.check(
        "ambiguous_creation_has_activation",
        "模糊时新建 transaction 创建首 activation。",
        actual=selected_snapshot["current_activation_id"],
        expected="non-empty",
        predicate=lambda value: bool(value),
        evidence="TransactionRecord",
    )
    return evidence.build(
        matcher_calls=matcher_calls,
        created_transaction=selected_snapshot,
    )


def _at_07(
    harness: SharedRuntimeHarness,
    variant_id: str,
) -> ScenarioExecution:
    evidence = _builder(harness, "AT-07", variant_id)
    local_conversation = _conversation(harness, "at-07-local")
    remote_conversation = _conversation(harness, "at-07-remote")
    remote = _seed_transaction(
        harness,
        remote_conversation,
        status=TransactionStatus.RUNNING,
        goal="修改年度报告",
    )
    matcher_calls = _install_recording_matcher(
        harness,
        lambda _stimulus, candidates: (
            candidates[0].transaction_id
            if candidates
            else None
        ),
    )
    selected, created = harness.resolve(
        _user_stimulus(
            harness,
            local_conversation,
            "继续修改年度报告",
        )
    )
    selected_snapshot = harness.transaction_snapshot(selected)
    evidence.check(
        "remote_transaction_not_visible",
        "matcher 看不到其他 conversation 的相似 transaction。",
        actual=any(
            remote.transaction_id in call["candidate_ids"]
            for call in matcher_calls
        ),
        expected=False,
        evidence="TransactionRegistry.list_for_conversation",
    )
    evidence.check(
        "local_no_match_creates_locally",
        "当前 conversation 无匹配时在当前 conversation 新建。",
        actual={
            "created": created,
            "conversation_id": selected.conversation_id,
            "selected_remote": (
                selected.transaction_id == remote.transaction_id
            ),
        },
        expected={
            "created": True,
            "conversation_id": local_conversation,
            "selected_remote": False,
        },
        evidence="TransactionAttributor._resolve_user",
    )
    evidence.check(
        "cross_conversation_creation_starts_continue",
        "跨 conversation 无匹配的新 transaction 为 continue。",
        actual=selected_snapshot["state"],
        expected="continue",
        evidence="TransactionRegistry.create",
    )
    evidence.check(
        "cross_conversation_creation_has_activation",
        "跨 conversation 无匹配的新 transaction 创建 activation。",
        actual=selected_snapshot["current_activation_id"],
        expected="non-empty",
        predicate=lambda value: bool(value),
        evidence="TransactionRecord",
    )
    return evidence.build(
        matcher_calls=matcher_calls,
        remote_transaction=harness.transaction_snapshot(remote),
        local_transaction=selected_snapshot,
    )


def _at_08(
    harness: SharedRuntimeHarness,
    variant_id: str,
) -> ScenarioExecution:
    evidence = _builder(harness, "AT-08", variant_id)
    conversation_id = _conversation(harness, "at-08")
    transaction = _seed_transaction(
        harness,
        conversation_id,
        status=TransactionStatus.SUSPENDED,
        goal="持续整理同一份文档",
        wm_note="原 transaction WM",
    )
    outputs = [_candidate_ref_at(1), None]

    def resolver(
        _stimulus: StimulusEnvelope,
        _candidates: Sequence[Any],
    ) -> Optional[str]:
        return outputs.pop(0) if outputs else None

    matcher_calls = _install_recording_matcher(harness, resolver)
    stimulus = _user_stimulus(
        harness,
        conversation_id,
        "继续补充文档第二节",
        stimulus_id="at-08-stable",
    )
    harness.gateway.submit(stimulus, schedule_drainer=False)
    popped = harness.inbox.pop_next(transaction.thread_id)
    if popped is None:
        raise RuntimeAdapterError("AT-08 stimulus disappeared from inbox")
    selected, created = harness.resolve(popped)
    from m_agent.runtime.domain.contracts import (
        SceneActor,
        SceneEntry,
        SceneEntryType,
    )

    harness.append_scene(
        conversation_id,
        SceneEntry(
            seq=0,
            occurred_at=popped.occurred_at,
            entry_type=SceneEntryType.UTTERANCE,
            actor=SceneActor.USER,
            text=str(popped.text or ""),
            append_id=str(popped.stimulus_id or "").strip() or None,
            transaction_id=selected.transaction_id,
        ),
    )
    scene = harness.read_scene(conversation_id=conversation_id)

    requeued = replace(
        popped,
        transaction_id=selected.transaction_id,
    )
    selected_after_reentry, reentry_created = harness.resolve(requeued)
    evidence.check(
        "initial_attribution_uses_one_transaction",
        "首次归因锁定目标 transaction。",
        actual={
            "transaction_matches": (
                selected.transaction_id == transaction.transaction_id
            ),
            "created": created,
        },
        expected={
            "transaction_matches": True,
            "created": False,
        },
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "reentry_does_not_reattribute",
        "已归因重入不再次调用 matcher。",
        actual=len(matcher_calls),
        expected=1,
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "reentry_keeps_stimulus_and_transaction",
        "合法重入保持 stimulus ID 与原 transaction 归属。",
        actual={
            "stimulus_id_stable": (
                requeued.stimulus_id == popped.stimulus_id
            ),
            "transaction_matches": (
                selected_after_reentry.transaction_id
                == selected.transaction_id
            ),
            "created": reentry_created,
        },
        expected={
            "stimulus_id_stable": True,
            "transaction_matches": True,
            "created": False,
        },
        evidence="TransactionAttributor.resolve",
    )
    scene_transaction_ids = [
        item.get("transaction_id")
        for item in scene.data.get("entries", [])
    ]
    evidence.check(
        "scene_uses_attributed_transaction",
        "归属于刺激的 Scene 条目使用同一个 transaction ID。",
        actual=scene_transaction_ids,
        expected=[transaction.transaction_id],
        evidence="PerceptionGateway._maybe_scene_on_ingress",
    )
    evidence.check(
        "attribution_has_activation_identity",
        "归因稳定性包含 activation 级身份。",
        actual=harness.transaction_snapshot(transaction)[
            "current_activation_id"
        ],
        expected="non-empty",
        predicate=lambda value: bool(value),
        evidence="TransactionRecord",
    )
    return evidence.build(
        matcher_calls=matcher_calls,
        scene=scene.to_dict(),
        first_transaction=harness.transaction_snapshot(transaction),
        reentry_transaction=harness.transaction_snapshot(
            selected_after_reentry
        ),
    )


def _run_matcher_case(
    harness: SharedRuntimeHarness,
    *,
    thread_id: str,
    candidate_count: int,
    resolver: Callable[..., Optional[str]],
) -> Dict[str, Any]:
    conversation_id = _conversation(harness, thread_id)
    candidates = [
        _seed_transaction(
            harness,
            conversation_id,
            status=TransactionStatus.SUSPENDED,
            goal=f"{thread_id} 候选 {index}",
        )
        for index in range(1, candidate_count + 1)
    ]
    calls = _install_recording_matcher(harness, resolver)
    try:
        selected, created = harness.resolve(
            _user_stimulus(
                harness,
                conversation_id,
                f"{thread_id} 新刺激",
            )
        )
        return {
            "outcome": "resolved",
            "selected_id": selected.transaction_id,
            "created": created,
            "candidate_ids": [
                item.transaction_id
                for item in candidates
            ],
            "calls": calls,
        }
    except Exception as exc:
        return {
            "outcome": "raised",
            "error": str(exc),
            "created": False,
            "candidate_ids": [
                item.transaction_id
                for item in candidates
            ],
            "calls": calls,
        }


def _at_09(
    harness: SharedRuntimeHarness,
    variant_id: str,
) -> ScenarioExecution:
    evidence = _builder(harness, "AT-09", variant_id)

    match_case_holder: Dict[str, str] = {}

    def select_second(
        _stimulus: StimulusEnvelope,
        candidates: Sequence[Any],
    ) -> str:
        if candidates and isinstance(candidates[0], dict):
            return str(candidates[1]["ref"])
        return str(candidates[1].transaction_id)

    matched = _run_matcher_case(
        harness,
        thread_id="at-09-match",
        candidate_count=2,
        resolver=select_second,
    )
    match_case_holder["expected"] = matched["candidate_ids"][1]
    no_match = _run_matcher_case(
        harness,
        thread_id="at-09-no-match",
        candidate_count=1,
        resolver=lambda _stimulus, _candidates: None,
    )
    ambiguous = _run_matcher_case(
        harness,
        thread_id="at-09-ambiguous",
        candidate_count=2,
        resolver=lambda _stimulus, _candidates: None,
    )
    illegal = _run_matcher_case(
        harness,
        thread_id="at-09-illegal",
        candidate_count=2,
        resolver=lambda _stimulus, _candidates: "not-a-candidate",
    )

    def raise_matcher(
        _stimulus: StimulusEnvelope,
        _candidates: Sequence[Any],
    ) -> Optional[str]:
        raise RuntimeError("fake matcher timeout")

    exceptional = _run_matcher_case(
        harness,
        thread_id="at-09-exception",
        candidate_count=1,
        resolver=raise_matcher,
    )
    evidence.check(
        "fake_matcher_selects_explicit_candidate",
        "fake matcher 明确匹配时选择指定候选且不新建。",
        actual={
            "selected_id": matched.get("selected_id"),
            "created": matched.get("created"),
        },
        expected={
            "selected_id": match_case_holder.get("expected"),
            "created": False,
        },
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "fake_matcher_no_match_creates",
        "fake matcher 返回无匹配时新建。",
        actual={
            "outcome": no_match["outcome"],
            "created": no_match["created"],
            "selected_existing": (
                no_match.get("selected_id")
                in no_match["candidate_ids"]
            ),
        },
        expected={
            "outcome": "resolved",
            "created": True,
            "selected_existing": False,
        },
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "fake_matcher_ambiguous_creates",
        "fake matcher 返回模糊结果时安全新建。",
        actual={
            "outcome": ambiguous["outcome"],
            "created": ambiguous["created"],
            "selected_existing": (
                ambiguous.get("selected_id")
                in ambiguous["candidate_ids"]
            ),
        },
        expected={
            "outcome": "resolved",
            "created": True,
            "selected_existing": False,
        },
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "fake_matcher_illegal_reference_creates",
        "fake matcher 返回非候选 ID 时安全新建。",
        actual={
            "outcome": illegal["outcome"],
            "created": illegal["created"],
            "selected_existing": (
                illegal.get("selected_id")
                in illegal["candidate_ids"]
            ),
        },
        expected={
            "outcome": "resolved",
            "created": True,
            "selected_existing": False,
        },
        evidence="TransactionAttributor.resolve",
    )
    evidence.check(
        "fake_matcher_exception_creates",
        "matcher 异常或超时一律安全新建。",
        actual={
            "outcome": exceptional["outcome"],
            "created": exceptional["created"],
        },
        expected={
            "outcome": "resolved",
            "created": True,
        },
        evidence="TransactionAttributor.resolve",
    )

    sourced_conversation = _conversation(harness, "at-09-source")
    sourced_transaction = _seed_transaction(
        harness,
        sourced_conversation,
        status=TransactionStatus.WAITING_EXECUTION,
        goal="等待来源 Feedback",
    )
    source_calls = _install_recording_matcher(
        harness,
        lambda _stimulus, _candidates: sourced_transaction.transaction_id,
    )
    feedback = harness.make_stimulus(
        conversation_id=sourced_conversation,
        kind="execution_feedback",
        text="来源 Feedback",
        source={
            "transaction_id": sourced_transaction.transaction_id,
            "activation_id": sourced_transaction.current_activation_id,
            "delegate_id": sourced_transaction.active_delegate_id,
        },
    )
    harness.resolve(feedback)
    invalid_feedback = harness.make_stimulus(
        conversation_id=sourced_conversation,
        kind="execution_feedback",
        text="无效来源 Feedback",
        source={
            "transaction_id": sourced_transaction.transaction_id,
            "activation_id": sourced_transaction.current_activation_id,
            "delegate_id": "wrong-delegate",
        },
    )
    invalid_rejected = False
    try:
        harness.resolve(invalid_feedback)
    except ValueError:
        invalid_rejected = True
    evidence.check(
        "sourced_feedback_never_reaches_fake_matcher",
        "合法和非法 Feedback 均不调用 fake matcher。",
        actual={
            "matcher_calls": len(source_calls),
            "invalid_rejected": invalid_rejected,
        },
        expected={
            "matcher_calls": 0,
            "invalid_rejected": True,
        },
        evidence="TransactionAttributor._resolve_feedback",
    )
    turn_local_calls = []
    for case in (matched, no_match, ambiguous, illegal, exceptional):
        turn_local_calls.extend(case.get("calls") or [])
    evidence.check(
        "matcher_contract_uses_turn_local_refs",
        "fake matcher 契约只接收 candidate_N，不暴露持久 transaction ID。",
        actual=all(
            bool(call.get("turn_local_refs"))
            for call in turn_local_calls
            if call.get("candidate_ids")
        ),
        expected=True,
        evidence="TransactionAttributor.SemanticResolver",
    )
    evidence.check(
        "runtime_creates_activation_after_match",
        "匹配器只选择候选，新 activation 由 Runtime 创建。",
        actual=harness.transaction_snapshot(sourced_transaction)[
            "current_activation_id"
        ],
        expected="non-empty",
        predicate=lambda value: bool(value),
        evidence="TransactionRecord",
    )
    return evidence.build(
        matched=matched,
        no_match=no_match,
        ambiguous=ambiguous,
        illegal_reference=illegal,
        exceptional=exceptional,
        sourced_matcher_calls=source_calls,
    )


def _at_10(
    harness: SharedRuntimeHarness,
    variant_id: str,
) -> ScenarioExecution:
    evidence = _builder(harness, "AT-10", variant_id)
    samples = load_matcher_dataset()
    dataset = validate_matcher_dataset(samples)
    result = harness.evaluate_matcher(samples=samples)
    metrics = normalize_harness_matcher_result(samples, result)

    evidence.check(
        "matcher_dataset_has_30_samples",
        "Matcher v1 数据集固定为 30 条。",
        actual=dataset["sample_count"],
        expected=30,
        evidence="at_10_matcher_v1.zh-CN.jsonl",
    )
    evidence.check(
        "matcher_dataset_distribution_is_frozen",
        "数据严格按 8/6/6/5/5 分布。",
        actual=dataset["category_counts"],
        expected=MATCHER_CATEGORY_COUNTS,
        evidence="validate_matcher_dataset",
    )
    evidence.check(
        "matcher_dataset_schema_is_valid",
        "所有样本符合 Matcher v1 输入和标注约束。",
        actual=dataset["errors"],
        expected=[],
        evidence="validate_matcher_dataset",
    )
    evidence.check(
        "matcher_dataset_covers_domains",
        "数据覆盖邮件、日程、查询、文档和出行领域。",
        actual=sorted(dataset["domain_counts"]),
        expected=sorted(MATCHER_DOMAINS),
        evidence="validate_matcher_dataset",
    )
    evidence.check(
        "real_matcher_evaluator_is_supported",
        "Runtime adapter 能用冻结数据实际调用 matcher。",
        actual=metrics["supported"],
        expected=True,
        evidence="SharedRuntimeHarness.evaluate_matcher",
    )
    evidence.check(
        "matcher_runs_three_times",
        "真实 matcher 使用确定性设置重复运行三次。",
        actual=metrics["run_count"],
        expected=3,
        evidence="SharedRuntimeHarness.evaluate_matcher",
    )
    evidence.check(
        "structured_output_valid_rate",
        "结构化输出有效率为 100%。",
        actual=metrics["structured_output_valid_rate"],
        expected=1.0,
        evidence="matcher_evaluation",
    )
    evidence.check(
        "external_candidate_reference_count",
        "引用候选集合之外 ID 的数量为零。",
        actual=metrics["external_reference_count"],
        expected=0,
        evidence="matcher_evaluation",
    )
    evidence.check(
        "wrong_candidate_count",
        "匹配到错误候选的数量为零。",
        actual=metrics["wrong_candidate_count"],
        expected=0,
        evidence="matcher_evaluation",
    )
    evidence.check(
        "false_reuse_count",
        "应新建却错误复用的数量为零。",
        actual=metrics["false_reuse_count"],
        expected=0,
        evidence="matcher_evaluation",
    )
    evidence.check(
        "matcher_total_accuracy",
        "总准确率至少 90%。",
        actual=metrics["total_accuracy"],
        expected=">=0.90",
        predicate=lambda value: float(value) >= 0.90,
        evidence="matcher_evaluation",
    )
    evidence.check(
        "matcher_reuse_recall",
        "应复用场景召回率至少 85%。",
        actual=metrics["reuse_recall"],
        expected=">=0.85",
        predicate=lambda value: float(value) >= 0.85,
        evidence="matcher_evaluation",
    )
    evidence.check(
        "matcher_stability_is_reported",
        "三轮运行报告 exact-decision 稳定率。",
        actual=metrics["decision_stability_rate"],
        expected="0.0..1.0",
        predicate=lambda value: (
            value is not None
            and 0.0 <= float(value) <= 1.0
        ),
        evidence="matcher_evaluation",
    )
    return evidence.build(
        dataset=dataset,
        matcher_result=result.to_dict(),
        metrics=metrics,
    )


_RUNNERS: Dict[
    str,
    Callable[[SharedRuntimeHarness, str], ScenarioExecution],
] = {
    "AT-01": _at_01,
    "AT-02": _at_02,
    "AT-03": _at_03,
    "AT-04": _at_04,
    "AT-05": _at_05,
    "AT-06": _at_06,
    "AT-07": _at_07,
    "AT-08": _at_08,
    "AT-09": _at_09,
    "AT-10": _at_10,
}


def run_at_scenario(
    harness: SharedRuntimeHarness,
    scenario_id: str,
    variant_id: str,
) -> ScenarioExecution:
    """Execute one AT variant using only Harness commands and observations."""

    scenario = str(scenario_id or "").strip().upper()
    variant = str(variant_id or "").strip()
    runner = _RUNNERS.get(scenario)
    if runner is None:
        raise RuntimeAdapterError(f"unknown AT scenario: {scenario}")
    expected_variant = (
        "AT-10/matcher_evaluation"
        if scenario == "AT-10"
        else f"{scenario}/core"
    )
    if variant.lower() != expected_variant.lower():
        raise RuntimeAdapterError(
            f"{scenario} requires variant {expected_variant}, got {variant}"
        )
    return runner(harness, expected_variant)
