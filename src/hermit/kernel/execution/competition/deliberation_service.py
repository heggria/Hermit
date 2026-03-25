from __future__ import annotations

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
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.execution.competition.llm_arbitrator import ArbitrationEngine
    from hermit.kernel.execution.workers.pool import WorkerPoolManager
    from hermit.kernel.ledger.journal.store import KernelStore

__all__ = ["DeliberationService"]

logger = structlog.get_logger()

# -- Deliberation trigger policy based on ActionClass -------------------------
#
# Read-only actions never need deliberation regardless of risk_level.
_READONLY_ACTIONS: frozenset[str] = frozenset(
    {
        "read_local",
        "network_read",
        "execute_command_readonly",
        "delegate_reasoning",
        "ephemeral_ui_mutation",
    }
)

# Mutation actions that warrant deliberation at medium risk.
_MEDIUM_RISK_DELIBERATION_ACTIONS: frozenset[str] = frozenset(
    {
        "write_local",
        "patch_file",
        "execute_command",
        "network_write",
        "external_mutation",
        "vcs_mutation",
        "publication",
        "rollback",
        "scheduler_mutation",
    }
)

# Mutation actions that warrant deliberation at high/critical risk.
# (superset of medium — includes orchestration and governance mutations)
_HIGH_RISK_DELIBERATION_ACTIONS: frozenset[str] = _MEDIUM_RISK_DELIBERATION_ACTIONS | frozenset(
    {
        "delegate_execution",
        "approval_resolution",
        "credentialed_api_call",
        "memory_write",
        "attachment_ingest",
        "patrol_execution",
    }
)


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DeliberationService:
    """Manages deliberation rounds: debate creation, proposals, critiques, and arbitration."""

    def __init__(self, store: KernelStore, *, arbitrator: ArbitrationEngine) -> None:
        self._store = store
        self._arbitrator = arbitrator
        self._debates: dict[str, DebateBundle] = {}

    # -- Query ----------------------------------------------------------------

    def should_deliberate(self, *, risk_level: str, action_class: str) -> bool:
        """Return True when the combination of risk and action class warrants deliberation.

        Policy:
        - Read-only actions (read_local, network_read, execute_command_readonly,
          delegate_reasoning, ephemeral_ui_mutation) never trigger deliberation.
        - high/critical risk + mutation action → deliberate.
        - medium risk + high-impact mutation (write, patch, execute, deploy, rollback, etc.) → deliberate.
        - low risk → never deliberate.
        """
        if action_class in _READONLY_ACTIONS:
            return False
        if risk_level in ("high", "critical"):
            return action_class in _HIGH_RISK_DELIBERATION_ACTIONS
        if risk_level == "medium":
            return action_class in _MEDIUM_RISK_DELIBERATION_ACTIONS
        return False

    @staticmethod
    def check_deliberation_needed(*, risk_level: str, action_class: str) -> bool:
        """Static check for whether deliberation is needed.

        Same logic as :meth:`should_deliberate` but callable without an
        instance — used by the dispatch layer to gate step attempts before
        they enter the thread pool.
        """
        if action_class in _READONLY_ACTIONS:
            return False
        if risk_level in ("high", "critical"):
            return action_class in _HIGH_RISK_DELIBERATION_ACTIONS
        if risk_level == "medium":
            return action_class in _MEDIUM_RISK_DELIBERATION_ACTIONS
        return False

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

    def arbitrate(
        self,
        debate_id: str,
        *,
        task_id: str,
        pool: WorkerPoolManager,
        store: KernelStore,
        artifact_store: ArtifactStore,
    ) -> ArbitrationDecision:
        """Delegate arbitration to the LLM-driven ArbitrationEngine.

        The engine claims a verifier slot from the pool, creates a step for
        audit, applies hard constraints, then uses LLM reasoning.
        """
        bundle = self._debates.get(debate_id)
        if bundle is None:
            raise ValueError(f"Debate not found: {debate_id}")

        decision = self._arbitrator.arbitrate(
            bundle,
            task_id=task_id,
            pool=pool,
            store=store,
            artifact_store=artifact_store,
        )

        logger.info(
            "deliberation.arbitration_decided",
            debate_id=debate_id,
            selected=decision.selected_candidate_id,
            confidence=decision.confidence,
            escalation=decision.escalation_required,
        )
        return decision
