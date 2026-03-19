from __future__ import annotations

from typing import Any

from hermit.kernel.task.constants import _FEISHU_META_RE, _SESSION_TIME_RE


def _clean_topic_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    cleaned = _SESSION_TIME_RE.sub("", text)
    cleaned = _FEISHU_META_RE.sub("", cleaned)
    cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip())
    return cleaned.strip()


def _append_item(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
    signature = (
        str(item.get("kind", "")),
        str(item.get("text", "")),
        str(item.get("phase", "") or ""),
        item.get("progress_percent"),
    )
    if items:
        previous = items[-1]
        previous_signature = (
            str(previous.get("kind", "")),
            str(previous.get("text", "")),
            str(previous.get("phase", "") or ""),
            previous.get("progress_percent"),
        )
        if signature == previous_signature:
            return
    items.append(item)


def build_task_topic(
    events: list[dict[str, Any]],
    *,
    initial: dict[str, Any] | None = None,
) -> dict[str, Any]:
    seed = dict(initial or {})
    items = list(seed.get("items", []) or [])
    current_hint = str(seed.get("current_hint", "") or "Task is running.")
    current_phase = str(seed.get("current_phase", "") or "")
    current_progress_percent = seed.get("current_progress_percent")
    try:
        current_progress_percent = (
            int(current_progress_percent) if current_progress_percent is not None else None
        )
    except (TypeError, ValueError):
        current_progress_percent = None
    status = str(seed.get("status", "") or "running")

    for event in events:
        event_type = str(event.get("event_type", ""))
        payload = dict(event.get("payload", {}) or {})
        item: dict[str, Any] | None = None

        if event_type == "task.created":
            body = (
                _clean_topic_text(payload.get("title", "") or payload.get("goal", ""))
                or "Task started."
            )
            item = {"kind": "task.started", "text": body}
            current_hint = body
            current_phase = "started"
            current_progress_percent = None
        elif event_type == "tool.submitted":
            body = (
                _clean_topic_text(payload.get("topic_summary", "") or "")
                or _clean_topic_text(payload.get("display_name", "") or "")
                or _clean_topic_text(payload.get("tool_name", "") or "")
            ).strip() or "Tool submitted."
            item = {"kind": "tool.submitted", "text": body, "phase": "submitted"}
            current_hint = body
            current_phase = "submitted"
            current_progress_percent = None
        elif event_type == "tool.progressed":
            summary = _clean_topic_text(payload.get("summary", "") or "")
            detail = _clean_topic_text(payload.get("detail", "") or "")
            body = summary or detail or "Tool progressed."
            if detail and detail != summary:
                body = f"{body}\n{detail}"
            percent = payload.get("progress_percent")
            try:
                progress_percent = int(percent) if percent is not None else None
            except (TypeError, ValueError):
                progress_percent = None
            phase = str(payload.get("phase", "") or "").strip() or "running"
            item = {
                "kind": "tool.progressed",
                "text": body,
                "phase": phase,
                "progress_percent": progress_percent,
            }
            current_hint = summary or body
            current_phase = phase
            current_progress_percent = progress_percent
        elif event_type == "tool.status.changed":
            body = _clean_topic_text(payload.get("topic_summary", "") or payload.get("status", ""))
            if body and body != current_hint:
                item = {"kind": "tool.status.changed", "text": body}
                current_hint = body
                current_phase = str(payload.get("status", "") or current_phase)
                current_progress_percent = None
        elif event_type == "task.progress.summarized":
            summary = _clean_topic_text(payload.get("summary", "") or "")
            detail = _clean_topic_text(payload.get("detail", "") or "")
            body = summary or detail
            if body:
                if detail and detail != summary:
                    body = f"{body}\n{detail}"
                percent = payload.get("progress_percent")
                try:
                    progress_percent = int(percent) if percent is not None else None
                except (TypeError, ValueError):
                    progress_percent = None
                phase = str(payload.get("phase", "") or "").strip() or current_phase or "running"
                item = {
                    "kind": "task.progress.summarized",
                    "text": body,
                    "phase": phase,
                    "progress_percent": progress_percent,
                }
                current_hint = summary or body
                current_phase = phase
                if progress_percent is not None:
                    current_progress_percent = progress_percent
        elif event_type == "task.note.appended":
            body = _clean_topic_text(payload.get("raw_text", "") or payload.get("prompt", ""))
            if body:
                item = {"kind": "user.note.appended", "text": body}
        elif event_type == "execution_contract.selected":
            body = (
                _clean_topic_text(payload.get("objective", "") or "")
                or "Execution contract selected."
            )
            item = {"kind": "execution_contract.selected", "text": body, "phase": "contracting"}
            current_hint = body
            current_phase = "contracting"
        elif event_type == "execution_contract.superseded":
            body = "Execution contract superseded."
            item = {"kind": "execution_contract.superseded", "text": body, "phase": "contracting"}
            current_hint = body
            current_phase = "contracting"
        elif event_type == "evidence_case.recorded":
            body = (
                _clean_topic_text(payload.get("operator_summary", "") or "") or "Evidence compiled."
            )
            item = {"kind": "evidence_case.recorded", "text": body, "phase": "preflighting"}
            current_hint = body
            current_phase = "preflighting"
        elif event_type == "evidence_case.invalidated":
            body = _clean_topic_text(payload.get("summary", "") or "") or "Evidence invalidated."
            item = {"kind": "evidence_case.invalidated", "text": body, "phase": "preflighting"}
            current_hint = body
            current_phase = "preflighting"
        elif event_type == "authorization_plan.recorded":
            body = (
                _clean_topic_text(payload.get("approval_route", "") or "")
                or "Authorization plan prepared."
            )
            item = {"kind": "authorization_plan.recorded", "text": body, "phase": "preflighting"}
            current_hint = body
            current_phase = "preflighting"
        elif event_type == "authorization_plan.invalidated":
            body = (
                _clean_topic_text(payload.get("summary", "") or "")
                or "Authorization plan invalidated."
            )
            item = {"kind": "authorization_plan.invalidated", "text": body, "phase": "preflighting"}
            current_hint = body
            current_phase = "preflighting"
        elif event_type == "approval.requested":
            body = "Approval requested."
            item = {"kind": "approval.requested", "text": body, "phase": "awaiting_approval"}
            current_hint = body
            current_phase = "awaiting_approval"
            current_progress_percent = None
        elif event_type in {"approval.drifted", "approval.expired"}:
            body = "Approval drifted and must be revalidated."
            item = {"kind": "approval.drifted", "text": body, "phase": "awaiting_approval"}
            current_hint = body
            current_phase = "awaiting_approval"
            current_progress_percent = None
        elif event_type in {"approval.granted", "approval.denied", "approval.consumed"}:
            body = f"Approval {event_type.split('.', 1)[1]}."
            item = {"kind": "approval.resolved", "text": body}
            current_hint = body
            current_phase = "approval_resolved"
            current_progress_percent = None
        elif event_type == "reconciliation.closed":
            result_class = _clean_topic_text(payload.get("result_class", "") or "")
            body = f"Reconciliation closed: {result_class or 'completed'}."
            item = {"kind": "reconciliation.closed", "text": body, "phase": "reconciling"}
            current_hint = body
            current_phase = "reconciling"
        elif event_type in {"task.completed", "task.failed", "task.cancelled"}:
            terminal = event_type.split(".", 1)[1]
            preview = _clean_topic_text(payload.get("result_preview", "") or "")
            body = preview or f"Task {terminal}."
            item = {"kind": event_type, "text": body, "phase": terminal}
            status = terminal
            if preview:
                current_hint = preview
                current_phase = terminal
            elif not current_hint or current_hint == "Task is running.":
                current_hint = body
            if not current_phase:
                current_phase = terminal
            if terminal == "completed" and current_progress_percent is None:
                current_progress_percent = 100

        if item is None:
            continue
        item["event_seq"] = int(event.get("event_seq", 0) or 0)
        item["occurred_at"] = event.get("occurred_at")
        _append_item(items, item)

    return {
        "status": status,
        "current_hint": current_hint,
        "current_phase": current_phase,
        "current_progress_percent": current_progress_percent,
        "items": items[-20:],
    }
