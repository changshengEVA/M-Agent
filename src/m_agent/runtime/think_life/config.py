"""Think-life runtime configuration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass
class ThinkLifeSchedulerConfig:
    preempt_enabled: bool = False
    max_preempt_per_stimulus: int = 3
    default_user_priority: int = 10
    default_feedback_priority: int = 20
    default_schedule_priority: int = 30
    default_heartbeat_priority: int = 40


@dataclass
class ThinkLifeConfig:
    max_delegates_per_transaction: Optional[int] = None
    max_think_rounds: Optional[int] = None
    scene_context_max_entries: int = 40
    scene_persist_jsonl: bool = True
    scheduler: ThinkLifeSchedulerConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.scheduler is None:
            self.scheduler = ThinkLifeSchedulerConfig()


def load_think_life_config(raw: Mapping[str, Any] | None) -> ThinkLifeConfig:
    data = dict(raw or {})
    sched_raw = data.get("scheduler") if isinstance(data.get("scheduler"), dict) else {}
    scheduler = ThinkLifeSchedulerConfig(
        preempt_enabled=bool(sched_raw.get("preempt_enabled", False)),
        max_preempt_per_stimulus=int(sched_raw.get("max_preempt_per_stimulus", 3) or 3),
        default_user_priority=int(sched_raw.get("default_user_priority", 10) or 10),
        default_feedback_priority=int(sched_raw.get("default_feedback_priority", 20) or 20),
        default_schedule_priority=int(sched_raw.get("default_schedule_priority", 30) or 30),
        default_heartbeat_priority=int(sched_raw.get("default_heartbeat_priority", 40) or 40),
    )
    max_delegates_raw = data.get("max_delegates_per_transaction")
    max_delegates: Optional[int]
    if max_delegates_raw is None or max_delegates_raw == "":
        max_delegates = None
    else:
        parsed = int(max_delegates_raw)
        max_delegates = parsed if parsed > 0 else None
    max_think_raw = data.get("max_think_rounds")
    max_think: Optional[int]
    if max_think_raw is None or max_think_raw == "":
        max_think = None
    else:
        parsed_think = int(max_think_raw)
        max_think = parsed_think if parsed_think > 0 else None
    return ThinkLifeConfig(
        max_delegates_per_transaction=max_delegates,
        max_think_rounds=max_think,
        scene_context_max_entries=int(data.get("scene_context_max_entries", 40) or 40),
        scene_persist_jsonl=bool(data.get("scene_persist_jsonl", True)),
        scheduler=scheduler,
    )
