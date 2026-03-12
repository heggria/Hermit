from __future__ import annotations

import base64

from hermit.provider.contracts import ProviderRequest
from hermit.provider.images import prepare_messages_for_provider
from hermit.provider.providers.claude import ClaudeProvider
from hermit.provider.providers.codex import CodexOAuthProvider, CodexOAuthTokenManager, CodexProvider


def test_prepare_messages_for_provider_compresses_base64_images(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermit.provider.images.compress_image_bytes",
        lambda image_bytes, media_type, *, max_bytes: ("image/jpeg", b"tiny"),
    )

    messages = prepare_messages_for_provider(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(b"0123456789").decode("ascii"),
                        },
                    }
                ],
            }
        ],
        max_inline_image_bytes=5,
    )

    source = messages[0]["content"][0]["source"]
    assert source["media_type"] == "image/jpeg"
    assert base64.b64decode(source["data"]) == b"tiny"


def test_prepare_messages_for_provider_keeps_url_images() -> None:
    original = {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {"type": "url", "url": "https://example.com/a.png"},
            }
        ],
    }

    messages = prepare_messages_for_provider([original])

    assert messages == [original]


def test_claude_provider_payload_uses_shared_image_preparation(monkeypatch) -> None:
    prepared_messages = [{"role": "user", "content": "prepared"}]
    monkeypatch.setattr(
        "hermit.provider.providers.claude.prepare_messages_for_provider",
        lambda messages: prepared_messages,
    )

    provider = ClaudeProvider(client=object(), model="claude-test")
    payload = provider._payload(
        ProviderRequest(
            model="claude-test",
            max_tokens=128,
            messages=[{"role": "user", "content": "original"}],
        )
    )

    assert payload["messages"] == prepared_messages


def test_codex_provider_payload_uses_shared_image_preparation(monkeypatch, tmp_path) -> None:
    prepared_messages = [{"role": "user", "content": "prepared"}]
    monkeypatch.setattr(
        "hermit.provider.providers.codex.prepare_messages_for_provider",
        lambda messages: prepared_messages,
    )

    provider = CodexProvider(api_key="test-key", model="gpt-5.4", cwd=tmp_path)
    payload = provider._payload(
        ProviderRequest(
            model="gpt-5.4",
            max_tokens=128,
            messages=[{"role": "user", "content": "original"}],
        )
    )

    assert payload["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "prepared"}],
        }
    ]


def test_codex_oauth_provider_payload_uses_shared_image_preparation(monkeypatch, tmp_path) -> None:
    prepared_messages = [{"role": "user", "content": "prepared"}]
    monkeypatch.setattr(
        "hermit.provider.providers.codex.prepare_messages_for_provider",
        lambda messages: prepared_messages,
    )
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        '{"tokens":{"access_token":"header.eyJleHAiOiA0MTAyNDQ0ODAwLCAiY2xpZW50X2lkIjogImFwcCJ9.sig","refresh_token":"rt"}}',
        encoding="utf-8",
    )

    provider = CodexOAuthProvider(
        token_manager=CodexOAuthTokenManager(auth_path=auth_path),
        model="gpt-5.4",
    )
    payload = provider._payload(
        ProviderRequest(
            model="gpt-5.4",
            max_tokens=128,
            messages=[{"role": "user", "content": "original"}],
        )
    )

    assert payload["input"] == [{"type": "message", "role": "user", "content": "prepared"}]
