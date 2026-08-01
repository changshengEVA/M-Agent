"""Runtime-independent P1 scenario/variant contracts and observations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import (
    CaseResult,
    ScenarioResult,
    ScenarioVariantResult,
    TestRef,
)


SCENARIO_DOMAINS = frozenset({"TX", "SP", "AT"})
SCENARIO_LAYERS = frozenset(
    {"core", "robustness", "matcher_evaluation", "supporting", "poc"}
)
SCENARIO_LAYER_ALIASES = {
    "matcher": "matcher_evaluation",
}
RUNTIME_IDS = ("think_life_v1", "langgraph_v1")
RUNTIME_AVAILABILITIES = frozenset(
    {"executable", "not_implemented", "not_covered", "future"}
)


@dataclass(frozen=True)
class RuntimeBindingSpec:
    """How one scenario variant is represented for one Runtime backend."""

    runtime_id: str
    availability: str
    tests: Tuple[TestRef, ...] = ()
    known_gap: Optional[str] = None
    known_gap_keys: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "availability": self.availability,
            "known_gap": self.known_gap,
            "known_gap_keys": list(self.known_gap_keys),
            "tests": [item.to_dict() for item in self.tests],
        }


@dataclass(frozen=True)
class ScenarioVariantSpec:
    """One independently executable layer/variant below a parent scenario."""

    parent_scenario_id: str
    variant_id: str
    title: str
    layer: str
    bindings: Tuple[RuntimeBindingSpec, ...]

    def binding_for(self, runtime_id: str) -> RuntimeBindingSpec:
        requested = str(runtime_id or "").strip().lower()
        for binding in self.bindings:
            if binding.runtime_id == requested:
                return binding
        raise ValueError(
            f"variant {self.variant_id} has no Runtime binding for {requested}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.variant_id,
            "variant_id": self.variant_id,
            "parent_scenario_id": self.parent_scenario_id,
            "title": self.title,
            "layer": self.layer,
            "bindings": [item.to_dict() for item in self.bindings],
        }


@dataclass(frozen=True)
class ScenarioSpec:
    """A stable TX/SP/AT semantic scenario independent of Runtime classes."""

    scenario_id: str
    domain: str
    order: int
    title: str
    principle: str
    acceptance: str
    variants: Tuple[ScenarioVariantSpec, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.scenario_id,
            "scenario_id": self.scenario_id,
            "domain": self.domain,
            "order": self.order,
            "title": self.title,
            "principle": self.principle,
            "acceptance": self.acceptance,
            "variants": [item.to_dict() for item in self.variants],
        }


@dataclass(frozen=True)
class ScenarioObservation:
    """Normalized, serializable evidence returned by a Runtime adapter."""

    scenario_id: str
    variant_id: str
    runtime_id: str
    data: Dict[str, Any]
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "variant_id": self.variant_id,
            "runtime_id": self.runtime_id,
            "data": dict(self.data),
        }

    def attach(self, request: Any) -> None:
        request.node.user_properties.append(
            (
                "semantic_observation",
                json.dumps(self.to_dict(), ensure_ascii=False),
            )
        )

    @property
    def checks(self) -> List[Dict[str, Any]]:
        raw = self.data.get("checks", [])
        if not isinstance(raw, list):
            return []
        return [dict(item) for item in raw if isinstance(item, dict)]

    @property
    def failed_checks(self) -> List[Dict[str, Any]]:
        return [
            item
            for item in self.checks
            if item.get("passed") is not True
        ]

    @property
    def satisfied(self) -> bool:
        return bool(self.checks) and not self.failed_checks

    def assert_satisfied(self) -> None:
        """Fail with the normalized evidence instead of a Runtime-specific stack."""

        assert self.checks, (
            f"{self.scenario_id}/{self.variant_id} produced no contract checks"
        )
        assert not self.failed_checks, json.dumps(
            {
                "scenario_id": self.scenario_id,
                "variant_id": self.variant_id,
                "runtime_id": self.runtime_id,
                "failed_checks": self.failed_checks,
                "facts": self.data.get("facts", {}),
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def validate_scenario_specs(
    scenarios: Sequence[ScenarioSpec],
    *,
    expected_ids: Sequence[str],
) -> List[str]:
    """Return structural contract defects without consulting Runtime internals."""

    errors: List[str] = []
    actual_ids = [item.scenario_id for item in scenarios]
    if actual_ids != list(expected_ids):
        errors.append(f"expected scenario ids {list(expected_ids)}, got {actual_ids}")
    seen_variants: set[str] = set()
    for scenario in scenarios:
        if scenario.domain not in SCENARIO_DOMAINS:
            errors.append(f"{scenario.scenario_id} has invalid domain {scenario.domain}")
        if not scenario.variants:
            errors.append(f"{scenario.scenario_id} has no variants")
        for variant in scenario.variants:
            if variant.parent_scenario_id != scenario.scenario_id:
                errors.append(
                    f"{variant.variant_id} parent is {variant.parent_scenario_id}, "
                    f"expected {scenario.scenario_id}"
                )
            if variant.variant_id in seen_variants:
                errors.append(f"duplicate variant id: {variant.variant_id}")
            seen_variants.add(variant.variant_id)
            if variant.layer not in SCENARIO_LAYERS:
                errors.append(
                    f"{variant.variant_id} has invalid layer {variant.layer}"
                )
            binding_ids = [item.runtime_id for item in variant.bindings]
            if binding_ids != list(RUNTIME_IDS):
                errors.append(
                    f"{variant.variant_id} Runtime bindings must be {list(RUNTIME_IDS)}"
                )
            for binding in variant.bindings:
                if binding.availability not in RUNTIME_AVAILABILITIES:
                    errors.append(
                        f"{variant.variant_id}/{binding.runtime_id} has invalid "
                        f"availability {binding.availability}"
                    )
                if binding.availability == "executable" and not binding.tests:
                    errors.append(
                        f"{variant.variant_id}/{binding.runtime_id} is executable "
                        "without an allowlisted test"
                    )
                if binding.availability != "executable" and binding.tests:
                    errors.append(
                        f"{variant.variant_id}/{binding.runtime_id} is "
                        f"{binding.availability} but has tests"
                    )
                if binding.known_gap and not binding.known_gap_keys:
                    errors.append(
                        f"{variant.variant_id}/{binding.runtime_id} has a "
                        "known gap reason without registered check keys"
                    )
                if binding.known_gap_keys and not binding.known_gap:
                    errors.append(
                        f"{variant.variant_id}/{binding.runtime_id} has "
                        "known gap check keys without a reason"
                    )
                for test in binding.tests:
                    if "::test_" not in test.nodeid:
                        errors.append(
                            f"{variant.variant_id}/{binding.runtime_id} has invalid "
                            f"nodeid {test.nodeid}"
                        )
    return errors


def selected_variants(
    scenarios: Iterable[ScenarioSpec],
    *,
    layers: Sequence[str],
) -> List[Tuple[ScenarioSpec, ScenarioVariantSpec]]:
    requested = {
        SCENARIO_LAYER_ALIASES.get(
            str(item or "").strip().lower(),
            str(item or "").strip().lower(),
        )
        for item in layers
    }
    requested.discard("")
    unknown = sorted(requested - SCENARIO_LAYERS)
    if unknown:
        raise ValueError(f"unknown scenario layer(s): {', '.join(unknown)}")
    if not requested:
        raise ValueError("at least one scenario layer is required")
    return [
        (scenario, variant)
        for scenario in scenarios
        for variant in scenario.variants
        if variant.layer in requested
    ]


def annotate_scenario_cases(
    cases: Iterable[CaseResult],
    selections: Sequence[Tuple[ScenarioSpec, ScenarioVariantSpec]],
    *,
    runtime_id: str,
) -> List[CaseResult]:
    """Attach scenario identity and adapter observations to pytest cases."""

    refs_by_nodeid: Dict[str, List[Dict[str, str]]] = {}
    gap_by_nodeid: Dict[str, str] = {}
    for scenario, variant in selections:
        binding = variant.binding_for(runtime_id)
        for ref in binding.tests:
            refs_by_nodeid.setdefault(ref.nodeid, []).append(
                {
                    "scenario_id": scenario.scenario_id,
                    "variant_id": variant.variant_id,
                    "runtime_id": runtime_id,
                    "layer": variant.layer,
                    "proves": ref.proves,
                }
            )
            if binding.known_gap:
                gap_by_nodeid[ref.nodeid] = binding.known_gap

    annotated: List[CaseResult] = []
    for case in cases:
        refs = refs_by_nodeid.get(case.nodeid, [])
        primary = refs[0] if refs else {}
        known_gap = (
            gap_by_nodeid.get(case.nodeid)
            if case.outcome == "xfailed"
            else case.known_gap
        )
        annotated.append(
            CaseResult(
                nodeid=case.nodeid,
                outcome=case.outcome,
                duration_seconds=case.duration_seconds,
                message=case.message,
                trace=list(case.trace),
                evidence="scenario_contract",
                proves=str(primary.get("proves", case.proves)),
                evidence_refs=[dict(item) for item in refs],
                known_gap=known_gap,
                known_gap_reasons=(
                    [
                        {
                            "scenario_id": str(primary.get("scenario_id", "")),
                            "variant_id": str(primary.get("variant_id", "")),
                            "reason": known_gap,
                        }
                    ]
                    if known_gap
                    else []
                ),
                scenario_id=str(primary.get("scenario_id", case.scenario_id)),
                variant_id=str(primary.get("variant_id", case.variant_id)),
                runtime_id=str(primary.get("runtime_id", runtime_id)),
                scenario_layer=str(primary.get("layer", case.scenario_layer)),
                observation=dict(case.observation),
            )
        )
    return annotated


def aggregate_variant(
    scenario: ScenarioSpec,
    variant: ScenarioVariantSpec,
    *,
    runtime_id: str,
    case_by_nodeid: Dict[str, CaseResult],
    run_status: str,
) -> ScenarioVariantResult:
    binding = variant.binding_for(runtime_id)
    if binding.availability != "executable":
        return ScenarioVariantResult(
            parent_scenario_id=scenario.scenario_id,
            variant_id=variant.variant_id,
            title=variant.title,
            layer=variant.layer,
            runtime_id=runtime_id,
            availability=binding.availability,
            status=binding.availability,
            known_gap=binding.known_gap,
        )

    cases = [
        case_by_nodeid.get(
            ref.nodeid,
            CaseResult(
                nodeid=ref.nodeid,
                outcome="not_run",
                message="The allowlisted scenario test did not produce a report.",
                scenario_id=scenario.scenario_id,
                variant_id=variant.variant_id,
                runtime_id=runtime_id,
                scenario_layer=variant.layer,
                proves=ref.proves,
            ),
        )
        for ref in binding.tests
    ]
    outcomes = {item.outcome for item in cases}
    if run_status in {"cancelled", "timeout"} and "not_run" in outcomes:
        status = run_status
    elif outcomes & {"failed", "error", "xpassed", "not_run"}:
        status = "failed"
    elif "xfailed" in outcomes:
        status = "known_gap"
    elif outcomes == {"skipped"}:
        status = "not_covered"
    else:
        status = "passed"
    return ScenarioVariantResult(
        parent_scenario_id=scenario.scenario_id,
        variant_id=variant.variant_id,
        title=variant.title,
        layer=variant.layer,
        runtime_id=runtime_id,
        availability=binding.availability,
        status=status,
        cases=cases,
        known_gap=binding.known_gap,
    )


def aggregate_scenarios(
    selections: Sequence[Tuple[ScenarioSpec, ScenarioVariantSpec]],
    *,
    runtime_id: str,
    cases: Sequence[CaseResult],
    run_status: str,
) -> List[ScenarioResult]:
    case_by_nodeid = {item.nodeid: item for item in cases}
    variants_by_scenario: Dict[str, List[ScenarioVariantResult]] = {}
    specs_by_id: Dict[str, ScenarioSpec] = {}
    for scenario, variant in selections:
        specs_by_id[scenario.scenario_id] = scenario
        variants_by_scenario.setdefault(scenario.scenario_id, []).append(
            aggregate_variant(
                scenario,
                variant,
                runtime_id=runtime_id,
                case_by_nodeid=case_by_nodeid,
                run_status=run_status,
            )
        )

    results: List[ScenarioResult] = []
    for scenario_id, variants in variants_by_scenario.items():
        statuses = {item.status for item in variants}
        if statuses & {"failed", "error", "timeout", "cancelled"}:
            status = next(
                item
                for item in ("failed", "error", "timeout", "cancelled")
                if item in statuses
            )
        elif "not_implemented" in statuses:
            status = "not_implemented"
        elif "not_covered" in statuses:
            status = "not_covered"
        elif "known_gap" in statuses:
            status = "known_gap"
        elif statuses == {"future"}:
            status = "future"
        else:
            status = "passed"
        spec = specs_by_id[scenario_id]
        results.append(
            ScenarioResult(
                scenario_id=scenario_id,
                title=spec.title,
                domain=spec.domain,
                order=spec.order,
                status=status,
                variants=variants,
            )
        )
    return results
