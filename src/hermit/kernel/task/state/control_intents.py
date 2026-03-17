from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from hermit.infra.system.i18n import catalog_locales, tr_list


@dataclass(frozen=True)
class ControlIntent:
    action: str
    target_id: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# Lazy-loading helpers
# ---------------------------------------------------------------------------

_REGEX_CACHE: dict[str, re.Pattern[str]] = {}
_SET_CACHE: dict[str, frozenset[str]] = {}


def _all_locale_keywords(key: str) -> list[str]:
    """Load a locale key from ALL available locales and merge results."""
    seen: set[str] = set()
    result: list[str] = []
    for loc in catalog_locales():
        for item in tr_list(key, locale=loc):
            if item and item not in seen:
                seen.add(item)
                result.append(item)
    return result


def _cached_re(key: str, builder: Callable[[], re.Pattern[str]]) -> re.Pattern[str]:
    if key not in _REGEX_CACHE:
        _REGEX_CACHE[key] = builder()
    return _REGEX_CACHE[key]


def _cached_set(key: str) -> frozenset[str]:
    if key not in _SET_CACHE:
        _SET_CACHE[key] = frozenset(_all_locale_keywords(key))
    return _SET_CACHE[key]


# ---------------------------------------------------------------------------
# Regex builders — each combines locale keywords with fixed English patterns
# ---------------------------------------------------------------------------


def _approve_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.approve_keywords")
    parts = [r"/task\s+approve"] + [re.escape(k) for k in kw if k] + ["approve"]
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _approve_once_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.approve_once_keywords")
    parts = [re.escape(k) for k in kw if k] + ["approve_once"]
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _approve_mutable_workspace_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.approve_mutable_workspace_keywords")
    parts = [re.escape(k) for k in kw if k] + [
        "approve_mutable_workspace",
        "approve-mutable-workspace",
    ]
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _deny_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.deny_keywords")
    parts = [r"/task\s+deny"] + [re.escape(k) for k in kw if k] + ["deny"]
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)(?:\s+(.+))?$", re.IGNORECASE)


def _task_case_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.task_case_keywords")
    parts = [r"/task\s+case", r"task\s+case"] + [re.escape(k) for k in kw if k]
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _task_switch_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.task_switch_keywords")
    parts = [re.escape(k) for k in kw if k]
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _task_events_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.task_events_keywords")
    parts = [r"/task\s+events", r"task\s+events"] + [re.escape(k) for k in kw if k]
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _task_receipts_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.task_receipts_keywords")
    parts = [r"/task\s+receipts", r"task\s+receipts"] + [re.escape(k) for k in kw if k]
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _task_proof_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.task_proof_keywords")
    parts = [r"/task\s+proof", r"task\s+proof"] + [re.escape(k) for k in kw if k]
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _task_proof_export_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.task_proof_export_keywords")
    parts = [r"/task\s+proof-export"] + [re.escape(k) for k in kw if k]
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _rollback_with_id_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.rollback_keywords")
    parts = [r"/task\s+rollback", r"task\s+rollback"] + [re.escape(k) for k in kw if k]
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _task_grant_revoke_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.grant_revoke_keywords")
    parts = [re.escape(k) for k in kw if k]
    if not parts:
        return re.compile(r"(?!)")  # never matches
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _schedule_history_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.schedule_history_keywords")
    parts = [re.escape(k) for k in kw if k]
    if not parts:
        return re.compile(r"(?!)")
    return re.compile(r"^(?:" + "|".join(parts) + r")\s*([a-z0-9_]+)?$", re.IGNORECASE)


def _schedule_enable_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.schedule_enable_keywords")
    parts = [re.escape(k) for k in kw if k]
    if not parts:
        return re.compile(r"(?!)")
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _schedule_disable_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.schedule_disable_keywords")
    parts = [re.escape(k) for k in kw if k]
    if not parts:
        return re.compile(r"(?!)")
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


def _schedule_remove_re() -> re.Pattern[str]:
    kw = _all_locale_keywords("kernel.nlp.control.schedule_remove_keywords")
    parts = [re.escape(k) for k in kw if k]
    if not parts:
        return re.compile(r"(?!)")
    return re.compile(r"^(?:" + "|".join(parts) + r")\s+([a-z0-9_]+)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse_control_intent(
    text: str,
    *,
    pending_approval_id: str | None = None,
    latest_task_id: str | None = None,
    latest_receipt_id: str | None = None,
) -> ControlIntent | None:
    stripped = text.strip()
    lowered = stripped.lower()
    if not stripped:
        return None

    match = _cached_re("approve", _approve_re).match(stripped)
    if match:
        return ControlIntent("approve_once", match.group(1))
    match = _cached_re("approve_once", _approve_once_re).match(stripped)
    if match:
        return ControlIntent("approve_once", match.group(1))
    match = _cached_re("approve_mutable", _approve_mutable_workspace_re).match(stripped)
    if match:
        return ControlIntent("approve_mutable_workspace", match.group(1))
    match = _cached_re("deny", _deny_re).match(stripped)
    if match:
        return ControlIntent("deny", match.group(1), match.group(2) or "")
    if pending_approval_id and stripped in _cached_set("kernel.nlp.control.pending_approve_texts"):
        return ControlIntent("approve_once", pending_approval_id)

    if lowered in _cached_set("kernel.nlp.control.help_texts"):
        return ControlIntent("show_help")
    if lowered in _cached_set("kernel.nlp.control.new_texts"):
        return ControlIntent("new_session")
    if lowered in _cached_set("kernel.nlp.control.history_texts"):
        return ControlIntent("show_history")
    if lowered in _cached_set("kernel.nlp.control.task_list_texts"):
        return ControlIntent("task_list")

    match = _cached_re("task_switch", _task_switch_re).match(stripped)
    if match:
        return ControlIntent("focus_task", match.group(1), "explicit_task_switch")

    match = _cached_re("task_case", _task_case_re).match(stripped)
    if match:
        return ControlIntent("case", match.group(1))
    if latest_task_id and lowered in _cached_set("kernel.nlp.control.case_latest_texts"):
        return ControlIntent("case", latest_task_id)

    match = _cached_re("task_events", _task_events_re).match(stripped)
    if match:
        return ControlIntent("task_events", match.group(1))
    if latest_task_id and lowered in _cached_set("kernel.nlp.control.events_latest_texts"):
        return ControlIntent("task_events", latest_task_id)

    match = _cached_re("task_receipts", _task_receipts_re).match(stripped)
    if match:
        return ControlIntent("task_receipts", match.group(1))
    if latest_task_id and lowered in _cached_set("kernel.nlp.control.receipts_latest_texts"):
        return ControlIntent("task_receipts", latest_task_id)

    match = _cached_re("task_proof", _task_proof_re).match(stripped)
    if match:
        return ControlIntent("task_proof", match.group(1))
    if latest_task_id and lowered in _cached_set("kernel.nlp.control.proof_latest_texts"):
        return ControlIntent("task_proof", latest_task_id)

    match = _cached_re("task_proof_export", _task_proof_export_re).match(stripped)
    if match:
        return ControlIntent("task_proof_export", match.group(1))
    if latest_task_id and lowered in _cached_set("kernel.nlp.control.proof_export_latest_texts"):
        return ControlIntent("task_proof_export", latest_task_id)

    if lowered in _cached_set("kernel.nlp.control.plan_enter_texts"):
        return ControlIntent("plan_enter", latest_task_id or "")
    if latest_task_id and lowered in _cached_set("kernel.nlp.control.plan_confirm_texts"):
        return ControlIntent("plan_confirm", latest_task_id)
    if latest_task_id and lowered in _cached_set("kernel.nlp.control.plan_exit_texts"):
        return ControlIntent("plan_exit", latest_task_id)

    match = _cached_re("rollback", _rollback_with_id_re).match(stripped)
    if match:
        return ControlIntent("rollback", match.group(1))
    if latest_receipt_id and lowered in _cached_set("kernel.nlp.control.rollback_latest_texts"):
        return ControlIntent("rollback", latest_receipt_id)

    if lowered in _cached_set("kernel.nlp.control.grant_list_texts"):
        return ControlIntent("capability_list")
    match = _cached_re("grant_revoke", _task_grant_revoke_re).match(stripped)
    if match:
        return ControlIntent("capability_revoke", match.group(1))

    if lowered in _cached_set("kernel.nlp.control.schedule_list_texts"):
        return ControlIntent("schedule_list")
    match = _cached_re("schedule_history", _schedule_history_re).match(stripped)
    if match:
        return ControlIntent("schedule_history", (match.group(1) or "").strip())
    match = _cached_re("schedule_enable", _schedule_enable_re).match(stripped)
    if match:
        return ControlIntent("schedule_enable", match.group(1))
    match = _cached_re("schedule_disable", _schedule_disable_re).match(stripped)
    if match:
        return ControlIntent("schedule_disable", match.group(1))
    match = _cached_re("schedule_remove", _schedule_remove_re).match(stripped)
    if match:
        return ControlIntent("schedule_remove", match.group(1))

    if lowered in _cached_set("kernel.nlp.control.rebuild_projection_texts") and latest_task_id:
        return ControlIntent("projection_rebuild", latest_task_id)
    if lowered in _cached_set("kernel.nlp.control.rebuild_all_projection_texts"):
        return ControlIntent("projection_rebuild_all")

    return None
