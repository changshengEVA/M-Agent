"""Transaction-side logical unit of work for the P2 foundation.

The durable implementation lives in :mod:`.store`.  This facade keeps
Runtime callers independent from SQLite details and makes the command shape
used for idempotency explicit.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, Mapping, Optional

from ..domain.contracts import TransactionRecord


Mutation = Callable[[Optional[TransactionRecord]], Dict[str, Any]]


def canonical_command_json(command: Mapping[str, Any]) -> str:
    """Return the stable JSON representation used by the transition ledger."""

    if not isinstance(command, Mapping):
        raise TypeError("command must be a mapping")
    return json.dumps(
        dict(command),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def command_digest(command: Mapping[str, Any]) -> str:
    """Hash a normalized command for idempotency conflict detection."""

    return hashlib.sha256(
        canonical_command_json(command).encode("utf-8")
    ).hexdigest()


class RuntimeUnitOfWork:
    """Apply one revision-fenced transaction mutation exactly once."""

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def apply_transition(
        self,
        transition_id: str,
        command: Mapping[str, Any],
        transaction_id: Optional[str],
        expected_revision: Optional[int],
        mutate: Mutation,
    ) -> Dict[str, Any]:
        transition = str(transition_id or "").strip()
        if not transition:
            raise ValueError("transition_id is required")
        if not callable(mutate):
            raise TypeError("mutate must be callable")
        return self._registry.apply_transition(
            transition_id=transition,
            command=dict(command),
            command_digest=command_digest(command),
            transaction_id=(
                str(transaction_id or "").strip() or None
            ),
            expected_revision=expected_revision,
            mutate=mutate,
        )


__all__ = [
    "Mutation",
    "RuntimeUnitOfWork",
    "canonical_command_json",
    "command_digest",
]
