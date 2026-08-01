"""Pause a transaction when the model explicitly waits for the user."""

from __future__ import annotations

import logging
from typing import Any

from m_agent.runtime.think_life.contracts import (
    PauseReason,
    TransactionRecord,
    TransactionState,
)

logger = logging.getLogger(__name__)


def pause_for_user_collaboration(
    registry: Any,
    record: TransactionRecord,
    *,
    require_reply_finalized: bool = True,
) -> bool:
    """Pause after an explicit model pause decision.

    Pause means waiting for user collaboration. Silence is a consequence of
    ``state==pause``, not a trigger for pause.

    When ``require_reply_finalized`` is True, pause is allowed only if this
    activation already delivered a user-visible reply (necessary guard, not a
    pause trigger by itself).
    """

    current = registry.get(record.transaction_id) or record
    if current.state != TransactionState.CONTINUE:
        return False
    if require_reply_finalized and not bool(
        getattr(current, "reply_finalized_in_activation", False)
    ):
        return False
    try:
        registry.pause(
            record.transaction_id,
            reason=PauseReason.AWAITING_USER,
        )
        return True
    except Exception:
        logger.exception(
            "failed to pause for user collaboration txn=%s",
            record.transaction_id,
        )
        return False


def pause_after_clarification(registry: Any, record: TransactionRecord) -> bool:
    """Compatibility alias for :func:`pause_for_user_collaboration`."""

    return pause_for_user_collaboration(registry, record)


def pause_if_awaiting_user(registry: Any, record: TransactionRecord) -> bool:
    """Compatibility alias for the explicit awaiting-user pause command."""

    return pause_for_user_collaboration(registry, record)


__all__ = [
    "pause_after_clarification",
    "pause_for_user_collaboration",
    "pause_if_awaiting_user",
]
