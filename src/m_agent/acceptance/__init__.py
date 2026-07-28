"""Runtime semantic acceptance platform.

The acceptance package is intentionally isolated from the production chat API.
It provides a curated catalog, a subprocess-only pytest runner, and two clients:
the command line interface and a local FastAPI dashboard.
"""

from .catalog import PHASE0_SUITE, catalog_payload, get_invariants
from .runner import AcceptanceRunner, RunBusyError

__all__ = [
    "AcceptanceRunner",
    "PHASE0_SUITE",
    "RunBusyError",
    "catalog_payload",
    "get_invariants",
]
