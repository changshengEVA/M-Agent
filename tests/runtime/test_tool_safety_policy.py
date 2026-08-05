"""Runtime enforcement for tool side-effect metadata and opt-in profiles."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from m_agent.layers.execution.core import ExecutionAgent
from m_agent.layers.execution.model_provider import ModelProvider
from m_agent.systems.tools import (
    ControllerCapabilityContext,
    ControllerCapabilityRegistry,
    ControllerCapabilitySpec,
    ToolPolicyError,
    build_controller_tools,
    load_tool_suite_system,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_CONFIG_DIR = PROJECT_ROOT / "config" / "systems" / "tools"


def _execution_agent(profile_name: str, *, email_agent: Any = None) -> ExecutionAgent:
    system = load_tool_suite_system(TOOLS_CONFIG_DIR / profile_name)
    provider = ModelProvider(
        model=MagicMock(),
        network_retry_attempts=1,
        network_retry_backoff_seconds=0.0,
    )
    descriptions = {
        name: manifest.descriptions["en"]
        for name, manifest in system.manifests.items()
    }
    return ExecutionAgent(
        model_provider=provider,
        enabled_capability_names=system.enabled,
        capability_descriptions=descriptions,
        tool_defaults=system.defaults,
        email_agent_provider=(lambda: email_agent) if email_agent is not None else None,
        registry=system.registry,
    )


def test_default_execution_agent_cannot_select_or_invoke_write_tools() -> None:
    agent = _execution_agent("default.yaml")
    visible = {item.name for item in agent.describe_capabilities()}

    assert "reply_to_user" in visible
    assert "schedule_query" in visible
    assert "schedule_create" not in visible
    assert "schedule_delete" not in visible
    assert "email_send" not in visible

    for tool_name in ("schedule_create", "schedule_delete", "email_send"):
        with pytest.raises(ValueError, match="Unknown or disabled tool"):
            agent.invoke_tool_direct(
                tool_name=tool_name,
                tool_input={},
                thread_id="safe-default-thread",
            )


def test_explicit_profile_restores_email_send_execution() -> None:
    email_agent = MagicMock()
    email_agent.send.return_value = {
        "success": True,
        "type": "send",
        "status": "sent",
    }
    agent = _execution_agent(
        "external_writes_enabled.yaml",
        email_agent=email_agent,
    )
    visible = {item.name for item in agent.describe_capabilities()}
    assert {"schedule_create", "schedule_delete", "email_send"} <= visible

    result = agent.invoke_tool_direct(
        tool_name="email_send",
        tool_input={
            "content": "release check",
            "to": "recipient@example.com",
            "subject": "v0.2.1",
        },
        thread_id="explicit-write-thread",
    )

    assert result.success is True
    email_agent.send.assert_called_once_with(
        content="release check",
        to="recipient@example.com",
        subject="v0.2.1",
        cc="",
        bcc="",
        body_html=None,
        reply_to=None,
    )


@pytest.mark.parametrize("side_effect", ["unspecified", "network", ""])
def test_execution_gate_blocks_unknown_effect_before_builder(
    side_effect: str,
) -> None:
    built = False

    def _builder(context: ControllerCapabilityContext, description: str) -> Any:
        nonlocal built
        built = True
        return MagicMock()

    registry = ControllerCapabilityRegistry()
    registry.register(
        ControllerCapabilitySpec(
            name="unsafe_tool",
            build_tool=_builder,
            side_effect=side_effect,
        )
    )
    context = ControllerCapabilityContext(
        active_thread_id="policy-thread",
        recall_state={},
        controller_state={},
        tool_defaults={},
        logger=logging.getLogger(__name__),
    )

    with pytest.raises(ToolPolicyError, match="blocked by policy"):
        build_controller_tools(
            context=context,
            enabled_tool_names=["unsafe_tool"],
            tool_descriptions={"unsafe_tool": "must not build"},
            registry=registry,
        )
    assert built is False
