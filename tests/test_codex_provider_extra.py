from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermit.core.tools import ToolSpec
from hermit.provider.contracts import ProviderEvent, ProviderRequest
from hermit.provider.providers.codex import (
    _CODEX_OAUTH_BASE_URL,
    CodexOAuthProvider,
    CodexOAuthTokenManager,
    CodexProvider,
    _codex_oauth_image_part_from_block,
    _decode_unverified_jwt_claims,
    _error_code_message,
    _format_stream_error,
    _image_part_from_block,
    _json_error_message,
    _message_content_parts,
    _normalize_openai_schema,
    _parse_output,
    _responses_url,
    _stringify_tool_output,
    _tool_result_follow_up_items,
    _tool_result_image_parts,
    _tool_result_output,
    _tool_schema,
    _usage,
)


class _FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _tool() -> ToolSpec:
    return ToolSpec(
        name="bash",
        description="Run shell command",
        input_schema={"type": "array"},
        handler=lambda payload: payload,
        action_class="execute_command",
        risk_hint="critical",
        requires_receipt=True,
    )


def test_codex_helper_functions_cover_core_serialization_paths() -> None:
    assert _responses_url("https://api.openai.com/v1") == "https://api.openai.com/v1/responses"
    assert (
        _responses_url("https://api.openai.com/v1/responses")
        == "https://api.openai.com/v1/responses"
    )
    assert _responses_url("https://example.com/custom") == "https://example.com/custom/v1/responses"
    assert _stringify_tool_output({"ok": True}) == '{"ok": true}'

    image_block = {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}}
    mixed_blocks = [{"type": "text", "text": "before"}, image_block]
    assert _tool_result_image_parts(image_block, codex_oauth=False) == [
        {"type": "input_image", "image_url": "https://example.com/a.png"}
    ]
    assert _tool_result_output(image_block, codex_oauth=False) == "[tool returned image content]"
    assert (
        _tool_result_output(mixed_blocks, codex_oauth=False)
        == '[{"type": "text", "text": "before"}]'
    )
    follow_up = _tool_result_follow_up_items("call_1", mixed_blocks, codex_oauth=True)
    assert follow_up[0]["content"][1]["type"] == "input_image"


def test_codex_error_and_image_helpers_cover_edge_cases() -> None:
    assert _error_code_message({"error": {"code": "bad", "message": "broken"}}) == ("bad", "broken")
    assert _error_code_message({"response": {"error": {"code": "nested", "detail": "oops"}}}) == (
        "nested",
        "oops",
    )
    assert _error_code_message({"response": {"incomplete_details": {"reason": "truncated"}}}) == (
        "truncated",
        "truncated",
    )
    assert _format_stream_error("prefix", {"message": "oops"}) == "prefix: oops"
    assert _format_stream_error("prefix", {"foo": "bar"}).startswith("prefix: {")

    assert _image_part_from_block(
        {"source": {"type": "base64", "media_type": "image/png", "data": "abc"}}
    )["image_url"].startswith("data:image/png;base64,abc")
    assert _codex_oauth_image_part_from_block(
        {"source": {"type": "url", "url": "https://example.com"}}
    ) == {
        "type": "input_image",
        "image_url": "https://example.com",
    }
    with pytest.raises(ValueError, match="missing source"):
        _image_part_from_block({"source": None})
    with pytest.raises(ValueError, match="empty image URL"):
        _codex_oauth_image_part_from_block({"source": {"type": "url", "url": " "}})
    with pytest.raises(ValueError, match="Unsupported image source type"):
        _image_part_from_block({"source": {"type": "file"}})


def test_codex_message_schema_usage_and_output_parsers() -> None:
    assert _message_content_parts("hello") == [{"type": "input_text", "text": "hello"}]
    assert _message_content_parts(123) == [{"type": "input_text", "text": "123"}]
    assert _message_content_parts(
        [
            {"type": "text", "text": "hi"},
            {"type": "thinking", "thinking": "plan"},
            {"type": "image", "source": {"type": "url", "url": "https://example.com"}},
        ]
    ) == [
        {"type": "input_text", "text": "hi"},
        {"type": "input_image", "image_url": "https://example.com"},
    ]
    assert _normalize_openai_schema({"type": "array"}) == {"type": "array", "items": {}}
    assert _tool_schema(_tool())["parameters"] == {"type": "array", "items": {}}
    assert _usage({"usage": {"input_tokens": 1, "output_tokens": 2, "cached_tokens": 3}}).extra == {
        "cached_tokens": 3
    }
    assert _usage({"usage": "bad"}).input_tokens == 0

    parsed = _parse_output(
        {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "hello"}, "skip"]},
                {"type": "function_call", "call_id": "call_1", "name": "bash", "arguments": "{bad"},
                "skip",
            ]
        }
    )
    assert parsed == [
        {"type": "text", "text": "hello"},
        {"type": "tool_use", "id": "call_1", "name": "bash", "input": {}},
    ]
    assert _json_error_message('{"error":{"message":"bad"}}') == "bad"
    assert _json_error_message('{"detail":"blocked"}') == "blocked"
    assert _json_error_message("not-json") == "not-json"


def test_codex_provider_init_clone_headers_and_stream_events() -> None:
    with pytest.raises(ValueError, match="API key is required"):
        CodexProvider(api_key=" ", model="gpt-5.4")

    provider = CodexProvider(
        api_key=" test-key ",
        model="gpt-5.4",
        system_prompt="system",
        default_headers={"X-Test": "1"},
    )
    clone = provider.clone(model="gpt-5.5", system_prompt="child")
    assert clone.model == "gpt-5.5"
    assert clone.system_prompt == "child"
    assert provider._headers()["X-Test"] == "1"

    provider.generate = lambda _request: SimpleNamespace(  # type: ignore[assignment]
        content=[
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "call_1", "name": "bash", "input": {}},
        ],
        error=None,
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=1, output_tokens=2),
    )
    events = list(
        provider.stream(
            ProviderRequest(
                model="gpt-5.4", max_tokens=64, messages=[{"role": "user", "content": "hi"}]
            )
        )
    )
    assert [event.type for event in events] == ["text", "block_end", "block_end", "message_end"]


def test_codex_jwt_and_token_manager_read_refresh_and_access_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _decode_unverified_jwt_claims("bad-token") == {}
    token = "header.eyJjbGllbnRfaWQiOiAiYXBwIiwgImV4cCI6IDQxMDI0NDQ4MDB9.sig"
    assert _decode_unverified_jwt_claims(token)["client_id"] == "app"

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Invalid ~/.codex/auth.json format"):
        CodexOAuthTokenManager(auth_path=invalid_path)._read()

    missing_tokens = tmp_path / "missing-tokens.json"
    missing_tokens.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Missing tokens"):
        CodexOAuthTokenManager(auth_path=missing_tokens)._refresh({})

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"tokens": {"access_token": token, "refresh_token": "rt_test"}}),
        encoding="utf-8",
    )
    manager = CodexOAuthTokenManager(auth_path=auth_path)

    def fake_urlopen(request, timeout: int):
        return _FakeHttpResponse(
            {"access_token": "new-access", "refresh_token": "new-refresh", "id_token": "id"}
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.strftime", lambda *_args, **_kwargs: "2026-03-12T10:00:00Z")
    refreshed = manager._refresh(manager._read())
    assert refreshed["tokens"]["access_token"] == "new-access"
    assert refreshed["tokens"]["refresh_token"] == "new-refresh"
    assert refreshed["tokens"]["id_token"] == "id"

    fresh_token = "header.eyJleHAiOiA0MTAyNDQ0ODAwLCAiY2xpZW50X2lkIjogImFwcCJ9.sig"
    auth_path.write_text(
        json.dumps({"tokens": {"access_token": fresh_token, "refresh_token": "rt"}}),
        encoding="utf-8",
    )
    assert manager.get_access_token() == fresh_token

    auth_path.write_text(
        json.dumps(
            {"tokens": {"access_token": "header.eyJleHAiOiAxfQ.sig", "refresh_token": "rt"}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        manager, "_refresh", lambda data: {"tokens": {"access_token": "refreshed-token"}}
    )
    assert manager.get_access_token() == "refreshed-token"


def test_codex_token_manager_refresh_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "header.eyJjbGllbnRfaWQiOiAiYXBwIn0.sig"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"tokens": {"access_token": token, "refresh_token": "rt"}}), encoding="utf-8"
    )
    manager = CodexOAuthTokenManager(auth_path=auth_path)

    def http_error(request, timeout: int):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=_FakeHttpResponse({"error": {"message": "bad refresh"}}),
        )

    monkeypatch.setattr("urllib.request.urlopen", http_error)
    with pytest.raises(RuntimeError, match="bad refresh"):
        manager._refresh(manager._read())

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: _FakeHttpResponse({}))
    with pytest.raises(RuntimeError, match="did not return access_token"):
        manager._refresh(manager._read())

    auth_path.write_text(
        json.dumps({"tokens": {"access_token": "", "refresh_token": "rt"}}), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="Missing access_token"):
        manager.get_access_token()


def test_codex_oauth_provider_helpers_and_stream_impl_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_manager = SimpleNamespace(get_access_token=lambda: "access-token")
    provider = CodexOAuthProvider(
        token_manager=token_manager,
        model="gpt-5.4",
        system_prompt=None,
        default_headers={"X-Test": "1"},
    )
    payload = provider._payload(
        ProviderRequest(
            model="gpt-5.4", max_tokens=32, messages=[{"role": "user", "content": "hi"}]
        )
    )
    assert payload["instructions"] == "You are Hermit's coding assistant."
    assert payload["stream"] is True
    assert provider._headers()["Accept"] == "text/event-stream"
    assert provider._headers()["X-Test"] == "1"
    assert provider.base_url == _CODEX_OAUTH_BASE_URL

    class _FakeSseResponse:
        def __init__(self, lines: list[str]) -> None:
            self._lines = [line.encode("utf-8") for line in lines]

        def __iter__(self):
            return iter(self._lines)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    provider._open_stream = lambda request: _FakeSseResponse(  # type: ignore[assignment]
        [
            "event: response.output_text.delta\n",
            'data: {"delta":"hel","item_id":"msg_1"}\n',
            "\n",
            "event: response.output_item.done\n",
            'data: {"item":{"type":"message","content":[{"type":"output_text","text":"hello"}]}}\n',
            "\n",
            "event: response.completed\n",
            'data: {"response":{"output":[],"usage":{"input_tokens":3,"output_tokens":4}}}\n',
            "\n",
        ]
    )
    events = list(
        provider._stream_impl(
            ProviderRequest(
                model="gpt-5.4", max_tokens=64, messages=[{"role": "user", "content": "hi"}]
            )
        )
    )
    assert [event.type for event in events] == ["text", "block_end", "message_end"]

    provider._stream_impl = lambda request: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[assignment]
    response = provider.generate(
        ProviderRequest(
            model="gpt-5.4", max_tokens=64, messages=[{"role": "user", "content": "hi"}]
        )
    )
    assert response.error == "Codex OAuth API error: boom"

    def http_error_stream(_request):
        raise urllib.error.HTTPError(
            "https://example.com",
            403,
            "Forbidden",
            hdrs=None,
            fp=_FakeHttpResponse({"detail": "blocked"}),
        )
        yield ProviderEvent(type="message_end")

    provider._stream_impl = http_error_stream  # type: ignore[assignment]
    error_response = provider.generate(
        ProviderRequest(
            model="gpt-5.4", max_tokens=64, messages=[{"role": "user", "content": "hi"}]
        )
    )
    assert error_response.error == "Codex OAuth API error 403: blocked"
