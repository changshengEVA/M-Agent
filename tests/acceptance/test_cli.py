from __future__ import annotations

import json
from pathlib import Path

import pytest

from m_agent.acceptance.cli import main


def test_cli_coverage_reports_complete_catalog(capsys) -> None:
    project_root = Path(__file__).resolve().parents[2]
    exit_code = main(
        [
            "--project-root",
            str(project_root),
            "coverage",
            "--format",
            "text",
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "specified=14/14" in output
    assert "complete=true" in output


def test_cli_list_json_exposes_invariant_ids(capsys) -> None:
    project_root = Path(__file__).resolve().parents[2]
    exit_code = main(
        [
            "--project-root",
            str(project_root),
            "list",
            "--format",
            "json",
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"INV-01"' in output
    assert '"INV-14"' in output


def test_cli_contract_coverage_reports_complete_executable_matrix(capsys) -> None:
    project_root = Path(__file__).resolve().parents[2]
    exit_code = main(
        [
            "--project-root",
            str(project_root),
            "contract",
            "coverage",
            "--format",
            "text",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "scenarios=28/28" in output
    assert "core=27/27" in output
    assert "robustness=7/7" in output
    assert "matcher=1/1" in output
    assert "catalog_complete=true" in output
    assert "p1_exit_ready=true" in output


def test_cli_contract_list_json_reports_every_variant_executable(
    capsys,
) -> None:
    """P8 closed the matrix: all 38 variants are executable on both runtimes."""

    project_root = Path(__file__).resolve().parents[2]
    exit_code = main(
        [
            "--project-root",
            str(project_root),
            "contract",
            "list",
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    total_variants = sum(
        len(scenario["variants"]) for scenario in payload["scenarios"]
    )

    assert exit_code == 0
    assert payload["coverage"]["p1_exit_ready"] is True
    assert total_variants == 38
    assert payload["execution"]["executable_variants_by_runtime"] == {
        "think_life_v1": total_variants,
        "langgraph_v1": total_variants,
    }


def test_cli_contract_list_json_exposes_runtime_availability(capsys) -> None:
    project_root = Path(__file__).resolve().parents[2]
    exit_code = main(
        [
            "--project-root",
            str(project_root),
            "contract",
            "list",
            "--format",
            "json",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"TX-01"' in output
    assert '"AT-10"' in output
    assert '"think_life_v1"' in output
    assert '"langgraph_v1"' in output
    assert '"matcher_evaluation"' in output
    assert '"executable_variants_by_runtime"' in output
    assert '"availability": "executable"' in output
    assert '"not_implemented"' not in output


@pytest.mark.parametrize(
    ("host", "expected_url"),
    [
        ("127.0.0.1", "http://127.0.0.1:8788/"),
        ("localhost", "http://localhost:8788/"),
        ("::1", "http://[::1]:8788/"),
    ],
)
def test_cli_ui_prints_browser_url_before_starting_server(
    monkeypatch,
    capsys,
    host: str,
    expected_url: str,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    calls = []

    monkeypatch.setattr(
        "m_agent.acceptance.web.create_app",
        lambda *, runner: ("app", runner),
    )

    def fake_run(app, **kwargs) -> None:
        output_before_run = capsys.readouterr().out
        calls.append((app, kwargs, output_before_run))

    monkeypatch.setattr("uvicorn.run", fake_run)

    exit_code = main(
        [
            "--project-root",
            str(project_root),
            "ui",
            "--host",
            host,
            "--port",
            "8788",
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    app, kwargs, output_before_run = calls[0]
    assert app[0] == "app"
    assert kwargs == {
        "host": host,
        "port": 8788,
        "log_level": "warning",
    }
    assert expected_url in output_before_run
    assert "Ctrl+C" in output_before_run


@pytest.mark.parametrize("port", ["0", "65536", "-1", "not-a-port"])
def test_cli_ui_rejects_invalid_port(port: str, capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["ui", "--port", port])

    assert exc_info.value.code == 2
    assert "port must" in capsys.readouterr().err
