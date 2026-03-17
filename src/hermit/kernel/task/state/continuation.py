from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from hermit.infra.system.i18n import tr_list_all_locales
from hermit.kernel.context.memory.text import shares_topic, topic_tokens


def _load_markers(key: str) -> tuple[str, ...]:
    return tuple(tr_list_all_locales(key))


def _branch_markers() -> tuple[str, ...]:
    return _load_markers("kernel.nlp.continuation.branch")


def _explicit_new_task_markers() -> tuple[str, ...]:
    return _load_markers("kernel.nlp.continuation.new_task")


def _continue_markers() -> tuple[str, ...]:
    return _load_markers("kernel.nlp.continuation.continue")


def _ambiguous_followup_markers() -> tuple[str, ...]:
    return _load_markers("kernel.nlp.continuation.ambiguous_followup")


def _corrective_markers() -> tuple[str, ...]:
    return _load_markers("kernel.nlp.continuation.corrective")


def _corrective_pattern() -> re.Pattern[str]:
    parts = [p for p in tr_list_all_locales("kernel.nlp.continuation.corrective_re") if p]
    if not parts:
        return re.compile(r"(?!)")
    return re.compile("|".join(parts))


def _branch_primary_keywords() -> tuple[str, ...]:
    return tuple(tr_list_all_locales("kernel.nlp.continuation.branch_primary"))


_MULTILINE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    return _MULTILINE_RE.sub(" ", str(text or "")).strip()


def has_explicit_new_task_marker(text: str) -> bool:
    cleaned = normalize_text(text)
    return any(marker in cleaned for marker in _explicit_new_task_markers())


def has_continue_marker(text: str) -> bool:
    cleaned = normalize_text(text)
    return any(marker in cleaned for marker in _continue_markers())


def has_ambiguous_followup_marker(text: str) -> bool:
    cleaned = normalize_text(text)
    return any(marker in cleaned for marker in _ambiguous_followup_markers())


def has_branch_marker(text: str) -> bool:
    cleaned = normalize_text(text)
    return any(marker in cleaned for marker in _branch_markers())


def has_corrective_marker(text: str) -> bool:
    cleaned = normalize_text(text)
    if any(marker in cleaned for marker in _corrective_markers()):
        return True
    return bool(_corrective_pattern().search(cleaned))


def texts_overlap(text: str, candidate_text: str) -> bool:
    cleaned = normalize_text(text)
    candidate = normalize_text(candidate_text)
    if not cleaned or not candidate:
        return False
    if shares_topic(candidate, cleaned):
        return True
    query_tokens = {token for token in topic_tokens(cleaned) if len(token) >= 2}
    candidate_tokens = {token for token in topic_tokens(candidate) if len(token) >= 2}
    if query_tokens & candidate_tokens:
        return True
    if any(token in candidate for token in query_tokens):
        return True
    return any(token in cleaned for token in candidate_tokens if len(token) >= 4)


@dataclass(frozen=True)
class ContinuationGuidance:
    mode: Literal[
        "no_anchor",
        "anchor_correction",
        "explicit_topic_shift",
        "strong_topic_shift",
        "plain_new_task",
    ]
    has_anchor: bool
    is_short_request: bool
    is_ambiguous_request: bool
    is_corrective_request: bool
    has_continue_marker: bool
    has_explicit_topic_shift: bool
    has_strong_topic_shift: bool
    has_topic_overlap: bool
    anchor_task_id: str = ""
    anchor_title: str = ""
    anchor_goal: str = ""
    anchor_user_request: str = ""
    outcome_summary: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "has_anchor": self.has_anchor,
            "is_short_request": self.is_short_request,
            "is_ambiguous_request": self.is_ambiguous_request,
            "is_corrective_request": self.is_corrective_request,
            "has_continue_marker": self.has_continue_marker,
            "has_explicit_topic_shift": self.has_explicit_topic_shift,
            "has_strong_topic_shift": self.has_strong_topic_shift,
            "has_topic_overlap": self.has_topic_overlap,
            "anchor_task_id": self.anchor_task_id,
            "anchor_title": self.anchor_title,
            "anchor_goal": self.anchor_goal,
            "anchor_user_request": self.anchor_user_request,
            "outcome_summary": self.outcome_summary,
        }


def build_continuation_guidance(
    *, current_request: str, anchor: dict[str, Any] | None
) -> ContinuationGuidance:
    cleaned = normalize_text(current_request)
    if not anchor:
        return ContinuationGuidance(
            mode="no_anchor",
            has_anchor=False,
            is_short_request=_is_short_request(cleaned),
            is_ambiguous_request=_is_ambiguous_request(cleaned),
            is_corrective_request=has_corrective_marker(cleaned),
            has_continue_marker=has_continue_marker(cleaned),
            has_explicit_topic_shift=has_explicit_new_task_marker(cleaned)
            and not any(kw in cleaned for kw in _branch_primary_keywords()),
            has_strong_topic_shift=False,
            has_topic_overlap=False,
        )

    anchor_title = str(anchor.get("anchor_title", "") or "")
    anchor_goal = str(anchor.get("anchor_goal", "") or "")
    anchor_user_request = str(anchor.get("anchor_user_request", "") or "")
    outcome_summary = str(anchor.get("outcome_summary", "") or "")
    anchor_texts = [anchor_user_request, anchor_goal, anchor_title, outcome_summary]

    short_request = _is_short_request(cleaned)
    ambiguous_request = _is_ambiguous_request(cleaned)
    corrective_request = has_corrective_marker(cleaned)
    continue_request = has_continue_marker(cleaned)
    explicit_topic_shift = has_explicit_new_task_marker(cleaned) and not any(
        kw in cleaned for kw in _branch_primary_keywords()
    )
    topic_overlap = any(texts_overlap(cleaned, text) for text in anchor_texts if text)
    strong_topic_shift = (
        bool(cleaned)
        and not explicit_topic_shift
        and not topic_overlap
        and not corrective_request
        and not continue_request
        and not ambiguous_request
    )

    if explicit_topic_shift:
        mode = "explicit_topic_shift"
    elif strong_topic_shift:
        mode = "strong_topic_shift"
    elif short_request and (ambiguous_request or corrective_request or continue_request):
        mode = "anchor_correction"
    else:
        mode = "plain_new_task"

    return ContinuationGuidance(
        mode=mode,
        has_anchor=True,
        is_short_request=short_request,
        is_ambiguous_request=ambiguous_request,
        is_corrective_request=corrective_request,
        has_continue_marker=continue_request,
        has_explicit_topic_shift=explicit_topic_shift,
        has_strong_topic_shift=strong_topic_shift,
        has_topic_overlap=topic_overlap,
        anchor_task_id=str(anchor.get("anchor_task_id", "") or ""),
        anchor_title=anchor_title,
        anchor_goal=anchor_goal,
        anchor_user_request=anchor_user_request,
        outcome_summary=outcome_summary,
    )


def _is_short_request(text: str) -> bool:
    return bool(text) and len(text) <= 18


def _is_ambiguous_request(text: str) -> bool:
    return has_ambiguous_followup_marker(text) or has_corrective_marker(text)


BRANCH_MARKERS = _branch_markers
EXPLICIT_NEW_TASK_MARKERS = _explicit_new_task_markers
CONTINUE_MARKERS = _continue_markers
AMBIGUOUS_FOLLOWUP_MARKERS = _ambiguous_followup_markers
CORRECTIVE_MARKERS = _corrective_markers

__all__ = [
    "AMBIGUOUS_FOLLOWUP_MARKERS",
    "BRANCH_MARKERS",
    "CONTINUE_MARKERS",
    "CORRECTIVE_MARKERS",
    "EXPLICIT_NEW_TASK_MARKERS",
    "ContinuationGuidance",
    "build_continuation_guidance",
    "has_ambiguous_followup_marker",
    "has_branch_marker",
    "has_continue_marker",
    "has_corrective_marker",
    "has_explicit_new_task_marker",
    "normalize_text",
    "texts_overlap",
]
