from __future__ import annotations

import json
from pathlib import Path

import yaml
import pytest

from m_agent.agents.schedule_agent import ScheduleAgent
from m_agent.schedule.models import ScheduleItem


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)


def _build_agent(tmp_path: Path) -> ScheduleAgent:
    config_path = tmp_path / "config" / "agents" / "schedule" / "schedule_agent.yaml"
    _write_yaml(
        config_path,
        {
            "provider": "local_schedule",
            "default_timezone_name": "Asia/Shanghai",
            "storage_dir": "./schedule_data",
            "execution": {
                "query_limit_default": 10,
                "query_limit_max": 50,
            },
        },
    )
    return ScheduleAgent(config_path=config_path)


def _fixed_now_context() -> dict:
    return {
        "iso_datetime": "2026-04-05T08:00:00+08:00",
    }


def test_empty_default_storage_uses_writable_data_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "schedule_agent.yaml"
    _write_yaml(
        config_path,
        {
            "provider": "local_schedule",
            "default_timezone_name": "UTC",
            "storage_dir": "",
        },
    )
    data_root = tmp_path / "runtime-data"
    monkeypatch.setenv("M_AGENT_DATA_DIR", str(data_root))

    agent = ScheduleAgent(config_path=config_path)

    assert agent.store.storage_root == (data_root / "schedules").resolve()


def test_schedule_create_query_delete_flow(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)
    thread_id = "demo-thread"

    created = agent.handle_create_command(
        thread_id=thread_id,
        due_at="2026-04-06T09:00:00+08:00",
        deferred_objective="提醒用户准备参加会议",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert created["success"] is True
    assert created["action"] == "create"
    assert created["schedule_id"].startswith("sch_")
    assert created["item"]["text"] == "提醒用户准备参加会议"
    assert created["item"]["deferred_objective"] == {
        "description": "提醒用户准备参加会议",
        "encoding": "native",
    }
    assert created["item"]["status"] == "pending"

    queried = agent.handle_query_command(
        thread_id=thread_id,
        keyword="会议",
        start_at="2026-04-06T00:00:00+08:00",
        end_at="2026-04-06T23:59:59+08:00",
        timezone_name="Asia/Shanghai",
    )
    assert queried["success"] is True
    assert queried["count"] == 1
    assert created["schedule_id"] in queried["schedule_ids"]

    deleted = agent.handle_delete_command(
        thread_id=thread_id,
        schedule_id=created["schedule_id"],
    )
    assert deleted["success"] is True
    assert deleted["action"] == "delete"
    assert deleted["item"]["status"] == "canceled"

    empty = agent.handle_query_command(
        thread_id=thread_id,
        keyword="会议",
        timezone_name="Asia/Shanghai",
    )
    assert empty["success"] is True
    assert empty["count"] == 0


def test_schedule_create_marks_partial_for_bulk_text(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)
    result = agent.handle_create_command(
        thread_id="demo-thread",
        due_at="2026-04-06T06:00:00+08:00",
        deferred_objective="每天6点提醒用户起床；持续一周。",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert result.get("success") is True
    assert result.get("count") == 1
    assert result.get("partial") is True
    assert result.get("schedule_id", "").startswith("sch_")


def test_schedule_create_requires_due_at_and_text(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)
    missing_due = agent.handle_create_command(
        thread_id="demo-thread",
        due_at="",
        text="预定的会议时间已到，请准备参加会议。",
    )
    assert missing_due["success"] is False
    assert missing_due["needs_clarification"] is True

    missing_text = agent.handle_create_command(
        thread_id="demo-thread",
        due_at="2026-04-06T09:00:00+08:00",
        text="",
    )
    assert missing_text["success"] is False
    assert missing_text["needs_clarification"] is True


def test_schedule_delete_invalid_or_missing_id(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)
    bad_format = agent.handle_delete_command(
        thread_id="demo-thread",
        schedule_id="not-an-id",
    )
    assert bad_format["success"] is False
    assert bad_format["needs_clarification"] is True

    missing = agent.handle_delete_command(
        thread_id="demo-thread",
        schedule_id="sch_doesnotexist00",
    )
    assert missing["success"] is False
    assert missing["needs_clarification"] is True


def test_schedule_persists_v2_deferred_objective_as_authority(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)
    created = agent.handle_create_command(
        thread_id="demo-thread",
        due_at="2026-04-06T09:00:00+08:00",
        deferred_objective="提醒用户准备参加会议",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert created["success"] is True

    schedule_path = next((agent.store.storage_root / "by_user").rglob("schedules.json"))
    payload = json.loads(schedule_path.read_text(encoding="utf-8"))
    assert set(payload["items"][0]) == {
        "schedule_id",
        "thread_id",
        "due_at_utc",
        "timezone_name",
        "deferred_objective",
        "status",
        "created_at",
        "origin",
        "lease_token",
        "lease_owner",
        "lease_until",
        "attempt",
        "last_error",
        "schema_version",
    }
    assert "text" not in payload["items"][0]
    assert payload["items"][0]["schema_version"] == 2
    assert payload["items"][0]["lease_token"] == ""
    assert payload["items"][0]["lease_owner"] == ""
    assert payload["items"][0]["lease_until"] == ""
    assert payload["items"][0]["attempt"] == 0
    assert payload["items"][0]["last_error"] == ""
    assert payload["items"][0]["deferred_objective"] == {
        "description": "提醒用户准备参加会议",
        "encoding": "native",
    }


@pytest.mark.parametrize(
    "legacy_fields, expected",
    [
        ({"text": "Legacy text"}, "Legacy text"),
        ({"action_payload": {"prompt": "Legacy prompt"}}, "Legacy prompt"),
        ({"title": "Legacy title"}, "Legacy title"),
        ({"source_text": "Legacy source"}, "Legacy source"),
    ],
)
def test_legacy_schedule_text_migrates_to_deferred_objective(
    legacy_fields: dict,
    expected: str,
) -> None:
    item = ScheduleItem.from_dict(
        {
            "schedule_id": "sch_legacy123456",
            "owner_id": "agent",
            "thread_id": "demo-thread",
            "due_at_utc": "2026-04-06T01:00:00Z",
            "timezone_name": "Asia/Shanghai",
            "status": "pending",
            "created_at": "2026-04-05T00:00:00Z",
            "updated_at": "2026-04-05T01:00:00Z",
            **legacy_fields,
        }
    )
    assert item.text == expected
    assert item.deferred_objective.description == expected
    assert item.deferred_objective.encoding == "legacy_text"
    persisted = item.to_dict()
    assert persisted["deferred_objective"] == {
        "description": expected,
        "encoding": "legacy_text",
    }
    assert "text" not in persisted
    assert "owner_id" not in persisted
    assert "updated_at" not in persisted
    assert persisted["schema_version"] == 2
    assert persisted["lease_token"] == ""
    assert persisted["lease_owner"] == ""
    assert persisted["lease_until"] == ""
    assert persisted["attempt"] == 0
    assert persisted["last_error"] == ""


def test_v2_objective_wins_over_stale_legacy_text() -> None:
    item = ScheduleItem.from_dict(
        {
            "schema_version": 2,
            "schedule_id": "sch_v2123456",
            "thread_id": "demo-thread",
            "due_at_utc": "2026-04-06T01:00:00Z",
            "timezone_name": "Asia/Shanghai",
            "deferred_objective": {
                "description": "提醒用户准备会议",
                "encoding": "native",
            },
            "text": "会议已经提醒完成",
        }
    )
    assert item.text == "提醒用户准备会议"
    assert item.deferred_objective.encoding == "native"
