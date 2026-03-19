from __future__ import annotations

from typing import Any

from hermit.kernel.task.constants import _FEISHU_META_RE, _SESSION_TIME_RE
from hermit.kernel.task.services.topics import build_task_topic

TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}


def clean_runtime_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    cleaned = _SESSION_TIME_RE.sub("", text)
    cleaned = _FEISHU_META_RE.sub("", cleaned)
    cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip())
    return cleaned.strip()


def trim_text(value: Any, *, limit: int) -> str:
    cleaned = clean_runtime_text(value)
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 1:
        return cleaned[:limit]
    return cleaned[: limit - 1].rstrip() + "…"


def outcome_source_artifact_refs(store: Any, task_id: str, *, limit: int = 5) -> list[str]:
    refs: list[str] = []
    for receipt in store.list_receipts(task_id=task_id, limit=50):
        for artifact_ref in list(receipt.output_refs or []):
            artifact_id = str(artifact_ref or "").strip()
            if artifact_id and artifact_id not in refs:
                refs.append(artifact_id)
            if len(refs) >= limit:
                return refs[:limit]
    return refs[:limit]


def build_task_outcome(
    *,
    store: Any,
    task_id: str,
    status: str,
    events: list[dict[str, Any]],
    summary_limit: int = 280,
    artifact_limit: int = 5,
) -> dict[str, Any] | None:
    if status not in TERMINAL_TASK_STATUSES:
        return None
    terminal_event = next(
        (event for event in reversed(events) if event["event_type"] == f"task.{status}"),
        None,
    )
    if terminal_event is None:
        return None
    payload = dict(terminal_event.get("payload") or {})
    result_preview = clean_runtime_text(payload.get("result_preview", ""))
    result_text_excerpt = trim_text(payload.get("result_text", ""), limit=summary_limit)
    topic = build_task_topic(events)
    outcome_summary = (
        result_text_excerpt
        or result_preview
        or trim_text(topic.get("current_hint", ""), limit=summary_limit)
        or f"Task {status}."
    )
    return {
        "status": status,
        "result_preview": result_preview,
        "result_text_excerpt": result_text_excerpt,
        "completed_at": float(terminal_event.get("occurred_at") or 0.0),
        "outcome_summary": outcome_summary,
        "source_artifact_refs": outcome_source_artifact_refs(store, task_id, limit=artifact_limit),
    }


__all__ = [
    "TERMINAL_TASK_STATUSES",
    "build_task_outcome",
    "clean_runtime_text",
    "outcome_source_artifact_refs",
    "trim_text",
]
