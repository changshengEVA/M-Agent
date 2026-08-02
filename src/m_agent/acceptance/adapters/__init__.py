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


def get_runtime_adapter(runtime_id: str) -> RuntimeAdapter:
    """Resolve only adapters owned by the curated acceptance package."""

    requested = str(runtime_id or "").strip().lower()
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
    "get_runtime_adapter",
]
