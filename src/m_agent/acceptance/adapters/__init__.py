"""Runtime adapters used by shared semantic acceptance scenarios."""

from .base import (
    REQUIRED_HARNESS_OPERATIONS,
    HarnessResult,
    RuntimeAdapter,
    RuntimeAdapterError,
    RuntimeHarness,
    ScenarioExecution,
)
from .langgraph_harness import LangGraphV1Harness
from .langgraph_v1 import LangGraphV1Adapter
from .think_life_harness import ThinkLifeV1Harness
from .think_life_v1 import ThinkLifeV1Adapter


def get_runtime_adapter(runtime_id: str) -> RuntimeAdapter:
    """Resolve only adapters owned by the curated acceptance package."""

    requested = str(runtime_id or "").strip().lower()
    if requested == "think_life_v1":
        return ThinkLifeV1Adapter()
    if requested == "langgraph_v1":
        return LangGraphV1Adapter()
    raise RuntimeAdapterError(f"unknown Runtime adapter: {requested}")


__all__ = [
    "HarnessResult",
    "REQUIRED_HARNESS_OPERATIONS",
    "LangGraphV1Adapter",
    "LangGraphV1Harness",
    "RuntimeAdapter",
    "RuntimeAdapterError",
    "RuntimeHarness",
    "ScenarioExecution",
    "ThinkLifeV1Harness",
    "ThinkLifeV1Adapter",
    "get_runtime_adapter",
]
