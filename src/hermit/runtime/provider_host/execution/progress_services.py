"""Progress summary formatting service.

This module contains:
- ``_PROGRESS_SUMMARY_SYSTEM_PROMPT`` – default system prompt for the LLM summarizer
- ``LLMProgressSummarizer`` – produces :class:`hermit.kernel.task.projections.progress_summary.ProgressSummary`
  objects from a live task-facts snapshot
- ``build_progress_summarizer`` – factory that wires up the summarizer from settings
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from hermit.infra.system.i18n import resolve_locale
from hermit.kernel.task.projections.progress_summary import (
    ProgressSummary,
    ProgressSummaryFormatter,
)
from hermit.runtime.provider_host.execution.vision_services import _parse_json_response
from hermit.runtime.provider_host.shared.contracts import Provider, ProviderRequest

log = structlog.get_logger()

_PROGRESS_SUMMARY_SYSTEM_PROMPT = (
    "You write short live progress updates for an AI agent task. "
    "You must only use the supplied JSON facts and must not invent any steps, results, blockers, or tools. "
    "Return strict JSON with exactly these keys: summary, detail, phase, progress_percent. "
    "The summary must be a single short sentence describing what the task is doing right now. "
    "The detail should be optional, compact, and explain the next likely step or blocker when useful. "
    "Keep the tone calm and operator-friendly, like a live task update. "
    "Do not mention internal IDs, JSON, or implementation details."
)


class LLMProgressSummarizer:
    def __init__(
        self,
        provider: Provider,
        *,
        model: str,
        locale: str | None = None,
        max_tokens: int = 160,
    ) -> None:
        self.provider = provider
        self.model = model
        self.locale = resolve_locale(locale)
        self.max_tokens = max_tokens

    def summarize(self, *, facts: dict[str, Any]) -> ProgressSummary | None:
        response = self.provider.generate(
            ProviderRequest(
                model=self.model,
                max_tokens=self.max_tokens,
                system_prompt=self._system_prompt(),
                messages=[
                    {"role": "user", "content": json.dumps(facts, ensure_ascii=False, indent=2)}
                ],
            )
        )
        parsed = _parse_json_response(response)
        if not isinstance(parsed, dict):
            return None
        summary = str(parsed.get("summary", "") or "").strip()
        if not summary:
            return None
        return ProgressSummary.from_dict(parsed)

    def _system_prompt(self) -> str:
        language = "Simplified Chinese" if self.locale.lower().startswith("zh") else "English"
        return f"{_PROGRESS_SUMMARY_SYSTEM_PROMPT} Write the summary in {language}."


def build_progress_summarizer(
    settings: Any,
    *,
    provider: Provider,
    model: str,
) -> ProgressSummaryFormatter | None:
    if not bool(getattr(settings, "progress_summary_enabled", True)):
        return None
    summary_model = getattr(settings, "progress_summary_model", None) or model
    try:
        summary_provider = provider.clone(model=summary_model, system_prompt=None)
        return LLMProgressSummarizer(
            summary_provider,
            model=summary_model,
            locale=getattr(settings, "locale", None),
            max_tokens=int(getattr(settings, "progress_summary_max_tokens", 160) or 160),
        )
    except Exception as exc:
        log.warning("progress_summarizer_init_failed", error=str(exc))
        return None
