"""Think-life runtime: perception bus, transaction lines, Scene log, Think CPU."""

from __future__ import annotations

from .config import ThinkLifeConfig, load_think_life_config
from .contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusKind,
    StimulusEnvelope,
    ActivationRecord,
    DelegateRecord,
    PauseReason,
    TransactionKind,
    TransactionLifecycle,
    TransactionRecord,
    TransactionState,
)
from .transaction import (
    FlushCoordinator,
    RuntimeUnitOfWork,
    SQLiteRuntimeStore,
)
from .runtime import ThinkLifeRuntime

__all__ = [
    "SceneActor",
    "SceneEntry",
    "SceneEntryType",
    "ActivationRecord",
    "DelegateRecord",
    "FlushCoordinator",
    "PauseReason",
    "RuntimeUnitOfWork",
    "SQLiteRuntimeStore",
    "StimulusEnvelope",
    "StimulusKind",
    "ThinkLifeConfig",
    "ThinkLifeRuntime",
    "TransactionKind",
    "TransactionLifecycle",
    "TransactionRecord",
    "TransactionState",
    "load_think_life_config",
]
