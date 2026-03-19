"""Approval copy formatting service.

This module contains:
- ``_APPROVAL_COPY_SYSTEM_PROMPT`` – default system prompt for the LLM formatter
- ``LLMApprovalFormatter`` – rewrites raw approval facts into user-friendly copy
- ``build_approval_copy_service`` – factory that returns a configured
  :class:`hermit.kernel.ApprovalCopyService`
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.kernel import ApprovalCopyService
from hermit.runtime.provider_host.execution.vision_services import _parse_json_response
from hermit.runtime.provider_host.shared.contracts import Provider, ProviderRequest

log = structlog.get_logger()

_APPROVAL_COPY_SYSTEM_PROMPT = (
    "You rewrite approval prompts into user-friendly English product copy. "
    "You must only use the supplied JSON facts and must not invent any targets, commands, risks, or services. "
    "Return strict JSON with exactly these keys: title, summary, detail. "
    "Keep summary and detail concise, clear, and human. "
    "Explain what the tool is about to do and why approval is needed. "
    "Do not dump raw shell commands into summary or detail unless absolutely necessary."
)


class LLMApprovalFormatter:
    def __init__(
        self,
        provider: Provider,
        *,
        model: str,
        locale: str | None = None,
        max_tokens: int = 120,
    ) -> None:
        self.provider = provider
        self.model = model
        self.locale = resolve_locale(locale)
        self.max_tokens = max_tokens

    def format(self, facts: dict[str, Any]) -> dict[str, str] | None:
        response = self.provider.generate(
            ProviderRequest(
                model=self.model,
                max_tokens=self.max_tokens,
                system_prompt=tr(
                    "kernel.provider.approval_formatter.system_prompt",
                    locale=self.locale,
                    default=_APPROVAL_COPY_SYSTEM_PROMPT,
                ),
                messages=[
                    {"role": "user", "content": json.dumps(facts, ensure_ascii=False, indent=2)}
                ],
            )
        )
        parsed = _parse_json_response(response)
        if not isinstance(parsed, dict):
            return None
        title = str(parsed.get("title", "")).strip()
        summary = str(parsed.get("summary", "")).strip()
        detail = str(parsed.get("detail", "")).strip()
        if not title or not summary or not detail:
            return None
        return {
            "title": title,
            "summary": summary,
            "detail": detail,
        }


def build_approval_copy_service(settings: Any) -> ApprovalCopyService:
    locale = getattr(settings, "locale", None)
    if not bool(getattr(settings, "approval_copy_formatter_enabled", False)):
        return ApprovalCopyService(locale=locale)
    try:
        from hermit.runtime.provider_host.execution.services import build_provider

        model = getattr(settings, "approval_copy_model", None) or getattr(settings, "model", "")
        provider = build_provider(settings, model=model, system_prompt=None)
        formatter = LLMApprovalFormatter(
            provider,
            model=getattr(provider, "model", model),
            locale=locale,
        )
        return ApprovalCopyService(
            formatter=formatter.format,
            formatter_timeout_ms=int(getattr(settings, "approval_copy_formatter_timeout_ms", 500)),
            locale=locale,
        )
    except Exception as exc:
        log.warning("approval_copy_formatter_init_failed", error=str(exc))
        return ApprovalCopyService(locale=locale)
