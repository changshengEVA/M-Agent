from __future__ import annotations

from m_agent.utils.time_utils import get_current_time_context


def test_get_current_time_context_supports_utc() -> None:
    payload = get_current_time_context("UTC")

    assert payload["ok"] is True
    assert payload["timezone_name"] == "UTC"
    assert payload["timezone_source"] == "requested"
    assert payload["utc_offset"] == "+00:00"
    assert payload["relative_dates"]["today"]["date"] == payload["local_date"]


def test_get_current_time_context_rejects_unknown_timezone() -> None:
    payload = get_current_time_context("Mars/Olympus_Mons")

    assert payload["ok"] is False
    assert payload["requested_timezone_name"] == "Mars/Olympus_Mons"
    assert "Unknown timezone" in payload["error"]
