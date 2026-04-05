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


def test_schedule_agent_create_query_update_cancel_flow(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)
    thread_id = "demo-thread"

    created = agent.handle_manage_command(
        thread_id=thread_id,
        instruction="明天上午9点提醒我开会",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert created["success"] is True
    assert created["action"] == "create"
    assert created["item"]["title"] == "开会"
    assert created["item"]["status"] == "pending"
    assert created["item"]["owner_id"] == "__anonymous__"

    queried = agent.handle_query_command(
        thread_id=thread_id,
        query="明天",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert queried["success"] is True
    assert queried["count"] == 1
    assert queried["items"][0]["title"] == "开会"

    updated = agent.handle_manage_command(
        thread_id=thread_id,
        instruction="把明天那个提醒改到后天下午3点",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert updated["success"] is True
    assert updated["action"] == "update"
    assert updated["item"]["title"] == "开会"
    assert updated["item"]["due_display"].endswith("15:00")

    canceled = agent.handle_manage_command(
        thread_id=thread_id,
        instruction="取消开会",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert canceled["success"] is True
    assert canceled["action"] == "cancel"
    assert canceled["item"]["status"] == "canceled"

    empty_active = agent.handle_query_command(
        thread_id=thread_id,
        query="",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert empty_active["success"] is True
    assert empty_active["count"] == 0


def test_schedule_agent_returns_clarification_when_time_is_missing(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)

    result = agent.handle_manage_command(
        thread_id="demo-thread",
        instruction="提醒我开会",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert result["success"] is False
    assert result["needs_clarification"] is True
    assert result["action"] == "create"


def test_schedule_agent_returns_clarification_when_advance_offset_is_missing(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)

    result = agent.handle_manage_command(
        thread_id="demo-thread",
        instruction="提前通知我下午2点的会议",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert result["success"] is False
    assert result["needs_clarification"] is True
    assert result["action"] == "create"
    assert result["machine"]["parse_error"] == "missing_lead_time"
    assert "提前多久" in result["answer"]


def test_schedule_agent_creates_advance_reminder_with_event_metadata(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)

    result = agent.handle_manage_command(
        thread_id="demo-thread",
        instruction="提前30分钟提醒我明天下午2点开会",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert result["success"] is True
    assert result["action"] == "create"
    assert result["item"]["title"] == "开会"
    assert result["item"]["schedule_kind"] == "before_event"
    assert result["item"]["due_display"].endswith("13:30")
    assert result["item"]["event_display"].endswith("14:00")
    assert result["item"]["reminder_offset_minutes"] == 30
    assert result["item"]["metadata"]["event_display"].endswith("14:00")
    assert result["machine"]["parse"]["trigger_kind"] == "before_event"


def test_schedule_agent_parses_natural_rephrase_of_advance_reminder(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)

    result = agent.handle_manage_command(
        thread_id="demo-thread",
        instruction="重新安排后天（4月7日）下午2点的会议提醒，提前30分钟提醒，也就是下午1:30提醒",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert result["success"] is True
    assert result["item"]["title"] == "会议"
    assert result["item"]["schedule_kind"] == "before_event"
    assert result["item"]["due_display"] == "2026-04-07 13:30"
    assert result["item"]["event_display"] == "2026-04-07 14:00"


def test_schedule_agent_explicit_owner_isolation_by_scoped_thread(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)

    alice_created = agent.handle_manage_command(
        thread_id="alice::demo-thread",
        instruction="明天上午9点提醒我开会",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    bob_created = agent.handle_manage_command(
        thread_id="bob::demo-thread",
        instruction="明天下午2点提醒我面试",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )

    assert alice_created["success"] is True
    assert bob_created["success"] is True
    assert alice_created["item"]["owner_id"] == "alice"
    assert bob_created["item"]["owner_id"] == "bob"

    alice_query = agent.handle_query_command(
        thread_id="alice::demo-thread",
        query="",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    bob_query = agent.handle_query_command(
        thread_id="bob::demo-thread",
        query="",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )

    assert alice_query["count"] == 1
    assert bob_query["count"] == 1
    assert alice_query["items"][0]["title"] == "开会"
    assert bob_query["items"][0]["title"] == "面试"


def test_schedule_agent_shares_schedules_across_threads_for_same_owner(tmp_path: Path) -> None:
    agent = _build_agent(tmp_path)

    created = agent.handle_manage_command(
        thread_id="alice::work-thread",
        instruction="明天上午10点提醒我提交日报",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert created["success"] is True
    assert created["item"]["owner_id"] == "alice"

    queried = agent.handle_query_command(
        thread_id="alice::life-thread",
        query="",
        timezone_name="Asia/Shanghai",
        now_context=_fixed_now_context(),
    )
    assert queried["success"] is True
    assert queried["count"] == 1
    assert queried["items"][0]["title"] == "提交日报"
    assert queried["items"][0]["thread_id"] == "alice::work-thread"
