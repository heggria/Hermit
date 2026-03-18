from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

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


def _governed_spec() -> SubagentSpec:
    return SubagentSpec(
        name="gov_agent",
        description="A governed agent",
        system_prompt="You are governed.",
        governed=True,
    )


def _ungoverned_spec() -> SubagentSpec:
    return SubagentSpec(
        name="plain_agent",
        description="A plain agent",
        system_prompt="You are plain.",
        governed=False,
    )


# ── _register_subagent_principal tests ──


def test_register_principal_returns_none_for_ungoverned() -> None:
    """Ungoverned spec should return None without touching the store."""
    pm = PluginManager()
    assert pm._register_subagent_principal(_ungoverned_spec()) is None


def test_register_principal_returns_none_without_store() -> None:
    """Governed spec without kernel_store returns None."""
    pm = PluginManager()
    pm._runtime = SimpleNamespace()  # no kernel_store attr
    assert pm._register_subagent_principal(_governed_spec()) is None


def test_register_principal_success(tmp_path: object) -> None:
    """Governed spec with a working kernel_store registers and returns principal_id."""
    store = MagicMock()
    pm = PluginManager()
    pm._runtime = SimpleNamespace(kernel_store=store)

    result = pm._register_subagent_principal(_governed_spec())

    assert result == "principal_subagent_gov_agent"
    store.ensure_principal.assert_called_once_with(
        principal_id="principal_subagent_gov_agent",
        principal_type="subagent",
        display_name="gov_agent",
        metadata={"parent_principal": "principal_user", "tools": []},
    )


def test_register_principal_handles_store_error() -> None:
    """If ensure_principal raises, return None gracefully."""
    store = MagicMock()
    store.ensure_principal.side_effect = RuntimeError("db locked")
    pm = PluginManager()
    pm._runtime = SimpleNamespace(kernel_store=store)

    assert pm._register_subagent_principal(_governed_spec()) is None


# ── _emit_subagent_event tests ──


def test_emit_event_noop_without_store() -> None:
    """No kernel_store → silent no-op."""
    pm = PluginManager()
    pm._runtime = SimpleNamespace()  # no kernel_store
    # Should not raise
    pm._emit_subagent_event("subagent_spawned", _governed_spec(), "pid_1")


def test_emit_event_calls_append_event() -> None:
    """Event emission calls store.append_event with correct args."""
    store = MagicMock()
    pm = PluginManager()
    pm._runtime = SimpleNamespace(kernel_store=store)

    spec = _governed_spec()
    pm._emit_subagent_event("subagent_spawned", spec, "pid_1", {"key": "val"})

    store.append_event.assert_called_once_with(
        event_type="subagent_spawned",
        entity_type="subagent",
        entity_id="pid_1",
        task_id=None,
        actor="pid_1",
        payload={"key": "val"},
    )


def test_emit_event_handles_store_error() -> None:
    """If append_event raises, log warning but don't propagate."""
    store = MagicMock()
    store.append_event.side_effect = RuntimeError("disk full")
    pm = PluginManager()
    pm._runtime = SimpleNamespace(kernel_store=store)

    # Should not raise
    pm._emit_subagent_event("subagent_failed", _governed_spec(), "pid_1")


# ── _run_subagent governed lifecycle tests ──


def test_run_subagent_governed_emits_spawned_and_completed() -> None:
    """Governed subagent emits spawned + completed events on success."""
    store = MagicMock()
    mock_result = SimpleNamespace(turns=2, tool_calls=3, text="done")
    mock_runtime = MagicMock()
    mock_runtime.kernel_store = store
    mock_runtime.clone.return_value.run.return_value = mock_result

    pm = PluginManager()
    pm._runtime = mock_runtime
    pm._registry = ToolRegistry()
    pm._model = "test-model"

    spec = _governed_spec()
    result = pm._run_subagent(spec, "do something")

    assert result == "done"
    # Should have called append_event twice: spawned + completed
    assert store.append_event.call_count == 2
    event_types = [c.kwargs["event_type"] for c in store.append_event.call_args_list]
    assert event_types == ["subagent_spawned", "subagent_completed"]


def test_run_subagent_governed_emits_spawned_and_failed_on_error() -> None:
    """Governed subagent emits spawned + failed events on exception."""
    store = MagicMock()
    mock_runtime = MagicMock()
    mock_runtime.kernel_store = store
    mock_runtime.clone.return_value.run.side_effect = RuntimeError("boom")

    pm = PluginManager()
    pm._runtime = mock_runtime
    pm._registry = ToolRegistry()
    pm._model = "test-model"

    spec = _governed_spec()
    result = pm._run_subagent(spec, "do something")

    assert "error" in result.lower()
    assert store.append_event.call_count == 2
    event_types = [c.kwargs["event_type"] for c in store.append_event.call_args_list]
    assert event_types == ["subagent_spawned", "subagent_failed"]


def test_run_subagent_ungoverned_no_events() -> None:
    """Ungoverned subagent does not emit lifecycle events."""
    store = MagicMock()
    mock_result = SimpleNamespace(turns=1, tool_calls=0, text="hi")
    mock_runtime = MagicMock()
    mock_runtime.kernel_store = store
    mock_runtime.clone.return_value.run.return_value = mock_result

    pm = PluginManager()
    pm._runtime = mock_runtime
    pm._registry = ToolRegistry()
    pm._model = "test-model"

    spec = _ungoverned_spec()
    result = pm._run_subagent(spec, "say hi")

    assert result == "hi"
    store.append_event.assert_not_called()
