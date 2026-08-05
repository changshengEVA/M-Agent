"""Tool-suite safety defaults and declarative side-effect validation."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from m_agent.systems.loader import SystemsConfigError
from m_agent.systems.tools import (
    build_default_tool_suite_system,
    load_tool_capability_manifest,
    load_tool_suite_system,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_CONFIG_DIR = PROJECT_ROOT / "config" / "systems" / "tools"
CHAT_CONFIG_DIR = PROJECT_ROOT / "config" / "agents" / "chat"


def test_default_profile_exposes_only_read_and_reply_effects() -> None:
    system = load_tool_suite_system(TOOLS_CONFIG_DIR / "default.yaml")

    assert "reply_to_user" in system.enabled
    assert "schedule_query" in system.enabled
    assert "email_read" in system.enabled
    assert "schedule_create" not in system.enabled
    assert "schedule_delete" not in system.enabled
    assert "email_send" not in system.enabled
    assert {
        system.manifests[name].side_effect for name in system.enabled
    } <= {"read", "emit"}


def test_programmatic_default_is_safe_but_explicit_name_opt_in_is_preserved() -> None:
    default_system = build_default_tool_suite_system()
    assert "reply_to_user" in default_system.enabled
    assert "schedule_query" in default_system.enabled
    assert "schedule_create" not in default_system.enabled
    assert "schedule_delete" not in default_system.enabled
    assert "email_send" not in default_system.enabled

    opted_in = build_default_tool_suite_system(
        enabled=["schedule_create", "schedule_delete", "email_send"]
    )
    assert opted_in.enabled == ["schedule_create", "schedule_delete", "email_send"]


def test_external_writes_profile_is_an_explicit_opt_in() -> None:
    system = load_tool_suite_system(TOOLS_CONFIG_DIR / "external_writes_enabled.yaml")

    assert "schedule_create" in system.enabled
    assert "schedule_delete" in system.enabled
    assert "email_send" in system.enabled
    assert system.registry.get("schedule_create").side_effect == "write"
    assert system.registry.get("schedule_delete").side_effect == "write"
    assert system.registry.get("email_send").side_effect == "send"


def test_external_writes_chat_profile_pairs_tools_with_send_scoped_gmail() -> None:
    chat_path = CHAT_CONFIG_DIR / "chat_controller_external_writes.yaml"
    chat = yaml.safe_load(chat_path.read_text(encoding="utf-8"))
    tools_path = (chat_path.parent / chat["systems"]["tools"]).resolve()
    email_path = (chat_path.parent / chat["email_agent_config_path"]).resolve()
    email = yaml.safe_load(email_path.read_text(encoding="utf-8"))

    assert tools_path == TOOLS_CONFIG_DIR / "external_writes_enabled.yaml"
    assert email_path.name == "gmail_email_agent_external_writes.yaml"
    assert "https://www.googleapis.com/auth/gmail.send" in email["gmail"]["scopes"]
    assert email["gmail"]["token_path"].endswith("token-send.json")


def test_manifest_suite_without_enabled_list_falls_back_to_safe_allowlist(
    tmp_path: Path,
) -> None:
    suite_path = tmp_path / "implicit-defaults.yaml"
    suite_path.write_text(
        "\n".join(
            [
                "system: tools",
                f"capabilities_dir: {(TOOLS_CONFIG_DIR / 'capabilities').as_posix()}",
            ]
        ),
        encoding="utf-8",
    )

    system = load_tool_suite_system(suite_path)

    assert "shallow_recall" in system.enabled
    assert "reply_to_user" in system.enabled
    assert "deep_recall" not in system.enabled
    assert "schedule_create" not in system.enabled
    assert "schedule_delete" not in system.enabled
    assert "email_send" not in system.enabled


@pytest.mark.parametrize("declaration", [None, "unspecified", "network"])
def test_manifest_rejects_missing_or_unknown_side_effect(
    tmp_path: Path,
    declaration: str | None,
) -> None:
    policy_lines = ["policy:"]
    if declaration is not None:
        policy_lines.append(f"  side_effect: {declaration}")
    path = tmp_path / "unsafe.yaml"
    path.write_text(
        "\n".join(
            [
                "name: unsafe_tool",
                "builder: m_agent.systems.tools.default.capabilities.time_context:_build_get_current_time_tool",
                "descriptions:",
                "  en: Unsafe metadata test tool",
                "input:",
                "  mode: no_args",
                *policy_lines,
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemsConfigError, match="blocked by policy"):
        load_tool_capability_manifest(path)
