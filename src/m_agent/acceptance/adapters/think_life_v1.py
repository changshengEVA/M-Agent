"""Unified Think-life adapter for the Runtime-independent P1 contract."""

from __future__ import annotations

from .base import RuntimeAdapterError, ScenarioExecution
from .think_life_at import run_at_scenario
from .think_life_harness import ThinkLifeV1Harness
from .think_life_sp import run_sp_scenario
from .think_life_tx import run_tx_scenario


class ThinkLifeV1Adapter:
    """Dispatch TX/SP/AT variants through a fresh normalized Harness."""

    runtime_id = "think_life_v1"

    def run_scenario(
        self,
        scenario_id: str,
        variant_id: str,
    ) -> ScenarioExecution:
        scenario = str(scenario_id or "").strip().upper()
        variant = str(variant_id or "").strip()
        harness = ThinkLifeV1Harness()
        try:
            if scenario.startswith("TX-"):
                return run_tx_scenario(harness, scenario, variant)
            if scenario.startswith("SP-"):
                return run_sp_scenario(harness, scenario, variant)
            if scenario.startswith("AT-"):
                return run_at_scenario(harness, scenario, variant)
            raise RuntimeAdapterError(
                f"{self.runtime_id} does not implement {scenario}/{variant}"
            )
        finally:
            harness.close()


__all__ = ["ThinkLifeV1Adapter"]
