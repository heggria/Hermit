from __future__ import annotations

import re
from typing import Any, cast

import structlog

from hermit.plugins.builtin.hooks.trigger.models import TriggerMatch

log = structlog.get_logger()


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
        for rule in self._rules:
            if not rule.enabled:
                continue
            for m in re.finditer(rule.match_pattern, text):
                matched = m.group(0)[:200]  # cap match length
                goal = rule.suggested_goal_template.replace("{match}", matched)
                cooldown_key = rule.cooldown_key_template.replace("{match}", matched[:80])
                matches.append(
                    TriggerMatch(
                        rule=rule,
                        matched_text=matched,
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
