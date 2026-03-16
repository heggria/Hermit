from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import httpx
import pytest

from hermit.runtime.capability.contracts.base import CommandSpec
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec
from hermit.runtime.provider_host.execution import services
from hermit.runtime.provider_host.execution.runtime import AgentRuntime
from hermit.runtime.provider_host.execution.services import (
    LLMProgressSummarizer,
    StructuredExtractionService,
    VisionAnalysisService,
    build_approval_copy_service,
    build_progress_summarizer,
    build_provider,
    build_provider_client_kwargs,
)
from hermit.runtime.provider_host.shared.contracts import (
    ProviderEvent,
    ProviderFeatures,
    ProviderRequest,
    ProviderResponse,
    UsageMetrics,
)


class FakeProvider:
    def __init__(
        self,
        *,
        name: str = "fake",
        features: ProviderFeatures | None = None,
        responses: list[ProviderResponse] | None = None,
        stream_events: list[list[ProviderEvent]] | None = None,
        generate_error: Exception | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self.name = name
        self.features = features or ProviderFeatures()
        self._responses = list(responses or [])
        self._stream_events = list(stream_events or [])
        self._generate_error = generate_error
        self._stream_error = stream_error
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if self._generate_error is not None:
            raise self._generate_error
        return self._responses.pop(0)

    def stream(self, request: ProviderRequest) -> Iterable[ProviderEvent]:
        self.requests.append(request)
        if self._stream_error is not None:
            raise self._stream_error
        yield from self._stream_events.pop(0)

    def clone(
        self, *, model: str | None = None, system_prompt: str | None = None
    ) -> "FakeProvider":
        return self


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo",
            description="Echo back input",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            handler=lambda payload: {"echo": payload["value"]},
            readonly=True,
            action_class="read_local",
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
        )
    )
    return registry


def _mutating_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="write_echo",
            description="Mutate state by echoing input",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            handler=lambda payload: {"echo": payload["value"]},
            action_class="write_local",
            risk_hint="high",
            requires_receipt=True,
        )
    )
    return registry


def test_runtime_run_returns_api_error_when_provider_raises() -> None:
    runtime = AgentRuntime(
        provider=FakeProvider(generate_error=RuntimeError("boom")),
        registry=ToolRegistry(),
        model="fake",
    )

    result = runtime.run("hello")

    assert result.text == "[API Error] boom"
    assert result.turns == 1
    assert result.tool_calls == 0


def test_runtime_run_stream_falls_back_when_provider_is_not_streaming() -> None:
    provider = FakeProvider(
        features=ProviderFeatures(supports_streaming=False),
        responses=[
            ProviderResponse(
                content=[
                    {"type": "thinking", "thinking": "plan"},
                    {"type": "text", "text": "done"},
                ],
                stop_reason="end_turn",
            )
        ],
    )
    runtime = AgentRuntime(provider=provider, registry=ToolRegistry(), model="fake")
    tokens: list[tuple[str, str]] = []

    result = runtime.run_stream("hello", on_token=lambda kind, text: tokens.append((kind, text)))

    assert result.text == "done"
    assert tokens == [("thinking", "plan"), ("text", "done")]


def test_runtime_run_stream_returns_stream_error() -> None:
    runtime = AgentRuntime(
        provider=FakeProvider(
            features=ProviderFeatures(supports_streaming=True),
            stream_error=RuntimeError("stream broke"),
        ),
        registry=ToolRegistry(),
        model="fake",
    )

    result = runtime.run_stream("hello")

    assert result.text == "[Stream Error] stream broke"
    assert result.turns == 1


def test_runtime_run_stream_executes_tool_loop_and_returns_text() -> None:
    provider = FakeProvider(
        features=ProviderFeatures(supports_streaming=True),
        stream_events=[
            [
                ProviderEvent(
                    type="block_end",
                    block={
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "echo",
                        "input": {"value": "hi"},
                    },
                ),
                ProviderEvent(
                    type="message_end",
                    stop_reason="tool_use",
                    usage=UsageMetrics(input_tokens=2, output_tokens=1),
                ),
            ],
            [
                ProviderEvent(type="text", text="final"),
                ProviderEvent(type="block_end", block={"type": "text", "text": "final"}),
                ProviderEvent(
                    type="message_end",
                    stop_reason="end_turn",
                    usage=UsageMetrics(input_tokens=1, output_tokens=1),
                ),
            ],
        ],
    )
    runtime = AgentRuntime(provider=provider, registry=_tool_registry(), model="fake")
    tokens: list[tuple[str, str]] = []

    result = runtime.run_stream("hello", on_token=lambda kind, text: tokens.append((kind, text)))

    assert result.text == "final"
    assert result.tool_calls == 1
    assert result.input_tokens == 3
    assert tokens == [("block_end", ""), ("text", "final"), ("block_end", "")]
    tool_result = result.messages[2]["content"][0]["content"]
    assert "kernel executor is required" in tool_result


def test_runtime_run_stream_rejects_mutating_tool_without_task_context() -> None:
    provider = FakeProvider(
        features=ProviderFeatures(supports_streaming=True),
        stream_events=[
            [
                ProviderEvent(
                    type="block_end",
                    block={
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "write_echo",
                        "input": {"value": "hi"},
                    },
                ),
                ProviderEvent(
                    type="message_end",
                    stop_reason="tool_use",
                    usage=UsageMetrics(input_tokens=2, output_tokens=1),
                ),
            ],
            [
                ProviderEvent(type="text", text="final"),
                ProviderEvent(type="block_end", block={"type": "text", "text": "final"}),
                ProviderEvent(
                    type="message_end",
                    stop_reason="end_turn",
                    usage=UsageMetrics(input_tokens=1, output_tokens=1),
                ),
            ],
        ],
    )
    runtime = AgentRuntime(
        provider=provider, registry=_mutating_tool_registry(), model="fake", tool_executor=object()
    )  # type: ignore[arg-type]

    result = runtime.run_stream("hello")

    assert result.text == "final"
    tool_result = result.messages[2]["content"][0]["content"]
    assert "task context is missing" in tool_result


def test_build_provider_raises_for_unsupported_provider() -> None:
    settings = SimpleNamespace(provider="unknown")

    with pytest.raises(RuntimeError, match="Unsupported provider"):
        build_provider(settings, model="x")


def test_build_provider_codex_requires_api_key_when_auth_file_exists() -> None:
    settings = SimpleNamespace(
        provider="codex",
        resolved_openai_api_key=None,
        codex_auth_mode="chatgpt",
        codex_auth_file_exists=True,
        openai_base_url=None,
        parsed_openai_headers={},
    )

    with pytest.raises(RuntimeError, match="no local OpenAI API key is available"):
        build_provider(settings, model="gpt-5.4")


def test_build_provider_client_kwargs_for_codex_oauth() -> None:
    settings = SimpleNamespace(
        provider="codex-oauth",
        codex_access_token="token",
        parsed_openai_headers={"X-Test": "1"},
    )

    kwargs = build_provider_client_kwargs(settings)

    assert kwargs == {"access_token": "token", "default_headers": {"X-Test": "1"}}


def test_structured_extraction_service_parses_fenced_and_fragment_json() -> None:
    provider = FakeProvider(
        responses=[
            ProviderResponse(content=[{"type": "text", "text": '```json\n{"ok": true}\n```'}]),
            ProviderResponse(content=[{"type": "text", "text": 'prefix {"ok": true'}]),
        ]
    )
    service = StructuredExtractionService(provider, model="fake")

    assert service.extract_json(system_prompt="s", user_content="u") == {"ok": True}
    assert service.extract_json(system_prompt="s", user_content="u") == {"ok": True}


def test_vision_analysis_service_requires_image_support() -> None:
    service = VisionAnalysisService(FakeProvider(), model="fake")

    with pytest.raises(RuntimeError, match="does not support image analysis"):
        service.analyze_image(system_prompt="s", text="t", image_block={"type": "image"})


def test_vision_analysis_service_passes_image_blocks() -> None:
    provider = FakeProvider(
        features=ProviderFeatures(supports_images=True),
        responses=[ProviderResponse(content=[{"type": "text", "text": '{"summary":"ok"}'}])],
    )
    service = VisionAnalysisService(provider, model="fake")

    result = service.analyze_image(
        system_prompt="vision",
        text="what is this",
        image_block={
            "type": "image",
            "source": {"type": "url", "url": "https://example.com/a.png"},
        },
    )

    assert result == {"summary": "ok"}


def test_build_approval_copy_service_returns_template_when_disabled() -> None:
    service = build_approval_copy_service(
        SimpleNamespace(
            approval_copy_formatter_enabled=False,
        )
    )

    copy = service.resolve_copy(
        {
            "tool_name": "write_file",
            "target_paths": ["src/app.py"],
            "risk_level": "high",
        },
        "approval_demo",
    )

    assert copy.title == "Confirm File Change"
    assert "modify 1 file" in copy.summary


def test_llm_approval_formatter_output_is_used_when_enabled(monkeypatch) -> None:
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                content=[
                    {
                        "type": "text",
                        "text": '{"title":"Confirm Code Change","summary":"The agent is about to modify 1 file `src/app.py`.","detail":"This is a local code change. Confirm to continue."}',
                    }
                ]
            )
        ]
    )

    monkeypatch.setattr(
        "hermit.runtime.provider_host.execution.services.build_provider",
        lambda settings, *, model, system_prompt=None: provider,
    )

    service = build_approval_copy_service(
        SimpleNamespace(
            approval_copy_formatter_enabled=True,
            approval_copy_model="gpt-5.4-mini",
            approval_copy_formatter_timeout_ms=500,
            model="gpt-5.4",
        )
    )
    copy = service.resolve_copy(
        {
            "tool_name": "write_file",
            "target_paths": ["src/app.py"],
            "risk_level": "high",
        },
        "approval_demo",
    )

    assert copy.title == "Confirm Code Change"
    assert copy.summary == "The agent is about to modify 1 file `src/app.py`."
    assert provider.requests[0].system_prompt is not None


def test_llm_approval_formatter_falls_back_when_provider_init_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermit.runtime.provider_host.execution.services.build_provider",
        lambda settings, *, model, system_prompt=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    service = build_approval_copy_service(
        SimpleNamespace(
            approval_copy_formatter_enabled=True,
            approval_copy_model="gpt-5.4-mini",
            approval_copy_formatter_timeout_ms=500,
            model="gpt-5.4",
        )
    )
    copy = service.resolve_copy(
        {
            "tool_name": "bash",
            "command_preview": "git status",
            "risk_level": "medium",
        },
        "approval_demo",
    )

    assert (
        copy.summary == "The agent is about to run a command that changes the current environment."
    )


def test_build_approval_copy_service_can_render_zh_cn(monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")

    service = build_approval_copy_service(
        SimpleNamespace(
            approval_copy_formatter_enabled=False,
            locale="zh-CN",
        )
    )

    copy = service.resolve_copy(
        {
            "tool_name": "write_file",
            "target_paths": ["src/app.py"],
            "risk_level": "high",
        },
        "approval_demo",
    )

    assert copy.title == "确认文件修改"
    assert "准备修改 1 个文件" in copy.summary


def test_build_provider_supports_claude_codex_and_oauth(monkeypatch, tmp_path: Path) -> None:
    claude_calls: list[tuple[object, str, str | None]] = []
    codex_calls: dict[str, object] = {}
    oauth_calls: dict[str, object] = {}
    token_paths: list[Path] = []

    monkeypatch.setattr(
        services,
        "build_claude_provider",
        lambda settings, *, model, system_prompt=None: (
            claude_calls.append((settings, model, system_prompt)) or "claude-provider"
        ),
    )
    monkeypatch.setattr(
        services, "_resolve_codex_model", lambda settings, requested_model: "gpt-test"
    )
    monkeypatch.setattr(
        services,
        "CodexProvider",
        lambda **kwargs: codex_calls.update(kwargs) or {"provider": "codex", **kwargs},
    )

    class FakeTokenManager:
        def __init__(self, auth_path: Path) -> None:
            token_paths.append(auth_path)

    monkeypatch.setattr(services, "CodexOAuthTokenManager", FakeTokenManager)
    monkeypatch.setattr(
        services,
        "CodexOAuthProvider",
        lambda **kwargs: oauth_calls.update(kwargs) or {"provider": "oauth", **kwargs},
    )
    monkeypatch.setattr(services.Path, "home", lambda: tmp_path)

    claude_settings = SimpleNamespace(provider="claude")
    codex_settings = SimpleNamespace(
        provider="codex",
        resolved_openai_api_key="sk-test",
        openai_base_url=None,
        parsed_openai_headers={"X-Test": "1"},
    )
    oauth_settings = SimpleNamespace(
        provider="codex-oauth",
        codex_auth_file_exists=True,
        parsed_openai_headers={"X-Test": "1"},
    )

    assert (
        build_provider(claude_settings, model="claude-sonnet", system_prompt="sys")
        == "claude-provider"
    )
    codex_provider = build_provider(codex_settings, model="claude-3", system_prompt="sys")
    oauth_provider = build_provider(oauth_settings, model="claude-3", system_prompt="sys")

    assert claude_calls == [(claude_settings, "claude-sonnet", "sys")]
    assert codex_provider["provider"] == "codex"
    assert codex_calls["model"] == "gpt-test"
    assert codex_calls["base_url"] == "https://api.openai.com/v1"
    assert codex_calls["default_headers"] == {"X-Test": "1"}
    assert oauth_provider["provider"] == "oauth"
    assert oauth_calls["model"] == "gpt-test"
    assert token_paths == [tmp_path / ".codex" / "auth.json"]


def test_build_provider_errors_when_credentials_are_missing() -> None:
    codex_settings = SimpleNamespace(
        provider="codex",
        resolved_openai_api_key=None,
        codex_auth_mode=None,
        codex_auth_file_exists=False,
        openai_base_url=None,
        parsed_openai_headers={},
    )
    oauth_settings = SimpleNamespace(
        provider="codex-oauth",
        codex_auth_file_exists=False,
        parsed_openai_headers={},
    )

    with pytest.raises(RuntimeError, match="requires an OpenAI API key"):
        build_provider(codex_settings, model="gpt-5.4")

    with pytest.raises(RuntimeError, match="requires a local Codex login"):
        build_provider(oauth_settings, model="gpt-5.4")


def test_build_provider_client_kwargs_cover_supported_providers() -> None:
    claude_settings = SimpleNamespace(
        provider="claude",
        claude_api_key="claude-key",
        claude_auth_token="auth-token",
        claude_base_url="https://claude.example.com",
        parsed_claude_headers={"X-Claude": "1"},
        command_timeout_seconds=30,
    )
    codex_settings = SimpleNamespace(
        provider="codex",
        resolved_openai_api_key="openai-key",
        openai_base_url="https://api.example.com/v1",
        parsed_openai_headers={"X-OpenAI": "1"},
    )

    assert build_provider_client_kwargs(claude_settings) == {
        "api_key": "claude-key",
        "auth_token": "auth-token",
        "base_url": "https://claude.example.com",
        "default_headers": {"X-Claude": "1"},
        "timeout": httpx.Timeout(600.0, connect=30),
    }
    assert build_provider_client_kwargs(codex_settings) == {
        "api_key": "openai-key",
        "base_url": "https://api.example.com/v1",
        "default_headers": {"X-OpenAI": "1"},
    }
    assert (
        build_provider_client_kwargs(SimpleNamespace(provider="unknown"), provider="unknown") == {}
    )


def test_build_runtime_wires_runtime_and_filters_cli_only_commands(
    monkeypatch, tmp_path: Path
) -> None:
    from hermit.runtime.control.runner.runner import AgentRunner

    registry = ToolRegistry()
    provider = SimpleNamespace(model="resolved-model")
    core_commands = {
        "/always": (lambda *_: None, "Always visible", False),
        "/cli": (lambda *_: None, "CLI only", True),
    }

    class FakePluginManager:
        def __init__(self) -> None:
            self._all_commands = [
                CommandSpec(name="/plugin", help_text="Plugin command", handler=lambda *_: None),
                CommandSpec(
                    name="/plugin-cli",
                    help_text="Plugin CLI command",
                    handler=lambda *_: None,
                    cli_only=True,
                ),
            ]
            self.setup_registry: ToolRegistry | None = None
            self.started_registry: ToolRegistry | None = None
            self.system_prompt_calls: list[tuple[str, list[str] | None]] = []
            self.configured_runtime = None

        @property
        def all_commands(self) -> list:
            return list(self._all_commands)

        def setup_tools(self, registry: ToolRegistry) -> None:
            self.setup_registry = registry

        def start_mcp_servers(self, registry: ToolRegistry) -> None:
            self.started_registry = registry

        def build_system_prompt(
            self, base_prompt: str, preloaded_skills: list[str] | None = None
        ) -> str:
            self.system_prompt_calls.append((base_prompt, preloaded_skills))
            return f"system::{base_prompt}"

        def configure_subagent_runtime(self, runtime, on_tool_call=None) -> None:
            self.configured_runtime = runtime

    settings = SimpleNamespace(
        sandbox_mode="l0",
        command_timeout_seconds=15,
        kernel_db_path=tmp_path / "kernel.db",
        kernel_artifacts_dir=tmp_path / "artifacts",
        base_dir=tmp_path / ".hermit",
        tool_output_limit=321,
        max_turns=9,
        thinking_budget=11,
        model="claude-3",
        plugins_dir=tmp_path / "plugins",
        effective_max_tokens=lambda: 777,
    )
    pm = FakePluginManager()

    monkeypatch.setattr(services, "build_base_context", lambda settings, cwd: f"base::{cwd.name}")
    monkeypatch.setattr(
        services,
        "create_builtin_tool_registry",
        lambda workdir, sandbox, config_root_dir=None: registry,
    )
    monkeypatch.setattr(
        services, "build_provider", lambda settings, *, model, system_prompt=None: provider
    )
    monkeypatch.setattr(AgentRunner, "_core_commands", core_commands, raising=False)

    runtime, returned_pm = services.build_runtime(
        settings,
        preloaded_skills=["feishu-format"],
        pm=pm,
        serve_mode=True,
        cwd=tmp_path,
    )

    assert returned_pm is pm
    assert runtime.model == "resolved-model"
    assert runtime.max_tokens == 777
    assert runtime.tool_output_limit == 321
    assert runtime.thinking_budget == 11
    assert runtime.workspace_root == str(tmp_path.resolve())
    assert pm.setup_registry is registry
    assert pm.started_registry is registry
    assert pm.configured_runtime is runtime

    base_prompt, preloaded = pm.system_prompt_calls[-1]
    assert "base::" in base_prompt
    assert "`/always`" in base_prompt
    assert "`/plugin`" in base_prompt
    assert "/cli" not in base_prompt
    assert "/plugin-cli" not in base_prompt
    assert preloaded == ["feishu-format"]


def test_build_background_runtime_creates_plugin_manager_when_missing(
    monkeypatch, tmp_path: Path
) -> None:
    created: list["FakePluginManager"] = []

    class FakePluginManager:
        def __init__(self, settings=None) -> None:
            self.settings = settings
            self._all_commands: list[CommandSpec] = []
            self.discover_calls: list[tuple[Path, Path]] = []
            self.setup_tools_called = False
            self.start_mcp_called = False
            self.configured_runtime = None
            created.append(self)

        @property
        def all_commands(self) -> list:
            return list(self._all_commands)

        def discover_and_load(self, *search_dirs: Path) -> None:
            self.discover_calls.append(search_dirs)  # type: ignore[arg-type]

        def setup_tools(self, registry: ToolRegistry) -> None:
            self.setup_tools_called = True

        def start_mcp_servers(self, registry: ToolRegistry) -> None:
            self.start_mcp_called = True

        def build_system_prompt(
            self, base_prompt: str, preloaded_skills: list[str] | None = None
        ) -> str:
            return base_prompt

        def configure_subagent_runtime(self, runtime, on_tool_call=None) -> None:
            self.configured_runtime = runtime

    settings = SimpleNamespace(
        sandbox_mode="l0",
        command_timeout_seconds=15,
        kernel_db_path=tmp_path / "kernel.db",
        kernel_artifacts_dir=tmp_path / "artifacts",
        base_dir=tmp_path / ".hermit",
        tool_output_limit=321,
        max_turns=9,
        thinking_budget=11,
        model="claude-3",
        plugins_dir=tmp_path / "plugins",
        effective_max_tokens=lambda: 777,
    )

    monkeypatch.setattr(services, "PluginManager", FakePluginManager)
    monkeypatch.setattr(services, "build_base_context", lambda settings, cwd: "base")
    monkeypatch.setattr(
        services,
        "create_builtin_tool_registry",
        lambda workdir, sandbox, config_root_dir=None: ToolRegistry(),
    )
    monkeypatch.setattr(
        services,
        "build_provider",
        lambda settings, *, model, system_prompt=None: SimpleNamespace(model=model),
    )

    runtime, pm = services.build_background_runtime(settings, cwd=tmp_path)

    assert runtime.model == "claude-3"
    assert created and pm is created[0]
    assert pm.discover_calls and pm.discover_calls[0][1] == settings.plugins_dir
    assert pm.setup_tools_called is True
    assert pm.start_mcp_called is True
    assert pm.configured_runtime is runtime


def test_resolve_codex_model_and_json_helpers(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / ".codex"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('model = "gpt-from-config"\n', encoding="utf-8")
    monkeypatch.setattr(services.Path, "home", lambda: tmp_path)

    assert services._resolve_codex_model(SimpleNamespace(), "claude-3") == "gpt-from-config"
    assert services._resolve_codex_model(SimpleNamespace(), "gpt-explicit") == "gpt-explicit"

    (config_dir / "config.toml").write_text("model = \n", encoding="utf-8")
    assert services._resolve_codex_model(SimpleNamespace(), "claude-3") == "gpt-5.4"

    assert (
        services._parse_json_response(ProviderResponse(content=[{"type": "text", "text": ""}]))
        is None
    )
    assert (
        services._parse_json_response(ProviderResponse(content=[{"type": "text", "text": "[]"}]))
        is None
    )
    assert (
        services._parse_json_response(
            ProviderResponse(content=[{"type": "text", "text": "not-json"}])
        )
        is None
    )


def test_llm_approval_formatter_requires_complete_fields() -> None:
    formatter = services.LLMApprovalFormatter(
        FakeProvider(
            responses=[ProviderResponse(content=[{"type": "text", "text": '{"title":"ok"}'}])]
        ),
        model="fake",
    )

    assert formatter.format({"tool_name": "bash"}) is None


def test_llm_progress_summarizer_requires_summary_field() -> None:
    summarizer = LLMProgressSummarizer(
        FakeProvider(
            responses=[ProviderResponse(content=[{"type": "text", "text": '{"detail":"waiting"}'}])]
        ),
        model="fake",
    )

    assert summarizer.summarize(facts={"task": {"title": "Watch logs"}}) is None


def test_build_progress_summarizer_uses_provider_clone_and_locale() -> None:
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                content=[
                    {
                        "type": "text",
                        "text": '{"summary":"正在等待 dev server 就绪","detail":"下一步会做健康检查","phase":"starting","progress_percent":30}',
                    }
                ]
            )
        ]
    )
    settings = SimpleNamespace(
        progress_summary_enabled=True,
        progress_summary_model="gpt-progress",
        progress_summary_max_tokens=88,
        locale="zh-CN",
    )

    summarizer = build_progress_summarizer(settings, provider=provider, model="fallback-model")

    assert summarizer is not None
    summary = summarizer.summarize(facts={"task": {"title": "Watch dev"}})
    assert summary is not None
    assert summary.summary == "正在等待 dev server 就绪"
    assert summary.phase == "starting"
    assert provider.requests[0].model == "gpt-progress"
    assert "Simplified Chinese" in provider.requests[0].system_prompt
