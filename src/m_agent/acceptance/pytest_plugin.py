"""Minimal pytest result reporter used by the isolated acceptance subprocess."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List


_CASES: Dict[str, Dict[str, Any]] = {}
_ORDER: List[str] = []
_STARTED = 0.0


def pytest_configure(config: Any) -> None:
    del config
    global _STARTED
    _CASES.clear()
    _ORDER.clear()
    _STARTED = time.monotonic()


def pytest_collection_modifyitems(session: Any, config: Any, items: List[Any]) -> None:
    del session, config
    _ORDER.extend(str(item.nodeid) for item in items)


def _message(report: Any) -> str:
    try:
        return str(report.longreprtext or "")
    except Exception:
        return str(getattr(report, "longrepr", "") or "")


def pytest_runtest_logreport(report: Any) -> None:
    nodeid = str(report.nodeid)
    current = _CASES.setdefault(
        nodeid,
        {
            "nodeid": nodeid,
            "outcome": "not_run",
            "duration_seconds": 0.0,
            "message": "",
            "trace": [],
        },
    )
    current["duration_seconds"] = float(current["duration_seconds"]) + float(
        getattr(report, "duration", 0.0) or 0.0
    )

    was_xfail = bool(getattr(report, "wasxfail", False))
    if report.when == "setup":
        if report.failed:
            current["outcome"] = "error"
            current["message"] = _message(report)
        elif report.skipped:
            current["outcome"] = "xfailed" if was_xfail else "skipped"
            current["message"] = _message(report)
        return
    if report.when == "call":
        for name, value in list(getattr(report, "user_properties", []) or []):
            if name != "semantic_trace":
                continue
            try:
                parsed = json.loads(str(value))
            except Exception:
                continue
            events = parsed.get("events", []) if isinstance(parsed, dict) else parsed
            if isinstance(events, list):
                current["trace"] = [
                    dict(item) for item in events if isinstance(item, dict)
                ]
        if was_xfail and report.skipped:
            current["outcome"] = "xfailed"
        elif was_xfail and report.passed:
            current["outcome"] = "xpassed"
        else:
            current["outcome"] = str(report.outcome)
        if report.failed or report.skipped or was_xfail:
            current["message"] = _message(report)
        return
    if report.when == "teardown" and report.failed:
        current["outcome"] = "error"
        current["message"] = _message(report)


def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
    del session
    output = str(os.environ.get("M_AGENT_ACCEPTANCE_REPORT", "") or "").strip()
    if not output:
        return
    ordered = []
    seen: set[str] = set()
    for nodeid in _ORDER:
        if nodeid in _CASES:
            ordered.append(_CASES[nodeid])
            seen.add(nodeid)
    ordered.extend(value for key, value in _CASES.items() if key not in seen)
    payload = {
        "schema_version": 1,
        "exit_code": int(exitstatus),
        "duration_seconds": max(0.0, time.monotonic() - _STARTED),
        "cases": ordered,
    }
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
