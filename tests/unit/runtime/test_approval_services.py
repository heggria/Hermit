"""Tests for approval_services.py — LLMApprovalFormatter and build_approval_copy_service."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermit.kernel import ApprovalCopyService
from hermit.runtime.provider_host.execution.approval_services import (
    LLMApprovalFormatter,
    build_approval_copy_service,
)
from hermit.runtime.provider_host.shared.contracts import (
    ProviderResponse,
)


def _make_response(text: str) -> ProviderResponse:
    return ProviderResponse(content=[{"type": "text", "text": text}])


# ── LLMApprovalFormatter ──────────────────────────────────────────


def test_formatter_returns_dict_on_valid_json() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response(
        json.dumps({"title": "Run bash", "summary": "Execute command", "detail": "rm -rf /tmp/x"})
    )
    fmt = LLMApprovalFormatter(provider, model="m")

    result = fmt.format({"tool": "bash", "command": "rm -rf /tmp/x"})

    assert result == {
        "title": "Run bash",
        "summary": "Execute command",
        "detail": "rm -rf /tmp/x",
    }


def test_formatter_returns_none_on_non_dict() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response("[1, 2, 3]")
    fmt = LLMApprovalFormatter(provider, model="m")

    result = fmt.format({"tool": "bash"})
    assert result is None


def test_formatter_returns_none_when_title_empty() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response(
        json.dumps({"title": "", "summary": "s", "detail": "d"})
    )
    fmt = LLMApprovalFormatter(provider, model="m")

    result = fmt.format({})
    assert result is None


def test_formatter_returns_none_when_summary_empty() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response(
        json.dumps({"title": "t", "summary": "", "detail": "d"})
    )
    fmt = LLMApprovalFormatter(provider, model="m")

    result = fmt.format({})
    assert result is None


def test_formatter_returns_none_when_detail_empty() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response(
        json.dumps({"title": "t", "summary": "s", "detail": ""})
    )
    fmt = LLMApprovalFormatter(provider, model="m")

    result = fmt.format({})
    assert result is None


def test_formatter_returns_none_when_title_missing() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response(json.dumps({"summary": "s", "detail": "d"}))
    fmt = LLMApprovalFormatter(provider, model="m")

    result = fmt.format({})
    assert result is None


def test_formatter_strips_whitespace() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response(
        json.dumps({"title": "  t  ", "summary": "  s  ", "detail": "  d  "})
    )
    fmt = LLMApprovalFormatter(provider, model="m")

    result = fmt.format({})
    assert result == {"title": "t", "summary": "s", "detail": "d"}


def test_formatter_calls_provider_with_correct_request() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response(
        json.dumps({"title": "t", "summary": "s", "detail": "d"})
    )
    fmt = LLMApprovalFormatter(provider, model="test-model", max_tokens=200)

    facts = {"action": "write_file"}
    fmt.format(facts)

    provider.generate.assert_called_once()
    request = provider.generate.call_args[0][0]
    assert request.model == "test-model"
    assert request.max_tokens == 200
    msg_content = request.messages[0]["content"]
    assert json.loads(msg_content) == facts


def test_formatter_constructor_sets_locale() -> None:
    provider = MagicMock()
    fmt = LLMApprovalFormatter(provider, model="m", locale="zh-CN")
    assert fmt.locale.lower().startswith("zh")


def test_formatter_constructor_default_locale() -> None:
    provider = MagicMock()
    fmt = LLMApprovalFormatter(provider, model="m")
    assert fmt.locale  # Should be non-empty


def test_formatter_constructor_default_max_tokens() -> None:
    provider = MagicMock()
    fmt = LLMApprovalFormatter(provider, model="m")
    assert fmt.max_tokens == 120


# ── build_approval_copy_service ────────────────────────────────────


def test_build_returns_plain_service_when_disabled() -> None:
    settings = SimpleNamespace(
        approval_copy_formatter_enabled=False,
        locale=None,
    )
    result = build_approval_copy_service(settings)
    assert isinstance(result, ApprovalCopyService)
    assert result._formatter is None


def test_build_returns_plain_service_when_not_set() -> None:
    settings = SimpleNamespace(locale=None)
    result = build_approval_copy_service(settings)
    assert isinstance(result, ApprovalCopyService)
    assert result._formatter is None


@patch("hermit.runtime.provider_host.execution.services.build_provider")
def test_build_returns_configured_service_when_enabled(mock_build_provider: MagicMock) -> None:
    mock_provider = MagicMock()
    mock_provider.model = "test-model"
    mock_build_provider.return_value = mock_provider

    settings = SimpleNamespace(
        approval_copy_formatter_enabled=True,
        locale="en-US",
        model="claude-3",
        approval_copy_model=None,
        approval_copy_formatter_timeout_ms=500,
    )

    result = build_approval_copy_service(settings)
    assert isinstance(result, ApprovalCopyService)
    assert result._formatter is not None


@patch("hermit.runtime.provider_host.execution.services.build_provider")
def test_build_returns_plain_on_exception(mock_build_provider: MagicMock) -> None:
    mock_build_provider.side_effect = RuntimeError("no provider")

    settings = SimpleNamespace(
        approval_copy_formatter_enabled=True,
        locale="en-US",
        model="claude-3",
        approval_copy_model=None,
        approval_copy_formatter_timeout_ms=500,
    )

    result = build_approval_copy_service(settings)
    assert isinstance(result, ApprovalCopyService)
    assert result._formatter is None


@patch("hermit.runtime.provider_host.execution.services.build_provider")
def test_build_passes_locale(mock_build_provider: MagicMock) -> None:
    mock_provider = MagicMock()
    mock_provider.model = "m"
    mock_build_provider.return_value = mock_provider

    settings = SimpleNamespace(
        approval_copy_formatter_enabled=True,
        locale="zh-CN",
        model="m",
        approval_copy_model=None,
        approval_copy_formatter_timeout_ms=500,
    )

    result = build_approval_copy_service(settings)
    assert isinstance(result, ApprovalCopyService)


@patch("hermit.runtime.provider_host.execution.services.build_provider")
def test_build_uses_approval_copy_model(mock_build_provider: MagicMock) -> None:
    mock_provider = MagicMock()
    mock_provider.model = "custom-model"
    mock_build_provider.return_value = mock_provider

    settings = SimpleNamespace(
        approval_copy_formatter_enabled=True,
        locale=None,
        model="claude-3",
        approval_copy_model="haiku-fast",
        approval_copy_formatter_timeout_ms=300,
    )

    result = build_approval_copy_service(settings)
    assert isinstance(result, ApprovalCopyService)
    # build_provider should be called with the approval_copy_model
    call_kwargs = mock_build_provider.call_args
    assert call_kwargs[1]["model"] == "haiku-fast"


@patch("hermit.runtime.provider_host.execution.services.build_provider")
def test_build_uses_default_timeout(mock_build_provider: MagicMock) -> None:
    mock_provider = MagicMock()
    mock_provider.model = "m"
    mock_build_provider.return_value = mock_provider

    settings = SimpleNamespace(
        approval_copy_formatter_enabled=True,
        locale=None,
        model="m",
        approval_copy_model=None,
    )

    result = build_approval_copy_service(settings)
    assert isinstance(result, ApprovalCopyService)
