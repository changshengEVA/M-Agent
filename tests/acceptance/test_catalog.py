from __future__ import annotations

from pathlib import Path

from m_agent.acceptance.catalog import (
    INVARIANT_GROUPS,
    INVARIANTS,
    catalog_payload,
    validate_catalog,
)


def test_phase0_catalog_has_exactly_fourteen_executable_invariants() -> None:
    assert [item.invariant_id for item in INVARIANTS] == [
        f"INV-{index:02d}" for index in range(1, 15)
    ]
    assert validate_catalog() == []
    assert all(item.gate_tests for item in INVARIANTS)


def test_every_catalog_node_points_to_a_real_test_file() -> None:
    project_root = Path(__file__).resolve().parents[2]
    for invariant in INVARIANTS:
        for ref in (*invariant.gate_tests, *invariant.supporting_tests):
            relative_file = ref.nodeid.split("::", 1)[0]
            assert (project_root / relative_file).is_file(), (
                invariant.invariant_id,
                ref.nodeid,
            )


def test_catalog_payload_makes_coverage_and_safety_explicit() -> None:
    payload = catalog_payload()
    assert payload["coverage"] == {
        "required": 14,
        "specified": 14,
        "complete": True,
        "errors": [],
    }
    assert payload["safety"]["subprocess_only"] is True
    assert payload["safety"]["allowlisted_tests_only"] is True
    assert payload["safety"]["real_external_side_effects"] is False


def test_catalog_exposes_stable_hierarchy_and_profile_case_counts() -> None:
    payload = catalog_payload()

    assert [group.group_id for group in INVARIANT_GROUPS] == [
        "outer_runtime",
        "transaction_state",
        "reasoning_execution",
        "scene_memory",
        "recovery_idempotency",
    ]
    assert payload["profile_counts"] == {
        "gate": {
            "invariant_count": 14,
            "case_count": 20,
            "gate_case_count": 20,
            "supporting_case_count": 0,
        },
        "full": {
            "invariant_count": 14,
            "case_count": 36,
            "gate_case_count": 20,
            "supporting_case_count": 16,
        },
    }
    assert {
        item["id"]: (
            item["counts"]["invariant_count"],
            item["counts"]["gate_case_count"],
            item["counts"]["full_case_count"],
            item["counts"]["known_gap_count"],
        )
        for item in payload["groups"]
    } == {
        "outer_runtime": (3, 4, 8, 3),
        "transaction_state": (3, 4, 7, 1),
        "reasoning_execution": (5, 6, 11, 1),
        "scene_memory": (2, 5, 9, 0),
        "recovery_idempotency": (1, 1, 1, 1),
    }
    assert [item["order"] for item in payload["invariants"]] == list(
        range(1, 15)
    )
    assert all(item["group_id"] for item in payload["invariants"])
