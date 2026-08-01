"""Guard the R1 dev smoke entry point against rot.

The full 5-round criterion is run manually via
``python scripts/smoke_langgraph_runtime.py --rounds 5``; here one round is
enough to keep the script importable and its invariants wired.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "smoke_langgraph_runtime.py"


def _load_smoke_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "m_agent_smoke_langgraph_runtime",
        SCRIPT_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke script: {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.integration
def test_smoke_round_passes(tmp_path: Path) -> None:
    smoke = _load_smoke_module()
    result = smoke.run_round(
        round_index=1,
        persist_root=tmp_path / "lg-smoke",
        thread_id="pytest-smoke",
        turns=2,
    )
    assert result["ok"], result["failures"] or result.get("error")
    assert result["checks_failed"] == 0
    assert result["checks_total"] > 0
    assert all(
        view["runtime_engine"] == LANGGRAPH_RUNTIME_ENGINE
        for view in result["transactions"]
    )


@pytest.mark.integration
def test_smoke_detects_broken_invariant(tmp_path: Path) -> None:
    smoke = _load_smoke_module()
    smoke.EXPECTED_GRAPH_PHASE = "not-a-real-phase"
    result = smoke.run_round(
        round_index=1,
        persist_root=tmp_path / "lg-smoke-negative",
        thread_id="pytest-smoke-negative",
        turns=1,
    )
    assert result["ok"] is False
    assert any("graph_phase" in item["name"] for item in result["failures"])
