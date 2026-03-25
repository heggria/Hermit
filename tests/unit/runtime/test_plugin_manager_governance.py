from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermit.runtime.capability.contracts.base import (
    AdapterSpec,
    CommandSpec,
    HookEvent,
    McpServerSpec,
    PluginManifest,
    SubagentSpec,
)
from hermit.runtime.capability.registry.manager import PluginManager
from hermit.runtime.capability.registry.tools import ToolGovernanceError, ToolRegistry, ToolSpec


def _tool(name: str, handler=None) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=handler or (lambda payload: {"ok": True}),
        readonly=True,
        action_class="read_local",
        idempotent=True,
        risk_hint="low",
        requires_receipt=False,
    )


def test_discover_and_load_skips_disabled_builtin_plugins(tmp_path: Path, monkeypatch) -> None:
    settings = SimpleNamespace(disabled_builtin_plugins=["skip-me"])
    pm = PluginManager(settings=settings)
    manifests = [
        PluginManifest(name="skip-me", builtin=True, plugin_dir=tmp_path),
        PluginManifest(name="keep-me", builtin=True, plugin_dir=tmp_path),
        PluginManifest(name="third-party", builtin=False, plugin_dir=tmp_path),
    ]
    loaded: list[str] = []

    monkeypatch.setattr(
        "hermit.runtime.capability.registry.manager.discover_plugins", lambda *dirs: manifests
    )
    monkeypatch.setattr(pm, "_load_one", lambda manifest: loaded.append(manifest.name))

    pm.discover_and_load(tmp_path)

    assert loaded == ["keep-me", "third-party"]


def test_plugin_manager_setup_commands_hooks_and_adapters(tmp_path: Path) -> None:
    pm = PluginManager(settings=SimpleNamespace(profile="dev"))
    runner_calls: list[tuple[str, str, bool]] = []
    post_results: list[object] = []
    session_events: list[tuple[str, object]] = []

    class FakeRunner:
        def add_command(self, name, handler, help_text, cli_only) -> None:
            runner_calls.append((name, help_text, cli_only))

    pm._all_commands.append(
        CommandSpec(name="/demo", help_text="Demo command", handler=lambda *_: None, cli_only=True)
    )
    pm._all_adapters["feishu"] = AdapterSpec(
        name="feishu",
        description="Feishu adapter",
        factory=lambda settings: {"profile": settings.profile},
    )

    pm.hooks.register(HookEvent.PRE_RUN, lambda prompt, **kwargs: f"{prompt} [string]")
    pm.hooks.register(
        HookEvent.PRE_RUN,
        lambda prompt, **kwargs: {
            "prompt": f"{prompt} [dict]",
            "disable_tools": True,
            "readonly_only": True,
        },
    )
    pm.hooks.register(HookEvent.POST_RUN, lambda result, **kwargs: post_results.append(result))
    pm.hooks.register(
        HookEvent.SESSION_START, lambda session_id: session_events.append(("start", session_id))
    )
    pm.hooks.register(
        HookEvent.SESSION_END,
        lambda session_id, messages: session_events.append(("end", len(messages))),
    )

    pm.setup_commands(FakeRunner())
    prompt, run_opts = pm.on_pre_run("hello", session_id="s1")
    pm.on_post_run("done", session_id="s1")
    pm.on_session_start("s1")
    pm.on_session_end("s1", [{"role": "user", "content": "hi"}])

    assert runner_calls == [("/demo", "Demo command", True)]
    assert prompt == "hello [dict]"
    assert run_opts == {"disable_tools": True, "readonly_only": True}
    assert post_results == ["done"]
    assert pm.get_adapter("feishu") == {"profile": "dev"}
    assert pm.list_adapters() == ["feishu"]
    assert session_events == [("start", "s1"), ("end", 1)]

    with pytest.raises(KeyError, match="Available: feishu"):
        pm.get_adapter("slack")


def test_plugin_manager_setup_tools_handles_duplicates() -> None:
    pm = PluginManager()
    registry = ToolRegistry()
    registry.register(_tool("echo"))

    pm._all_tools.append(_tool("echo"))
    pm._all_subagents.append(
        SubagentSpec(name="researcher", description="Research things", system_prompt="Use tools.")
    )

    pm.setup_tools(registry)

    assert registry.get("echo").name == "echo"
    delegation = registry.get("delegate_researcher")
    assert delegation.name == "delegate_researcher"
    assert delegation.readonly is True
    assert delegation.action_class == "delegate_reasoning"
    assert delegation.requires_receipt is False


def test_tool_spec_requires_explicit_governance_metadata_for_mutations() -> None:
    with pytest.raises(ToolGovernanceError, match="action_class explicitly"):
        ToolSpec(
            name="mutate",
            description="Mutate state",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda payload: payload,
        )


class _FakeSubAgent:
    def __init__(self, *, text: str = "done", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[tuple[str, bool]] = []

    def run(self, task: str, on_tool_call=None, readonly_only: bool = False):
        self.calls.append((task, readonly_only))
        if on_tool_call is not None:
            on_tool_call("echo", {"value": "hello"}, {"echo": "hello"})
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text, turns=2, tool_calls=1)


class _FakeRuntime:
    def __init__(self, sub_agent: _FakeSubAgent) -> None:
        self.sub_agent = sub_agent
        self.model = "root-model"
        self.max_tokens = 2048
        self.tool_output_limit = 4000
        self.clone_calls: list[dict[str, object]] = []

    def clone(self, **kwargs):
        self.clone_calls.append(kwargs)
        return self.sub_agent


def test_run_subagent_supports_unavailable_success_and_error_paths(monkeypatch) -> None:
    pm = PluginManager()
    spec = SubagentSpec(
        name="researcher",
        description="Research tasks",
        system_prompt="Be concise.",
        tools=["echo", "missing"],
        model="child-model",
    )

    se = pm._subagent_executor
    assert (
        se.run_subagent(spec, "hello")
        == "[Subagent 'researcher' unavailable: agent runner not configured]"
    )

    registry = ToolRegistry()
    registry.register(_tool("echo"))
    sub_agent = _FakeSubAgent(text="research complete")
    runtime = _FakeRuntime(sub_agent)
    stderr = StringIO()

    monkeypatch.setattr("sys.stderr", stderr)
    pm._registry = registry
    pm.configure_subagent_runtime(runtime)

    se = pm._subagent_executor
    result = se.run_subagent(spec, "x" * 100)

    assert result == "research complete"
    assert sub_agent.calls == [("x" * 100, True)]
    assert runtime.clone_calls[0]["model"] == "child-model"
    assert runtime.clone_calls[0]["max_turns"] == 15
    assert "subagent:researcher" in stderr.getvalue()
    assert "done" in stderr.getvalue()

    error_pm = PluginManager()
    error_pm._registry = registry
    error_pm.configure_subagent_runtime(_FakeRuntime(_FakeSubAgent(error=RuntimeError("boom"))))
    error_se = error_pm._subagent_executor
    stderr = StringIO()
    monkeypatch.setattr("sys.stderr", stderr)

    assert error_se.run_subagent(spec, "hello") == "[Subagent 'researcher' error: boom]"
    assert "error: boom" in stderr.getvalue()


def test_start_and_stop_mcp_servers_register_tools_and_cleanup(monkeypatch) -> None:
    pm = PluginManager()
    registry = ToolRegistry()
    registry.register(_tool("mcp_echo"))
    pm._all_mcp.append(
        McpServerSpec(name="demo", description="Demo MCP", transport="stdio", command=["demo"])
    )
    closed: list[bool] = []

    class FakeMcpManager:
        def __init__(self) -> None:
            self.connected_specs = None

        def connect_all_sync(self, specs) -> None:
            self.connected_specs = specs
            raise RuntimeError("startup failed")

        def get_tool_specs(self):
            return [_tool("mcp_echo")]

        def close_all_sync(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        "hermit.runtime.capability.resolver.mcp_client.McpClientManager", FakeMcpManager
    )

    pm.start_mcp_servers(registry)

    assert pm._mcp_manager is not None
    pm.stop_mcp_servers()

    assert closed == [True]
    assert pm._mcp_manager is None
