from __future__ import annotations

from pathlib import Path

import yaml

from m_agent.agents.schedule_agent import ScheduleAgent


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
                "target_candidate_limit": 5,
            },
        },
    )
    return ScheduleAgent(config_path=config_path)


def _fixed_now_context() -> dict:
    return {
        "iso_datetime": "2026-04-05T08:00:00+08:00",
    }


def test_schedule_create_query_delete_flow(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)
    thread_id = "demo-thread"

    created = agent.handle_create_command(
        thread_id=thread_id,
        due_at="2026-04-06T09:00:00+08:00",
        action="开会",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert created["success"] is True
    assert created["action"] == "create"
    assert created["schedule_id"].startswith("sch_")
    assert created["item"]["title"] == "开会"
    assert created["item"]["status"] == "pending"

    queried = agent.handle_query_command(
        thread_id=thread_id,
        keyword="开会",
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
        keyword="开会",
        timezone_name="Asia/Shanghai",
    )
    assert empty["success"] is True
    assert empty["count"] == 0


def test_schedule_create_marks_partial_for_bulk_action(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)
    result = agent.handle_create_command(
        thread_id="demo-thread",
        due_at="2026-04-06T06:00:00+08:00",
        action="每天6点起床一周",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert result.get("success") is True
    assert result.get("count") == 1
    assert result.get("partial") is True
    assert result.get("schedule_id", "").startswith("sch_")


def test_schedule_create_requires_due_at_and_action(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)
    missing_due = agent.handle_create_command(
        thread_id="demo-thread",
        due_at="",
        action="开会",
    )
    assert missing_due["success"] is False
    assert missing_due["needs_clarification"] is True

    missing_action = agent.handle_create_command(
        thread_id="demo-thread",
        due_at="2026-04-06T09:00:00+08:00",
        action="",
    )
    assert missing_action["success"] is False
    assert missing_action["needs_clarification"] is True


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
