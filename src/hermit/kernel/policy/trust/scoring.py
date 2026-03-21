from __future__ import annotations

import time

import structlog

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.trust.models import RiskAdjustment, TrustScore

logger = structlog.get_logger()

_MIN_EXECUTIONS = 5

_RISK_BANDS = ("low", "medium", "high", "critical")


class TrustScorer:
    """Computes trust scores from historical kernel execution data.

    Score formula:
      composite = 0.5 * success_rate + 0.3 * (1 - rollback_rate) + 0.2 * avg_recon_confidence

    Requires at least ``_MIN_EXECUTIONS`` receipts before producing a score;
    returns ``None`` otherwise.
    """

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    def score_action_class(
        self,
        action_class: str,
        *,
        task_id: str | None = None,
        limit: int = 500,
    ) -> TrustScore | None:
        """Compute a trust score for a given action class.

        If *task_id* is supplied the score is scoped to that task; otherwise it
        spans all tasks in the store.
        """
        receipts = self._store.list_receipts(task_id=task_id, limit=limit)
        relevant = [r for r in receipts if r.action_type == action_class]

        if len(relevant) < _MIN_EXECUTIONS:
            return None

        total = len(relevant)
        successful = sum(1 for r in relevant if r.result_code == "succeeded")
        rolled_back = sum(1 for r in relevant if r.rollback_status in ("completed", "rolled_back"))

        success_rate = successful / total
        rollback_rate = rolled_back / total

        reconciliations = self._store.list_reconciliations(task_id=task_id, limit=limit)
        if reconciliations:
            total_confidence = sum(
                max(0.0, min(1.0, 0.5 + r.confidence_delta)) for r in reconciliations
            )
            avg_recon_confidence = total_confidence / len(reconciliations)
        else:
            avg_recon_confidence = 0.5  # neutral default when no reconciliations exist

        composite = 0.5 * success_rate + 0.3 * (1 - rollback_rate) + 0.2 * avg_recon_confidence

        return TrustScore(
            subject_kind="action_class",
            subject_ref=action_class,
            total_executions=total,
            successful_executions=successful,
            rolled_back_executions=rolled_back,
            reconciliation_count=len(reconciliations),
            avg_reconciliation_confidence=avg_recon_confidence,
            composite_score=round(composite, 4),
            computed_at=time.time(),
        )

    def suggest_risk_adjustment(
        self,
        action_class: str,
        current_risk_band: str,
        *,
        task_id: str | None = None,
    ) -> RiskAdjustment | None:
        """Return an advisory risk adjustment if the trust score warrants one.

        Returns ``None`` when there is insufficient data or the current band
        already matches the suggested band.
        """
        score = self.score_action_class(action_class, task_id=task_id)
        if score is None:
            return None

        suggested = self._band_for_score(score.composite_score)
        if suggested == current_risk_band:
            return None

        return RiskAdjustment(
            subject_kind="action_class",
            subject_ref=action_class,
            current_risk_band=current_risk_band,
            suggested_risk_band=suggested,
            reason=self._build_reason(score, current_risk_band, suggested),
            trust_score_ref=score.composite_score,
        )

    def log_adjustment_decision(
        self,
        adjustment: RiskAdjustment,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
    ) -> str:
        """Log the advisory adjustment as a decision event (never auto-applied)."""
        return self._store.append_event(
            event_type="trust.risk_adjustment_suggested",
            entity_type="action_class",
            entity_id=adjustment.subject_ref,
            task_id=task_id,
            step_id=step_id,
            actor="trust_scorer",
            payload={
                "subject_kind": adjustment.subject_kind,
                "subject_ref": adjustment.subject_ref,
                "current_risk_band": adjustment.current_risk_band,
                "suggested_risk_band": adjustment.suggested_risk_band,
                "reason": adjustment.reason,
                "trust_score_ref": adjustment.trust_score_ref,
                "advisory_only": True,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _band_for_score(composite: float) -> str:
        if composite >= 0.85:
            return "low"
        if composite >= 0.65:
            return "medium"
        if composite >= 0.40:
            return "high"
        return "critical"

    @staticmethod
    def _build_reason(score: TrustScore, current: str, suggested: str) -> str:
        return (
            f"Trust score {score.composite_score:.4f} "
            f"({score.successful_executions}/{score.total_executions} succeeded, "
            f"{score.rolled_back_executions} rolled back) "
            f"suggests moving from '{current}' to '{suggested}'"
        )
