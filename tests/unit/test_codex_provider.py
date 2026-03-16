from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.runtime.provider_host.execution.services import build_provider
from hermit.runtime.provider_host.llm.codex import (
    CodexOAuthProvider,
    CodexOAuthTokenManager,
    CodexProvider,
    _responses_input,
)
from hermit.runtime.provider_host.shared.contracts import ProviderRequest, ProviderResponse


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def close(self) -> None:
        return None

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_responses_input_maps_tool_history_and_images() -> None:
    items = _responses_input(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image",
                        "source": {"type": "url", "url": "https://example.com/a.png"},
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "read_file",
                        "input": {"path": "README.md"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": {"ok": True}}
                ],
            },
        ]
    )

    assert items[0]["type"] == "message"
    assert items[0]["content"][0] == {"type": "input_text", "text": "look"}
    assert items[0]["content"][1] == {
        "type": "input_image",
        "image_url": "https://example.com/a.png",
    }
    assert items[1] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "read_file",
        "arguments": '{"path": "README.md"}',
    }
    assert items[2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"ok": true}',
    }


def test_codex_payload_moves_internal_tool_context_into_instructions(tmp_path: Path) -> None:
    provider = CodexProvider(
        api_key="test-key", model="gpt-5.4", cwd=tmp_path, system_prompt="base system"
    )

    payload = provider._payload(
        ProviderRequest(
            model="gpt-5.4",
            max_tokens=256,
            messages=[
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_skill",
                            "name": "read_skill",
                            "input": {"name": "grok-search"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_skill",
                            "content": '<skill_content name="grok-search">secret</skill_content>',
                            "internal_context": True,
                            "tool_name": "read_skill",
                        }
                    ],
                },
            ],
        )
    )

    assert "secret" in payload["instructions"]
    assert "do not quote" in payload["instructions"]
    assert payload["input"][1] == {
        "type": "function_call_output",
        "call_id": "call_skill",
        "output": "[internal context loaded]",
    }


def test_responses_input_hoists_tool_result_images_for_codex_oauth() -> None:
    items = _responses_input(
        [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_img",
                        "name": "computer_screenshot",
                        "input": {},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_img",
                        "content": {
                            "type": "image",
                            "source": {"type": "url", "url": "https://example.com/screen.png"},
                        },
                    }
                ],
            },
        ],
        codex_oauth=True,
    )

    assert items[0] == {
        "type": "function_call",
        "call_id": "call_img",
        "name": "computer_screenshot",
        "arguments": "{}",
    }
    assert items[1] == {
        "type": "function_call_output",
        "call_id": "call_img",
        "output": "[tool returned image content]",
    }
    assert items[2]["type"] == "message"
    assert items[2]["role"] == "user"
    assert items[2]["content"][0]["type"] == "input_text"
    assert items[2]["content"][1] == {
        "type": "input_image",
        "image_url": "https://example.com/screen.png",
    }


def test_codex_provider_generate_parses_text_and_tool_calls(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request, timeout: int):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = dict(request.header_items())
        return _FakeHttpResponse(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Need a tool"}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_123",
                        "name": "bash",
                        "arguments": '{"command": "pwd"}',
                    },
                ],
                "usage": {"input_tokens": 11, "output_tokens": 7},
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = CodexProvider(api_key="test-key", model="gpt-5.4", cwd=tmp_path)
    response = provider.generate(
        ProviderRequest(
            model="gpt-5.4",
            max_tokens=256,
            system_prompt="system rules",
            messages=[{"role": "user", "content": "hello"}],
        )
    )

    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["body"]["instructions"] == "system rules"
    assert captured["body"]["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}
    ]
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert response.stop_reason == "tool_use"
    assert response.content == [
        {"type": "text", "text": "Need a tool"},
        {"type": "tool_use", "id": "call_123", "name": "bash", "input": {"command": "pwd"}},
    ]
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 7


def test_build_provider_uses_local_openai_api_key() -> None:
    settings = SimpleNamespace(
        provider="codex",
        resolved_openai_api_key="sk-local",
        codex_auth_mode="api_key",
        codex_auth_file_exists=True,
        codex_command="codex",
        openai_api_key=None,
        openai_base_url=None,
        openai_headers=None,
        parsed_openai_headers={},
    )

    provider = build_provider(settings, model="gpt-5.4")

    assert isinstance(provider, CodexProvider)
    assert provider.api_key == "sk-local"


def test_codex_oauth_provider_generate_parses_sse_blocks(monkeypatch, tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": "header.eyJleHAiOiA0MTAyNDQ0ODAwLCAiY2xpZW50X2lkIjogImFwcCJ9.sig",
                    "refresh_token": "rt_test",
                },
            }
        ),
        encoding="utf-8",
    )

    class _FakeSseResponse:
        def __init__(self, lines: list[str]) -> None:
            self._lines = [line.encode("utf-8") for line in lines]
            self.headers = {}
            self.status = 200

        def __iter__(self):
            return iter(self._lines)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    captured: dict[str, Any] = {}

    def fake_urlopen(request, timeout: int):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeSseResponse(
            [
                "event: response.output_item.done\n",
                'data: {"type":"response.output_item.done","item":{"type":"function_call","call_id":"call_1","name":"bash","arguments":"{\\"command\\":\\"pwd\\"}"}}\n',
                "\n",
                "event: response.completed\n",
                'data: {"type":"response.completed","response":{"output":[{"type":"function_call","call_id":"call_1","name":"bash","arguments":"{\\"command\\":\\"pwd\\"}"}],"usage":{"input_tokens":5,"output_tokens":2}}}\n',
                "\n",
            ]
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = CodexOAuthProvider(
        token_manager=CodexOAuthTokenManager(auth_path=auth_path),
        model="gpt-5.4",
    )
    response = provider.generate(
        ProviderRequest(
            model="gpt-5.4",
            max_tokens=256,
            system_prompt="system rules",
            messages=[{"role": "user", "content": "hello"}],
        )
    )

    assert captured["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert captured["body"]["stream"] is True
    assert captured["body"]["instructions"] == "system rules"
    assert response.stop_reason == "tool_use"
    assert response.content == [
        {"type": "tool_use", "id": "call_1", "name": "bash", "input": {"command": "pwd"}}
    ]
    assert response.usage.input_tokens == 5
    assert response.usage.output_tokens == 2


def test_build_provider_supports_codex_oauth() -> None:
    settings = SimpleNamespace(
        provider="codex-oauth",
        codex_auth_file_exists=True,
        parsed_openai_headers={},
    )

    provider = build_provider(settings, model="gpt-5.4")

    assert isinstance(provider, CodexOAuthProvider)


def test_codex_provider_stream_raises_when_generate_has_error() -> None:
    provider = CodexProvider(api_key="test-key", model="gpt-5.4")
    provider.generate = lambda request: ProviderResponse(content=[], error="boom")  # type: ignore[assignment]

    try:
        list(
            provider.stream(
                ProviderRequest(
                    model="gpt-5.4",
                    max_tokens=64,
                    messages=[{"role": "user", "content": "hello"}],
                )
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("Expected stream() to raise when generate() returns an error")


def test_codex_provider_generate_handles_http_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout: int):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=_FakeHttpResponse({"error": {"message": "bad key"}}),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = CodexProvider(api_key="test-key", model="gpt-5.4")

    response = provider.generate(
        ProviderRequest(
            model="gpt-5.4",
            max_tokens=64,
            messages=[{"role": "user", "content": "hello"}],
        )
    )

    assert "OpenAI Responses API error 401: bad key" == response.error


def test_codex_oauth_provider_stream_wraps_http_error(monkeypatch, tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "header.eyJleHAiOiA0MTAyNDQ0ODAwLCAiY2xpZW50X2lkIjogImFwcCJ9.sig",
                    "refresh_token": "rt_test",
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_urlopen(request, timeout: int):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=_FakeHttpResponse({"detail": "blocked"}),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = CodexOAuthProvider(
        token_manager=CodexOAuthTokenManager(auth_path=auth_path),
        model="gpt-5.4",
    )

    with pytest.raises(RuntimeError, match="Codex OAuth API error 403: blocked"):
        list(
            provider.stream(
                ProviderRequest(
                    model="gpt-5.4",
                    max_tokens=64,
                    messages=[{"role": "user", "content": "hello"}],
                )
            )
        )


def test_codex_oauth_provider_generate_surfaces_nested_stream_errors(
    monkeypatch, tmp_path: Path
) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "header.eyJleHAiOiA0MTAyNDQ0ODAwLCAiY2xpZW50X2lkIjogImFwcCJ9.sig",
                    "refresh_token": "rt_test",
                }
            }
        ),
        encoding="utf-8",
    )

    class _FakeSseResponse:
        def __init__(self, lines: list[str]) -> None:
            self._lines = [line.encode("utf-8") for line in lines]
            self.headers = {}
            self.status = 200

        def __iter__(self):
            return iter(self._lines)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    def fake_urlopen(request, timeout: int):
        return _FakeSseResponse(
            [
                "event: error\n",
                'data: {"type":"error","error":{"type":"invalid_request_error","code":"context_length_exceeded","message":"Too much input.","param":"input"}}\n',
                "\n",
                "event: response.failed\n",
                'data: {"type":"response.failed","response":{"status":"failed","error":{"code":"context_length_exceeded","message":"Too much input."}}}\n',
                "\n",
            ]
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = CodexOAuthProvider(
        token_manager=CodexOAuthTokenManager(auth_path=auth_path),
        model="gpt-5.4",
    )

    response = provider.generate(
        ProviderRequest(
            model="gpt-5.4",
            max_tokens=64,
            messages=[{"role": "user", "content": "hello"}],
        )
    )

    assert (
        response.error
        == "Codex OAuth API error: Codex OAuth stream error context_length_exceeded: Too much input."
    )


def test_codex_oauth_token_manager_requires_client_id_for_refresh(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "header.eyJleHAiOiAxfQ.sig",
                    "refresh_token": "rt_test",
                }
            }
        ),
        encoding="utf-8",
    )
    manager = CodexOAuthTokenManager(auth_path=auth_path)

    with pytest.raises(RuntimeError, match="Missing client_id"):
        manager.get_access_token()
