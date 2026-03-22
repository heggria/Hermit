from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class InteractionType(StrEnum):
    handoff = "handoff"
    query = "query"
    escalation = "escalation"
    feedback = "feedback"


@dataclass(frozen=True)
class TaskContractPacket:
    task_id: str
    goal: str
    scope: dict[str, Any] = field(default_factory=lambda: {})
    inputs: list[str] = field(default_factory=lambda: [])
    constraints: list[str] = field(default_factory=lambda: [])
    acceptance_criteria: list[str] = field(default_factory=lambda: [])
    risk_band: str = "medium"
    suggested_plan: list[str] = field(default_factory=lambda: [])
    dependencies: list[str] = field(default_factory=lambda: [])
    expected_artifacts: list[str] = field(default_factory=lambda: [])
    verification_requirements: dict[str, Any] = field(default_factory=lambda: {})


@dataclass(frozen=True)
class CompletionPacket:
    task_id: str
    status: str
    changed_files: list[str] = field(default_factory=lambda: [])
    artifacts: dict[str, str] = field(default_factory=lambda: {})
    known_risks: list[str] = field(default_factory=lambda: [])
    needs_review_focus: list[str] = field(default_factory=lambda: [])


@dataclass(frozen=True)
class VerdictPacket:
    task_id: str
    verdict: str
    acceptance_check: dict[str, bool] = field(default_factory=lambda: {})
    issues: list[dict[str, Any]] = field(default_factory=lambda: [])
    recommended_next_action: str = ""


@dataclass(frozen=True)
class SupervisorQuery:
    query_id: str
    type: str = "query"
    task_id: str = ""
    question: str = ""
    options: list[str] = field(default_factory=lambda: [])
    blocking: bool = False
    from_role: str = ""


@dataclass(frozen=True)
class SupervisorEscalation:
    escalation_id: str
    task_id: str
    reason: str
    severity: str = "medium"
    from_role: str = ""
    context: dict[str, Any] = field(default_factory=lambda: {})


_VALID_VERDICTS: frozenset[str] = frozenset(
    {"accepted", "accepted_with_followups", "rejected", "blocked"}
)

_VALID_SEVERITIES: frozenset[str] = frozenset({"low", "medium", "high", "critical"})

_VALID_RISK_BANDS: frozenset[str] = frozenset({"low", "medium", "high", "critical"})


def create_task_contract(
    *,
    task_id: str,
    goal: str,
    scope: dict[str, Any] | None = None,
    inputs: list[str] | None = None,
    constraints: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    risk_band: str = "medium",
    suggested_plan: list[str] | None = None,
    dependencies: list[str] | None = None,
    expected_artifacts: list[str] | None = None,
    verification_requirements: dict[str, Any] | None = None,
) -> TaskContractPacket:
    if risk_band not in _VALID_RISK_BANDS:
        raise ValueError(
            f"Invalid risk_band: {risk_band!r}; expected one of {sorted(_VALID_RISK_BANDS)}"
        )
    return TaskContractPacket(
        task_id=task_id,
        goal=goal,
        scope=scope or {},
        inputs=inputs or [],
        constraints=constraints or [],
        acceptance_criteria=acceptance_criteria or [],
        risk_band=risk_band,
        suggested_plan=suggested_plan or [],
        dependencies=dependencies or [],
        expected_artifacts=expected_artifacts or [],
        verification_requirements=verification_requirements or {},
    )


def create_completion(
    *,
    task_id: str,
    status: str,
    changed_files: list[str] | None = None,
    artifacts: dict[str, str] | None = None,
    known_risks: list[str] | None = None,
    needs_review_focus: list[str] | None = None,
) -> CompletionPacket:
    return CompletionPacket(
        task_id=task_id,
        status=status,
        changed_files=changed_files or [],
        artifacts=artifacts or {},
        known_risks=known_risks or [],
        needs_review_focus=needs_review_focus or [],
    )


def create_verdict(
    *,
    task_id: str,
    verdict: str,
    acceptance_check: dict[str, bool] | None = None,
    issues: list[dict[str, Any]] | None = None,
    recommended_next_action: str = "",
) -> VerdictPacket:
    if verdict not in _VALID_VERDICTS:
        raise ValueError(f"Invalid verdict: {verdict!r}; expected one of {sorted(_VALID_VERDICTS)}")
    return VerdictPacket(
        task_id=task_id,
        verdict=verdict,
        acceptance_check=acceptance_check or {},
        issues=issues or [],
        recommended_next_action=recommended_next_action,
    )


def create_query(
    *,
    task_id: str,
    question: str,
    options: list[str] | None = None,
    blocking: bool = False,
    from_role: str = "",
) -> SupervisorQuery:
    return SupervisorQuery(
        query_id=f"query_{uuid.uuid4().hex[:12]}",
        task_id=task_id,
        question=question,
        options=options or [],
        blocking=blocking,
        from_role=from_role,
    )


def create_escalation(
    *,
    task_id: str,
    reason: str,
    severity: str = "medium",
    from_role: str = "",
    context: dict[str, Any] | None = None,
) -> SupervisorEscalation:
    if severity not in _VALID_SEVERITIES:
        raise ValueError(
            f"Invalid severity: {severity!r}; expected one of {sorted(_VALID_SEVERITIES)}"
        )
    return SupervisorEscalation(
        escalation_id=f"esc_{uuid.uuid4().hex[:12]}",
        task_id=task_id,
        reason=reason,
        severity=severity,
        from_role=from_role,
        context=context or {},
    )


__all__ = [
    "CompletionPacket",
    "InteractionType",
    "SupervisorEscalation",
    "SupervisorQuery",
    "TaskContractPacket",
    "VerdictPacket",
    "create_completion",
    "create_escalation",
    "create_query",
    "create_task_contract",
    "create_verdict",
]
