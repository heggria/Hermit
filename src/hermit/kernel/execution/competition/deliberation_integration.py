from __future__ import annotations

import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import structlog

from hermit.kernel.execution.competition.deliberation import (
    ArbitrationDecision,
    CandidateProposal,
    CritiqueRecord,
    DeliberationTrigger,
    PostExecutionReview,
)
from hermit.kernel.execution.competition.deliberation_service import (
    DeliberationService,
)
from hermit.kernel.execution.controller.supervisor_protocol import (
    TaskContractPacket,
    create_task_contract,
)

if TYPE_CHECKING:
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.ledger.journal.store import KernelStore

__all__ = ["DeliberationIntegration"]

logger = structlog.get_logger()

# Risk bands that always trigger deliberation.
_HIGH_RISK_BANDS: frozenset[str] = frozenset({"high", "critical"})

# Step kinds that trigger deliberation at medium risk.
_MEDIUM_RISK_STEP_KINDS: frozenset[str] = frozenset(
    {
        "planning",
        "patch",
        "deploy",
        "rollback",
    }
)

# Maps risk band + step kind combination to a deliberation trigger.
_TRIGGER_MAP: dict[str, DeliberationTrigger] = {
    "planning": DeliberationTrigger.high_risk_planning,
    "patch": DeliberationTrigger.high_risk_patch,
    "deploy": DeliberationTrigger.high_risk_planning,
    "rollback": DeliberationTrigger.high_risk_planning,
}


class DeliberationIntegration:
    """Integrates deliberation into the governed execution flow.

    Sits between Planning and Execution phases. When a task contract
    is high-risk, this service:
    1. Triggers deliberation (creates DebateBundle)
    2. Stores all proposals/critiques as artifacts
    3. Produces ArbitrationDecision
    4. Converts winning proposal into formal TaskContractPacket

    Low-risk tasks bypass deliberation entirely.
    """

    def __init__(self, store: KernelStore, artifact_store: ArtifactStore) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.deliberation = DeliberationService(store)

    # -- Routing --------------------------------------------------------------

    def evaluate_and_route(
        self,
        *,
        task_id: str,
        step_id: str,
        risk_band: str,
        step_kind: str,
    ) -> dict[str, Any]:
        """Decide if deliberation is needed and route accordingly.

        Returns ``{"deliberation_required": bool, "debate_id": str | None}``.
        When deliberation is required a new debate is opened, and an event is
        appended to the ledger so the decision is auditable.
        """
        required = self.deliberation.should_deliberate(risk_band, step_kind)

        if not required:
            logger.debug(
                "deliberation_integration.bypass",
                task_id=task_id,
                step_id=step_id,
                risk_band=risk_band,
                step_kind=step_kind,
            )
            return {"deliberation_required": False, "debate_id": None}

        trigger = _TRIGGER_MAP.get(step_kind, DeliberationTrigger.high_risk_planning)
        decision_point = f"task={task_id} step={step_id} risk={risk_band} kind={step_kind}"
        bundle = self.deliberation.create_debate(decision_point, trigger)

        self.store.append_event(
            event_type="deliberation.routed",
            entity_type="deliberation",
            entity_id=bundle.debate_id,
            task_id=task_id,
            step_id=step_id,
            payload={
                "risk_band": risk_band,
                "step_kind": step_kind,
                "trigger": trigger.value,
            },
        )

        logger.info(
            "deliberation_integration.routed",
            task_id=task_id,
            step_id=step_id,
            debate_id=bundle.debate_id,
            trigger=trigger.value,
        )

        return {"deliberation_required": True, "debate_id": bundle.debate_id}

    # -- Proposals ------------------------------------------------------------

    def submit_proposal(
        self,
        *,
        debate_id: str,
        proposer_role: str,
        plan_summary: str,
        contract_draft: dict[str, Any],
        expected_cost: str,
        expected_risk: str,
        expected_reward: str = "",
    ) -> str:
        """Submit a candidate proposal. Returns ``candidate_id``.

        The proposal is stored as an artifact so it remains part of the
        evidence spine regardless of the debate outcome.
        """
        candidate_id = self.store.generate_id("dlb_cand")
        now = time.time()

        proposal = CandidateProposal(
            candidate_id=candidate_id,
            proposer_role=proposer_role,
            target_scope=debate_id,
            plan_summary=plan_summary,
            contract_draft=dict(contract_draft),
            expected_cost=expected_cost,
            expected_risk=expected_risk,
            expected_reward=expected_reward,
            created_at=now,
        )

        self.deliberation.add_proposal(debate_id, proposal)

        # Persist proposal as artifact.
        artifact_payload = {
            "artifact_type": "deliberation_proposal",
            "debate_id": debate_id,
            **asdict(proposal),
        }
        artifact_ref, _hash = self.artifact_store.store_json(artifact_payload)

        self.store.append_event(
            event_type="deliberation.proposal_submitted",
            entity_type="deliberation",
            entity_id=debate_id,
            task_id=None,
            payload={
                "candidate_id": candidate_id,
                "proposer_role": proposer_role,
                "artifact_ref": artifact_ref,
            },
        )

        logger.info(
            "deliberation_integration.proposal_submitted",
            debate_id=debate_id,
            candidate_id=candidate_id,
            proposer_role=proposer_role,
            artifact_ref=artifact_ref,
        )

        return candidate_id

    # -- Critiques ------------------------------------------------------------

    def submit_critique(
        self,
        *,
        debate_id: str,
        target_candidate_id: str,
        critic_role: str,
        issue_type: str,
        severity: str,
        evidence_refs: list[str] | None = None,
        suggested_fix: str = "",
    ) -> str:
        """Submit a critique against a candidate. Returns ``critique_id``.

        The critique is stored as an artifact for the audit trail.
        """
        critique_id = self.store.generate_id("dlb_crit")
        now = time.time()

        critique = CritiqueRecord(
            critique_id=critique_id,
            target_candidate_id=target_candidate_id,
            critic_role=critic_role,
            issue_type=issue_type,
            severity=severity,
            evidence_refs=list(evidence_refs) if evidence_refs else [],
            suggested_fix=suggested_fix,
            created_at=now,
        )

        self.deliberation.add_critique(debate_id, critique)

        # Persist critique as artifact.
        artifact_payload = {
            "artifact_type": "deliberation_critique",
            "debate_id": debate_id,
            **asdict(critique),
        }
        artifact_ref, _hash = self.artifact_store.store_json(artifact_payload)

        self.store.append_event(
            event_type="deliberation.critique_submitted",
            entity_type="deliberation",
            entity_id=debate_id,
            task_id=None,
            payload={
                "critique_id": critique_id,
                "target_candidate_id": target_candidate_id,
                "severity": severity,
                "artifact_ref": artifact_ref,
            },
        )

        logger.info(
            "deliberation_integration.critique_submitted",
            debate_id=debate_id,
            critique_id=critique_id,
            target_candidate_id=target_candidate_id,
            severity=severity,
            artifact_ref=artifact_ref,
        )

        return critique_id

    # -- Resolution -----------------------------------------------------------

    def resolve_debate(self, debate_id: str) -> dict[str, Any]:
        """Arbitrate and produce final decision.

        Returns the ``ArbitrationDecision`` as a dict.  The full debate
        bundle (proposals + critiques + post-execution reviews + decision)
        is stored as a single composite artifact so the entire deliberation
        history is immutably captured.
        """
        decision: ArbitrationDecision = self.deliberation.arbitrate(debate_id)

        bundle = self.deliberation.get_debate(debate_id)
        if bundle is None:
            raise ValueError(f"Debate not found after arbitration: {debate_id}")

        # Build composite artifact containing the full debate record.
        bundle_payload = {
            "artifact_type": "deliberation_bundle",
            "debate_id": debate_id,
            "decision_point": bundle.decision_point,
            "trigger": bundle.trigger.value,
            "proposals": [asdict(p) for p in bundle.proposals],
            "critiques": [asdict(c) for c in bundle.critiques],
            "post_execution_reviews": [asdict(r) for r in bundle.post_execution_reviews],
            "decision": asdict(decision),
        }
        bundle_ref, _hash = self.artifact_store.store_json(bundle_payload)

        # Also persist the decision alone for quick lookups.
        decision_payload = {
            "artifact_type": "arbitration_decision",
            "debate_id": debate_id,
            **asdict(decision),
        }
        decision_ref, _decision_hash = self.artifact_store.store_json(decision_payload)

        self.store.append_event(
            event_type="deliberation.resolved",
            entity_type="deliberation",
            entity_id=debate_id,
            task_id=None,
            payload={
                "decision_id": decision.decision_id,
                "selected_candidate_id": decision.selected_candidate_id,
                "escalation_required": decision.escalation_required,
                "confidence": decision.confidence,
                "bundle_artifact_ref": bundle_ref,
                "decision_artifact_ref": decision_ref,
            },
        )

        logger.info(
            "deliberation_integration.resolved",
            debate_id=debate_id,
            decision_id=decision.decision_id,
            selected=decision.selected_candidate_id,
            escalation=decision.escalation_required,
            confidence=decision.confidence,
            bundle_ref=bundle_ref,
            decision_ref=decision_ref,
        )

        return asdict(decision)

    # -- Contract conversion ---------------------------------------------------

    def to_contract_packet(
        self,
        *,
        debate_id: str,
        task_id: str,
    ) -> TaskContractPacket:
        """Convert the winning proposal into a formal ``TaskContractPacket``.

        This is the critical boundary: only the winner of deliberation
        becomes an admitted contract that enters the execution plane.
        The decision must have already been resolved (via ``resolve_debate``).
        """
        bundle = self.deliberation.get_debate(debate_id)
        if bundle is None:
            raise ValueError(f"Debate not found: {debate_id}")

        # Find the resolved decision by looking at the arbitration output.
        decision = self.deliberation.arbitrate(debate_id)
        if decision.escalation_required or decision.selected_candidate_id is None:
            raise ValueError(f"Cannot convert to contract: debate {debate_id} requires escalation")

        # Find the winning proposal.
        winning_proposals = [
            p for p in bundle.proposals if p.candidate_id == decision.selected_candidate_id
        ]
        if not winning_proposals:
            raise ValueError(
                f"Winning candidate {decision.selected_candidate_id} not found "
                f"in debate {debate_id}"
            )
        winner = winning_proposals[0]

        contract = create_task_contract(
            task_id=task_id,
            goal=winner.plan_summary,
            scope=winner.contract_draft,
            risk_band=winner.expected_risk if winner.expected_risk else "medium",
        )

        self.store.append_event(
            event_type="deliberation.contract_emitted",
            entity_type="deliberation",
            entity_id=debate_id,
            task_id=task_id,
            payload={
                "selected_candidate_id": decision.selected_candidate_id,
                "contract_task_id": task_id,
                "goal": winner.plan_summary,
            },
        )

        logger.info(
            "deliberation_integration.contract_emitted",
            debate_id=debate_id,
            task_id=task_id,
            selected_candidate_id=decision.selected_candidate_id,
        )

        return contract

    # -- Executor feasibility --------------------------------------------------

    def submit_executor_feasibility(
        self,
        *,
        debate_id: str,
        target_candidate_id: str,
        executor_role: str = "executor",
        feasibility_assessment: str = "",
        workspace_conflicts: list[str] | None = None,
        tool_chain_issues: list[str] | None = None,
        estimated_cost: str = "",
        is_feasible: bool = True,
    ) -> str:
        """Submit executor feasibility feedback as a structured critique.

        Per spec: "执行面可以产出局部反例、执行反馈、可行性证据".
        Executors participate by providing feasibility assessments without
        owning the competition mechanism itself.
        """
        issue_type = "feasibility"
        severity = "low" if is_feasible else "high"

        evidence: list[str] = []
        if workspace_conflicts:
            evidence.extend(f"workspace_conflict:{c}" for c in workspace_conflicts)
        if tool_chain_issues:
            evidence.extend(f"tool_chain_issue:{i}" for i in tool_chain_issues)

        suggested_fix = feasibility_assessment
        if estimated_cost:
            suggested_fix = f"{suggested_fix} [estimated_cost={estimated_cost}]"

        return self.submit_critique(
            debate_id=debate_id,
            target_candidate_id=target_candidate_id,
            critic_role=executor_role,
            issue_type=issue_type,
            severity=severity,
            evidence_refs=evidence if evidence else None,
            suggested_fix=suggested_fix.strip(),
        )

    # -- Post-execution adversarial review ------------------------------------

    def open_post_execution_review(
        self,
        *,
        task_id: str,
        decision_point: str,
    ) -> str:
        """Open a new debate for post-execution adversarial review.

        Per spec "放法 B: Verification 内部的 adversarial review":
        after execution completes, reviewers can challenge whether the
        result truly satisfies the spec, benchmarks, or risk constraints.

        Returns the debate_id of the newly created review debate.
        """
        trigger = DeliberationTrigger.post_execution_review
        bundle = self.deliberation.create_debate(decision_point, trigger)

        self.store.append_event(
            event_type="deliberation.post_execution_review_opened",
            entity_type="deliberation",
            entity_id=bundle.debate_id,
            task_id=task_id,
            payload={
                "decision_point": decision_point,
                "trigger": trigger.value,
            },
        )

        logger.info(
            "deliberation_integration.post_execution_review_opened",
            debate_id=bundle.debate_id,
            task_id=task_id,
            decision_point=decision_point,
        )

        return bundle.debate_id

    def submit_post_execution_review(
        self,
        *,
        debate_id: str,
        task_id: str,
        reviewer_role: str,
        challenge_type: str,
        finding: str,
        severity: str,
        evidence_refs: list[str] | None = None,
        recommendation: str = "",
    ) -> str:
        """Submit an adversarial review of completed execution results.

        Per spec: reviewers challenge whether the patch satisfies the spec,
        benchmarks are correctly interpreted, or risk judgments are sound.
        The review is stored as an artifact for the audit trail.

        Returns ``review_id``.
        """
        review_id = self.store.generate_id("dlb_review")
        now = time.time()

        review = PostExecutionReview(
            review_id=review_id,
            debate_id=debate_id,
            task_id=task_id,
            reviewer_role=reviewer_role,
            challenge_type=challenge_type,
            finding=finding,
            severity=severity,
            evidence_refs=list(evidence_refs) if evidence_refs else [],
            recommendation=recommendation,
            created_at=now,
        )

        self.deliberation.add_post_execution_review(debate_id, review)

        # Persist review as artifact.
        artifact_payload = {
            "artifact_type": "post_execution_review",
            "debate_id": debate_id,
            "task_id": task_id,
            **asdict(review),
        }
        artifact_ref, _hash = self.artifact_store.store_json(artifact_payload)

        self.store.append_event(
            event_type="deliberation.post_execution_review_submitted",
            entity_type="deliberation",
            entity_id=debate_id,
            task_id=task_id,
            payload={
                "review_id": review_id,
                "reviewer_role": reviewer_role,
                "challenge_type": challenge_type,
                "severity": severity,
                "recommendation": recommendation,
                "artifact_ref": artifact_ref,
            },
        )

        logger.info(
            "deliberation_integration.post_execution_review_submitted",
            debate_id=debate_id,
            review_id=review_id,
            reviewer_role=reviewer_role,
            challenge_type=challenge_type,
            severity=severity,
            artifact_ref=artifact_ref,
        )

        return review_id

    # -- Query ----------------------------------------------------------------

    def get_debate_summary(self, debate_id: str) -> dict[str, Any]:
        """Get human-readable debate summary for status queries."""
        bundle = self.deliberation.get_debate(debate_id)
        if bundle is None:
            return {"error": f"Debate not found: {debate_id}"}

        proposals_summary = [
            {
                "candidate_id": p.candidate_id,
                "proposer_role": p.proposer_role,
                "plan_summary": p.plan_summary,
                "expected_cost": p.expected_cost,
                "expected_risk": p.expected_risk,
                "expected_reward": p.expected_reward,
            }
            for p in bundle.proposals
        ]

        critiques_summary = [
            {
                "critique_id": c.critique_id,
                "target_candidate_id": c.target_candidate_id,
                "critic_role": c.critic_role,
                "issue_type": c.issue_type,
                "severity": c.severity,
            }
            for c in bundle.critiques
        ]

        reviews_summary = [
            {
                "review_id": r.review_id,
                "reviewer_role": r.reviewer_role,
                "challenge_type": r.challenge_type,
                "severity": r.severity,
                "recommendation": r.recommendation,
            }
            for r in bundle.post_execution_reviews
        ]

        critical_count = sum(1 for c in bundle.critiques if c.severity == "critical")
        critical_review_count = sum(
            1 for r in bundle.post_execution_reviews if r.severity == "critical"
        )

        return {
            "debate_id": debate_id,
            "decision_point": bundle.decision_point,
            "trigger": bundle.trigger.value,
            "proposal_count": len(bundle.proposals),
            "critique_count": len(bundle.critiques),
            "critical_critique_count": critical_count,
            "post_execution_review_count": len(bundle.post_execution_reviews),
            "critical_review_count": critical_review_count,
            "proposals": proposals_summary,
            "critiques": critiques_summary,
            "post_execution_reviews": reviews_summary,
        }
