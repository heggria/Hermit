from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class IntentClass(StrEnum):
    new_work = "new_work"
    status_query = "status_query"
    control_command = "control_command"


class ControlAction(StrEnum):
    pause_program = "pause_program"
    resume_team = "resume_team"
    raise_budget = "raise_budget"
    lower_concurrency = "lower_concurrency"
    promote_benchmark = "promote_benchmark"
    escalate_approval = "escalate_approval"


@dataclass(frozen=True)
class IntentResolution:
    intent_class: IntentClass
    target_program_id: str | None = None
    target_team_id: str | None = None
    target_task_id: str | None = None
    target_attempt_id: str | None = None
    raw_input: str = ""
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


_STATUS_KEYWORDS: frozenset[str] = frozenset(
    {
        # English
        "status",
        "progress",
        "report",
        "show",
        "list",
        "how",
        "what",
        "where",
        "check",
        "overview",
        "summary",
        "dashboard",
        "metrics",
        "health",
        # Chinese (zh-CN)
        "状态",
        "进展",
        "进度",
        "报告",
        "查看",
        "显示",
        "列表",
        "概览",
        "摘要",
        "总结",
        "仪表盘",
        "指标",
        "健康",
    }
)

_CONTROL_KEYWORDS: frozenset[str] = frozenset(
    {
        # English
        "pause",
        "resume",
        "stop",
        "cancel",
        "raise",
        "lower",
        "increase",
        "decrease",
        "escalate",
        "budget",
        "concurrency",
        "throttle",
        "halt",
        "restart",
        "scale",
        "promote",
        # Chinese (zh-CN)
        "暂停",
        "恢复",
        "停止",
        "取消",
        "提高",
        "降低",
        "增加",
        "减少",
        "升级",
        "预算",
        "并发",
        "限流",
        "重启",
        "扩容",
        "提升",
    }
)

_TASK_ID_RE = re.compile(r"\btask[_-]([a-z0-9]{6,})\b", re.IGNORECASE)
_PROGRAM_ID_RE = re.compile(r"\bprog(?:ram)?[_-]([a-z0-9]{6,})\b", re.IGNORECASE)
_TEAM_ID_RE = re.compile(r"\bteam[_-]([a-z0-9]{6,})\b", re.IGNORECASE)
_ATTEMPT_ID_RE = re.compile(r"\battempt[_-]([a-z0-9]{6,})\b", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class GovernorService:
    """Rule-based intent classification and resolution for governor-level commands."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def classify_intent(
        self, message: str, context: dict[str, Any] | None = None
    ) -> IntentResolution:
        normalized = self._normalize(message)
        tokens = set(normalized.split())
        ctx = context or {}

        target_task_id = self._extract_id(_TASK_ID_RE, normalized)
        target_program_id = self._extract_id(_PROGRAM_ID_RE, normalized)
        target_team_id = self._extract_id(_TEAM_ID_RE, normalized)
        target_attempt_id = self._extract_id(_ATTEMPT_ID_RE, normalized)

        # Match English tokens via set intersection and Chinese keywords via
        # substring search (Chinese text has no word-boundary whitespace).
        control_hits = (tokens & _CONTROL_KEYWORDS) | self._match_cjk(normalized, _CONTROL_KEYWORDS)
        status_hits = (tokens & _STATUS_KEYWORDS) | self._match_cjk(normalized, _STATUS_KEYWORDS)

        if control_hits:
            return IntentResolution(
                intent_class=IntentClass.control_command,
                target_program_id=target_program_id,
                target_team_id=target_team_id,
                target_task_id=target_task_id,
                target_attempt_id=target_attempt_id,
                raw_input=message,
                confidence=min(1.0, 0.6 + 0.1 * len(control_hits)),
                metadata={
                    "matched_keywords": sorted(control_hits),
                    "source_context": ctx,
                },
            )

        if status_hits:
            return IntentResolution(
                intent_class=IntentClass.status_query,
                target_program_id=target_program_id,
                target_team_id=target_team_id,
                target_task_id=target_task_id,
                target_attempt_id=target_attempt_id,
                raw_input=message,
                confidence=min(1.0, 0.6 + 0.1 * len(status_hits)),
                metadata={
                    "matched_keywords": sorted(status_hits),
                    "source_context": ctx,
                },
            )

        return IntentResolution(
            intent_class=IntentClass.new_work,
            target_program_id=target_program_id,
            target_team_id=target_team_id,
            target_task_id=target_task_id,
            target_attempt_id=target_attempt_id,
            raw_input=message,
            confidence=0.5,
            metadata={"source_context": ctx},
        )

    def resolve_program(
        self,
        hint: str | None = None,
        *,
        session_bound_program_id: str | None = None,
    ) -> str | None:
        """Resolve the target program using the spec's priority strategy.

        Resolution order:
        1. Current session-bound program (highest priority).
        2. Explicit ID extracted from *hint* text.
        3. Most recently active program in the store.
        4. ``None`` — caller should list candidates.
        """
        # 1. Session-bound program — spec: "当前会话绑定 Program" is top priority.
        if session_bound_program_id is not None:
            return session_bound_program_id

        # 2. Explicit ID in hint text.
        if hint is not None:
            match = _PROGRAM_ID_RE.search(hint)
            if match:
                return f"prog_{match.group(1)}"

        # 3. Most recently active program (created_at DESC, active status).
        try:
            active = self.store.list_programs(status="active", limit=1)
            if active:
                return active[0].program_id
        except (AttributeError, TypeError):
            # Store may not implement list_programs (e.g. in unit tests
            # with a bare SimpleNamespace).
            pass

        return None

    def handle_intent(self, resolution: IntentResolution) -> dict[str, Any]:
        handlers: dict[IntentClass, str] = {
            IntentClass.new_work: "create_task",
            IntentClass.status_query: "query_status",
            IntentClass.control_command: "dispatch_control",
        }
        action = handlers.get(resolution.intent_class, "unknown")
        return {
            "action": action,
            "intent_class": str(resolution.intent_class),
            "target_program_id": resolution.target_program_id,
            "target_team_id": resolution.target_team_id,
            "target_task_id": resolution.target_task_id,
            "confidence": resolution.confidence,
            "raw_input": resolution.raw_input,
        }

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(str(text or "").lower().split()).strip()

    @staticmethod
    def _match_cjk(text: str, keywords: frozenset[str]) -> set[str]:
        """Return CJK keywords found as substrings in *text*.

        Chinese text has no whitespace word boundaries, so we do a simple
        substring search for each keyword that contains CJK characters.
        """
        return {kw for kw in keywords if _CJK_RE.search(kw) and kw in text}

    @staticmethod
    def _extract_id(pattern: re.Pattern[str], text: str) -> str | None:
        match = pattern.search(text)
        if match:
            return match.group(0)
        return None


__all__ = [
    "ControlAction",
    "GovernorService",
    "IntentClass",
    "IntentResolution",
]
