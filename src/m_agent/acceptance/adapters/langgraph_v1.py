"""LangGraph adapter for the P8 full semantic matrix."""

from __future__ import annotations

from .base import RuntimeAdapterError, ScenarioExecution
from .langgraph_harness import LangGraphV1Harness
from .langgraph_tx import run_tx_scenario
from .shared_at import run_at_scenario
from .shared_sp import run_sp_scenario


class LangGraphV1Adapter:
    """Dispatch TX/SP/AT variants through the LangGraph-backed harness."""

    runtime_id = "langgraph_v1"

    def run_scenario(
        self,
        scenario_id: str,
        variant_id: str,
    ) -> ScenarioExecution:
        scenario = str(scenario_id or "").strip().upper()
        variant = str(variant_id or "").strip()
        harness = LangGraphV1Harness()
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


__all__ = ["LangGraphV1Adapter"]
