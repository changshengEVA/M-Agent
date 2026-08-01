"""Shared execution helper for Runtime-neutral P1 scenario tests."""

from __future__ import annotations

import os
from typing import Any, Callable

import pytest

from m_agent.acceptance.adapters import get_runtime_adapter
from m_agent.acceptance.scenario_catalog import get_scenarios


def known_gap(scenario_id: str, variant_id: str) -> Callable[[Any], Any]:
    """Validate gap registration while leaving outcome classification dynamic."""

    scenario = get_scenarios([scenario_id])[0]
    variant = next(
        item
        for item in scenario.variants
        if item.variant_id == variant_id
    )
    binding = variant.binding_for("think_life_v1")
    if not binding.known_gap or not binding.known_gap_keys:
        raise ValueError(
            f"{variant_id} is decorated as a Known Gap without a "
            "registered reason and check key"
        )

    def decorate(func: Any) -> Any:
        setattr(func, "_p1_known_gap_reason", binding.known_gap)
        setattr(func, "_p1_known_gap_keys", binding.known_gap_keys)
        return func

    return decorate


def run_contract(
    request: pytest.FixtureRequest,
    scenario_id: str,
    variant_id: str,
) -> None:
    runtime_id = os.environ.get(
        "M_AGENT_ACCEPTANCE_RUNTIME_ID",
        "think_life_v1",
    )
    execution = get_runtime_adapter(runtime_id).run_scenario(
        scenario_id,
        variant_id,
    )
    execution.attach(request)
    scenario = get_scenarios([scenario_id])[0]
    variant = next(
        item
        for item in scenario.variants
        if item.variant_id == variant_id
    )
    binding = variant.binding_for(runtime_id)
    failures = execution.observation.failed_checks
    if not failures:
        execution.observation.assert_satisfied()
        if binding.known_gap:
            pytest.fail(
                "registered P1 gap no longer reproduced; remove the stale "
                f"registration for {variant_id}"
            )
        return

    registered = set(binding.known_gap_keys)
    unexpected = [
        item
        for item in failures
        if str(item.get("known_gap_key", "")) not in registered
    ]
    assert not unexpected, (
        "unregistered P1 failure(s): "
        + repr(unexpected)
    )
    pytest.xfail(binding.known_gap or "registered P1 semantic gap")
