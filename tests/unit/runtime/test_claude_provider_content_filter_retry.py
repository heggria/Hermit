"""Tests for ClaudeProvider retry on content-filter false positives.

These tests verify that transient `sensitive_words_detected` errors from
API gateways/proxies are retried transparently by the provider layer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermit.runtime.provider_host.llm.claude import (
    _CONTENT_FILTER_MAX_RETRIES,
    _CONTENT_FILTER_RETRY_DELAY,
    ClaudeProvider,
    _is_content_filter_error,
    _nudge_payload_for_retry,
)
from hermit.runtime.provider_host.shared.contracts import ProviderRequest


def _simple_request() -> ProviderRequest:
    return ProviderRequest(
        model="claude-3",
        max_tokens=10,
        messages=[{"role": "user", "content": "hello"}],
    )


def _ok_response() -> SimpleNamespace:
    return SimpleNamespace(
        content=[{"type": "text", "text": "hi"}],
        stop_reason="end_turn",
        error=None,
        usage=SimpleNamespace(
            input_tokens=5,
            output_tokens=3,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


class _ContentFilterError(Exception):
    """Simulates the 500 error from the API gateway."""

    def __init__(self) -> None:
        super().__init__(
            '500 {"error":{"type":"system_error",'
            '"message":"sensitive_words_detected (request id: abc123)"}}'
        )


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


def test_is_content_filter_error_matches_sensitive_words() -> None:
    exc = _ContentFilterError()
    assert _is_content_filter_error(exc) is True


def test_is_content_filter_error_ignores_other_errors() -> None:
    assert _is_content_filter_error(RuntimeError("connection reset")) is False
    assert _is_content_filter_error(RuntimeError("prompt is too long")) is False


def test_nudge_payload_adds_metadata_without_mutating_original() -> None:
    original = {"model": "claude-3", "messages": []}
    nudged = _nudge_payload_for_retry(original, 1)
    assert nudged["metadata"] == {"retry_hint": "attempt_1"}
    assert "metadata" not in original


def test_nudge_payload_different_attempts_produce_different_hints() -> None:
    p1 = _nudge_payload_for_retry({}, 1)
    p2 = _nudge_payload_for_retry({}, 2)
    assert p1["metadata"] != p2["metadata"]


# ---------------------------------------------------------------------------
# generate() retry tests
# ---------------------------------------------------------------------------


@patch("hermit.runtime.provider_host.llm.claude.time.sleep")
def test_generate_retries_on_content_filter_then_succeeds(mock_sleep) -> None:
    """First call raises sensitive_words_detected, second succeeds."""
    call_count = 0

    def fake_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _ContentFilterError()
        return _ok_response()

    provider = ClaudeProvider(
        client=SimpleNamespace(messages=SimpleNamespace(create=fake_create)),
        model="claude-3",
    )

    response = provider.generate(_simple_request())

    assert response.error is None
    assert response.content[0]["text"] == "hi"
    assert call_count == 2
    mock_sleep.assert_called_once_with(_CONTENT_FILTER_RETRY_DELAY)


@patch("hermit.runtime.provider_host.llm.claude.time.sleep")
def test_generate_raises_after_max_retries_exhausted(mock_sleep) -> None:
    """All attempts raise sensitive_words_detected — error propagates."""
    call_count = 0

    def fake_create(**kwargs):
        nonlocal call_count
        call_count += 1
        raise _ContentFilterError()

    provider = ClaudeProvider(
        client=SimpleNamespace(messages=SimpleNamespace(create=fake_create)),
        model="claude-3",
    )

    with pytest.raises(_ContentFilterError):
        provider.generate(_simple_request())

    assert call_count == _CONTENT_FILTER_MAX_RETRIES + 1
    assert mock_sleep.call_count == _CONTENT_FILTER_MAX_RETRIES


@patch("hermit.runtime.provider_host.llm.claude.time.sleep")
def test_generate_does_not_retry_non_filter_errors(mock_sleep) -> None:
    """Non-filter errors are raised immediately without retry."""

    def fake_create(**kwargs):
        raise RuntimeError("connection refused")

    provider = ClaudeProvider(
        client=SimpleNamespace(messages=SimpleNamespace(create=fake_create)),
        model="claude-3",
    )

    with pytest.raises(RuntimeError, match="connection refused"):
        provider.generate(_simple_request())

    mock_sleep.assert_not_called()


@patch("hermit.runtime.provider_host.llm.claude.time.sleep")
def test_generate_nudges_payload_on_retry(mock_sleep) -> None:
    """Retry calls include metadata to vary the request hash."""
    payloads: list[dict] = []

    def fake_create(**kwargs):
        payloads.append(dict(kwargs))
        if len(payloads) == 1:
            raise _ContentFilterError()
        return _ok_response()

    provider = ClaudeProvider(
        client=SimpleNamespace(messages=SimpleNamespace(create=fake_create)),
        model="claude-3",
    )

    provider.generate(_simple_request())

    assert len(payloads) == 2
    # First call has no metadata
    assert "metadata" not in payloads[0]
    # Retry call has metadata with retry hint
    assert payloads[1]["metadata"] == {"retry_hint": "attempt_1"}


# ---------------------------------------------------------------------------
# stream() retry tests
# ---------------------------------------------------------------------------


@patch("hermit.runtime.provider_host.llm.claude.time.sleep")
def test_stream_retries_on_content_filter_then_succeeds(mock_sleep) -> None:
    """Stream: first call raises, second succeeds."""
    call_count = 0

    stream_events = [
        SimpleNamespace(
            type="content_block_start",
            content_block={"type": "text", "text": ""},
        ),
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="text_delta", text="ok"),
        ),
        SimpleNamespace(type="content_block_stop"),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=1),
        ),
    ]

    def fake_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _ContentFilterError()
        return iter(stream_events)

    provider = ClaudeProvider(
        client=SimpleNamespace(messages=SimpleNamespace(create=fake_create)),
        model="claude-3",
    )

    events = list(provider.stream(_simple_request()))

    assert call_count == 2
    mock_sleep.assert_called_once_with(_CONTENT_FILTER_RETRY_DELAY)
    text_events = [e for e in events if e.type == "text"]
    assert text_events[0].text == "ok"


@patch("hermit.runtime.provider_host.llm.claude.time.sleep")
def test_stream_raises_after_max_retries_exhausted(mock_sleep) -> None:
    """Stream: all attempts raise — error propagates."""
    call_count = 0

    def fake_create(**kwargs):
        nonlocal call_count
        call_count += 1
        raise _ContentFilterError()

    provider = ClaudeProvider(
        client=SimpleNamespace(messages=SimpleNamespace(create=fake_create)),
        model="claude-3",
    )

    with pytest.raises(_ContentFilterError):
        list(provider.stream(_simple_request()))

    assert call_count == _CONTENT_FILTER_MAX_RETRIES + 1
    assert mock_sleep.call_count == _CONTENT_FILTER_MAX_RETRIES


@patch("hermit.runtime.provider_host.llm.claude.time.sleep")
def test_stream_does_not_retry_non_filter_errors(mock_sleep) -> None:
    """Stream: non-filter errors raise immediately."""

    def fake_create(**kwargs):
        raise RuntimeError("network timeout")

    provider = ClaudeProvider(
        client=SimpleNamespace(messages=SimpleNamespace(create=fake_create)),
        model="claude-3",
    )

    with pytest.raises(RuntimeError, match="network timeout"):
        list(provider.stream(_simple_request()))

    mock_sleep.assert_not_called()
