"""Shared utilities for runner sub-modules.

All of the following symbols were previously duplicated across
``runner.py``, ``task_executor.py``, ``session_context_builder.py``,
``control_actions.py``, ``message_compiler.py``, and
``approval_resolver.py``.  They now live here so every module imports
from a single source of truth.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.runtime.provider_host.execution.runtime import AgentResult

if TYPE_CHECKING:
    from hermit.runtime.capability.registry.manager import PluginManager
    from hermit.runtime.control.runner.runner import AgentRunner

# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

#: Matches ``<session_time>…</session_time>`` blocks (including trailing
#: whitespace) so they can be stripped from result text before storage or
#: display.
_SESSION_TIME_RE = re.compile(r"<session_time>.*?</session_time>\s*", re.DOTALL)

#: Matches any ``<feishu_…>…</feishu_…>`` metadata block so it can be
#: removed from result text that is not intended for the Feishu adapter.
_FEISHU_META_RE = re.compile(r"<feishu_[^>]+>.*?</feishu_[^>]+>\s*", re.DOTALL)

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _strip_internal_markup(text: str) -> str:
    """Remove session-time and Feishu metadata blocks from *text*.

    Empty lines produced by the removal are also collapsed so the caller
    always gets a clean, non-padded string.
    """
    if not text:
        return ""
    cleaned = _SESSION_TIME_RE.sub("", text)
    cleaned = _FEISHU_META_RE.sub("", cleaned)
    cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip())
    return cleaned.strip()


def result_preview(text: str, *, limit: int = 280) -> str:
    """Return a short, single-line preview of *text* suitable for storage.

    Internal markup is stripped first; the result is then collapsed to a
    single run of whitespace and truncated at *limit* characters with an
    ellipsis if necessary.
    """
    cleaned = _strip_internal_markup(text)
    if not cleaned:
        return ""
    cleaned = " ".join(cleaned.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "\u2026"


# ---------------------------------------------------------------------------
# Status helper
# ---------------------------------------------------------------------------


def result_status(result: AgentResult) -> str:
    """Determine the canonical execution status string for *result*.

    Prefers an explicit ``execution_status`` attribute on the result; falls
    back to heuristic inspection of the result text for well-known prefixes.
    """
    explicit = str(getattr(result, "execution_status", "") or "").strip()
    if explicit:
        return explicit
    text = result.text or ""
    if text.startswith("[Execution Requires Attention]"):
        return "needs_attention"
    if text.startswith("[API Error]") or text.startswith("[Policy Denied]"):
        return "failed"
    return "succeeded"


# ---------------------------------------------------------------------------
# i18n helper
# ---------------------------------------------------------------------------


def _locale_for(
    *,
    runner: AgentRunner | None = None,
    pm: PluginManager | None = None,
) -> str:
    """Resolve the active locale from the most specific available source.

    Priority: *runner* → *pm* → system default.
    """
    # runner carries pm internally; try runner first so callers can pass
    # either one without worrying about the other.
    if runner is not None:
        settings = getattr(getattr(runner, "pm", None), "settings", None)
    elif pm is not None:
        settings = getattr(pm, "settings", None)
    else:
        settings = None
    return resolve_locale(getattr(settings, "locale", None))


def _t(
    message_key: str,
    *,
    runner: AgentRunner | None = None,
    pm: PluginManager | None = None,
    default: str | None = None,
    **kwargs: object,
) -> str:
    """Translate *message_key* using the locale resolved from *runner* or *pm*.

    This is the canonical replacement for the three previously duplicated
    ``_t()`` functions in ``control_actions``, ``message_compiler``, and
    ``approval_resolver``.  Callers that previously passed ``runner=`` or
    ``pm=`` continue to work unchanged; callers that passed neither (e.g.
    ``approval_resolver``) also work because ``_locale_for`` falls back to
    the system default.
    """
    return tr(
        message_key,
        locale=_locale_for(runner=runner, pm=pm),
        default=default,
        **kwargs,
    )
