"""Tests for progress_services.py — LLMProgressSummarizer and build_progress_summarizer."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from hermit.kernel.task.projections.progress_summary import ProgressSummary
from hermit.runtime.provider_host.execution.progress_services import (
    _PROGRESS_SUMMARY_SYSTEM_PROMPT,
    LLMProgressSummarizer,
    build_progress_summarizer,
)
from hermit.runtime.provider_host.shared.contracts import (
    ProviderResponse,
)


def _make_response(text: str) -> ProviderResponse:
    return ProviderResponse(content=[{"type": "text", "text": text}])


# ── LLMProgressSummarizer ─────────────────────────────────────────


def test_summarize_returns_progress_summary_on_valid_response() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response(
        json.dumps(
            {
                "summary": "Running tests",
                "detail": "pytest suite",
                "phase": "testing",
                "progress_percent": 50,
            }
        )
    )
    summarizer = LLMProgressSummarizer(provider, model="m")

    result = summarizer.summarize(facts={"step": "test"})

    assert isinstance(result, ProgressSummary)
    assert result.summary == "Running tests"
    assert result.detail == "pytest suite"
    assert result.phase == "testing"
    assert result.progress_percent == 50


def test_summarize_returns_none_on_non_dict() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response("[1, 2]")
    summarizer = LLMProgressSummarizer(provider, model="m")

    result = summarizer.summarize(facts={})
    assert result is None


def test_summarize_returns_none_when_summary_empty() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response(json.dumps({"summary": "", "phase": "running"}))
    summarizer = LLMProgressSummarizer(provider, model="m")

    result = summarizer.summarize(facts={})
    assert result is None


def test_summarize_returns_none_when_summary_missing() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response(
        json.dumps({"phase": "running", "detail": "stuff"})
    )
    summarizer = LLMProgressSummarizer(provider, model="m")

    result = summarizer.summarize(facts={})
    assert result is None


def test_summarize_returns_none_when_summary_whitespace_only() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response(
        json.dumps({"summary": "   ", "phase": "running"})
    )
    summarizer = LLMProgressSummarizer(provider, model="m")

    result = summarizer.summarize(facts={})
    assert result is None


def test_summarize_calls_provider_with_correct_request() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response(json.dumps({"summary": "ok"}))
    summarizer = LLMProgressSummarizer(provider, model="test-model", max_tokens=200)

    facts = {"step": "analyze"}
    summarizer.summarize(facts=facts)

    provider.generate.assert_called_once()
    request = provider.generate.call_args[0][0]
    assert request.model == "test-model"
    assert request.max_tokens == 200
    msg_content = request.messages[0]["content"]
    assert json.loads(msg_content) == facts


def test_summarize_returns_none_on_unparseable_response() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response("not json")
    summarizer = LLMProgressSummarizer(provider, model="m")

    result = summarizer.summarize(facts={})
    assert result is None


def test_constructor_sets_locale() -> None:
    provider = MagicMock()
    summarizer = LLMProgressSummarizer(provider, model="m", locale="zh-CN")
    assert summarizer.locale.lower().startswith("zh")


def test_constructor_default_max_tokens() -> None:
    provider = MagicMock()
    summarizer = LLMProgressSummarizer(provider, model="m")
    assert summarizer.max_tokens == 160


# ── _system_prompt ─────────────────────────────────────────────────


def test_system_prompt_english_locale() -> None:
    provider = MagicMock()
    summarizer = LLMProgressSummarizer(provider, model="m", locale="en-US")

    prompt = summarizer._system_prompt()
    assert "English" in prompt
    assert _PROGRESS_SUMMARY_SYSTEM_PROMPT in prompt


def test_system_prompt_chinese_locale() -> None:
    provider = MagicMock()
    summarizer = LLMProgressSummarizer(provider, model="m", locale="zh-CN")

    prompt = summarizer._system_prompt()
    assert "Simplified Chinese" in prompt


def test_system_prompt_zh_prefix() -> None:
    provider = MagicMock()
    summarizer = LLMProgressSummarizer(provider, model="m", locale="zh")

    prompt = summarizer._system_prompt()
    assert "Simplified Chinese" in prompt


# ── build_progress_summarizer ──────────────────────────────────────


def test_build_returns_none_when_disabled() -> None:
    settings = SimpleNamespace(progress_summary_enabled=False)
    provider = MagicMock()

    result = build_progress_summarizer(settings, provider=provider, model="m")
    assert result is None


def test_build_returns_summarizer_when_default_enabled() -> None:
    provider = MagicMock()
    provider.clone.return_value = provider
    settings = SimpleNamespace(locale=None, progress_summary_max_tokens=160)

    result = build_progress_summarizer(settings, provider=provider, model="m")
    assert isinstance(result, LLMProgressSummarizer)


def test_build_uses_progress_summary_model() -> None:
    provider = MagicMock()
    provider.clone.return_value = provider
    settings = SimpleNamespace(
        progress_summary_model="haiku",
        locale=None,
        progress_summary_max_tokens=160,
    )

    build_progress_summarizer(settings, provider=provider, model="claude-3")
    provider.clone.assert_called_once_with(model="haiku", system_prompt=None)


def test_build_falls_back_to_model_param() -> None:
    provider = MagicMock()
    provider.clone.return_value = provider
    settings = SimpleNamespace(
        progress_summary_model=None,
        locale=None,
        progress_summary_max_tokens=160,
    )

    build_progress_summarizer(settings, provider=provider, model="claude-3")
    provider.clone.assert_called_once_with(model="claude-3", system_prompt=None)


def test_build_returns_none_when_clone_fails() -> None:
    provider = MagicMock()
    provider.clone.side_effect = RuntimeError("clone failed")
    settings = SimpleNamespace(
        progress_summary_model=None,
        locale=None,
        progress_summary_max_tokens=160,
    )

    result = build_progress_summarizer(settings, provider=provider, model="m")
    assert result is None


def test_build_passes_locale() -> None:
    provider = MagicMock()
    provider.clone.return_value = provider
    settings = SimpleNamespace(
        progress_summary_model=None,
        locale="zh-CN",
        progress_summary_max_tokens=160,
    )

    result = build_progress_summarizer(settings, provider=provider, model="m")
    assert isinstance(result, LLMProgressSummarizer)
    assert result.locale.lower().startswith("zh")


def test_build_uses_custom_max_tokens() -> None:
    provider = MagicMock()
    provider.clone.return_value = provider
    settings = SimpleNamespace(
        progress_summary_model=None,
        locale=None,
        progress_summary_max_tokens=256,
    )

    result = build_progress_summarizer(settings, provider=provider, model="m")
    assert isinstance(result, LLMProgressSummarizer)
    assert result.max_tokens == 256


def test_build_handles_none_max_tokens() -> None:
    provider = MagicMock()
    provider.clone.return_value = provider
    settings = SimpleNamespace(
        progress_summary_model=None,
        locale=None,
        progress_summary_max_tokens=None,
    )

    result = build_progress_summarizer(settings, provider=provider, model="m")
    assert isinstance(result, LLMProgressSummarizer)
    assert result.max_tokens == 160
