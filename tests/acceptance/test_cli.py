from __future__ import annotations

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
