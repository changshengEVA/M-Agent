"""Helpers for emitting deterministic P1 contract evidence."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..scenario_contract import ScenarioObservation
from ..trace import SemanticTrace
from .base import ScenarioExecution


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.name
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (set, frozenset)):
        items = [_json_value(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


class EvidenceBuilder:
    """Collect facts and explicit target-vs-actual checks for one variant."""

    def __init__(
        self,
        *,
        scenario_id: str,
        variant_id: str,
        runtime_id: str,
    ) -> None:
        self.scenario_id = scenario_id
        self.variant_id = variant_id
        self.runtime_id = runtime_id
        self.trace = SemanticTrace(scenario_id=scenario_id)
        self._facts: Dict[str, Any] = {}
        self._checks: List[Dict[str, Any]] = []
        self.trace.record(
            "scenario_started",
            phase="arrange",
            source=runtime_id,
            variant_id=variant_id,
        )

    def fact(self, key: str, value: Any) -> Any:
        normalized = _json_value(value)
        self._facts[str(key)] = normalized
        return normalized

    def event(
        self,
        event_type: str,
        *,
        phase: str,
        source: str,
        **data: Any,
    ) -> None:
        self.trace.record(
            event_type,
            phase=phase,
            source=source,
            **_json_value(data),
        )

    def check(
        self,
        check_id: str,
        description: str,
        *,
        actual: Any,
        expected: Any = True,
        evidence: str = "",
        known_gap_key: str = "",
        predicate: Optional[Callable[[Any], bool]] = None,
    ) -> bool:
        normalized_actual = _json_value(actual)
        normalized_expected = _json_value(expected)
        if predicate is None:
            passed = normalized_actual == normalized_expected
        else:
            try:
                passed = bool(predicate(actual))
            except Exception:
                passed = False
        item = {
            "id": str(check_id),
            "description": str(description),
            "expected": normalized_expected,
            "actual": normalized_actual,
            "passed": passed,
            "evidence": str(evidence),
            # A Known Gap may explain a failing target check, but it must
            # never mask a check that the Runtime now satisfies.
            "known_gap_key": str(known_gap_key) if not passed else "",
        }
        self._checks.append(item)
        self.trace.record(
            "contract_check",
            phase="assert",
            source=evidence or self.runtime_id,
            check_id=check_id,
            passed=passed,
        )
        return passed

    def unsupported(
        self,
        check_id: str,
        description: str,
        *,
        capability: str,
        reason: str,
        known_gap_key: str,
    ) -> None:
        self.check(
            check_id,
            description,
            actual={
                "supported": False,
                "capability": capability,
                "reason": reason,
            },
            expected={"supported": True},
            evidence=f"{self.runtime_id}.capabilities",
            known_gap_key=known_gap_key,
        )

    def build(self, **extra: Any) -> ScenarioExecution:
        passed = sum(1 for item in self._checks if item["passed"])
        failed = len(self._checks) - passed
        normalized_extra = _json_value(extra)
        normalized_facts = dict(self._facts)
        if isinstance(normalized_extra, dict):
            for key, value in normalized_extra.items():
                normalized_facts.setdefault(str(key), value)
        data = {
            "summary": {
                "total_checks": len(self._checks),
                "passed_checks": passed,
                "failed_checks": failed,
            },
            "facts": normalized_facts,
            "checks": list(self._checks),
        }
        data.update(normalized_extra)
        self.trace.record(
            "observation_emitted",
            phase="observe",
            source=self.runtime_id,
            total_checks=len(self._checks),
            failed_checks=failed,
        )
        return ScenarioExecution(
            observation=ScenarioObservation(
                scenario_id=self.scenario_id,
                variant_id=self.variant_id,
                runtime_id=self.runtime_id,
                data=data,
            ),
            trace=self.trace,
        )
