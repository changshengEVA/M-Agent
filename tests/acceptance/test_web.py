from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional, Sequence

from fastapi.testclient import TestClient

from m_agent.acceptance.manager import RunManager
from m_agent.acceptance.web import create_app


class _Store:
    def __init__(self) -> None:
        self.result_kinds: Dict[str, str] = {}
        self.artifact_paths: Dict[tuple[str, str], Path] = {}

    @staticmethod
    def validate_run_id(run_id: str) -> str:
        return run_id

    def load_result(self, run_id: str) -> Optional[SimpleNamespace]:
        result_kind = self.result_kinds.get(run_id)
        if result_kind is None:
            return None
        return SimpleNamespace(
            result_kind=result_kind,
            profile="gate",
            invariant_ids=[],
            status="known_gaps",
            error="",
            to_dict=lambda **_kwargs: {
                "result_kind": result_kind,
                "status": "known_gaps",
            },
        )

    def artifact_path(self, run_id: str, artifact_name: str) -> Path:
        return self.artifact_paths.get(
            (run_id, artifact_name),
            Path("missing"),
        )


class _Runner:
    def __init__(self, store: Optional[_Store] = None) -> None:
        self.store = store or _Store()


class _Manager:
    def __init__(self) -> None:
        self.last_start: Optional[Dict[str, Any]] = None

    def start(
        self,
        *,
        invariant_ids: Optional[Sequence[str]],
        profile: str,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        self.last_start = {
            "invariant_ids": list(invariant_ids or []),
            "profile": profile,
            "timeout_seconds": timeout_seconds,
        }
        return {
            "run_id": "run_1234567890abcdef1234",
            "status": "queued",
            "profile": profile,
            "invariant_ids": list(invariant_ids or []),
        }

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        if run_id != "run_1234567890abcdef1234":
            return None
        return {
            "run_id": run_id,
            "status": "passed",
            "result": {"invariants": [], "cases": []},
        }

    def cancel(self, run_id: str) -> bool:
        return run_id == "run_1234567890abcdef1234"


class _ScenarioManager:
    def __init__(self) -> None:
        self.last_start: Optional[Dict[str, Any]] = None
        self.last_latest_runtime: Optional[str] = None

    def start(
        self,
        *,
        scenario_ids: Optional[Sequence[str]],
        runtime_id: str,
        layers: Sequence[str],
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        self.last_start = {
            "scenario_ids": list(scenario_ids or []),
            "runtime_id": runtime_id,
            "layers": list(layers),
            "timeout_seconds": timeout_seconds,
        }
        return {
            "run_id": "run_contract1234567890ab",
            "status": "queued",
            **self.last_start,
        }

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        if run_id != "run_contract1234567890ab":
            return None
        return {
            "run_id": run_id,
            "status": "known_gaps",
            "result": {
                "result_kind": "scenario_contract",
                "runtime_id": "langgraph_v1",
                "scenarios": [],
            },
        }

    def latest(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        self.last_latest_runtime = runtime_id
        if runtime_id != "langgraph_v1":
            raise ValueError(f"unknown Runtime id: {runtime_id}")
        return self.get("run_contract1234567890ab")

    def cancel(self, run_id: str) -> bool:
        return run_id == "run_contract1234567890ab"


def _client(
    *,
    store: Optional[_Store] = None,
    manager: Optional[Any] = None,
) -> tuple[TestClient, Any, _ScenarioManager]:
    active_store = store or _Store()
    active_manager = manager or _Manager()
    scenario_manager = _ScenarioManager()
    app = create_app(
        runner=_Runner(active_store),
        manager=active_manager,
        scenario_runner=_Runner(active_store),
        scenario_manager=scenario_manager,
    )  # type: ignore[arg-type]
    return TestClient(app), active_manager, scenario_manager


def test_local_dashboard_and_catalog_are_served_with_security_headers() -> None:
    client, _manager, _scenario_manager = _client()
    page = client.get("/")
    catalog = client.get("/api/catalog")
    contract_catalog = client.get("/api/contract/catalog")

    assert page.status_code == 200
    assert 'id="runtimeSelect"' in page.text
    assert 'id="evidencePanel"' in page.text
    assert page.headers["x-frame-options"] == "DENY"
    assert catalog.status_code == 200
    catalog_payload = catalog.json()
    assert len(catalog_payload["invariants"]) == 14
    assert len(catalog_payload["groups"]) == 5
    assert catalog_payload["profile_counts"]["gate"]["case_count"] == 20
    assert catalog_payload["profile_counts"]["full"]["case_count"] == 36
    assert contract_catalog.status_code == 200
    contract_payload = contract_catalog.json()
    assert contract_payload["coverage"]["specified_core"] == 27
    assert contract_payload["coverage"]["specified_robustness"] == 7
    assert contract_payload["coverage"]["specified_matcher"] == 1
    assert contract_payload["coverage"]["p1_exit_ready"] is True
    total_variants = sum(
        len(scenario["variants"]) for scenario in contract_payload["scenarios"]
    )
    assert total_variants == 38
    assert contract_payload["execution"]["executable_variants_by_runtime"] == {
        "langgraph_v1": total_variants,
    }


def test_default_dashboard_is_p1_only_and_legacy_suite_is_separate() -> None:
    client, _manager, _scenario_manager = _client()

    page = client.get("/")
    script = client.get("/app.js")
    style = client.get("/p1.css")
    legacy_page = client.get("/legacy")
    legacy_script = client.get("/legacy.js")

    assert (
        page.status_code
        == script.status_code
        == style.status_code
        == legacy_page.status_code
        == legacy_script.status_code
        == 200
    )
    assert style.headers["content-type"].startswith("text/css")
    assert legacy_script.headers["content-type"].startswith(
        "application/javascript"
    )
    for marker in (
        'id="runtimeSelect"',
        'id="runButton"',
        'id="runStatus"',
        'id="metricExecuted"',
        'id="metricPassed"',
        'id="metricGaps"',
        'id="metricUnexpected"',
        'id="domainOverview"',
        'id="contractFilters"',
        'id="variantList"',
        'id="evidencePanel"',
        'href="/legacy"',
    ):
        assert marker in page.text

    for retired_marker in (
        'id="profileGate"',
        'id="profileFull"',
        'id="gateProfileCount"',
        'id="fullProfileCount"',
        'id="groupFilter"',
        'id="invariantList"',
        'id="diagnosticsList"',
    ):
        assert retired_marker not in page.text

    assert "buildContractRows" in script.text
    assert "renderEvidenceDetail" in script.text
    assert "loadContractCatalog" in script.text
    assert "startContractRun" in script.text
    assert "cancelContractRun" in script.text
    assert "restoreLastContractRun" in script.text
    assert "known_gap_key" in script.text
    assert "Expected" in script.text
    assert "Actual" in script.text
    assert "/contract/runs" in script.text
    assert "loadCatalog()" not in script.text
    assert "EXPECTED_INVARIANT_COUNT" not in script.text

    assert ".results-shell" in style.text
    assert ".variant-list" in style.text
    assert ".evidence-panel" in style.text
    assert "status-gap" in style.text
    assert "status-failed" in style.text

    assert "Legacy Supporting Tests" in legacy_page.text
    assert 'id="gateProfileCount"' in legacy_page.text
    assert 'id="invariantList"' in legacy_page.text
    assert 'src="./legacy.js"' in legacy_page.text
    assert "EXPECTED_INVARIANT_COUNT" in legacy_script.text
    assert "loadCatalog()" in legacy_script.text


def test_legacy_api_remains_available_after_ui_separation() -> None:
    client, _manager, _scenario_manager = _client()

    style = client.get("/style.css")
    catalog = client.get("/api/catalog")
    run = client.get("/api/runs/run_1234567890abcdef1234")

    assert style.status_code == 200
    assert catalog.status_code == 200
    assert len(catalog.json()["invariants"]) == 14
    assert run.status_code == 200
    assert run.json()["status"] == "passed"


def test_run_api_accepts_only_structured_allowlist_selection() -> None:
    client, manager, _scenario_manager = _client()
    response = client.post(
        "/api/runs",
        json={"invariant_ids": ["INV-04"], "profile": "gate"},
    )
    assert response.status_code == 202
    assert response.json()["run_id"] == "run_1234567890abcdef1234"
    assert manager.last_start == {
        "invariant_ids": ["INV-04"],
        "profile": "gate",
        "timeout_seconds": 300.0,
    }

    rejected = client.post(
        "/api/runs",
        json={"invariant_ids": ["INV-04"], "pytest_args": ["--pdb"]},
    )
    assert rejected.status_code == 400
    assert "unsupported field" in rejected.json()["detail"]


def test_contract_run_api_accepts_only_structured_scenario_selection() -> None:
    client, _manager, scenario_manager = _client()
    response = client.post(
        "/api/contract/runs",
        json={
            "scenario_ids": ["TX-01", "AT-10"],
            "runtime_id": "langgraph_v1",
            "layers": ["core", "matcher_evaluation"],
            "timeout_seconds": 90,
        },
    )

    assert response.status_code == 202
    assert response.json()["run_id"] == "run_contract1234567890ab"
    assert scenario_manager.last_start == {
        "scenario_ids": ["TX-01", "AT-10"],
        "runtime_id": "langgraph_v1",
        "layers": ["core", "matcher_evaluation"],
        "timeout_seconds": 90.0,
    }
    assert (
        client.get(
            "/api/contract/runs/run_contract1234567890ab"
        ).json()["status"]
        == "known_gaps"
    )
    cancelled = client.post(
        "/api/contract/runs/run_contract1234567890ab/cancel"
    )
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "known_gaps"

    rejected = client.post(
        "/api/contract/runs",
        json={"scenario_ids": ["TX-01"], "pytest_args": ["--pdb"]},
    )
    assert rejected.status_code == 400
    assert "unsupported field" in rejected.json()["detail"]


def test_latest_contract_run_is_scoped_to_langgraph() -> None:
    client, _manager, scenario_manager = _client()

    latest = client.get(
        "/api/contract/runs/latest",
        params={"runtime_id": "langgraph_v1"},
    )
    assert latest.status_code == 200
    assert latest.json()["run_id"] == "run_contract1234567890ab"
    assert latest.json()["result"]["runtime_id"] == "langgraph_v1"
    assert scenario_manager.last_latest_runtime == "langgraph_v1"

    invalid = client.get(
        "/api/contract/runs/latest",
        params={"runtime_id": "unknown_runtime"},
    )
    assert invalid.status_code == 400


def test_artifact_routes_enforce_result_kind_boundary(tmp_path: Path) -> None:
    legacy_run_id = "run_11111111111111111111"
    contract_run_id = "run_22222222222222222222"
    legacy_summary = tmp_path / "legacy-summary.json"
    contract_summary = tmp_path / "contract-summary.json"
    legacy_summary.write_text('{"result_kind":"legacy_invariants"}', encoding="utf-8")
    contract_summary.write_text(
        '{"result_kind":"scenario_contract"}',
        encoding="utf-8",
    )
    store = _Store()
    store.result_kinds = {
        legacy_run_id: "legacy_invariants",
        contract_run_id: "scenario_contract",
    }
    store.artifact_paths = {
        (legacy_run_id, "summary"): legacy_summary,
        (contract_run_id, "summary"): contract_summary,
    }
    client, _manager, _scenario_manager = _client(store=store)

    legacy_artifact = client.get(
        f"/api/runs/{legacy_run_id}/artifacts/summary"
    )
    contract_artifact = client.get(
        f"/api/contract/runs/{contract_run_id}/artifacts/summary"
    )
    assert legacy_artifact.status_code == 200
    assert legacy_artifact.json()["result_kind"] == "legacy_invariants"
    assert contract_artifact.status_code == 200
    assert contract_artifact.json()["result_kind"] == "scenario_contract"

    assert (
        client.get(
            f"/api/runs/{contract_run_id}/artifacts/summary"
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/contract/runs/{legacy_run_id}/artifacts/summary"
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/contract/runs/{contract_run_id}/artifacts/unknown"
        ).status_code
        == 404
    )


def test_legacy_run_lookup_rejects_p1_persisted_result() -> None:
    contract_run_id = "run_33333333333333333333"
    store = _Store()
    store.result_kinds[contract_run_id] = "scenario_contract"
    manager = RunManager(_Runner(store))  # type: ignore[arg-type]
    client, _manager, _scenario_manager = _client(
        store=store,
        manager=manager,
    )

    response = client.get(f"/api/runs/{contract_run_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "run not found"


def test_unknown_run_and_artifact_are_not_path_endpoints() -> None:
    client, _manager, _scenario_manager = _client()
    assert client.get("/api/runs/run_aaaaaaaaaaaaaaaaaaaa").status_code == 404
    assert client.get("/api/runs/../../pyproject.toml").status_code == 404
    assert (
        client.get(
            "/api/contract/runs/run_aaaaaaaaaaaaaaaaaaaa"
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/contract/runs/run_aaaaaaaaaaaaaaaaaaaa/artifacts/summary"
        ).status_code
        == 404
    )
