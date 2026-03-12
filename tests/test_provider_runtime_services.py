from __future__ import annotations

from types import SimpleNamespace
from typing import Iterable

import pytest

from hermit.core.tools import ToolRegistry, ToolSpec
from hermit.provider.contracts import (
    ProviderEvent,
    ProviderFeatures,
    ProviderRequest,
    ProviderResponse,
    UsageMetrics,
)
from hermit.provider.runtime import AgentRuntime
from hermit.provider.services import (
    StructuredExtractionService,
    VisionAnalysisService,
    build_approval_copy_service,
    build_provider,
    build_provider_client_kwargs,
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

    def clone(self, *, model: str | None = None, system_prompt: str | None = None) -> "FakeProvider":
        return self


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo",
            description="Echo back input",
            input_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
            handler=lambda payload: {"echo": payload["value"]},
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
                    block={"type": "tool_use", "id": "call_1", "name": "echo", "input": {"value": "hi"}},
                ),
                ProviderEvent(type="message_end", stop_reason="tool_use", usage=UsageMetrics(input_tokens=2, output_tokens=1)),
            ],
            [
                ProviderEvent(type="text", text="final"),
                ProviderEvent(type="block_end", block={"type": "text", "text": "final"}),
                ProviderEvent(type="message_end", stop_reason="end_turn", usage=UsageMetrics(input_tokens=1, output_tokens=1)),
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
            ProviderResponse(content=[{"type": "text", "text": "```json\n{\"ok\": true}\n```"}]),
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
        image_block={"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}},
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

    assert copy.title == "确认文件修改"
    assert "准备修改 1 个文件" in copy.summary


def test_llm_approval_formatter_output_is_used_when_enabled(monkeypatch) -> None:
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                content=[
                    {
                        "type": "text",
                        "text": '{"title":"确认修改代码","summary":"准备修改 1 个文件 `src/app.py`。","detail":"这是一次本地代码变更，请确认后继续。"}',
                    }
                ]
            )
        ]
    )

    monkeypatch.setattr(
        "hermit.provider.services.build_provider",
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

    assert copy.title == "确认修改代码"
    assert copy.summary == "准备修改 1 个文件 `src/app.py`。"
    assert provider.requests[0].system_prompt is not None


def test_llm_approval_formatter_falls_back_when_provider_init_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermit.provider.services.build_provider",
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

    assert copy.summary == "准备执行一条会修改当前环境的命令。"
