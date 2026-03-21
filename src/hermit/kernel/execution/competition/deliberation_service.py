from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import structlog

from hermit.kernel.execution.competition.deliberation import (
    ArbitrationDecision,
    CandidateProposal,
    CritiqueRecord,
    DebateBundle,
    DeliberationTrigger,
    PostExecutionReview,
)

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore

__all__ = ["DeliberationService"]

logger = structlog.get_logger()

# Risk bands that require deliberation regardless of step kind.
_HIGH_RISK_BANDS: frozenset[str] = frozenset({"high", "critical"})

# Step kinds that always warrant deliberation when risk is elevated.
_DELIBERATION_STEP_KINDS: frozenset[str] = frozenset(
    {
        "planning",
        "patch",
        "deploy",
        "rollback",
    }
)


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DeliberationService:
    """Manages deliberation rounds: debate creation, proposals, critiques, and arbitration."""

    def __init__(self, store: KernelStore) -> None:
        self._store = store
        self._debates: dict[str, DebateBundle] = {}

    # -- Query ----------------------------------------------------------------

    def should_deliberate(self, risk_band: str, step_kind: str) -> bool:
        """Return True when the combination of risk and step kind warrants deliberation."""
        if risk_band in _HIGH_RISK_BANDS:
            return True
        return step_kind in _DELIBERATION_STEP_KINDS and risk_band == "medium"

    @staticmethod
    def check_deliberation_needed(*, risk_band: str, step_kind: str) -> bool:
        """Static check for whether deliberation is needed.

        Same logic as :meth:`should_deliberate` but callable without an
        instance — used by the dispatch layer to gate step attempts before
        they enter the thread pool.
        """
        if risk_band in _HIGH_RISK_BANDS:
            return True
        return step_kind in _DELIBERATION_STEP_KINDS and risk_band == "medium"

    # -- Debate lifecycle -----------------------------------------------------

    def create_debate(
        self,
        decision_point: str,
        trigger: DeliberationTrigger,
    ) -> DebateBundle:
        """Open a new debate for the given decision point."""
        debate_id = _gen_id("debate")
        bundle = DebateBundle(
            debate_id=debate_id,
            decision_point=decision_point,
            trigger=trigger,
        )
        self._debates[debate_id] = bundle
        logger.info(
            "deliberation.debate_created",
            debate_id=debate_id,
            decision_point=decision_point,
            trigger=trigger.value,
        )
        return bundle

    def get_debate(self, debate_id: str) -> DebateBundle | None:
        return self._debates.get(debate_id)

    def add_proposal(self, debate_id: str, proposal: CandidateProposal) -> None:
        """Attach a proposal to an existing debate."""
        bundle = self._debates.get(debate_id)
        if bundle is None:
            raise ValueError(f"Debate not found: {debate_id}")
        # Immutable-style: build new list rather than mutating in place.
        bundle.proposals = [*bundle.proposals, proposal]
        logger.info(
            "deliberation.proposal_added",
            debate_id=debate_id,
            candidate_id=proposal.candidate_id,
            proposer_role=proposal.proposer_role,
        )

    def add_critique(self, debate_id: str, critique: CritiqueRecord) -> None:
        """Attach a critique to an existing debate."""
        bundle = self._debates.get(debate_id)
        if bundle is None:
            raise ValueError(f"Debate not found: {debate_id}")
        bundle.critiques = [*bundle.critiques, critique]
        logger.info(
            "deliberation.critique_added",
            debate_id=debate_id,
            critique_id=critique.critique_id,
            target_candidate_id=critique.target_candidate_id,
            severity=critique.severity,
        )

    def add_post_execution_review(
        self,
        debate_id: str,
        review: PostExecutionReview,
    ) -> None:
        """Attach a post-execution adversarial review to a debate."""
        bundle = self._debates.get(debate_id)
        if bundle is None:
            raise ValueError(f"Debate not found: {debate_id}")
        bundle.post_execution_reviews = [*bundle.post_execution_reviews, review]
        logger.info(
            "deliberation.post_execution_review_added",
            debate_id=debate_id,
            review_id=review.review_id,
            challenge_type=review.challenge_type,
            severity=review.severity,
        )

    # -- Arbitration ----------------------------------------------------------

    def arbitrate(self, debate_id: str) -> ArbitrationDecision:
        """Select the best proposal that has no unresolved critical critiques.

        Strategy:
        1. Disqualify any candidate with at least one ``critical`` critique.
        2. Among remaining candidates, pick the first proposal (stable ordering).
        3. If no candidates survive, return an escalation decision.
        """
        bundle = self._debates.get(debate_id)
        if bundle is None:
            raise ValueError(f"Debate not found: {debate_id}")

        # Collect candidate ids with critical critiques.
        critically_critiqued: set[str] = set()
        for critique in bundle.critiques:
            if critique.severity == "critical":
                critically_critiqued.add(critique.target_candidate_id)

        # Filter proposals to those without critical critiques.
        eligible = [p for p in bundle.proposals if p.candidate_id not in critically_critiqued]

        rejection_reasons: list[str] = []
        for cid in critically_critiqued:
            rejection_reasons.append(f"candidate {cid} has critical critique(s)")

        now = time.time()

        if not eligible:
            decision = ArbitrationDecision(
                decision_id=_gen_id("arb"),
                debate_id=debate_id,
                selected_candidate_id=None,
                rejection_reasons=rejection_reasons,
                merge_notes="",
                confidence=0.0,
                escalation_required=True,
                decided_at=now,
            )
            logger.warning(
                "deliberation.arbitration_escalated",
                debate_id=debate_id,
                reason="no_eligible_candidates",
            )
            return decision

        winner = eligible[0]

        # Confidence based on critique coverage: fewer critiques = higher confidence.
        total_critiques = len(bundle.critiques)
        winner_critiques = sum(
            1 for c in bundle.critiques if c.target_candidate_id == winner.candidate_id
        )
        if total_critiques == 0:
            confidence = 1.0
        else:
            confidence = max(0.0, 1.0 - winner_critiques / total_critiques)

        decision = ArbitrationDecision(
            decision_id=_gen_id("arb"),
            debate_id=debate_id,
            selected_candidate_id=winner.candidate_id,
            rejection_reasons=rejection_reasons,
            merge_notes=f"Selected {winner.proposer_role} proposal for {winner.target_scope}",
            confidence=confidence,
            escalation_required=False,
            decided_at=now,
        )
        logger.info(
            "deliberation.arbitration_decided",
            debate_id=debate_id,
            selected=winner.candidate_id,
            confidence=confidence,
        )
        return decision
