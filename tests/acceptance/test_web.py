from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from fastapi.testclient import TestClient

from m_agent.acceptance.web import create_app


class _Store:
    def artifact_path(self, run_id: str, artifact_name: str) -> Path:
        del run_id, artifact_name
        return Path("missing")


class _Runner:
    store = _Store()


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


def _client() -> tuple[TestClient, _Manager]:
    manager = _Manager()
    app = create_app(runner=_Runner(), manager=manager)  # type: ignore[arg-type]
    return TestClient(app), manager


def test_local_dashboard_and_catalog_are_served_with_security_headers() -> None:
    client, _manager = _client()
    page = client.get("/")
    catalog = client.get("/api/catalog")

    assert page.status_code == 200
    assert "Semantic Gate" in page.text
    assert page.headers["x-frame-options"] == "DENY"
    assert catalog.status_code == 200
    catalog_payload = catalog.json()
    assert len(catalog_payload["invariants"]) == 14
    assert len(catalog_payload["groups"]) == 5
    assert catalog_payload["profile_counts"]["gate"]["case_count"] == 20
    assert catalog_payload["profile_counts"]["full"]["case_count"] == 36


def test_dashboard_assets_expose_hierarchy_profiles_and_diagnostics() -> None:
    client, _manager = _client()

    page = client.get("/")
    script = client.get("/app.js")
    style = client.get("/style.css")

    assert page.status_code == script.status_code == style.status_code == 200
    for marker in (
        'id="gateProfileCount"',
        'id="fullProfileCount"',
        'id="groupFilter"',
        'id="invariantList"',
        'id="diagnosticsList"',
        "关键语义分层验收",
        "Semantic Full",
    ):
        assert marker in page.text
    assert "profile_counts" in script.text
    assert "createDomainGroup" in script.text
    assert "createTraceTimeline" in script.text
    assert "status-gap" in style.text
    assert "status-failed" in style.text


def test_run_api_accepts_only_structured_allowlist_selection() -> None:
    client, manager = _client()
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


def test_unknown_run_and_artifact_are_not_path_endpoints() -> None:
    client, _manager = _client()
    assert client.get("/api/runs/run_aaaaaaaaaaaaaaaaaaaa").status_code == 404
    assert client.get("/api/runs/../../pyproject.toml").status_code == 404
