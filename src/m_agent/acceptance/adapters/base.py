"""Minimal Runtime-neutral adapter protocol for P1 scenario execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Sequence

from ..scenario_contract import ScenarioObservation
from ..trace import SemanticTrace


REQUIRED_HARNESS_OPERATIONS = (
    "create_conversation",
    "seed_transaction",
    "seed_delegate",
    "submit_stimulus",
    "list_ready_stimuli",
    "load_stimulus",
    "load_partition_state",
    "acquire_consumer",
    "takeover_consumer",
    "claim_next_stimulus",
    "finalize_stimulus",
    "process_next_stimulus",
    "load_transaction",
    "list_match_candidates",
    "control_transaction",
    "restore_transaction",
    "read_scene",
    "load_schedule",
    "load_effects",
    "trigger_flush",
    "inject_fault",
    "set_worker_latch",
    "wait_for_disposition",
    "control_effect_sink",
    "restart_runtime",
    "evaluate_matcher",
)


class RuntimeAdapterError(RuntimeError):
    """Raised when a Runtime cannot execute a requested scenario command."""


@dataclass(frozen=True)
class ScenarioExecution:
    """Observation and normalized trace produced by one adapter execution."""

    observation: ScenarioObservation
    trace: SemanticTrace

    def attach(self, request: Any) -> None:
        self.trace.attach(request)
        self.observation.attach(request)


class RuntimeAdapter(Protocol):
    runtime_id: str

    def run_scenario(self, scenario_id: str, variant_id: str) -> ScenarioExecution:
        """Execute one allowlisted scenario variant and return domain evidence."""


@dataclass(frozen=True)
class HarnessResult:
    """Normalized result of one Runtime-neutral Harness operation."""

    operation: str
    outcome: str
    supported: bool
    data: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation,
            "outcome": self.outcome,
            "supported": self.supported,
            "reason": self.reason,
            "data": dict(self.data),
        }


class RuntimeHarness(Protocol):
    """P1 domain command/query boundary shared by every Runtime adapter."""

    def create_conversation(self, *, thread_id: str) -> HarnessResult: ...

    def seed_transaction(
        self,
        *,
        conversation_id: str,
        fixture: Dict[str, Any],
    ) -> HarnessResult: ...

    def seed_delegate(
        self,
        *,
        transaction_id: str,
        fixture: Dict[str, Any],
    ) -> HarnessResult: ...

    def submit_stimulus(
        self,
        *,
        conversation_id: str,
        kind: str,
        text: str,
        payload: Optional[Dict[str, Any]] = None,
        source: Optional[Dict[str, str]] = None,
        priority_override: Optional[int] = None,
        ingress_key: str = "",
    ) -> HarnessResult: ...

    def list_ready_stimuli(self, *, conversation_id: str) -> HarnessResult: ...

    def load_stimulus(self, *, stimulus_id: str) -> HarnessResult: ...

    def load_partition_state(
        self,
        *,
        conversation_id: str,
    ) -> HarnessResult: ...

    def acquire_consumer(
        self,
        *,
        conversation_id: str,
        consumer_id: str,
        lease_seconds: float,
    ) -> HarnessResult: ...

    def takeover_consumer(
        self,
        *,
        conversation_id: str,
        consumer_id: str,
    ) -> HarnessResult: ...

    def claim_next_stimulus(
        self,
        *,
        conversation_id: str,
        consumer_id: str,
    ) -> HarnessResult: ...

    def finalize_stimulus(
        self,
        *,
        stimulus_id: str,
        claim_token: Dict[str, Any],
        disposition: str,
        transition_id: str,
        command_digest: str,
    ) -> HarnessResult: ...

    def process_next_stimulus(
        self,
        *,
        conversation_id: str,
    ) -> HarnessResult: ...

    def load_transaction(self, *, transaction_id: str) -> HarnessResult: ...

    def list_match_candidates(self, *, conversation_id: str) -> HarnessResult: ...

    def control_transaction(
        self,
        *,
        transaction_id: str,
        action: str,
    ) -> HarnessResult: ...

    def restore_transaction(
        self,
        *,
        transaction_id: str,
        source: str,
    ) -> HarnessResult: ...

    def read_scene(
        self,
        *,
        conversation_id: str,
        after_seq: int = 0,
    ) -> HarnessResult: ...

    def load_schedule(self, *, schedule_run_id: str) -> HarnessResult: ...

    def load_effects(self, *, transaction_id: str) -> HarnessResult: ...

    def trigger_flush(
        self,
        *,
        conversation_id: str,
        flush_id: str,
    ) -> HarnessResult: ...

    def inject_fault(self, *, fault_point: str) -> HarnessResult: ...

    def set_worker_latch(
        self,
        *,
        conversation_id: str,
        phase: str,
    ) -> HarnessResult: ...

    def wait_for_disposition(
        self,
        *,
        stimulus_id: str,
        timeout_seconds: float,
    ) -> HarnessResult: ...

    def control_effect_sink(
        self,
        *,
        action: str,
        effect_id: str = "",
    ) -> HarnessResult: ...

    def restart_runtime(self) -> HarnessResult: ...

    def evaluate_matcher(
        self,
        *,
        samples: Sequence[Dict[str, Any]],
    ) -> HarnessResult: ...
