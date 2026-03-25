from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrustScore:
    """Computed trust score for a principal or action class.

    The composite score is derived from:
      0.5 * success_rate + 0.3 * (1 - rollback_rate) + 0.2 * avg_reconciliation_confidence

    Attributes:
        subject_kind: Category of the scored subject, e.g. "action_class" or "principal".
        subject_ref: Identifier of the scored subject, e.g. "write_local".
        total_executions: Total number of executions observed.
        successful_executions: Number of executions that completed without error.
        rolled_back_executions: Number of executions that were rolled back.
        reconciliation_count: Number of reconciliation events observed.
        avg_reconciliation_confidence: Mean confidence score from reconciliation events (0–1).
        composite_score: Weighted trust score in the range [0.0, 1.0].
        computed_at: Unix timestamp (seconds) when this score was computed.
    """

    subject_kind: str
    subject_ref: str
    total_executions: int
    successful_executions: int
    rolled_back_executions: int
    reconciliation_count: int
    avg_reconciliation_confidence: float
    composite_score: float
    computed_at: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.composite_score <= 1.0):
            raise ValueError(f"composite_score must be in [0.0, 1.0], got {self.composite_score!r}")


@dataclass
class RiskAdjustment:
    """Advisory risk adjustment derived from a TrustScore.

    This record is logged as a decision event but never auto-applied
    to policy evaluation.

    Attributes:
        subject_kind: Category of the subject being adjusted.
        subject_ref: Identifier of the subject being adjusted.
        current_risk_band: The subject's current risk classification.
        suggested_risk_band: The recommended risk classification after adjustment.
        reason: Human-readable explanation for the adjustment.
        composite_score_snapshot: The composite_score value from the TrustScore that
            triggered this adjustment, captured at the time the adjustment was created.
        evidence_refs: Optional list of artifact or event references that support
            this adjustment.
    """

    subject_kind: str
    subject_ref: str
    current_risk_band: str
    suggested_risk_band: str
    reason: str
    # Renamed from `trust_score_ref` — this is a scalar snapshot, not a reference.
    composite_score_snapshot: float
    # Fixed: was `field(default_factory=list[str])` which passes a generic alias
    # (not a callable) as the factory, causing a TypeError on Python < 3.9 and
    # producing unexpected behaviour on later versions. Correct factory is `list`.
    evidence_refs: list[str] = field(default_factory=list)
