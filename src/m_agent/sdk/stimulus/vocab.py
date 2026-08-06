"""Vocabulary helpers for pool state / disposition split and legacy migration."""

from __future__ import annotations

from typing import Optional, Tuple

from m_agent.sdk.stimulus.contracts import (
    Disposition,
    PoolState,
    TERMINAL_DISPOSITIONS,
)

# Legacy dual-write values that lived in both status and disposition.
_LEGACY_POOL = frozenset({"new", "ready", "claimed"})
_LEGACY_TERMINAL = frozenset(
    {"consumed", "expected_discard", "aborted", "failed"}
)


def normalize_pool_state(value: object) -> str:
    text = str(value or "").strip()
    if text == "claimed":
        return PoolState.RUNNING.value
    if text in {item.value for item in PoolState}:
        return text
    if text in _LEGACY_TERMINAL:
        return PoolState.TERMINATED.value
    return PoolState.NEW.value


def normalize_disposition(
    value: object,
    *,
    stage: Optional[str] = None,
) -> Optional[str]:
    text = str(value or "").strip()
    if not text or text in _LEGACY_POOL:
        return None
    if text == "consumed":
        return Disposition.COMPLETED.value
    if text == "expected_discard":
        stage_text = str(stage or "").strip()
        if stage_text == "admission":
            return Disposition.REJECTED.value
        return Disposition.DISCARDED.value
    if text in TERMINAL_DISPOSITIONS:
        return text
    return text


def migrate_legacy_row(
    *,
    status: object,
    disposition: object,
    disposition_stage: object = None,
) -> Tuple[str, Optional[str]]:
    """Map a pre-v0.3 stimuli row into (pool_state, disposition)."""

    status_text = str(status or "").strip()
    disposition_text = str(disposition or "").strip()
    stage = str(disposition_stage or "").strip() or None

    # Historical dual-write: both columns held the same legacy token.
    token = status_text or disposition_text
    if token in _LEGACY_POOL:
        return normalize_pool_state(token), None
    if token in _LEGACY_TERMINAL or token in TERMINAL_DISPOSITIONS:
        return (
            PoolState.TERMINATED.value,
            normalize_disposition(token, stage=stage),
        )
    pool = normalize_pool_state(status_text)
    disp = normalize_disposition(disposition_text, stage=stage)
    if disp is not None:
        pool = PoolState.TERMINATED.value
    return pool, disp


def is_terminal_disposition(value: object) -> bool:
    return str(value or "").strip() in TERMINAL_DISPOSITIONS


def is_claimable_pool_state(value: object) -> bool:
    return str(value or "").strip() == PoolState.READY.value


def is_running_pool_state(value: object) -> bool:
    return str(value or "").strip() == PoolState.RUNNING.value
