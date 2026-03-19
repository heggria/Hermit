"""Pure formatting and truncation helpers extracted from executor.py."""

from __future__ import annotations

import json
from typing import Any, cast

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.kernel.execution.coordination.observation import (
    normalize_observation_progress,
)
from hermit.kernel.task.projections.progress_summary import (
    normalize_progress_summary,
)
from hermit.runtime.capability.registry.tools import serialize_tool_result

BLOCK_TYPES = {"text", "image"}


def _t(message_key: str, *, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=resolve_locale(), default=default, **kwargs)


def truncate_middle(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 32:
        return text[:limit]
    head = max(1, limit // 2 - 8)
    tail = max(1, limit - head - len("\n...\n"))
    return f"{text[:head]}\n...\n{text[-tail:]}"


def format_model_content(value: Any, limit: int) -> Any:
    serialized: Any = serialize_tool_result(value)
    if isinstance(serialized, str):
        return truncate_middle(serialized, limit)
    if isinstance(serialized, dict) and cast(dict[str, Any], serialized).get("type") in BLOCK_TYPES:
        return cast(list[Any], [serialized])
    if isinstance(serialized, list) and all(
        isinstance(item, dict) and cast(dict[str, Any], item).get("type") in BLOCK_TYPES
        for item in cast(list[Any], serialized)
    ):
        return cast(list[Any], serialized)
    text = json.dumps(serialized, ensure_ascii=True, indent=2, sort_keys=True)
    return truncate_middle(text, limit)


def progress_signature(
    value: dict[str, Any] | None,
) -> tuple[str, str, str | None, int | None, bool] | None:
    progress = normalize_observation_progress(value)
    if progress is None:
        return None
    return progress.signature()


def progress_summary_signature(
    value: dict[str, Any] | None,
) -> tuple[str, str | None, str | None, int | None] | None:
    summary = normalize_progress_summary(value)
    if summary is None:
        return None
    return summary.signature()


def compact_progress_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"
