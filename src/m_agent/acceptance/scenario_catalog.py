"""Complete P1 TX/SP/AT catalog built on Runtime-independent contracts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .catalog import INVARIANTS, PHASE0_SUITE
from .models import TestRef
from .scenario_contract import (
    RUNTIME_IDS,
    RuntimeBindingSpec,
    ScenarioSpec,
    ScenarioVariantSpec,
    validate_scenario_specs,
)


P1_CONTRACT_SUITE = "p1-contract"

_SCENARIO_TITLES: Tuple[Tuple[str, str], ...] = (
    ("TX-01", "新 Transaction 的正常运行"),
    ("TX-02", "UI Pause 与原 Transaction 的后续恢复"),
    ("TX-03", "Complete 在 Flush 前重新激活"),
    ("TX-04", "Archive 只能由 Flush 产生"),
    ("TX-05", "通过合法显式动作按 ID 恢复 Archive"),
    ("TX-06", "Scheduled Plan 使用原 Transaction"),
    ("TX-07", "Feedback 因果校验与 Expected Discard"),
    ("TX-08", "多个 Transaction 状态隔离"),
    ("TX-09", "一个 Conversation 只有一条 Scene"),
    ("TX-10", "正常 Flush 生命周期"),
    ("SP-01", "接纳多种合法刺激并拒绝无效 Feedback"),
    ("SP-02", "按优先级消费"),
    ("SP-03", "相同优先级按接纳顺序 FIFO"),
    ("SP-04", "严格逐个消费"),
    ("SP-05", "多生产者并发放入"),
    ("SP-06", "消费者退出窗口到达新刺激"),
    ("SP-07", "下一次选择时优先取出高优先级刺激"),
    ("SP-08", "Conversation 之间相互隔离"),
    ("AT-01", "有来源刺激先校验，合法者绕过语义匹配"),
    ("AT-02", "候选集合正确"),
    ("AT-03", "匹配 Pause Transaction"),
    ("AT-04", "匹配未 Flush 的 Complete Transaction"),
    ("AT-05", "无匹配时新建"),
    ("AT-06", "语义模糊时新建"),
    ("AT-07", "不能跨 Conversation 匹配"),
    ("AT-08", "归因结果在后续流程中保持稳定"),
    ("AT-09", "匹配算法的确定性结构测试"),
    ("AT-10", "真实语义匹配评测"),
)
EXPECTED_SCENARIO_IDS = tuple(item[0] for item in _SCENARIO_TITLES)

_CORE_TEST_FUNCTIONS = {
    "TX-01": "test_tx_01_new_transaction_normal_flow",
    "TX-02": "test_tx_02_ui_pause_and_restore",
    "TX-03": "test_tx_03_complete_before_flush_reactivation",
    "TX-04": "test_tx_04_archive_only_by_flush",
    "TX-05": "test_tx_05_explicit_archive_restore",
    "TX-06": "test_tx_06_scheduled_plan_uses_original_transaction",
    "TX-07": "test_tx_07_feedback_causal_validation",
    "TX-08": "test_tx_08_transaction_isolation_and_delete",
    "TX-09": "test_tx_09_single_conversation_scene",
    "TX-10": "test_tx_10_flush_lifecycle",
    "SP-01": "test_sp_01_admission_and_preconsume_gate",
    "SP-02": "test_sp_02_priority_order",
    "SP-03": "test_sp_03_atomic_acceptance_fifo",
    "SP-04": "test_sp_04_serial_thinking_async_effect",
    "SP-05": "test_sp_05_concurrent_producers",
    "SP-06": "test_sp_06_worker_exit_wakeup",
    "SP-07": "test_sp_07_next_selection_priority",
    "SP-08": "test_sp_08_conversation_isolation",
    "AT-01": "test_at_01_sourced_stimulus_validation",
    "AT-02": "test_at_02_candidate_scope",
    "AT-03": "test_at_03_match_paused_transaction",
    "AT-04": "test_at_04_match_unflushed_complete",
    "AT-05": "test_at_05_no_match_creates_transaction",
    "AT-06": "test_at_06_ambiguous_match_creates_transaction",
    "AT-07": "test_at_07_no_cross_conversation_match",
    "AT-08": "test_at_08_attribution_stays_stable",
    "AT-09": "test_at_09_deterministic_matcher_contract",
    "AT-10": "test_at_10_real_matcher_evaluation",
}

_ROBUSTNESS_VARIANTS: Dict[
    str,
    Tuple[Tuple[str, str, str], ...],
] = {
    "TX-01": (
        (
            "TX-01/uow_replay_and_result",
            "UoW 重放返回原完整结果",
            "test_tx_01_uow_replay_and_result",
        ),
    ),
    "TX-06": (
        (
            "TX-06/schedule_atomicity_and_control_recovery",
            "Schedule 原子控制与恢复",
            "test_tx_06_schedule_atomicity_and_control_recovery",
        ),
    ),
    "TX-07": (
        (
            "TX-07/effect_result_feedback_outbox",
            "Effect result 到 Feedback ingress outbox",
            "test_tx_07_effect_result_feedback_outbox",
        ),
        (
            "TX-07/capability_delivery_guarantees",
            "Capability delivery guarantee 语义",
            "test_tx_07_capability_delivery_guarantees",
        ),
    ),
    "TX-10": (
        (
            "TX-10/flush_fault_recovery",
            "Flush 故障恢复",
            "test_tx_10_flush_fault_recovery",
        ),
    ),
    "SP-01": (
        (
            "SP-01/durable_ingress_restart",
            "Durable ingress 与重启",
            "test_sp_01_durable_ingress_restart",
        ),
        (
            "SP-01/lease_takeover_fencing",
            "Lease takeover 与 fencing",
            "test_sp_01_lease_takeover_fencing",
        ),
    ),
}

_P6_POC_VARIANTS: Dict[
    str,
    Tuple[Tuple[str, str, str], ...],
] = {
    "TX-01": (
        (
            "TX-01/poc_checkpoint_resume",
            "PoC pause→continue 恢复切片",
            "test_tx_01_poc_checkpoint_resume",
        ),
        (
            "TX-01/poc_sequential_fake_effects",
            "PoC 顺序 fake capability 与 reply",
            "test_tx_01_poc_sequential_fake_effects",
        ),
        (
            "TX-01/poc_stale_feedback_after_restore",
            "PoC 新 activation 后拒绝旧 Feedback",
            "test_tx_01_poc_stale_feedback_after_restore",
        ),
    ),
}

_LANGGRAPH_P6_EXECUTABLE = frozenset(
    {
        "TX-01/core",
        "TX-01/uow_replay_and_result",
        "TX-01/poc_checkpoint_resume",
        "TX-01/poc_sequential_fake_effects",
        "TX-01/poc_stale_feedback_after_restore",
    }
)

# P8: full matrix — all variants executable for langgraph_v1.
_LANGGRAPH_P8_EXECUTABLE = True

_KNOWN_GAPS: Dict[str, str] = {}

_ROBUSTNESS_REQUIREMENT_MAP: Tuple[Tuple[str, str, str], ...] = (
    (
        "R8.1.transaction_restart",
        "pause/archive/complete transaction、WM、任务状态、activation、delegate 与 revision 重启恢复",
        "TX-01/uow_replay_and_result",
    ),
    (
        "R8.1.schedule_restart",
        "schedule/run/delivery/claimed activation 与绑定重启恢复",
        "TX-06/schedule_atomicity_and_control_recovery",
    ),
    (
        "R8.1.inbox_restart",
        "ready/claimed stimulus、accepted_seq 与 disposition 重启恢复",
        "SP-01/durable_ingress_restart",
    ),
    (
        "R8.1.preconsume_restart",
        "来源失效 Feedback 重启后保持幂等 preconsume Expected Discard",
        "SP-01/durable_ingress_restart",
    ),
    (
        "R8.1.flush_scene_restart",
        "Scene 顺序、flush payload/watermark 与未完成物化重启恢复",
        "TX-10/flush_fault_recovery",
    ),
    (
        "R8.2.transition_replay",
        "相同 transition ID/digest 返回第一次完整结果，冲突 digest 明确拒绝",
        "TX-01/uow_replay_and_result",
    ),
    (
        "R8.2.feedback_dedup",
        "Feedback ingress、delegate 消费和 transaction 推进唯一",
        "TX-07/effect_result_feedback_outbox",
    ),
    (
        "R8.2.schedule_generation",
        "同 run/delivery generation 重放不重复 stimulus、activation 或执行",
        "TX-06/schedule_atomicity_and_control_recovery",
    ),
    (
        "R8.2.effect_result_outbox",
        "effect intent/result 与 Feedback outbox 只保留一个规范事实",
        "TX-07/effect_result_feedback_outbox",
    ),
    (
        "R8.2.delivery_guarantees",
        "三种 capability delivery guarantee 的 attempts、ack、uncertain 与可见效果范围",
        "TX-07/capability_delivery_guarantees",
    ),
    (
        "R8.2.flush_scene_idempotency",
        "Scene append/flush 重放不重复边界、watermark、archive 或物化",
        "TX-10/flush_fault_recovery",
    ),
    (
        "R8.3.uow_commit_faults",
        "transaction、Scene、checkpoint 与 effect intent 提交点故障",
        "TX-01/uow_replay_and_result",
    ),
    (
        "R8.3.schedule_half_commit",
        "schedule register/claim/complete/Pause/Delete 配对 mutation 半提交故障",
        "TX-06/schedule_atomicity_and_control_recovery",
    ),
    (
        "R8.3.result_relay_faults",
        "effect result/outbox 与 relay/Inbox admission 边界故障",
        "TX-07/effect_result_feedback_outbox",
    ),
    (
        "R8.3.capability_ack_loss",
        "capability 接收请求后 executor 失联的 guarantee 对应结果",
        "TX-07/capability_delivery_guarantees",
    ),
    (
        "R8.3.flush_commit_materialize",
        "Flush commit 前回滚及 commit 后物化恢复",
        "TX-10/flush_fault_recovery",
    ),
    (
        "R8.3.claim_crash",
        "Inbox claim 后、最终 disposition/transition 前崩溃恢复",
        "SP-01/durable_ingress_restart",
    ),
    (
        "R8.3.stale_claimant",
        "lease takeover 后旧 claimant StaleClaim 零 mutation 与已提交重放",
        "SP-01/lease_takeover_fencing",
    ),
    (
        "R8.4.feedback_concurrency",
        "并发重复 Feedback 最多消费和推进一次",
        "TX-07/effect_result_feedback_outbox",
    ),
    (
        "R8.4.schedule_concurrency",
        "Schedule 到期与 activation/control 并发不产生第二 activation",
        "TX-06/schedule_atomicity_and_control_recovery",
    ),
    (
        "R8.4.flush_activation",
        "Flush 与 transaction 激活/revision 竞态安全",
        "TX-10/flush_fault_recovery",
    ),
    (
        "R8.4.consumer_concurrency",
        "多生产者、单 conversation consumer 与 lease takeover fencing",
        "SP-01/lease_takeover_fencing",
    ),
    (
        "R8.5.transaction_schema",
        "旧 transaction/activation/schema 与 runtime engine 版本显式迁移或拒绝",
        "TX-01/uow_replay_and_result",
    ),
    (
        "R8.5.schedule_schema",
        "Schedule run/delivery schema 升级保持稳定绑定",
        "TX-06/schedule_atomicity_and_control_recovery",
    ),
    (
        "R8.5.inbox_schema",
        "Inbox ingress/claim/disposition schema 升级保持规范身份",
        "SP-01/durable_ingress_restart",
    ),
)

def known_gap_key(variant_id: str) -> str:
    return (
        str(variant_id)
        .lower()
        .replace("/", ".")
        .replace("-", "_")
    )


def registered_known_gap_key(variant_id: str) -> str:
    """Return the stable key only while the variant is registered as a gap."""

    normalized = str(variant_id or "").strip()
    return known_gap_key(normalized) if normalized in _KNOWN_GAPS else ""


def _test_file(scenario_id: str) -> str:
    domain = scenario_id.split("-", 1)[0].lower()
    return f"tests/acceptance/scenarios/test_{domain}_scenarios.py"


def _test_ref(
    scenario_id: str,
    function_name: str,
    *,
    variant_id: str,
) -> TestRef:
    return TestRef(
        nodeid=f"{_test_file(scenario_id)}::{function_name}",
        proves=(
            f"{variant_id} 只通过 Runtime Harness 执行领域命令，并以 normalized "
            "observation/trace 对目标断言逐项给出证据。"
        ),
    )


def _binding(
    runtime_id: str,
    *,
    variant_id: str,
    test: TestRef,
) -> RuntimeBindingSpec:
    return RuntimeBindingSpec(
        runtime_id=runtime_id,
        availability="executable",
        tests=(test,),
    )


def _core_variant(scenario_id: str, title: str) -> ScenarioVariantSpec:
    layer = (
        "matcher_evaluation"
        if scenario_id == "AT-10"
        else "core"
    )
    variant_id = f"{scenario_id}/{layer}"
    ref = _test_ref(
        scenario_id,
        _CORE_TEST_FUNCTIONS[scenario_id],
        variant_id=variant_id,
    )
    return ScenarioVariantSpec(
        parent_scenario_id=scenario_id,
        variant_id=variant_id,
        title=title,
        layer=layer,
        bindings=tuple(
            _binding(
                runtime_id,
                variant_id=variant_id,
                test=ref,
            )
            for runtime_id in RUNTIME_IDS
        ),
    )


def _robustness_variants(
    scenario_id: str,
) -> Tuple[ScenarioVariantSpec, ...]:
    variants = []
    for variant_id, title, function_name in _ROBUSTNESS_VARIANTS.get(
        scenario_id,
        (),
    ):
        ref = _test_ref(
            scenario_id,
            function_name,
            variant_id=variant_id,
        )
        variants.append(
            ScenarioVariantSpec(
                parent_scenario_id=scenario_id,
                variant_id=variant_id,
                title=title,
                layer="robustness",
                bindings=tuple(
                    _binding(
                        runtime_id,
                        variant_id=variant_id,
                        test=ref,
                    )
                    for runtime_id in RUNTIME_IDS
                ),
            )
        )
    return tuple(variants)


def _poc_variants(
    scenario_id: str,
) -> Tuple[ScenarioVariantSpec, ...]:
    variants = []
    for variant_id, title, function_name in _P6_POC_VARIANTS.get(
        scenario_id,
        (),
    ):
        ref = _test_ref(
            scenario_id,
            function_name,
            variant_id=variant_id,
        )
        variants.append(
            ScenarioVariantSpec(
                parent_scenario_id=scenario_id,
                variant_id=variant_id,
                title=title,
                layer="poc",
                bindings=tuple(
                    _binding(
                        runtime_id,
                        variant_id=variant_id,
                        test=ref,
                    )
                    for runtime_id in RUNTIME_IDS
                ),
            )
        )
    return tuple(variants)


SCENARIOS: Tuple[ScenarioSpec, ...] = tuple(
    ScenarioSpec(
        scenario_id=scenario_id,
        domain=scenario_id.split("-", 1)[0],
        order=order,
        title=title,
        principle=(
            "场景只使用领域输入和领域观察值，不依赖具体 Runtime 的私有类、方法或节点。"
        ),
        acceptance=(
            "详细输入与断言以 runtime-migration-semantic-test-spec.zh-CN.md "
            f"中的 {scenario_id} 为准。"
        ),
        variants=(
            _core_variant(scenario_id, title),
            *_robustness_variants(scenario_id),
            *_poc_variants(scenario_id),
        ),
    )
    for order, (scenario_id, title) in enumerate(
        _SCENARIO_TITLES,
        start=1,
    )
)

_BY_ID = {item.scenario_id: item for item in SCENARIOS}


def get_scenarios(
    scenario_ids: Optional[Sequence[str]] = None,
) -> Tuple[ScenarioSpec, ...]:
    if scenario_ids is None:
        return SCENARIOS
    requested = {
        str(item or "").strip().upper()
        for item in scenario_ids
    }
    requested.discard("")
    unknown = sorted(requested - set(_BY_ID))
    if unknown:
        raise ValueError(f"unknown scenario id(s): {', '.join(unknown)}")
    if not requested:
        raise ValueError("at least one scenario id is required")
    return tuple(
        item
        for item in SCENARIOS
        if item.scenario_id in requested
    )


def validate_contract_catalog() -> List[str]:
    errors = validate_scenario_specs(
        SCENARIOS,
        expected_ids=EXPECTED_SCENARIO_IDS,
    )
    variants = [
        variant
        for scenario in SCENARIOS
        for variant in scenario.variants
    ]
    counts = {
        layer: sum(variant.layer == layer for variant in variants)
        for layer in ("core", "robustness", "matcher_evaluation", "poc")
    }
    expected = {
        "core": 27,
        "robustness": 7,
        "matcher_evaluation": 1,
        "poc": 3,
    }
    for layer, count in counts.items():
        if count != expected[layer]:
            errors.append(
                f"expected {expected[layer]} {layer} variants, got {count}"
            )
    variant_ids = {item.variant_id for item in variants}
    unknown_gap_variants = sorted(set(_KNOWN_GAPS) - variant_ids)
    for variant_id in unknown_gap_variants:
        errors.append(f"known gap maps to unknown variant {variant_id}")
    gap_keys = [
        known_gap_key(variant_id)
        for variant_id in _KNOWN_GAPS
    ]
    if len(gap_keys) != len(set(gap_keys)):
        errors.append("known gap check keys must be unique")
    robustness_ids = {
        item.variant_id
        for item in variants
        if item.layer == "robustness"
    }
    requirement_ids: set[str] = set()
    for requirement_id, _title, variant_id in _ROBUSTNESS_REQUIREMENT_MAP:
        if requirement_id in requirement_ids:
            errors.append(
                f"duplicate robustness requirement id: {requirement_id}"
            )
        requirement_ids.add(requirement_id)
        if variant_id not in variant_ids:
            errors.append(
                f"{requirement_id} maps to unknown variant {variant_id}"
            )
        elif variant_id not in robustness_ids:
            errors.append(
                f"{requirement_id} maps to non-robustness variant {variant_id}"
            )
    return errors


def contract_catalog_payload() -> Dict[str, Any]:
    errors = validate_contract_catalog()
    variants = [
        variant
        for scenario in SCENARIOS
        for variant in scenario.variants
    ]
    by_layer = {
        layer: [
            item
            for item in variants
            if item.layer == layer
        ]
        for layer in ("core", "robustness", "matcher_evaluation", "poc")
    }
    executable_by_runtime = {
        runtime_id: sum(
            variant.binding_for(runtime_id).availability == "executable"
            for variant in variants
        )
        for runtime_id in RUNTIME_IDS
    }
    executable_by_layer_and_runtime = {
        layer: {
            runtime_id: sum(
                variant.binding_for(runtime_id).availability == "executable"
                for variant in layer_variants
            )
            for runtime_id in RUNTIME_IDS
        }
        for layer, layer_variants in by_layer.items()
    }
    langgraph_ready = all(
        variant.binding_for("langgraph_v1").availability == "executable"
        for variant in variants
    )
    return {
        "suite": P1_CONTRACT_SUITE,
        "title": "M-Agent LangGraph Runtime 语义契约",
        "description": (
            "TX/SP/AT × 测试层级；旧 INV 目录仅作为 Supporting Tests 保留。"
        ),
        "runtime_ids": list(RUNTIME_IDS),
        "layers": [
            "core",
            "robustness",
            "matcher_evaluation",
            "poc",
            "supporting",
        ],
        "coverage": {
            "required_scenarios": len(EXPECTED_SCENARIO_IDS),
            "specified_scenarios": len(SCENARIOS),
            "required_core": 27,
            "specified_core": len(by_layer["core"]),
            "required_matcher": 1,
            "specified_matcher": len(by_layer["matcher_evaluation"]),
            "required_robustness": 7,
            "specified_robustness": len(by_layer["robustness"]),
            "mapped_robustness_requirements": len(
                _ROBUSTNESS_REQUIREMENT_MAP
            ),
            "catalog_complete": not errors,
            "p1_exit_ready": (
                not errors
                and langgraph_ready
            ),
            "errors": errors,
        },
        "execution": {
            "executable_variants_by_runtime": executable_by_runtime,
            "executable_by_layer_and_runtime": (
                executable_by_layer_and_runtime
            ),
            "registered_known_gaps_by_runtime": {
                runtime_id: sum(
                    bool(variant.binding_for(runtime_id).known_gap)
                    for variant in variants
                )
                for runtime_id in RUNTIME_IDS
            },
        },
        "robustness_manifest": [
            {
                "requirement_id": requirement_id,
                "title": title,
                "parent_scenario_id": variant_id.split("/", 1)[0],
                "variant_id": variant_id,
                "layer": "robustness",
                "langgraph_v1": {
                    "availability": "executable",
                    "registered_status": "implemented",
                },
            }
            for requirement_id, title, variant_id
            in _ROBUSTNESS_REQUIREMENT_MAP
        ],
        "legacy_supporting": {
            "suite": PHASE0_SUITE,
            "role": "supporting",
            "invariant_ids": [
                item.invariant_id
                for item in INVARIANTS
            ],
        },
        "scenarios": [
            item.to_dict()
            for item in SCENARIOS
        ],
    }
