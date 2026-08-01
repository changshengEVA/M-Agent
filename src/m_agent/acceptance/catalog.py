"""Curated Phase-0 runtime semantic acceptance catalog."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import (
    InvariantGroupSpec,
    InvariantSpec,
    TestRef,
    unique_nodeids,
)


PHASE0_SUITE = "phase0"


def _test(nodeid: str, proves: str) -> TestRef:
    return TestRef(nodeid=nodeid, proves=proves)


_ACCEPTANCE = "tests/acceptance/invariants/test_runtime_semantics.py"


INVARIANT_GROUPS: Tuple[InvariantGroupSpec, ...] = (
    InvariantGroupSpec(
        group_id="outer_runtime",
        order=1,
        title="外层入口与调度",
        description="统一刺激入口、单消费者调度与抢占边界。",
        invariant_ids=("INV-01", "INV-02", "INV-12"),
    ),
    InvariantGroupSpec(
        group_id="transaction_state",
        order=2,
        title="事务归因与状态",
        description="保证 feedback 归属准确，并隔离每个事务的可变状态。",
        invariant_ids=("INV-03", "INV-04", "INV-05"),
    ),
    InvariantGroupSpec(
        group_id="reasoning_execution",
        order=3,
        title="规划、执行与完成",
        description="约束 Thinking、Execution、工具调用、回复与完成判定。",
        invariant_ids=("INV-07", "INV-08", "INV-09", "INV-10", "INV-11"),
    ),
    InvariantGroupSpec(
        group_id="scene_memory",
        order=4,
        title="Scene 与记忆生命周期",
        description="维护单一 Scene 时间线，并只在安全条件下推进记忆水位。",
        invariant_ids=("INV-06", "INV-14"),
    ),
    InvariantGroupSpec(
        group_id="recovery_idempotency",
        order=5,
        title="恢复与幂等",
        description="确保恢复和重复投递不会重复产生外部副作用。",
        invariant_ids=("INV-13",),
    ),
)


INVARIANTS: Tuple[InvariantSpec, ...] = (
    InvariantSpec(
        invariant_id="INV-01",
        group_id="outer_runtime",
        order=1,
        title="所有刺激先入队",
        principle="用户消息、执行反馈和 schedule stimulus 必须统一进入 Perception inbox。",
        acceptance="三类刺激提交后均可从 inbox 观察到，且提交阶段尚未创建或运行 transaction。",
        layer="Perception",
        risk="绕过队列会破坏优先级、审计和可恢复边界。",
        gate_tests=(
            _test(
                f"{_ACCEPTANCE}::test_inv_01_all_stimuli_enter_inbox_before_processing",
                "三种 stimulus 都先入队，transaction attribution 尚未执行。",
            ),
        ),
        supporting_tests=(
            _test(
                "tests/runtime/test_think_life_core.py::test_gateway_submits_user_utterance_to_scene",
                "用户入口同时保留 Scene ingress 语义。",
            ),
            _test(
                "tests/runtime/test_think_life_core.py::test_gateway_execution_feedback_does_not_schedule_drainer_by_default",
                "feedback 使用统一入口但不递归启动 drainer。",
            ),
        ),
        known_gap="当前用户消息会先写 Scene 再入 inbox；Scene 写入异常可能阻止 admission。",
    ),
    InvariantSpec(
        invariant_id="INV-02",
        group_id="outer_runtime",
        order=2,
        title="每个 conversation 单 drainer",
        principle="同一 thread/conversation 同时最多只有一个 inbox 消费者。",
        acceptance="并发调用 ensure_running 时只有一个 worker 获得执行权，第二次调用被拒绝。",
        layer="Runtime host",
        risk="双消费者会导致重复 attribution、乱序和副作用竞态。",
        gate_tests=(
            _test(
                f"{_ACCEPTANCE}::test_inv_02_only_one_drainer_runs_per_thread",
                "真实 ThreadDrainerService 的并发门闩只允许一个 worker。",
            ),
            _test(
                f"{_ACCEPTANCE}::test_inv_02_drainer_does_not_lose_wakeup_at_worker_exit",
                "刺激在 worker 退出窗口到达时仍会被后续 drainer 消费。",
            ),
        ),
        known_gap="worker 空队列检查与 active-map 移除之间存在 lost-wakeup 窗口。",
    ),
    InvariantSpec(
        invariant_id="INV-03",
        group_id="transaction_state",
        order=3,
        title="feedback 禁止重新语义归因",
        principle="非 feedback stimulus 先做事务归因；feedback 只能回到原 transaction。",
        acceptance="feedback 路径不调用 semantic_resolver，并返回明确关联的原 transaction。",
        layer="Attribution",
        risk="重新解释 feedback 可能把工具结果写进错误任务。",
        gate_tests=(
            _test(
                f"{_ACCEPTANCE}::test_inv_03_feedback_bypasses_semantic_reattribution",
                "semantic resolver 对普通消息可用，但对 feedback 完全不被调用。",
            ),
        ),
        supporting_tests=(
            _test(
                "tests/runtime/test_think_life_core.py::test_execution_feedback_attribution",
                "合法 feedback 被解析到原 transaction。",
            ),
        ),
    ),
    InvariantSpec(
        invariant_id="INV-04",
        group_id="transaction_state",
        order=4,
        title="feedback 双键匹配",
        principle="execution feedback 必须同时匹配 transaction_id 和 active_delegate_id。",
        acceptance="缺失或错误的任一 ID 都被拒绝；仅双键正确时接受。",
        layer="Attribution",
        risk="单键相关会污染 transaction 或接收过期 delegate 结果。",
        gate_tests=(
            _test(
                f"{_ACCEPTANCE}::test_inv_04_feedback_requires_transaction_and_active_delegate",
                "覆盖正确、缺失、错误 transaction 和错误 delegate。",
            ),
        ),
    ),
    InvariantSpec(
        invariant_id="INV-05",
        group_id="transaction_state",
        order=5,
        title="transaction 状态隔离",
        principle="每个 transaction 独占 task state、WM、turn count 和 episode buffer。",
        acceptance="修改一个 transaction 的四类状态不会改变同 conversation 下的另一个 transaction。",
        layer="Transaction",
        risk="共享可变状态会造成任务串扰和不可重放结果。",
        gate_tests=(
            _test(
                f"{_ACCEPTANCE}::test_inv_05_transaction_runtime_state_is_isolated",
                "在同一场景内同时验证四类权威状态的对象与内容隔离。",
            ),
            _test(
                f"{_ACCEPTANCE}::test_inv_05_flush_drains_the_transaction_episode_owner",
                "flush 从 transaction 的权威 episode buffer 排空数据。",
            ),
        ),
        supporting_tests=(
            _test(
                "tests/runtime/test_think_life_core.py::test_transaction_wm_isolation",
                "WM 列表独立。",
            ),
            _test(
                "tests/runtime/test_think_life_core.py::test_transaction_task_state_isolation",
                "TaskState 对象独立。",
            ),
        ),
        known_gap="",
    ),
    InvariantSpec(
        invariant_id="INV-06",
        group_id="scene_memory",
        order=6,
        title="conversation 单一 Scene 时间线",
        principle="同一 conversation 只有一条跨 transaction 的追加式 Scene 时间线。",
        acceptance="不同 transaction 写入同一 conversation 时获得严格递增 seq，读取顺序保持写入顺序。",
        layer="Scene",
        risk="按 transaction 分裂 Scene 会破坏审计、flush 和上下文顺序。",
        gate_tests=(
            _test(
                f"{_ACCEPTANCE}::test_inv_06_scene_is_one_monotonic_conversation_timeline",
                "跨 transaction 写入仍共享单调序号。",
            ),
        ),
        supporting_tests=(
            _test(
                "tests/runtime/test_think_life_core.py::test_scene_chronological_cross_transaction",
                "跨 transaction 的 Scene 顺序和持久化。",
            ),
            _test(
                "tests/runtime/test_think_life_core.py::test_scene_append_after_restart_continues_seq",
                "进程重启后 Scene seq 继续递增。",
            ),
        ),
    ),
    InvariantSpec(
        invariant_id="INV-07",
        group_id="reasoning_execution",
        order=7,
        title="Thinking 只规划",
        principle="Thinking 不得直接调用 capability，只能输出结构化 decision。",
        acceptance="execute decision 从 Thinking 返回，但 fake tool/capability 没有被调用。",
        layer="Thinking",
        risk="Thinking 内联工具会形成第二个不可审计 ReAct loop。",
        gate_tests=(
            _test(
                "tests/agents/thinking/test_thinking_agent_plan_only.py::test_execute_decision_is_returned_without_inline_tool_work",
                "真实 ThinkingAgent 返回 execute decision 且没有内联执行。",
            ),
        ),
        supporting_tests=(
            _test(
                "tests/agents/thinking/test_thinking_agent_plan_only.py::test_event_emitter_reports_plan_only_phases_for_execute_decision",
                "Thinking trace 只报告规划阶段。",
            ),
        ),
    ),
    InvariantSpec(
        invariant_id="INV-08",
        group_id="reasoning_execution",
        order=8,
        title="每个 delegate 最多一个 capability",
        principle="一次 delegate 只能参数化并调用一个 capability。",
        acceptance="单次 delegate 只调用一次 invoke_tool_direct，并只产生一个 tool history 项。",
        layer="Execution",
        risk="多工具 delegate 会模糊反馈归属和副作用幂等键。",
        gate_tests=(
            _test(
                f"{_ACCEPTANCE}::test_inv_08_delegate_invokes_at_most_one_capability",
                "从 delegate 到 feedback 的完整适配路径仅发生一次 direct invoke。",
            ),
        ),
        supporting_tests=(
            _test(
                "tests/layers/execution/test_execution_direct_invoke.py::test_invoke_tool_direct_records_history",
                "direct invoke 生成可审计的一项工具历史。",
            ),
        ),
    ),
    InvariantSpec(
        invariant_id="INV-09",
        group_id="reasoning_execution",
        order=9,
        title="参数不足不误调工具",
        principle="缺少必要参数时返回澄清反馈，不得调用真实工具。",
        acceptance="param fill 返回 needs_clarification 后 invoke_tool_direct 调用次数为零，feedback 标记 tool_invoked=false。",
        layer="Execution",
        risk="猜测参数会产生错误邮件、日程或其他外部副作用。",
        gate_tests=(
            _test(
                "tests/runtime/test_think_life_param_gap.py::test_delegate_and_wait_submits_feedback_without_invoke_on_param_gap",
                "参数缺口短路真实工具，并生成结构化 feedback。",
            ),
        ),
        supporting_tests=(
            _test(
                "tests/runtime/test_think_life_tool_args.py::test_fill_tool_args_returns_clarify_outcome",
                "参数模型的澄清结果结构稳定。",
            ),
            _test(
                "tests/runtime/test_think_life_tool_args.py::test_fill_tool_args_returns_clarify_on_model_failure",
                "参数模型失败也安全降级为澄清。",
            ),
        ),
    ),
    InvariantSpec(
        invariant_id="INV-10",
        group_id="reasoning_execution",
        order=10,
        title="reply 走正常 capability 审计路径",
        principle="reply_to_user 必须作为普通 capability 经由 Execution 调用和审计。",
        acceptance="回复通过 invoke_tool_direct 调用 reply_to_user，并写入 Scene 与结构化 tool history。",
        layer="Execution / Scene",
        risk="旁路回复无法统一审计、去重或在恢复时判断是否已发送。",
        gate_tests=(
            _test(
                f"{_ACCEPTANCE}::test_inv_10_reply_uses_capability_and_audit_path",
                "reply 经 direct invoke，且输出进入 feedback 审计链。",
            ),
            _test(
                "tests/systems/test_reply_to_user_scene.py::test_reply_is_appended_to_conversation_segment",
                "reply capability 写入 conversation Scene。",
            ),
        ),
        known_gap="真实 reply_to_user 尚未调用 record_tool_use，ExecutionResult.tool_history 为空。",
    ),
    InvariantSpec(
        invariant_id="INV-11",
        group_id="reasoning_execution",
        order=11,
        title="回复成功不等于任务完成",
        principle="用户可见回复完成后，task completion 仍由更新后的 task state 决定。",
        acceptance="finalized reply 只进入 WAITING_EXECUTION 并提交 feedback，不把 USER_TASK 标记为 completed。",
        layer="Completion gate",
        risk="把传输完成当语义完成会提前关闭多步骤任务。",
        gate_tests=(
            _test(
                "tests/runtime/test_think_life_param_gap.py::test_finalized_reply_submits_feedback_instead_of_completing_task",
                "finalized reply 后 transaction 仍等待 feedback。",
            ),
        ),
        supporting_tests=(
            _test(
                "tests/runtime/test_think_life_silent_mode.py::test_finish_silent_plan_turn_keeps_transaction_open",
                "未显式完成的 silent turn 也保持 transaction 打开。",
            ),
        ),
    ),
    InvariantSpec(
        invariant_id="INV-12",
        group_id="outer_runtime",
        order=12,
        title="协作式安全抢占",
        principle="高优先级刺激只在安全边界抢占，并重新入队被中断刺激。",
        acceptance="抢占将 transaction 置为 suspended、保留 stimulus ID、增加有限 preempt count 并重新入队。",
        layer="Scheduling",
        risk="任意点中断会丢刺激或重复非幂等工具。",
        gate_tests=(
            _test(
                f"{_ACCEPTANCE}::test_inv_12_preemption_suspends_and_requeues_at_boundary",
                "安全边界的 suspend/requeue/checkpoint 元数据完整。",
            ),
        ),
        supporting_tests=(
            _test(
                "tests/runtime/test_think_life_core.py::test_inbox_priority_order",
                "外层 inbox 保持优先级。",
            ),
            _test(
                "tests/runtime/test_think_life_core.py::test_preempt_disabled_reuses_active_user_transaction",
                "关闭抢占时不拆分活跃任务。",
            ),
        ),
        known_gap="requeue 虽保留 transaction_id，但 attributor 会忽略它并创建新 transaction。",
    ),
    InvariantSpec(
        invariant_id="INV-13",
        group_id="recovery_idempotency",
        order=13,
        title="重复 feedback 与重放幂等",
        principle="重复 feedback、重复恢复和节点重放不得重复产生工具副作用或用户回复。",
        acceptance="同一 transaction_id + delegate_id 的 feedback 只能被消费一次；再次消费应拒绝或幂等返回。",
        layer="Recovery / Idempotency",
        risk="恢复重放可能重复发邮件、建日程或回复用户。",
        gate_tests=(
            _test(
                f"{_ACCEPTANCE}::test_inv_13_duplicate_feedback_is_consumed_once",
                "主动探测当前 runtime 对相同 feedback 的二次接受。",
            ),
        ),
        known_gap="当前进程内 runtime 尚未持久化 feedback 消费键；该用例会动态报告 xfail，修复后自然转为通过。",
    ),
    InvariantSpec(
        invariant_id="INV-14",
        group_id="scene_memory",
        order=14,
        title="flush 只在安全条件下推进",
        principle="存在未决刺激、在途执行或待回复结果时不得推进 Scene watermark。",
        acceptance="active drainer、pending reply 或半入队 stimulus 任一存在时 flush 均不可执行；失败可重试。",
        layer="Flush / Memory",
        risk="过早推进 watermark 会永久丢失尚未进入长期记忆的数据。",
        gate_tests=(
            _test(
                f"{_ACCEPTANCE}::test_inv_14_flush_gate_covers_every_unresolved_obligation",
                "drainer、queued、in-flight、pending reply 四类义务逐项阻断 flush。",
            ),
            _test(
                "tests/api/test_think_life_memory_capture.py::test_idle_flush_skips_active_drainer_even_after_deadline",
                "active drainer 阻止 idle flush。",
            ),
            _test(
                "tests/api/test_think_life_memory_capture.py::test_manual_flush_is_retryable_while_reply_is_pending",
                "pending reply 阻止 flush 且保持可重试。",
            ),
            _test(
                "tests/api/test_think_life_memory_capture.py::test_flush_cannot_interleave_half_admitted_stimulus",
                "stimulus admission 临界区阻止 flush 交错。",
            ),
        ),
        supporting_tests=(
            _test(
                "tests/api/test_think_life_memory_capture.py::test_successful_flush_disarms_idle_timer",
                "成功 flush 后才提交生命周期状态。",
            ),
            _test(
                "tests/runtime/test_think_life_core.py::test_scene_flush_watermark_persists_across_restart",
                "已提交 watermark 在重启后保持。",
            ),
        ),
    ),
)


_BY_ID = {item.invariant_id: item for item in INVARIANTS}
_GROUP_BY_ID = {item.group_id: item for item in INVARIANT_GROUPS}


def get_invariants(invariant_ids: Optional[Sequence[str]] = None) -> Tuple[InvariantSpec, ...]:
    """Resolve exact allowlisted invariant IDs in catalog order."""

    if invariant_ids is None:
        return INVARIANTS
    requested = {str(item or "").strip().upper() for item in invariant_ids}
    requested.discard("")
    unknown = sorted(requested - set(_BY_ID))
    if unknown:
        raise ValueError(f"unknown invariant id(s): {', '.join(unknown)}")
    if not requested:
        raise ValueError("at least one invariant id is required")
    return tuple(item for item in INVARIANTS if item.invariant_id in requested)


def validate_catalog() -> List[str]:
    """Return catalog defects; an empty list means the suite is executable."""

    errors: List[str] = []
    expected = [f"INV-{index:02d}" for index in range(1, 15)]
    actual = [item.invariant_id for item in INVARIANTS]
    if actual != expected:
        errors.append(f"expected invariant ids {expected}, got {actual}")
    expected_group_ids = [
        "outer_runtime",
        "transaction_state",
        "reasoning_execution",
        "scene_memory",
        "recovery_idempotency",
    ]
    actual_group_ids = [item.group_id for item in INVARIANT_GROUPS]
    if actual_group_ids != expected_group_ids:
        errors.append(
            f"expected invariant group ids {expected_group_ids}, "
            f"got {actual_group_ids}"
        )
    if [item.order for item in INVARIANT_GROUPS] != list(range(1, 6)):
        errors.append("invariant group order must be the contiguous range 1..5")
    grouped_ids = [
        invariant_id
        for group in INVARIANT_GROUPS
        for invariant_id in group.invariant_ids
    ]
    if len(grouped_ids) != len(set(grouped_ids)):
        errors.append("an invariant may belong to only one invariant group")
    if set(grouped_ids) != set(actual):
        errors.append("invariant groups must cover exactly the Phase-0 invariants")
    seen_nodeids: set[str] = set()
    for item in INVARIANTS:
        if not item.gate_tests:
            errors.append(f"{item.invariant_id} has no gate test")
        if item.order != int(item.invariant_id.split("-")[1]):
            errors.append(f"{item.invariant_id} has invalid catalog order")
        group = _GROUP_BY_ID.get(item.group_id)
        if group is None or item.invariant_id not in group.invariant_ids:
            errors.append(
                f"{item.invariant_id} has invalid group: {item.group_id}"
            )
        for test in (*item.gate_tests, *item.supporting_tests):
            if "::test_" not in test.nodeid:
                errors.append(f"{item.invariant_id} has invalid nodeid: {test.nodeid}")
            seen_nodeids.add(test.nodeid)
    if not seen_nodeids:
        errors.append("catalog contains no pytest nodes")
    return errors


def _profile_counts(
    invariants: Sequence[InvariantSpec],
) -> Dict[str, Dict[str, int]]:
    invariant_count = len(invariants)
    gate_count = len(unique_nodeids(invariants, profile="gate"))
    full_count = len(unique_nodeids(invariants, profile="full"))
    return {
        "gate": {
            "invariant_count": invariant_count,
            "case_count": gate_count,
            "gate_case_count": gate_count,
            "supporting_case_count": 0,
        },
        "full": {
            "invariant_count": invariant_count,
            "case_count": full_count,
            "gate_case_count": gate_count,
            "supporting_case_count": full_count - gate_count,
        },
    }


def _group_payload(group: InvariantGroupSpec) -> Dict[str, Any]:
    invariants = tuple(_BY_ID[item] for item in group.invariant_ids)
    payload = group.to_dict()
    payload["counts"] = {
        "invariant_count": len(invariants),
        "gate_case_count": len(unique_nodeids(invariants, profile="gate")),
        "full_case_count": len(unique_nodeids(invariants, profile="full")),
        "known_gap_count": sum(
            item.known_gap is not None for item in invariants
        ),
    }
    return payload


def catalog_payload() -> Dict[str, Any]:
    errors = validate_catalog()
    return {
        "suite": PHASE0_SUITE,
        "title": "M-Agent Runtime Phase-0 语义验收",
        "description": "LangGraph 迁移前的 14 项运行时语义基线。",
        "profiles": {
            "gate": "每项不变量的最小阻断测试。",
            "full": "阻断测试加现有回归证据。",
        },
        "profile_counts": _profile_counts(INVARIANTS),
        "coverage": {
            "required": 14,
            "specified": len(INVARIANTS),
            "complete": not errors,
            "errors": errors,
        },
        "safety": {
            "subprocess_only": True,
            "allowlisted_tests_only": True,
            "single_concurrency": True,
            "real_external_side_effects": False,
        },
        "groups": [_group_payload(item) for item in INVARIANT_GROUPS],
        "invariants": [item.to_dict() for item in INVARIANTS],
    }
