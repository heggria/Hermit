from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from hermit.builtin.grok.search import handle_grok_search
from hermit.i18n import tr


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any], *, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.x.ai/v1/responses",
        code=code,
        msg="boom",
        hdrs=None,
        fp=io.BytesIO(body.encode("utf-8")),
    )


def test_handle_grok_search_validates_query_and_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("GROK_API_KEY", raising=False)

    assert handle_grok_search({"query": "   "}) == tr(
        "tools.grok.search.error.empty_query",
        locale="en-US",
    )
    assert handle_grok_search({"query": "latest news"}) == tr(
        "tools.grok.search.error.missing_key",
        locale="en-US",
    )


def test_handle_grok_search_builds_request_and_renders_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    captured: dict[str, Any] = {}

    def fake_urlopen(request, timeout: float) -> _FakeHTTPResponse:
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "Fresh answer.\n"},
                            {"type": "ignored", "text": "skip me"},
                        ],
                    }
                ],
                "citations": [
                    {"title": "Source One", "url": "https://example.com/1"},
                    {"title": "Source Two"},
                ],
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = handle_grok_search(
        {
            "query": "latest ai funding",
            "model": "grok-test",
            "max_tokens": 321,
            "search_type": "both",
        }
    )

    assert captured["url"].endswith("/responses")
    assert captured["timeout"] > 0
    assert captured["headers"]["Authorization"] == "Bearer xai-test"
    assert captured["body"] == {
        "model": "grok-test",
        "input": [{"role": "user", "content": "latest ai funding"}],
        "max_output_tokens": 321,
        "tools": [{"type": "web_search"}, {"type": "x_search"}],
    }
    assert "Fresh answer." in result
    assert "[Source One](https://example.com/1)" in result
    assert "Source Two" in result


def test_handle_grok_search_falls_back_to_web_search_for_unknown_search_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    monkeypatch.setenv("GROK_API_KEY", "grok-test")
    captured: dict[str, Any] = {}

    def fake_urlopen(request, timeout: float) -> _FakeHTTPResponse:
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse({"output": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = handle_grok_search({"query": "repo news", "search_type": "invalid"})

    assert captured["body"]["tools"] == [{"type": "web_search"}]
    assert result == tr("tools.grok.search.empty_response", locale="en-US")


def test_handle_grok_search_handles_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            _http_error(429, '{"error":"quota exceeded"}')
        ),
    )
    assert "quota exceeded" in handle_grok_search({"query": "a"})

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(_http_error(401, "unauthorized")),
    )
    assert handle_grok_search({"query": "a"}) == tr(
        "tools.grok.search.error.invalid_key",
        locale="en-US",
    )

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(_http_error(500, "server exploded")),
    )
    result = handle_grok_search({"query": "a"})
    assert "500" in result
    assert "server exploded" in result


def test_handle_grok_search_handles_generic_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("network down")),
    )

    result = handle_grok_search({"query": "a"})

    assert "network down" in result
