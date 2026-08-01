"""Acceptance-only vocabulary for arranging transaction domain states."""

from __future__ import annotations

from enum import Enum
from typing import Any

from m_agent.runtime.think_life.contracts import (
    PauseReason,
    TransactionRecord,
)


class FixtureTransactionStatus(str, Enum):
    """Legacy scenario labels kept outside the production transaction model."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_EXECUTION = "waiting_execution"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def apply_fixture_status(
    registry: Any,
    record: TransactionRecord,
    status: FixtureTransactionStatus,
) -> TransactionRecord:
    """Arrange an old scenario label through current domain commands."""

    if status in {
        FixtureTransactionStatus.PENDING,
        FixtureTransactionStatus.RUNNING,
    }:
        return record
    if status == FixtureTransactionStatus.WAITING_EXECUTION:
        return registry.begin_delegate(
            record.transaction_id,
            f"fixture_delegate_{record.transaction_id}",
        )
    if status == FixtureTransactionStatus.SUSPENDED:
        return registry.pause(
            record.transaction_id,
            reason=PauseReason.MANUAL_HOLD,
        )
    if status == FixtureTransactionStatus.COMPLETED:
        return registry.complete(record.transaction_id)
    if status == FixtureTransactionStatus.FAILED:
        return registry.fail(
            record.transaction_id,
            error="acceptance fixture failure",
        )
    if status == FixtureTransactionStatus.CANCELLED:
        return registry.delete(record.transaction_id)
    raise ValueError(f"unsupported fixture status: {status!r}")


__all__ = ["FixtureTransactionStatus", "apply_fixture_status"]
