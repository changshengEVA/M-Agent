from __future__ import annotations

from pathlib import Path

from m_agent.acceptance.scenario_catalog import (
    EXPECTED_SCENARIO_IDS,
    SCENARIOS,
    contract_catalog_payload,
    get_scenarios,
    validate_contract_catalog,
)


def _variants():
    return [
        variant
        for scenario in SCENARIOS
        for variant in scenario.variants
    ]


def test_p1_catalog_is_complete_and_exit_ready() -> None:
    payload = contract_catalog_payload()

    assert [item.scenario_id for item in SCENARIOS] == list(
        EXPECTED_SCENARIO_IDS
    )
    assert validate_contract_catalog() == []
    assert payload["coverage"] == {
        **payload["coverage"],
        "required_scenarios": 28,
        "specified_scenarios": 28,
        "required_core": 27,
        "specified_core": 27,
        "required_matcher": 1,
        "specified_matcher": 1,
        "required_robustness": 7,
        "specified_robustness": 7,
        "mapped_robustness_requirements": 25,
        "catalog_complete": True,
        "p1_exit_ready": True,
        "errors": [],
    }

    layer_counts = {
        layer: sum(item.layer == layer for item in _variants())
        for layer in ("core", "robustness", "matcher_evaluation", "poc")
    }
    assert layer_counts == {
        "core": 27,
        "robustness": 7,
        "matcher_evaluation": 1,
        "poc": 3,
    }


def test_all_38_variants_are_executable_for_langgraph() -> None:
    variants = _variants()

    assert len(variants) == 38
    for variant in variants:
        langgraph = variant.binding_for("langgraph_v1")

        assert langgraph.availability == "executable"
        assert len(langgraph.tests) == 1
        assert langgraph.known_gap_keys == ()

    execution = contract_catalog_payload()["execution"]
    assert execution["executable_variants_by_runtime"] == {
        "langgraph_v1": 38,
    }
    assert execution["executable_by_layer_and_runtime"] == {
        "core": {"langgraph_v1": 27},
        "robustness": {"langgraph_v1": 7},
        "matcher_evaluation": {
            "langgraph_v1": 1,
        },
        "poc": {"langgraph_v1": 3},
    }
    assert execution["registered_known_gaps_by_runtime"] == {
        "langgraph_v1": 0,
    }


def test_robustness_manifest_covers_all_25_requirements_without_bad_mappings() -> None:
    payload = contract_catalog_payload()
    manifest = payload["robustness_manifest"]
    robustness_ids = {
        variant.variant_id
        for variant in _variants()
        if variant.layer == "robustness"
    }
    requirement_ids = [item["requirement_id"] for item in manifest]

    assert len(manifest) == 25
    assert len(requirement_ids) == len(set(requirement_ids))
    assert {item["variant_id"] for item in manifest} == robustness_ids
    for item in manifest:
        assert item["variant_id"] in robustness_ids
        assert item["layer"] == "robustness"
        assert item["parent_scenario_id"] == item["variant_id"].split("/", 1)[0]
        assert item["langgraph_v1"] == {
            "availability": "executable",
            "registered_status": "implemented",
        }


def test_get_scenarios_preserves_requested_catalog_order() -> None:
    selected = get_scenarios(["AT-10", "TX-01"])
    assert [item.scenario_id for item in selected] == ["TX-01", "AT-10"]


def test_allowlisted_scenario_nodes_point_to_real_files() -> None:
    project_root = Path(__file__).resolve().parents[2]
    for scenario in SCENARIOS:
        for variant in scenario.variants:
            for runtime_id in ("langgraph_v1",):
                for ref in variant.binding_for(runtime_id).tests:
                    relative_file = ref.nodeid.split("::", 1)[0]
                    assert (project_root / relative_file).is_file(), (
                        scenario.scenario_id,
                        variant.variant_id,
                        runtime_id,
                        ref.nodeid,
                    )


def test_legacy_inv_catalog_is_retained_only_as_supporting() -> None:
    legacy = contract_catalog_payload()["legacy_supporting"]

    assert legacy["role"] == "supporting"
    assert legacy["invariant_ids"] == [
        f"INV-{index:02d}" for index in range(1, 15)
    ]
