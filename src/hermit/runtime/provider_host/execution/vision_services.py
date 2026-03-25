"""Vision and structured JSON extraction services.

This module contains:
- ``_parse_json_response`` – shared helper used across service modules
- ``StructuredExtractionService`` – generic JSON extraction from an LLM provider
- ``VisionAnalysisService`` – image analysis via a vision-capable provider
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

import structlog

from hermit.runtime.provider_host.shared.contracts import (
    Provider,
    ProviderRequest,
    ProviderResponse,
)
from hermit.runtime.provider_host.shared.messages import extract_text

log = structlog.get_logger()


def _parse_json_response(response: ProviderResponse) -> dict[str, Any] | None:
    raw = extract_text(response.content)
    if not raw:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    for candidate in (cleaned, raw):
        try:
            parsed = json.loads(candidate)
            return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            continue
    brace_start = cleaned.find("{")
    if brace_start >= 0:
        fragment = cleaned[brace_start:]
        for suffix in ("", "}", "]}", '"}', '"]}', '"]}'):
            try:
                parsed = json.loads(fragment + suffix)
                return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                continue
    log.warning("provider_json_parse_failed", preview=raw[:200])
    return None


class StructuredExtractionService:
    def __init__(self, provider: Provider, *, model: str) -> None:
        self.provider = provider
        self.model = model

    def extract_json(
        self, *, system_prompt: str, user_content: str, max_tokens: int = 2048
    ) -> dict[str, Any] | None:
        response = self.provider.generate(
            request=self._request(
                system_prompt=system_prompt, user_content=user_content, max_tokens=max_tokens
            )
        )
        return _parse_json_response(response)

    def _request(self, *, system_prompt: str, user_content: str, max_tokens: int) -> Any:
        return ProviderRequest(
            model=self.model,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )


class VisionAnalysisService:
    def __init__(self, provider: Provider, *, model: str) -> None:
        self.provider = provider
        self.model = model

    def analyze_image(
        self, *, system_prompt: str, text: str, image_block: dict[str, Any], max_tokens: int = 512
    ) -> dict[str, Any] | None:
        if not self.provider.features.supports_images:
            raise RuntimeError(f"Provider '{self.provider.name}' does not support image analysis")
        response = self.provider.generate(
            ProviderRequest(
                model=self.model,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
                messages=[
                    {"role": "user", "content": [image_block, {"type": "text", "text": text}]}
                ],
            )
        )
        return _parse_json_response(response)
