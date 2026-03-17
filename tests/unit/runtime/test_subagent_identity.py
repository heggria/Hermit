from __future__ import annotations

from hermit.runtime.capability.contracts.base import SubagentSpec
from hermit.runtime.capability.registry.manager import PluginManager
from hermit.runtime.capability.registry.tools import ToolRegistry


def test_subagent_spec_defaults() -> None:
    """SubagentSpec defaults: governed=False, policy_profile='readonly'."""
    spec = SubagentSpec(
        name="test_agent",
        description="A test agent",
        system_prompt="You are a test agent.",
    )
    assert spec.governed is False
    assert spec.policy_profile == "readonly"
    assert spec.tools == []
    assert spec.model == ""


def test_subagent_spec_governed_mode() -> None:
    """SubagentSpec can be created with governed=True."""
    spec = SubagentSpec(
        name="governed_agent",
        description="A governed agent",
        system_prompt="You are governed.",
        governed=True,
        policy_profile="autonomous",
    )
    assert spec.governed is True
    assert spec.policy_profile == "autonomous"


def test_delegation_tool_readonly_mode() -> None:
    """governed=False keeps original delegate_reasoning behavior."""
    pm = PluginManager()
    spec = SubagentSpec(
        name="researcher",
        description="Research things",
        system_prompt="You are a researcher.",
        governed=False,
    )
    pm._all_subagents.append(spec)

    registry = ToolRegistry()
    pm.setup_tools(registry)

    tool = registry.get("delegate_researcher")
    assert tool.action_class == "delegate_reasoning"
    assert tool.readonly is True
    assert tool.requires_receipt is False
    assert tool.risk_hint == "low"


def test_delegation_tool_governed_mode() -> None:
    """governed=True switches to delegate_execution with receipt."""
    pm = PluginManager()
    spec = SubagentSpec(
        name="executor",
        description="Execute tasks",
        system_prompt="You execute tasks.",
        governed=True,
    )
    pm._all_subagents.append(spec)

    registry = ToolRegistry()
    pm.setup_tools(registry)

    tool = registry.get("delegate_executor")
    assert tool.action_class == "delegate_execution"
    assert tool.readonly is False
    assert tool.requires_receipt is True
    assert tool.risk_hint == "medium"
