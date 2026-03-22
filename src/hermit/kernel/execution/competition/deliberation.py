from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "ArbitrationDecision",
    "CandidateProposal",
    "CritiqueRecord",
    "DebateBundle",
    "DeliberationTrigger",
    "PostExecutionReview",
]


class DeliberationTrigger(StrEnum):
    """Conditions that activate a formal deliberation round."""

    high_risk_planning = "high_risk_planning"
    high_risk_patch = "high_risk_patch"
    ambiguous_spec = "ambiguous_spec"
    follow_up_decision = "follow_up_decision"
    benchmark_dispute = "benchmark_dispute"
    post_execution_review = "post_execution_review"
    review_council = "review_council"


@dataclass
class CandidateProposal:
    """A structured proposal submitted by a role during deliberation."""

    candidate_id: str
    proposer_role: str
    target_scope: str
    plan_summary: str
    contract_draft: dict[str, Any] = field(default_factory=lambda: {})
    expected_cost: str = ""
    expected_risk: str = ""
    expected_reward: str = ""
    created_at: float = 0.0


@dataclass
class CritiqueRecord:
    """A critique raised against a specific candidate proposal."""

    critique_id: str
    target_candidate_id: str
    critic_role: str
    issue_type: str
    severity: str
    evidence_refs: list[str] = field(default_factory=lambda: [])
    suggested_fix: str = ""
    created_at: float = 0.0


@dataclass
class DebateBundle:
    """Collects proposals, critiques, and arbitration input for a decision point."""

    debate_id: str
    decision_point: str
    trigger: DeliberationTrigger
    proposals: list[CandidateProposal] = field(default_factory=lambda: [])
    critiques: list[CritiqueRecord] = field(default_factory=lambda: [])
    post_execution_reviews: list[PostExecutionReview] = field(default_factory=lambda: [])
    arbitration_input: dict[str, Any] = field(default_factory=lambda: {})


@dataclass
class ArbitrationDecision:
    """The final ruling produced by the arbitration step."""

    decision_id: str
    debate_id: str
    selected_candidate_id: str | None = None
    rejection_reasons: list[str] = field(default_factory=lambda: [])
    merge_notes: str = ""
    confidence: float = 0.0
    escalation_required: bool = False
    decided_at: float = 0.0


@dataclass
class PostExecutionReview:
    """An adversarial review raised after execution completes.

    Used in the Verification / Reconciliation stage to challenge whether
    execution results truly satisfy the spec, benchmarks, or risk constraints.
    """

    review_id: str
    debate_id: str
    task_id: str
    reviewer_role: str
    challenge_type: str  # e.g. "spec_compliance", "benchmark_interpretation", "risk_assessment"
    finding: str
    severity: str  # "low", "medium", "high", "critical"
    evidence_refs: list[str] = field(default_factory=lambda: [])
    recommendation: str = ""  # e.g. "reject", "accept_with_followups", "re_execute"
    created_at: float = 0.0
