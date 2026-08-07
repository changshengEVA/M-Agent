"""Offline Stimulus Lab: replay duplicate / out-of-order / expired / irrelevant / valid."""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from m_agent.runtime.clock import VirtualClock
from m_agent.runtime.perception.observation import observation_to_envelope
from m_agent.runtime.transaction.store import SQLiteRuntimeStore
from m_agent.sdk.stimulus.contracts import (
    Disposition,
    IngestResult,
    Observation,
    PoolState,
    validate_observation,
)

SCENARIO_PACK: Dict[str, List[Dict[str, Any]]] = {
    "duplicate": [
        {
            "type": "external_event",
            "text": "invoice received",
            "occurred_at": "2026-01-01T10:00:00Z",
            "idempotency_key": "invoice:1",
        },
        {
            "type": "external_event",
            "text": "invoice received again",
            "occurred_at": "2026-01-01T10:00:05Z",
            "idempotency_key": "invoice:1",
        },
    ],
    "out_of_order": [
        {
            "type": "external_event",
            "text": "later event",
            "occurred_at": "2026-01-01T12:00:00Z",
            "idempotency_key": "ooo:later",
        },
        {
            "type": "external_event",
            "text": "earlier event",
            "occurred_at": "2026-01-01T11:00:00Z",
            "idempotency_key": "ooo:earlier",
        },
    ],
    "expired": [
        {
            "type": "external_event",
            "text": "stale notice",
            "occurred_at": "2026-01-01T08:00:00Z",
            "observed_at": "2026-01-01T12:00:00Z",
            "expires_at": "2026-01-01T09:00:00Z",
            "idempotency_key": "expired:1",
        },
    ],
    "irrelevant": [
        {
            "type": "irrelevant",
            "text": "noise",
            "occurred_at": "2026-01-01T10:00:00Z",
            "idempotency_key": "noise:1",
        },
    ],
    "valid": [
        {
            "type": "external_event",
            "text": "actionable update",
            "occurred_at": "2026-01-01T10:00:00Z",
            "idempotency_key": "valid:1",
        },
    ],
    "chat_duplicate": [
        {
            "source": "chat",
            "type": "user_message",
            "text": "hello",
            "occurred_at": "2026-01-01T10:00:00Z",
            "idempotency_key": "chat:lab-thread:run_1",
            "payload": {"user_turn": {"speaker": "user", "text": "hello"}},
        },
        {
            "source": "chat",
            "type": "user_message",
            "text": "hello again",
            "occurred_at": "2026-01-01T10:00:05Z",
            "idempotency_key": "chat:lab-thread:run_1",
            "payload": {"user_turn": {"speaker": "user", "text": "hello again"}},
        },
    ],
}


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)


class FakeStimulusRuntime:
    """Minimal durable ingest surface for offline Lab replay."""

    def __init__(
        self,
        *,
        path: Optional[Path] = None,
        clock: Optional[VirtualClock] = None,
    ) -> None:
        self.clock = clock or VirtualClock("2026-01-01T00:00:00Z")
        self.store = SQLiteRuntimeStore(path, clock=self.clock)

    def ingest(
        self,
        observation: Observation,
        *,
        schedule_drainer: bool = False,
    ) -> IngestResult:
        del schedule_drainer
        observation = validate_observation(observation)
        expires_at = str(observation.expires_at or "").strip()
        if expires_at and expires_at < observation.observed_at:
            return self._admit_terminal(
                observation,
                disposition=Disposition.DISCARDED.value,
                reason_code="expired",
                reason="expired",
            )
        if str(observation.type or "").strip().lower() == "irrelevant":
            return self._admit_terminal(
                observation,
                disposition=Disposition.DISCARDED.value,
                reason_code="irrelevant",
                reason="irrelevant",
            )
        envelope = observation_to_envelope(observation)
        prior = (
            self.store.load_stimulus_by_ingress_key(envelope.ingress_key)
            if envelope.ingress_key
            else None
        )
        stored = self.store.admit_stimulus(
            envelope,
            effective_priority=50,
            pool_state=PoolState.READY.value,
        )
        merged = prior is not None
        return IngestResult(
            stimulus_id=stored.stimulus_id,
            pool_state=stored.pool_state,
            created=not merged,
            merged=merged,
            disposition=(
                Disposition.MERGED.value if merged else stored.disposition
            ),
            reason_code=(
                "merged_existing" if merged else stored.reason_code
            ),
            reason=stored.disposition_reason,
        )

    def _admit_terminal(
        self,
        observation: Observation,
        *,
        disposition: str,
        reason_code: str,
        reason: str,
    ) -> IngestResult:
        envelope = observation_to_envelope(observation)
        stored = self.store.admit_stimulus(
            envelope,
            effective_priority=0,
            pool_state=PoolState.TERMINATED.value,
            disposition=disposition,
            disposition_stage="admission",
            disposition_reason=reason,
            reason_code=reason_code,
        )
        return IngestResult(
            stimulus_id=stored.stimulus_id,
            pool_state=stored.pool_state,
            created=True,
            merged=False,
            disposition=stored.disposition,
            reason_code=stored.reason_code,
            reason=stored.disposition_reason,
        )

    def list_stimulus_trace(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.store.list_stimulus_trace(**kwargs)

    def claim_and_complete(self, conversation_id: str) -> Optional[str]:
        self.store.acquire_consumer(
            conversation_id=conversation_id,
            consumer_id="lab-consumer",
            lease_seconds=30,
        )
        claimed = self.store.claim_next_stimulus(
            conversation_id=conversation_id,
            consumer_id="lab-consumer",
        )
        if claimed is None:
            return None
        self.store.finalize_stimulus(
            stimulus_id=claimed.stimulus_id,
            claim_token={
                "claimed_by": claimed.claimed_by,
                "consumer_epoch": claimed.consumer_epoch,
                "claim_epoch": claimed.claim_epoch,
            },
            disposition=Disposition.COMPLETED.value,
            transition_id=f"lab-finalize:{claimed.stimulus_id}",
            command_digest="completed",
        )
        return claimed.stimulus_id


class StimulusLab:
    """Replay packaged Observation scenarios against FakeStimulusRuntime."""

    def __init__(
        self,
        *,
        thread_id: str = "lab-thread",
        conversation_id: str = "lab-thread::0",
        work_dir: Optional[Path] = None,
    ) -> None:
        self.thread_id = thread_id
        self.conversation_id = conversation_id
        self._temp: Optional[tempfile.TemporaryDirectory[str]] = None
        if work_dir is None:
            self._temp = tempfile.TemporaryDirectory(prefix="m-agent-stimulus-lab-")
            work_dir = Path(self._temp.name)
        self.work_dir = Path(work_dir)
        self.clock = VirtualClock("2026-01-01T00:00:00Z")
        self.runtime = FakeStimulusRuntime(
            path=self.work_dir / "lab.sqlite3",
            clock=self.clock,
        )

    def close(self) -> None:
        self.runtime.store.close()
        if self._temp is not None:
            self._temp.cleanup()
            self._temp = None

    def __enter__(self) -> "StimulusLab":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _to_observation(self, event: Mapping[str, Any]) -> Observation:
        occurred_at = str(event.get("occurred_at", "") or self.clock())
        observed_at = str(event.get("observed_at", occurred_at) or occurred_at)
        self.clock.set(observed_at)
        return Observation(
            source=str(event.get("source", "stimulus_lab") or "stimulus_lab"),
            type=str(event.get("type", "external_event") or "external_event"),
            thread_id=self.thread_id,
            conversation_id=self.conversation_id,
            occurred_at=occurred_at,
            observed_at=observed_at,
            subject=str(event.get("subject", "") or ""),
            text=str(event.get("text", "") or ""),
            idempotency_key=str(event.get("idempotency_key", "") or "") or None,
            expires_at=str(event.get("expires_at", "") or "") or None,
            payload=dict(event.get("payload") or {}),
        )

    def run_scenario(self, name: str) -> ScenarioResult:
        events = SCENARIO_PACK.get(name)
        if events is None:
            return ScenarioResult(
                name=name,
                passed=False,
                details={"error": "unknown_scenario"},
            )
        results = [
            self.runtime.ingest(self._to_observation(event)).to_dict()
            for event in events
        ]
        stimuli = [
            {
                "stimulus_id": item.stimulus_id,
                "pool_state": item.pool_state,
                "disposition": item.disposition,
                "ingress_key": item.ingress_key,
                "accepted_seq": item.accepted_seq,
            }
            for item in self.runtime.store.list_stimuli(
                conversation_id=self.conversation_id
            )
        ]
        traces = self.runtime.list_stimulus_trace(
            thread_id=self.thread_id,
            limit=500,
        )
        passed = self._assert_scenario(name, results, stimuli, traces)
        if name == "valid" and passed:
            completed_id = self.runtime.claim_and_complete(self.conversation_id)
            stored = (
                self.runtime.store.load_stimulus(completed_id)
                if completed_id
                else None
            )
            passed = bool(
                stored is not None
                and stored.pool_state == PoolState.TERMINATED.value
                and stored.disposition == Disposition.COMPLETED.value
            )
            results.append({"completed_stimulus_id": completed_id})
        return ScenarioResult(
            name=name,
            passed=passed,
            details={
                "ingest_results": results,
                "stimuli": stimuli,
                "trace_count": len(traces),
            },
        )

    def _assert_scenario(
        self,
        name: str,
        results: Sequence[Mapping[str, Any]],
        stimuli: Sequence[Mapping[str, Any]],
        traces: Sequence[Mapping[str, Any]],
    ) -> bool:
        if name in {"duplicate", "chat_duplicate"}:
            return (
                len(results) == 2
                and bool(results[0].get("created"))
                and bool(results[1].get("merged"))
                and len(stimuli) == 1
                and any(
                    item.get("disposition") == Disposition.MERGED.value
                    or item.get("reason_code") == "duplicate_ingress"
                    for item in traces
                )
            )
        if name == "out_of_order":
            ready = [
                item for item in stimuli if item.get("pool_state") == "ready"
            ]
            return len(ready) == 2 and len({item["ingress_key"] for item in ready}) == 2
        if name == "expired":
            return (
                len(stimuli) == 1
                and stimuli[0].get("pool_state") == "terminated"
                and stimuli[0].get("disposition") == "discarded"
                and results[0].get("reason_code") == "expired"
            )
        if name == "irrelevant":
            return (
                len(stimuli) == 1
                and stimuli[0].get("disposition") == "discarded"
                and results[0].get("reason_code") == "irrelevant"
            )
        if name == "valid":
            return (
                len(stimuli) == 1
                and stimuli[0].get("pool_state") == "ready"
                and bool(results[0].get("created"))
            )
        return False

    def run_all(self) -> List[ScenarioResult]:
        # Fresh store per scenario for isolation.
        output: List[ScenarioResult] = []
        for name in SCENARIO_PACK:
            with StimulusLab(
                thread_id=self.thread_id,
                conversation_id=self.conversation_id,
            ) as isolated:
                output.append(isolated.run_scenario(name))
        return output


def run_stimulus_lab(
    scenarios: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    names = list(scenarios or SCENARIO_PACK.keys())
    results: List[ScenarioResult] = []
    for name in names:
        with StimulusLab() as lab:
            results.append(lab.run_scenario(name))
    return {
        "passed": all(item.passed for item in results),
        "results": [
            {"name": item.name, "passed": item.passed, "details": item.details}
            for item in results
        ],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="M-Agent Stimulus Lab")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=sorted(SCENARIO_PACK),
        help="Scenario to run (repeatable). Default: all.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = run_stimulus_lab(args.scenario)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for item in report["results"]:
            mark = "PASS" if item["passed"] else "FAIL"
            print(f"[{mark}] {item['name']}")
        print("ALL PASS" if report["passed"] else "FAILED")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
