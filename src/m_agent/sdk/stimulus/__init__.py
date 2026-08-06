"""Stimulus Kernel public contracts (v0.3 Public Alpha).

Stable public surface: ``Observation`` and ``Stimulus`` (exported here as
``PublicStimulus`` to avoid colliding with the internal think-layer
``Stimulus`` content type). Adapter-private ``Signal`` is intentionally not
part of this package.
"""

from m_agent.sdk.stimulus.contracts import (
    Disposition,
    IngestResult,
    Observation,
    PoolState,
    PublicStimulus,
    ReasonCode,
    validate_observation,
)

__all__ = [
    "Disposition",
    "IngestResult",
    "Observation",
    "PoolState",
    "PublicStimulus",
    "ReasonCode",
    "validate_observation",
]
