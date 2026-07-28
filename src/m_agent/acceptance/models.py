"""Serializable models shared by the acceptance CLI, runner, and UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class TestRef:
    """One allowlisted pytest node and the behavior it proves."""

    nodeid: str
    proves: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class InvariantGroupSpec:
    """Stable UI grouping for related runtime semantic invariants."""

    group_id: str
    order: int
    title: str
    description: str
    invariant_ids: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.group_id,
            "order": self.order,
            "title": self.title,
            "description": self.description,
            "invariant_ids": list(self.invariant_ids),
        }


@dataclass(frozen=True)
class InvariantSpec:
    """A migration invariant with executable acceptance evidence."""

    invariant_id: str
    title: str
    principle: str
    acceptance: str
    layer: str
    risk: str
    gate_tests: Tuple[TestRef, ...]
    supporting_tests: Tuple[TestRef, ...] = ()
    known_gap: Optional[str] = None
    risk_level: str = "high"
    group_id: str = ""
    order: int = 0

    def tests_for(self, profile: str) -> Tuple[TestRef, ...]:
        if profile == "gate":
            return self.gate_tests
        if profile == "full":
            seen: set[str] = set()
            selected: List[TestRef] = []
            for ref in (*self.gate_tests, *self.supporting_tests):
                if ref.nodeid not in seen:
                    seen.add(ref.nodeid)
                    selected.append(ref)
            return tuple(selected)
        raise ValueError(f"unknown acceptance profile: {profile}")

    def to_dict(self) -> Dict[str, Any]:
        gate_count = len(self.tests_for("gate"))
        full_count = len(self.tests_for("full"))
        return {
            "id": self.invariant_id,
            "order": self.order,
            "group_id": self.group_id,
            "title": self.title,
            "principle": self.principle,
            "acceptance": self.acceptance,
            "layer": self.layer,
            "risk": self.risk_level,
            "risk_description": self.risk,
            "known_gap": self.known_gap,
            "counts": {
                "gate": gate_count,
                "full": full_count,
                "supporting": full_count - gate_count,
            },
            "gate_tests": [item.to_dict() for item in self.gate_tests],
            "supporting_tests": [item.to_dict() for item in self.supporting_tests],
            "tests": [
                {**item.to_dict(), "profile": "gate", "evidence": "gate"}
                for item in self.gate_tests
            ]
            + [
                {
                    **item.to_dict(),
                    "profile": "full",
                    "evidence": "supporting",
                }
                for item in self.supporting_tests
            ],
        }


@dataclass
class CaseResult:
    """Outcome of a single allowlisted pytest node."""

    nodeid: str
    outcome: str
    duration_seconds: float = 0.0
    message: str = ""
    trace: List[Dict[str, Any]] = field(default_factory=list)
    invariant_id: str = ""
    invariant_ids: List[str] = field(default_factory=list)
    evidence: str = ""
    proves: str = ""
    evidence_refs: List[Dict[str, str]] = field(default_factory=list)
    known_gap: Optional[str] = None
    known_gap_reasons: List[Dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CaseResult":
        return cls(
            nodeid=str(data.get("nodeid", "") or ""),
            outcome=str(data.get("outcome", "error") or "error"),
            duration_seconds=float(data.get("duration_seconds", 0.0) or 0.0),
            message=str(data.get("message", "") or ""),
            trace=[
                dict(item)
                for item in data.get("trace", [])
                if isinstance(item, dict)
            ],
            invariant_id=str(data.get("invariant_id", "") or ""),
            invariant_ids=[
                str(item)
                for item in data.get("invariant_ids", [])
                if str(item)
            ],
            evidence=str(data.get("evidence", "") or ""),
            proves=str(data.get("proves", "") or ""),
            evidence_refs=[
                {
                    str(key): str(value)
                    for key, value in item.items()
                    if value is not None
                }
                for item in data.get("evidence_refs", [])
                if isinstance(item, dict)
            ],
            known_gap=(
                str(data["known_gap"])
                if data.get("known_gap") is not None
                else None
            ),
            known_gap_reasons=[
                {
                    str(key): str(value)
                    for key, value in item.items()
                    if value is not None
                }
                for item in data.get("known_gap_reasons", [])
                if isinstance(item, dict)
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InvariantResult:
    """Aggregated result for one invariant."""

    invariant_id: str
    title: str
    status: str
    cases: List[CaseResult] = field(default_factory=list)
    known_gap: Optional[str] = None
    group_id: str = ""
    order: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.invariant_id,
            "order": self.order,
            "group_id": self.group_id,
            "title": self.title,
            "status": self.status,
            "known_gap": self.known_gap,
            "cases": [item.to_dict() for item in self.cases],
        }


@dataclass
class RunResult:
    """Complete persisted result of one acceptance run."""

    run_id: str
    suite: str
    profile: str
    status: str
    invariant_ids: List[str]
    started_at: str
    finished_at: str
    duration_seconds: float
    pytest_exit_code: Optional[int]
    cases: List[CaseResult]
    invariants: List[InvariantResult]
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    artifacts: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunResult":
        return cls(
            run_id=str(data.get("run_id", "") or ""),
            suite=str(data.get("suite", "phase0") or "phase0"),
            profile=str(data.get("profile", "gate") or "gate"),
            status=str(data.get("status", "error") or "error"),
            invariant_ids=[
                str(item) for item in data.get("invariant_ids", []) if str(item)
            ],
            started_at=str(data.get("started_at", "") or ""),
            finished_at=str(data.get("finished_at", "") or ""),
            duration_seconds=float(data.get("duration_seconds", 0.0) or 0.0),
            pytest_exit_code=(
                int(data["pytest_exit_code"])
                if data.get("pytest_exit_code") is not None
                else None
            ),
            cases=[
                CaseResult.from_dict(item)
                for item in data.get("cases", [])
                if isinstance(item, dict)
            ],
            invariants=[
                InvariantResult(
                    invariant_id=str(item.get("id", "") or ""),
                    title=str(item.get("title", "") or ""),
                    status=str(item.get("status", "error") or "error"),
                    known_gap=item.get("known_gap"),
                    group_id=str(item.get("group_id", "") or ""),
                    order=int(item.get("order", 0) or 0),
                    cases=[
                        CaseResult.from_dict(case)
                        for case in item.get("cases", [])
                        if isinstance(case, dict)
                    ],
                )
                for item in data.get("invariants", [])
                if isinstance(item, dict)
            ],
            stdout=str(data.get("stdout", "") or ""),
            stderr=str(data.get("stderr", "") or ""),
            error=str(data.get("error", "") or ""),
            artifacts={
                str(key): str(value)
                for key, value in dict(data.get("artifacts", {}) or {}).items()
            },
        )

    def to_dict(self, *, include_logs: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "run_id": self.run_id,
            "suite": self.suite,
            "profile": self.profile,
            "status": self.status,
            "invariant_ids": list(self.invariant_ids),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "pytest_exit_code": self.pytest_exit_code,
            "cases": [item.to_dict() for item in self.cases],
            "invariants": [item.to_dict() for item in self.invariants],
            "error": self.error,
            "artifacts": dict(self.artifacts),
        }
        if include_logs:
            payload["stdout"] = self.stdout
            payload["stderr"] = self.stderr
        return payload


def unique_nodeids(
    invariants: Iterable[InvariantSpec],
    *,
    profile: str,
) -> List[str]:
    """Return stable, de-duplicated node IDs for the selected specifications."""

    seen: set[str] = set()
    nodeids: List[str] = []
    for invariant in invariants:
        for test in invariant.tests_for(profile):
            if test.nodeid not in seen:
                seen.add(test.nodeid)
                nodeids.append(test.nodeid)
    return nodeids


def annotate_cases(
    cases: Iterable[CaseResult],
    invariants: Iterable[InvariantSpec],
    *,
    profile: str,
) -> List[CaseResult]:
    """Attach catalog evidence to de-duplicated pytest case results."""

    refs_by_nodeid: Dict[str, List[Dict[str, str]]] = {}
    gaps_by_nodeid: Dict[str, List[Dict[str, str]]] = {}
    for invariant in invariants:
        gate_nodeids = {item.nodeid for item in invariant.gate_tests}
        for ref in invariant.tests_for(profile):
            evidence = "gate" if ref.nodeid in gate_nodeids else "supporting"
            refs_by_nodeid.setdefault(ref.nodeid, []).append(
                {
                    "invariant_id": invariant.invariant_id,
                    "evidence": evidence,
                    "proves": ref.proves,
                }
            )
            if invariant.known_gap:
                gaps_by_nodeid.setdefault(ref.nodeid, []).append(
                    {
                        "invariant_id": invariant.invariant_id,
                        "reason": invariant.known_gap,
                    }
                )

    annotated: List[CaseResult] = []
    for case in cases:
        evidence_refs = refs_by_nodeid.get(case.nodeid, case.evidence_refs)
        invariant_ids = list(
            dict.fromkeys(
                ref["invariant_id"]
                for ref in evidence_refs
                if ref.get("invariant_id")
            )
        )
        primary = evidence_refs[0] if evidence_refs else {}
        mapped_gap_reasons = (
            gaps_by_nodeid.get(case.nodeid, [])
            if case.outcome == "xfailed"
            else []
        )
        known_gap_reasons = (
            mapped_gap_reasons or list(case.known_gap_reasons)
        )
        annotated.append(
            CaseResult(
                nodeid=case.nodeid,
                outcome=case.outcome,
                duration_seconds=case.duration_seconds,
                message=case.message,
                trace=list(case.trace),
                invariant_id=str(primary.get("invariant_id", case.invariant_id)),
                invariant_ids=invariant_ids or list(case.invariant_ids),
                evidence=str(primary.get("evidence", case.evidence)),
                proves=str(primary.get("proves", case.proves)),
                evidence_refs=[dict(item) for item in evidence_refs],
                known_gap=(
                    known_gap_reasons[0]["reason"]
                    if known_gap_reasons
                    else case.known_gap
                ),
                known_gap_reasons=[
                    dict(item) for item in known_gap_reasons
                ],
            )
        )
    return annotated


def aggregate_invariant(
    spec: InvariantSpec,
    case_by_nodeid: Dict[str, CaseResult],
    *,
    profile: str,
    run_status: str,
) -> InvariantResult:
    """Aggregate pytest outcomes without hiding expected failures."""

    cases: List[CaseResult] = []
    for ref in spec.tests_for(profile):
        cases.append(
            case_by_nodeid.get(
                ref.nodeid,
                CaseResult(
                    nodeid=ref.nodeid,
                    outcome="not_run",
                    message="The allowlisted test did not produce a pytest report.",
                    invariant_id=spec.invariant_id,
                    invariant_ids=[spec.invariant_id],
                    evidence=(
                        "gate"
                        if ref.nodeid
                        in {item.nodeid for item in spec.gate_tests}
                        else "supporting"
                    ),
                    proves=ref.proves,
                    evidence_refs=[
                        {
                            "invariant_id": spec.invariant_id,
                            "evidence": (
                                "gate"
                                if ref.nodeid
                                in {
                                    item.nodeid
                                    for item in spec.gate_tests
                                }
                                else "supporting"
                            ),
                            "proves": ref.proves,
                        }
                    ],
                ),
            )
        )

    outcomes = {item.outcome for item in cases}
    if run_status in {"cancelled", "timeout"} and "not_run" in outcomes:
        status = run_status
    elif outcomes & {"failed", "error", "xpassed", "not_run"}:
        status = "failed"
    elif "xfailed" in outcomes:
        status = "known_gap"
    elif outcomes == {"skipped"} or (outcomes and outcomes <= {"passed", "skipped"} and "skipped" in outcomes):
        status = "skipped"
    else:
        status = "passed"
    return InvariantResult(
        invariant_id=spec.invariant_id,
        title=spec.title,
        status=status,
        cases=cases,
        known_gap=spec.known_gap,
        group_id=spec.group_id,
        order=spec.order,
    )
