from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrustScore:
    """Computed trust score for a principal or action class.

    The composite score is derived from:
      0.5 * success_rate + 0.3 * (1 - rollback_rate) + 0.2 * avg_reconciliation_confidence
    """

    subject_kind: str  # e.g. "action_class", "principal"
    subject_ref: str  # e.g. "write_local", "principal_operator"
    total_executions: int
    successful_executions: int
    rolled_back_executions: int
    reconciliation_count: int
    avg_reconciliation_confidence: float
    composite_score: float
    computed_at: float


@dataclass
class RiskAdjustment:
    """Advisory risk adjustment derived from a TrustScore.

    This record is logged as a decision event but never auto-applied
    to policy evaluation.
    """

    subject_kind: str
    subject_ref: str
    current_risk_band: str
    suggested_risk_band: str
    reason: str
    trust_score_ref: float  # composite_score at time of adjustment
    evidence_refs: list[str] = field(default_factory=list)
