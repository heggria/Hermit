from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer

from hermit.runtime.capability.contracts.base import McpServerSpec, McpToolGovernance
from hermit.runtime.capability.resolver.mcp_client import (
    MCP_TOOL_PREFIX,
    McpClientManager,
    _sanitize_http_headers,
    _ServerConnection,
    mcp_tool_name,
    parse_mcp_tool_name,
)


def test_main_env_helpers_and_output_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hermit.surfaces.cli._helpers as helpers_mod
    import hermit.surfaces.cli.main as main_mod

    base_dir = tmp_path / ".hermit"
    env_path = base_dir / ".env"
    base_dir.mkdir(parents=True)
    env_path.write_text("FOO=from-file\nBAR='quoted'\n# comment\nINVALID\n", encoding="utf-8")
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("FOO", "from-shell")
    monkeypatch.delenv("BAR", raising=False)

    assert main_mod.hermit_env_path() == env_path

    main_mod._load_hermit_env()

    assert main_mod.os.environ["FOO"] == "from-shell"
    assert main_mod.os.environ["BAR"] == "quoted"
    assert helpers_mod._tool_result_preview("a\nb", limit=3) == "a b"
    assert helpers_mod._tool_result_preview("abcdef", limit=3) == "abc..."

    echoed: list[str] = []
    monkeypatch.setattr(helpers_mod.typer, "echo", lambda text="": echoed.append(text))
    helpers_mod.on_tool_call("echo", {"value": "hi"}, {"ok": True})
    helpers_mod.print_result(
        helpers_mod.AgentResult(text="done", turns=1, tool_calls=0, thinking="plan")
    )
    assert any("echo(value='hi')" in line for line in echoed)
    assert any("thinking" in line for line in echoed)
    assert echoed[-1].endswith("done")


def test_stream_printer_handles_thinking_and_text(monkeypatch: pytest.MonkeyPatch) -> None:
    import hermit.surfaces.cli._helpers as helpers_mod

    stream = io.StringIO()
    monkeypatch.setattr(helpers_mod.sys, "stdout", stream)
    printer = helpers_mod._StreamPrinter()

    printer.on_token("thinking", "plan")
    printer.on_token("text", "done")
    printer.finish()

    output = stream.getvalue()
    assert "thinking" in output
    assert "plan" in output
    assert "done" in output


def test_main_auth_snapshot_workspace_caffeinate_and_require_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hermit.surfaces.cli._helpers as helpers_mod

    settings = SimpleNamespace(
        provider="codex-oauth",
        claude_api_key=None,
        claude_auth_token=None,
        claude_base_url=None,
        openai_api_key=None,
        resolved_openai_api_key=None,
        codex_auth_file_exists=True,
        codex_access_token="access",
        codex_refresh_token="refresh",
        codex_auth_mode="chatgpt",
        has_auth=True,
        base_dir=tmp_path / ".hermit",
        config_file=tmp_path / ".hermit" / "config.toml",
        default_profile="default",
        resolved_profile="local",
        model="gpt-5.4",
        image_model="gpt-image-1",
        max_tokens=1024,
        max_turns=8,
        tool_output_limit=500,
        thinking_budget=16,
        openai_base_url="https://api.openai.com",
        sandbox_mode="workspace-write",
        log_level="INFO",
        feishu_app_id="app",
        feishu_thread_progress=True,
        scheduler_enabled=True,
        scheduler_catch_up=False,
        scheduler_feishu_chat_id="chat-id",
        webhook_enabled=True,
        resolved_webhook_host="127.0.0.1",
        resolved_webhook_port=8080,
        memory_dir=tmp_path / ".hermit" / "memory",
        skills_dir=tmp_path / ".hermit" / "skills",
        rules_dir=tmp_path / ".hermit" / "rules",
        hooks_dir=tmp_path / ".hermit" / "hooks",
        plugins_dir=tmp_path / ".hermit" / "plugins",
        sessions_dir=tmp_path / ".hermit" / "sessions",
        image_memory_dir=tmp_path / ".hermit" / "image-memory",
        kernel_dir=tmp_path / ".hermit" / "kernel",
        kernel_artifacts_dir=tmp_path / ".hermit" / "kernel" / "artifacts",
        context_file=tmp_path / ".hermit" / "context.md",
        memory_file=tmp_path / ".hermit" / "memory" / "memories.json",
        prevent_sleep=True,
    )
    settings.base_dir.mkdir(parents=True)
    settings.config_file.write_text("", encoding="utf-8")

    snapshot = helpers_mod.resolved_config_snapshot(settings)
    assert snapshot["auth"]["provider"] == "codex-oauth"
    assert snapshot["webhook"]["port"] == 8080
    assert "reaction_enabled" not in snapshot["feishu"]

    created: list[Path] = []
    monkeypatch.setattr(
        helpers_mod, "ensure_default_context_file", lambda path: created.append(path)
    )

    class FakeMemoryEngine:
        def __init__(self, path: Path) -> None:
            self.path = path

        def save(self, payload: dict) -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(str(payload), encoding="utf-8")

    import hermit.plugins.builtin.hooks.memory.engine as memory_engine_mod

    monkeypatch.setattr(memory_engine_mod, "MemoryEngine", FakeMemoryEngine)
    helpers_mod.ensure_workspace(settings)
    assert created == [settings.context_file]
    assert settings.memory_file.exists()

    with pytest.raises(typer.BadParameter, match="requires HERMIT_OPENAI_API_KEY"):
        helpers_mod.require_auth(
            SimpleNamespace(has_auth=False, provider="codex", codex_auth_file_exists=False)
        )
    with pytest.raises(typer.BadParameter, match="does not expose an OpenAI API key"):
        helpers_mod.require_auth(
            SimpleNamespace(
                has_auth=False,
                provider="codex",
                codex_auth_file_exists=True,
                codex_auth_mode="chatgpt",
            )
        )
    with pytest.raises(typer.BadParameter, match="requires a local Codex login"):
        helpers_mod.require_auth(SimpleNamespace(has_auth=False, provider="codex-oauth"))
    with pytest.raises(typer.BadParameter, match="Missing authentication"):
        helpers_mod.require_auth(SimpleNamespace(has_auth=False, provider="claude"))

    monkeypatch.setattr(helpers_mod.sys, "platform", "linux")
    with helpers_mod.caffeinate(SimpleNamespace(prevent_sleep=True)):
        pass

    calls: list[str] = []

    class FakeProc:
        def terminate(self) -> None:
            calls.append("terminate")

        def wait(self) -> None:
            calls.append("wait")

    monkeypatch.setattr(helpers_mod.sys, "platform", "darwin")
    monkeypatch.setattr(
        helpers_mod.shutil,
        "which",
        lambda name: "/usr/bin/caffeinate" if name == "caffeinate" else None,
    )
    monkeypatch.setattr(helpers_mod.subprocess, "Popen", lambda *args, **kwargs: FakeProc())
    with helpers_mod.caffeinate(SimpleNamespace(prevent_sleep=True)):
        calls.append("body")
    assert calls == ["body", "terminate", "wait"]


def test_main_preflight_helpers_cover_codex_and_oauth_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hermit.surfaces.cli._preflight as preflight_mod
    import hermit.surfaces.cli.main as _main_init  # noqa: F401 – must load before _preflight to resolve circular imports

    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_PROVIDER", "codex")
    (base_dir / ".env").write_text(
        "HERMIT_PROVIDER=codex\nHERMIT_MODEL=gpt-5.4\n", encoding="utf-8"
    )

    env_keys = preflight_mod._read_env_file_keys()
    assert env_keys == {"HERMIT_PROVIDER", "HERMIT_MODEL"}
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    assert preflight_mod._resolve_env_key("MISSING", "OPENAI_API_KEY") == "OPENAI_API_KEY"
    assert preflight_mod._describe_env_source("HERMIT_PROVIDER", env_keys) == preflight_mod.t(
        "cli.preflight.source.env_file", "~/.hermit/.env"
    )
    assert preflight_mod._describe_env_source("OPENAI_API_KEY", env_keys) == preflight_mod.t(
        "cli.preflight.source.shell", "shell env"
    )
    missing_prefix = preflight_mod.t("cli.preflight.prefix.missing", "[MISSING]")
    assert (
        preflight_mod._format_preflight_item(preflight_mod._PreflightItem("鉴权", False, "缺失"))
        == f"  {missing_prefix} 鉴权: 缺失"
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    codex_settings = SimpleNamespace(
        base_dir=base_dir,
        resolved_profile=None,
        provider="codex",
        resolved_openai_api_key=None,
        codex_auth_file_exists=True,
        codex_auth_mode="chatgpt",
        model="gpt-5.4",
        feishu_app_id=None,
        feishu_app_secret=None,
        feishu_thread_progress=False,
        scheduler_feishu_chat_id=None,
        claude_api_key=None,
        claude_auth_token=None,
        claude_base_url=None,
        codex_access_token=None,
        codex_refresh_token=None,
    )
    items, errors = preflight_mod._build_serve_preflight("cli", codex_settings)
    details = {item.label: item.detail for item in items}
    provider_label = preflight_mod.t("cli.preflight.item.provider.label", "Provider")
    assert details[provider_label] == "codex (~/.hermit/.env)"
    codex_label = preflight_mod.t("cli.preflight.item.codex_auth.label", "Codex auth")
    assert codex_label in details
    assert errors

    oauth_settings = SimpleNamespace(
        base_dir=base_dir,
        resolved_profile="oauth",
        provider="codex-oauth",
        codex_auth_file_exists=True,
        codex_access_token=None,
        codex_refresh_token=None,
        codex_auth_mode="chatgpt",
        model="gpt-5.4",
        feishu_app_id=None,
        feishu_app_secret=None,
        feishu_thread_progress=False,
        scheduler_feishu_chat_id=None,
        claude_api_key=None,
        claude_auth_token=None,
        claude_base_url=None,
        resolved_openai_api_key=None,
    )
    _, oauth_errors = preflight_mod._build_serve_preflight("cli", oauth_settings)
    assert "access_token / refresh_token" in oauth_errors[0]

    echoed: list[str] = []
    monkeypatch.setattr(preflight_mod.typer, "echo", lambda text="": echoed.append(text))
    with pytest.raises(typer.Exit):
        preflight_mod.run_serve_preflight("cli", codex_settings)
    assert echoed[0] == preflight_mod.t("cli.preflight.title", "Hermit pre-start environment check")


def test_mcp_client_helpers_and_call_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _sanitize_http_headers({"X-Test": " 1 ", "Authorization": "Bearer ", "Blank": " "}) == {
        "X-Test": "1"
    }
    assert mcp_tool_name("server", "tool") == f"{MCP_TOOL_PREFIX}server__tool"
    assert parse_mcp_tool_name("mcp__server__tool") == ("server", "tool")
    with pytest.raises(ValueError):
        parse_mcp_tool_name("plain-tool")

    mgr = object.__new__(McpClientManager)
    mgr._connections = {
        "server": _ServerConnection(
            spec=McpServerSpec(
                name="server",
                description="s",
                transport="stdio",
                tool_governance={
                    "tool": McpToolGovernance(
                        action_class="network_read",
                        risk_hint="low",
                        requires_receipt=False,
                        readonly=True,
                    )
                },
            ),
            tools=[{"name": "tool", "description": "desc", "input_schema": {"type": "object"}}],
        )
    }
    called: list[tuple[str, str, dict]] = []
    mgr._run_async = lambda coro, timeout=60: (
        called.append(
            (
                coro.cr_frame.f_locals["server_name"],
                coro.cr_frame.f_locals["tool_name"],
                coro.cr_frame.f_locals["arguments"],
            )
        ),
        coro.close(),
        "ok",
    )[2]  # type: ignore[attr-defined]

    specs = mgr.get_tool_specs()
    assert specs[0].name == "mcp__server__tool"
    assert specs[0].handler({"x": 1}) == "ok"
    assert called == [("server", "tool", {"x": 1})]

    loop_calls: list[str] = []
    mgr._loop = SimpleNamespace(
        is_running=lambda: True,
        call_soon_threadsafe=lambda fn: (loop_calls.append("call"), fn()),
        stop=lambda: loop_calls.append("stop"),
    )
    mgr._shutdown_event = SimpleNamespace(set=lambda: loop_calls.append("shutdown"))
    mgr._lifecycle_future = SimpleNamespace(
        result=lambda timeout=15: (_ for _ in ()).throw(RuntimeError("close boom"))
    )
    mgr._thread = SimpleNamespace(join=lambda timeout=5: loop_calls.append("join"))
    mgr.close_all_sync()
    assert loop_calls == ["call", "shutdown", "call", "stop", "join"]
    assert mgr._connections == {}


@pytest.mark.asyncio
async def test_mcp_call_tool_and_connect_one_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = object.__new__(McpClientManager)
    mgr._connections = {}

    assert (
        await mgr._call_tool("missing", "tool", {}) == "Error: MCP server 'missing' not connected"
    )

    class FakeSession:
        def __init__(self, *_args) -> None:
            self.initialized = False

        async def initialize(self) -> None:
            self.initialized = True

        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="allowed", description="Allowed", inputSchema={"type": "object"}
                    ),
                    SimpleNamespace(
                        name="blocked", description="Blocked", inputSchema={"type": "object"}
                    ),
                ]
            )

        async def call_tool(self, tool_name: str, arguments: dict) -> object:
            if tool_name == "error":
                return SimpleNamespace(
                    isError=True,
                    content=[SimpleNamespace(text="failed badly")],
                    structuredContent=None,
                )
            if tool_name == "structured":
                return SimpleNamespace(isError=False, content=[], structuredContent={"ok": True})
            if tool_name == "plain":
                return SimpleNamespace(
                    isError=False,
                    content=[SimpleNamespace(text="line 1"), SimpleNamespace(text="line 2")],
                    structuredContent=None,
                )
            if tool_name == "empty":
                return SimpleNamespace(isError=False, content=[], structuredContent=None)
            raise RuntimeError("boom")

    monkeypatch.setattr("hermit.runtime.capability.resolver.mcp_client.ClientSession", FakeSession)
    monkeypatch.setattr(
        "hermit.runtime.capability.resolver.mcp_client.stdio_client",
        lambda params: SimpleNamespace(params=params),
    )
    monkeypatch.setattr(
        "hermit.runtime.capability.resolver.mcp_client.streamable_http_client",
        lambda url, http_client=None: SimpleNamespace(url=url, http_client=http_client),
    )

    class FakeAsyncClient:
        def __init__(self, headers=None) -> None:
            self.headers = headers

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=FakeAsyncClient))

    class FakeStack:
        def __init__(self, values):
            self._values = iter(values)

        async def enter_async_context(self, _ctx):
            return next(self._values)

    stdio_stack = FakeStack([("read", "write"), FakeSession("read", "write")])
    await mgr._connect_one(
        McpServerSpec(
            name="stdio",
            description="server",
            transport="stdio",
            command=["python", "server.py"],
            allowed_tools=["allowed"],
            tool_governance={
                "allowed": McpToolGovernance(
                    action_class="network_read",
                    risk_hint="low",
                    requires_receipt=False,
                    readonly=True,
                )
            },
        ),
        stdio_stack,
    )
    assert mgr._connections["stdio"].tools == [
        {"name": "allowed", "description": "Allowed", "input_schema": {"type": "object"}}
    ]

    http_stack = FakeStack(
        [FakeAsyncClient(headers={"X-Test": "1"}), ("read", "write"), FakeSession("read", "write")]
    )
    await mgr._connect_one(
        McpServerSpec(
            name="http",
            description="server",
            transport="http",
            url="https://example.com/mcp",
            headers={"X-Test": "1", "Authorization": "Bearer "},
            allowed_tools=["allowed"],
            tool_governance={
                "allowed": McpToolGovernance(
                    action_class="network_read",
                    risk_hint="low",
                    requires_receipt=False,
                    readonly=True,
                )
            },
        ),
        http_stack,
    )
    assert "http" in mgr._connections

    mgr._connections["stdio"].session = FakeSession("read", "write")
    assert await mgr._call_tool("stdio", "error", {}) == "Error: failed badly"
    assert await mgr._call_tool("stdio", "structured", {}) == '{\n  "ok": true\n}'
    assert await mgr._call_tool("stdio", "plain", {}) == "line 1\nline 2"
    assert await mgr._call_tool("stdio", "empty", {}) == "(no output)"
    assert (
        await mgr._call_tool("stdio", "explode", {}) == "Error calling MCP tool stdio/explode: boom"
    )
