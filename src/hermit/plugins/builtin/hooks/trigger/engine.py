from __future__ import annotations

import re
from typing import Any, cast

import structlog

from hermit.plugins.builtin.hooks.trigger.models import TriggerMatch

log = structlog.get_logger()

# Patterns for extracting structured context from matched lines
_TEST_PATH_RE = re.compile(r"(tests?/\S+?::\S+)")
_FILE_PATH_RE = re.compile(r"(\S+\.py(?:::\S+)?)")
_TODO_CLEAN_RE = re.compile(r"(?i)\b(TODO|FIXME|HACK|XXX)\b[:\s]*(.*)")


class TriggerEngine:
    def __init__(
        self,
        *,
        rules: list[Any] | None = None,
        cooldown_seconds: int = 86400,
        max_tasks_per_run: int = 3,
    ) -> None:
        from hermit.plugins.builtin.hooks.trigger.rules import BUILTIN_RULES

        self._rules = rules or BUILTIN_RULES
        self._cooldown_seconds = cooldown_seconds
        self._max_tasks_per_run = max_tasks_per_run
        self._runner: Any = None

    def set_runner(self, runner: Any) -> None:
        self._runner = runner

    def analyze(
        self,
        result: Any,
        *,
        session_id: str = "",
        task_id: str | None = None,
    ) -> list[TriggerMatch]:
        """Analyze execution result text for trigger patterns."""
        text = _extract_text(result)
        if not text:
            return []
        matches: list[TriggerMatch] = []
        seen_keys: set[str] = set()
        for rule in self._rules:
            if not rule.enabled:
                continue
            for m in re.finditer(rule.match_pattern, text):
                context_line = _extract_context_line(text, m.start(), m.end())
                context = _build_context(rule.source_kind, m.group(0), context_line)
                summary = (
                    _render_template(rule.summary_template, context)
                    if rule.summary_template
                    else context
                )
                goal = rule.suggested_goal_template.replace("{match}", context).replace(
                    "{context}", context
                )
                cooldown_key = rule.cooldown_key_template.replace(
                    "{match}", context[:80].lower()
                ).replace("{context}", context[:80].lower())
                if cooldown_key in seen_keys:
                    continue
                seen_keys.add(cooldown_key)
                matches.append(
                    TriggerMatch(
                        rule=rule,
                        matched_text=summary,
                        evidence_refs=[f"result://{session_id or 'unknown'}/{task_id or 'adhoc'}"],
                        suggested_goal=goal,
                        cooldown_key=cooldown_key,
                    )
                )
        return matches[: self._max_tasks_per_run]

    def analyze_and_dispatch(self, result: Any, *, session_id: str = "", **kwargs: Any) -> None:
        """POST_RUN hook entry: analyze + create follow-up tasks."""
        matches = self.analyze(result, session_id=session_id, task_id=kwargs.get("task_id"))
        if not matches or self._runner is None:
            return
        for match in matches:
            try:
                self._create_followup(match, session_id=session_id)
            except Exception:
                log.exception("trigger_followup_failed", rule=match.rule.name)

    def _create_followup(self, match: TriggerMatch, *, session_id: str) -> str | None:
        """Create a follow-up task via runner's task_controller."""
        if self._runner is None:
            return None
        # Use runner's task_controller to create a governed task
        tc = getattr(self._runner, "task_controller", None)
        if tc is None:
            return None
        # Check cooldown via signal store if available
        store = tc.store
        if (
            hasattr(store, "check_cooldown")
            and match.cooldown_key
            and store.check_cooldown(match.cooldown_key, self._cooldown_seconds)
        ):
            log.info("trigger_cooldown_active", cooldown_key=match.cooldown_key)
            return None
        # Emit signal if signal protocol available
        if hasattr(store, "create_signal"):
            from hermit.kernel.signals.models import EvidenceSignal

            signal = EvidenceSignal(
                source_kind=match.rule.source_kind,
                source_ref=match.evidence_refs[0] if match.evidence_refs else "",
                summary=match.matched_text,
                evidence_refs=match.evidence_refs,
                suggested_goal=match.suggested_goal,
                suggested_policy_profile=match.rule.policy_profile,
                risk_level=match.rule.risk_level,
                cooldown_key=match.cooldown_key,
                cooldown_seconds=self._cooldown_seconds,
            )
            store.create_signal(signal)
        log.info(
            "trigger_followup_created",
            rule=match.rule.name,
            goal=match.suggested_goal,
        )
        return match.suggested_goal


def _extract_context_line(text: str, match_start: int, match_end: int) -> str:
    """Extract the full line containing the regex match for context."""
    line_start = text.rfind("\n", 0, match_start) + 1
    line_end = text.find("\n", match_end)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end].strip()


def _build_context(source_kind: str, raw_match: str, context_line: str) -> str:
    """Build a human-readable context string from the match and its surrounding line."""
    if source_kind == "test_failure":
        return _build_test_failure_context(context_line)
    if source_kind == "todo_scan":
        return _build_todo_context(context_line)
    # Default: use the cleaned-up context line
    cleaned = context_line.strip()
    return cleaned[:120] if cleaned else raw_match[:120]


def _build_test_failure_context(context_line: str) -> str:
    """Extract test name or file path from a test failure line."""
    # Try "FAILED tests/test_foo.py::test_bar" or similar
    test_match = _TEST_PATH_RE.search(context_line)
    if test_match:
        return test_match.group(1)[:120]
    # Try any .py file reference
    file_match = _FILE_PATH_RE.search(context_line)
    if file_match:
        return file_match.group(1)[:120]
    # Fall back to the cleaned line, but strip common noise
    cleaned = context_line.strip()
    # Remove leading markers like "E   " or ">"
    cleaned = re.sub(r"^[E>]\s+", "", cleaned)
    return cleaned[:120] if cleaned else "test failure detected"


def _build_todo_context(context_line: str) -> str:
    """Extract a clean TODO description from a source line."""
    todo_match = _TODO_CLEAN_RE.search(context_line)
    if todo_match:
        tag = todo_match.group(1).upper()
        comment = todo_match.group(2).strip()
        # Strip trailing code artifacts (closing parens, quotes, etc.)
        comment = re.sub(r'["\')}\]]+\s*$', "", comment).strip()
        if comment:
            # Cap at first sentence or 100 chars
            end = _first_sentence_end(comment, max_len=100)
            return f"{tag}: {comment[:end]}"
        return f"{tag} marker"
    return context_line[:100]


def _first_sentence_end(text: str, *, max_len: int = 100) -> int:
    """Find the end of the first sentence within max_len characters."""
    # Look for sentence-ending punctuation
    for i, ch in enumerate(text[:max_len]):
        if ch in ".!?" and i > 10:
            return i + 1
    return min(len(text), max_len)


def _render_template(template: str, context: str) -> str:
    """Render a template with {context} and {match} placeholders."""
    return template.replace("{context}", context).replace("{match}", context)


def _extract_text(result: Any) -> str:
    """Extract searchable text from a run result."""
    if isinstance(result, str):
        return result
    text_parts: list[str] = []
    if hasattr(result, "result_text"):
        text_parts.append(str(result.result_text or ""))
    if hasattr(result, "messages"):
        messages: list[Any] = list(result.messages or [])
        for msg in messages:
            if isinstance(msg, dict):
                text_parts.append(str(cast(dict[str, Any], msg).get("content", "")))
            else:
                text_parts.append(str(getattr(msg, "content", "") or ""))
    if hasattr(result, "tool_outputs"):
        outputs: list[Any] = result.tool_outputs or []
        for out in outputs:
            text_parts.append(str(out))
    if not text_parts and hasattr(result, "__dict__"):
        text_parts.append(str(result.__dict__))
    return "\n".join(text_parts)
