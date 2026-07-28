from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from m_agent.acceptance.models import (
    CaseResult,
    aggregate_invariant,
    annotate_cases,
)
from m_agent.acceptance.runner import AcceptanceRunner
from m_agent.acceptance.catalog import get_invariants


def test_runner_command_contains_only_fixed_pytest_controls_and_nodeids(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    runner = AcceptanceRunner(
        project_root=project_root,
        artifact_root=tmp_path / "artifacts",
        python_executable="python-for-test",
    )
    paths = runner.store.prepare(runner.store.new_run_id())
    nodeid = get_invariants(["INV-04"])[0].gate_tests[0].nodeid

    command = runner._command(nodeids=[nodeid], paths=paths)

    assert command[0:3] == ["python-for-test", "-m", "pytest"]
    assert "addopts=" in command
    assert "--maxfail=0" in command
    assert "m_agent.acceptance.pytest_plugin" in command
    assert command[-1] == nodeid
    assert all(";" not in item and "&&" not in item for item in command)


def test_runner_environment_does_not_forward_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = Path(__file__).resolve().parents[2]
    runner = AcceptanceRunner(
        project_root=project_root,
        artifact_root=tmp_path / "artifacts",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("GMAIL_TOKEN", "must-not-leak")

    env = runner._environment(tmp_path, tmp_path / "report.json")

    assert "OPENAI_API_KEY" not in env
    assert "GMAIL_TOKEN" not in env
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert env["M_AGENT_ACCEPTANCE_MODE"] == "1"
    assert env["PYTHONPATH"] == str(project_root / "src")


def test_unknown_invariant_is_rejected_before_subprocess(tmp_path: Path) -> None:
    runner = AcceptanceRunner(
        project_root=Path(__file__).resolve().parents[2],
        artifact_root=tmp_path / "artifacts",
    )
    with pytest.raises(ValueError, match="unknown invariant"):
        runner.run(invariant_ids=["INV-99"])


def test_invalid_run_id_is_rejected_without_poisoning_runner_lock(
    tmp_path: Path,
) -> None:
    runner = AcceptanceRunner(
        project_root=Path(__file__).resolve().parents[2],
        artifact_root=tmp_path / "artifacts",
    )

    with pytest.raises(ValueError, match="invalid acceptance run id"):
        runner.run(invariant_ids=["INV-04"], run_id="../escape")

    assert runner._lock.acquire(blocking=False)
    runner._lock.release()


def test_expected_failure_is_reported_as_known_gap() -> None:
    spec = get_invariants(["INV-13"])[0]
    nodeid = spec.gate_tests[0].nodeid
    result = aggregate_invariant(
        spec,
        {nodeid: CaseResult(nodeid=nodeid, outcome="xfailed")},
        profile="gate",
        run_status="passed",
    )
    assert result.status == "known_gap"
    assert result.known_gap
    assert result.group_id == "recovery_idempotency"
    assert result.order == 13


def test_case_annotation_preserves_all_invariant_evidence_for_shared_node() -> None:
    first = get_invariants(["INV-13"])[0]
    second = replace(
        first,
        invariant_id="INV-99",
        known_gap="第二项不变量的结构化缺口原因。",
    )
    nodeid = first.gate_tests[0].nodeid

    annotated = annotate_cases(
        [CaseResult(nodeid=nodeid, outcome="xfailed")],
        [first, second],
        profile="gate",
    )

    assert len(annotated) == 1
    case = annotated[0]
    assert case.invariant_id == "INV-13"
    assert case.invariant_ids == ["INV-13", "INV-99"]
    assert case.evidence == "gate"
    assert case.proves == first.gate_tests[0].proves
    assert case.evidence_refs == [
        {
            "invariant_id": "INV-13",
            "evidence": "gate",
            "proves": first.gate_tests[0].proves,
        },
        {
            "invariant_id": "INV-99",
            "evidence": "gate",
            "proves": first.gate_tests[0].proves,
        },
    ]
    assert case.known_gap == first.known_gap
    assert case.known_gap_reasons == [
        {"invariant_id": "INV-13", "reason": first.known_gap},
        {
            "invariant_id": "INV-99",
            "reason": "第二项不变量的结构化缺口原因。",
        },
    ]
    assert CaseResult.from_dict(case.to_dict()) == case
