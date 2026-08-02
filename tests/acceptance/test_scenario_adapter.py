from __future__ import annotations

from m_agent.acceptance.adapters import LangGraphV1Adapter
from m_agent.acceptance.adapters.base import REQUIRED_HARNESS_OPERATIONS
from m_agent.acceptance.adapters.langgraph_harness import (
    LangGraphV1Harness,
)
from m_agent.acceptance.scenario_catalog import SCENARIOS


def test_langgraph_harness_implements_every_required_operation() -> None:
    harness = LangGraphV1Harness()

    assert REQUIRED_HARNESS_OPERATIONS
    missing = [
        operation
        for operation in REQUIRED_HARNESS_OPERATIONS
        if not callable(getattr(harness, operation, None))
    ]
    assert missing == []


def test_every_langgraph_variant_emits_evidence_matching_registered_status() -> None:
    adapter = LangGraphV1Adapter()
    empty_fact_variants = []

    for scenario in SCENARIOS:
        for variant in scenario.variants:
            binding = variant.binding_for("langgraph_v1")
            execution = adapter.run_scenario(
                scenario.scenario_id,
                variant.variant_id,
            )
            observation = execution.observation
            payload = observation.to_dict()

            assert payload["schema_version"] == 1
            assert payload["scenario_id"] == scenario.scenario_id
            assert payload["variant_id"] == variant.variant_id
            assert payload["runtime_id"] == "langgraph_v1"
            assert isinstance(payload["data"]["facts"], dict)
            if not payload["data"]["facts"]:
                empty_fact_variants.append(variant.variant_id)
            assert isinstance(payload["data"]["checks"], list)
            assert payload["data"]["checks"]
            assert payload["data"]["summary"]["total_checks"] == len(
                payload["data"]["checks"]
            )
            assert execution.trace.count("scenario_started") == 1
            assert execution.trace.count("observation_emitted") == 1

            failures = [
                item
                for item in payload["data"]["checks"]
                if item["passed"] is not True
            ]
            passing = [
                item
                for item in payload["data"]["checks"]
                if item["passed"] is True
            ]
            if binding.known_gap_keys:
                assert failures, (
                    f"{variant.variant_id} registers a known gap but "
                    "produced no failing check"
                )
                assert {
                    item.get("known_gap_key")
                    for item in failures
                } == set(binding.known_gap_keys)
            else:
                assert failures == [], (
                    f"{variant.variant_id} is registered as implemented but "
                    "still produced failing checks"
                )
            assert all(item.get("evidence") for item in failures)
            assert all(
                not item.get("known_gap_key")
                for item in passing
            ), (
                f"{variant.variant_id} masks a currently passing check "
                "behind a Known Gap key"
            )

    assert not empty_fact_variants, repr(empty_fact_variants)
