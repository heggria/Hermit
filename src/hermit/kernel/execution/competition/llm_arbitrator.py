"""LLM-driven arbitration engine using kernel WorkerPool for admission control.

The engine claims a ``WorkerRole.verifier`` slot and creates a kernel Step
for governance audit.  Hard constraints (critical critiques) are applied
first, then LLM reasoning selects from surviving candidates.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import structlog

from hermit.kernel.execution.competition.deliberation import (
    ArbitrationDecision,
    DebateBundle,
)
from hermit.kernel.execution.workers.models import WorkerRole

if TYPE_CHECKING:
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.execution.workers.pool import WorkerPoolManager
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.provider_host.shared.contracts import Provider

__all__ = ["ArbitrationEngine"]

log = structlog.get_logger()

_DEFAULT_MAX_TOKENS = 4096


def _parse_json_response(raw_text: str) -> dict[str, Any]:
    """Extract the first JSON object from an LLM response."""
    text = raw_text.strip()
    if text.startswith("```"):
        first_nl = text.index("\n") if "\n" in text else len(text)
        text = text[first_nl + 1 :]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    text = text.strip()

    try:
        parsed: Any = json.loads(text)
        if isinstance(parsed, dict):
            return cast(dict[str, Any], parsed)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            fallback: Any = json.loads(text[start : end + 1])
            if isinstance(fallback, dict):
                return cast(dict[str, Any], fallback)
        except json.JSONDecodeError:
            pass
    return {}


class ArbitrationEngine:
    """LLM-driven arbitrator with pool-gated execution.

    Claims a ``WorkerRole.verifier`` slot and creates a kernel Step.
    Pipeline: hard filter → LLM reasoning → structured decision.
    """

    def __init__(
        self,
        provider_factory: Callable[[], Provider],
        *,
        default_model: str,
    ) -> None:
        self._provider_factory = provider_factory
        self._default_model = default_model

    def arbitrate(
        self,
        bundle: DebateBundle,
        *,
        task_id: str,
        pool: WorkerPoolManager,
        store: KernelStore,
        artifact_store: ArtifactStore,
    ) -> ArbitrationDecision:
        """Produce an arbitration decision, pool-gated with step audit."""
        now = time.time()

        # 1. Hard filter: disqualify candidates with critical critiques.
        critically_critiqued: set[str] = set()
        for critique in bundle.critiques:
            if critique.severity == "critical":
                critically_critiqued.add(critique.target_candidate_id)

        rejection_reasons: list[str] = [
            f"candidate {cid} has critical critique(s)" for cid in critically_critiqued
        ]

        eligible = [p for p in bundle.proposals if p.candidate_id not in critically_critiqued]

        if not eligible:
            log.warning(
                "llm_arbitrator.all_disqualified",
                debate_id=bundle.debate_id,
            )
            return ArbitrationDecision(
                decision_id=f"arb_{uuid.uuid4().hex[:12]}",
                debate_id=bundle.debate_id,
                selected_candidate_id=None,
                rejection_reasons=rejection_reasons,
                merge_notes="",
                confidence=0.0,
                escalation_required=True,
                decided_at=now,
            )

        if len(eligible) == 1:
            winner = eligible[0]
            return ArbitrationDecision(
                decision_id=f"arb_{uuid.uuid4().hex[:12]}",
                debate_id=bundle.debate_id,
                selected_candidate_id=winner.candidate_id,
                rejection_reasons=rejection_reasons,
                merge_notes=(
                    f"Single eligible candidate: {winner.proposer_role} "
                    f"proposal for {winner.target_scope}"
                ),
                confidence=1.0,
                escalation_required=False,
                decided_at=now,
            )

        # 2. Claim a verifier slot for LLM reasoning.
        slot = pool.claim_slot(
            WorkerRole.verifier,
            supervisor_id=task_id,
            workspace=bundle.debate_id,
            module="deliberation_arbitration",
        )
        if slot is None:
            log.warning(
                "llm_arbitrator.slot_unavailable",
                debate_id=bundle.debate_id,
            )
            return self._fallback_decision(bundle, eligible, rejection_reasons, now)

        step = store.create_step(
            task_id=task_id,
            kind="verify",
            title="deliberation_arbitration",
        )
        attempt = store.create_step_attempt(
            task_id=task_id,
            step_id=step.step_id,
            executor_mode="deliberation_arbitrator",
        )

        try:
            decision = self._llm_arbitrate(bundle, eligible, rejection_reasons, now)

            artifact_payload = {
                "artifact_type": "deliberation_llm_arbitration",
                "debate_id": bundle.debate_id,
                "step_id": step.step_id,
                "attempt_id": attempt.step_attempt_id,
                "slot_id": slot.slot_id,
                "selected_candidate_id": decision.selected_candidate_id,
                "confidence": decision.confidence,
                "escalation_required": decision.escalation_required,
            }
            _ref, _hash = artifact_store.store_json(artifact_payload)

            store.update_step_attempt(attempt.step_attempt_id, status="succeeded")
            store.update_step(step.step_id, status="completed")
            return decision
        except Exception:
            log.warning(
                "llm_arbitrator.llm_fallback",
                debate_id=bundle.debate_id,
                exc_info=True,
            )
            decision = self._fallback_decision(bundle, eligible, rejection_reasons, now)
            # Fallback produced a valid decision — mark step succeeded.
            artifact_payload = {
                "artifact_type": "deliberation_llm_arbitration",
                "debate_id": bundle.debate_id,
                "step_id": step.step_id,
                "attempt_id": attempt.step_attempt_id,
                "slot_id": slot.slot_id,
                "selected_candidate_id": decision.selected_candidate_id,
                "confidence": decision.confidence,
                "escalation_required": decision.escalation_required,
                "fallback": True,
            }
            artifact_store.store_json(artifact_payload)
            store.update_step_attempt(attempt.step_attempt_id, status="succeeded")
            store.update_step(step.step_id, status="completed")
            return decision
        finally:
            pool.release_slot(slot.slot_id)

    def _llm_arbitrate(
        self,
        bundle: DebateBundle,
        eligible: list[Any],
        rejection_reasons: list[str],
        now: float,
    ) -> ArbitrationDecision:
        """Call the LLM to reason over eligible candidates."""
        from hermit.runtime.provider_host.shared.contracts import ProviderRequest

        provider = self._provider_factory()

        candidates_section = ""
        for p in eligible:
            critiques_for = [c for c in bundle.critiques if c.target_candidate_id == p.candidate_id]
            critique_text = ""
            for c in critiques_for:
                critique_text += (
                    f"  - [{c.severity}] {c.critic_role}: {c.issue_type} — {c.suggested_fix}\n"
                )
            if not critique_text:
                critique_text = "  (no critiques)\n"

            candidates_section += (
                f"\n### {p.candidate_id} (by {p.proposer_role})\n"
                f"**Plan:** {p.plan_summary}\n"
                f"**Scope:** {json.dumps(p.contract_draft, default=str)}\n"
                f"**Cost:** {p.expected_cost} | **Risk:** {p.expected_risk} | "
                f"**Reward:** {p.expected_reward}\n"
                f"**Critiques:**\n{critique_text}"
            )

        reviews_section = ""
        if bundle.post_execution_reviews:
            reviews_section = "\n## Post-Execution Reviews\n"
            for r in bundle.post_execution_reviews:
                reviews_section += (
                    f"- [{r.severity}] {r.reviewer_role}: {r.challenge_type} — "
                    f"{r.finding} (recommendation: {r.recommendation})\n"
                )

        user_prompt = (
            f"## Decision Point\n{bundle.decision_point}\n\n"
            f"## Eligible Candidates\n{candidates_section}\n"
            f"{reviews_section}\n"
            "Select the best candidate.  Return a JSON object with:\n"
            '- "selected_candidate_id": the chosen candidate_id\n'
            '- "confidence": float 0.0-1.0\n'
            '- "reasoning": explanation of why this candidate was chosen\n'
            '- "merge_notes": any notes for the merge/execution phase\n\n'
            "Return ONLY a JSON object, no other text."
        )

        system_prompt = (
            "You are an impartial technical arbitrator.  Your job is to select "
            "the best proposal from a set of candidates that have survived "
            "critical review.  Consider: risk/reward trade-offs, implementation "
            "feasibility, alignment with the decision point, and severity of "
            "remaining critiques.  Be decisive — always select one candidate "
            "unless none are viable."
        )

        request = ProviderRequest(
            model=self._default_model,
            max_tokens=_DEFAULT_MAX_TOKENS,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        try:
            response = provider.generate(request)
        except Exception:
            log.warning(
                "llm_arbitrator.llm_call_failed",
                debate_id=bundle.debate_id,
                exc_info=True,
            )
            return self._fallback_decision(bundle, eligible, rejection_reasons, now)

        raw_text = ""
        for block in response.content:
            if hasattr(block, "get") and block.get("type") == "text":
                raw_text += block.get("text", "")

        parsed = _parse_json_response(raw_text)
        if not parsed or "selected_candidate_id" not in parsed:
            log.warning(
                "llm_arbitrator.parse_failed",
                debate_id=bundle.debate_id,
                raw_length=len(raw_text),
            )
            return self._fallback_decision(bundle, eligible, rejection_reasons, now)

        selected_id = str(parsed["selected_candidate_id"])
        valid_ids = {p.candidate_id for p in eligible}
        if selected_id not in valid_ids:
            log.warning(
                "llm_arbitrator.invalid_selection",
                debate_id=bundle.debate_id,
                selected=selected_id,
                valid=list(valid_ids),
            )
            return self._fallback_decision(bundle, eligible, rejection_reasons, now)

        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.8))))
        reasoning = str(parsed.get("reasoning", ""))
        merge_notes = str(parsed.get("merge_notes", ""))

        log.info(
            "llm_arbitrator.decided",
            debate_id=bundle.debate_id,
            selected=selected_id,
            confidence=confidence,
        )

        return ArbitrationDecision(
            decision_id=f"arb_{uuid.uuid4().hex[:12]}",
            debate_id=bundle.debate_id,
            selected_candidate_id=selected_id,
            rejection_reasons=rejection_reasons,
            merge_notes=f"{merge_notes}\n\n[LLM reasoning] {reasoning}".strip(),
            confidence=confidence,
            escalation_required=False,
            decided_at=now,
        )

    def _fallback_decision(
        self,
        bundle: DebateBundle,
        eligible: list[Any],
        rejection_reasons: list[str],
        now: float,
    ) -> ArbitrationDecision:
        """Fallback when LLM call fails: select first eligible candidate."""
        winner = eligible[0]
        log.info(
            "llm_arbitrator.fallback",
            debate_id=bundle.debate_id,
            selected=winner.candidate_id,
        )
        return ArbitrationDecision(
            decision_id=f"arb_{uuid.uuid4().hex[:12]}",
            debate_id=bundle.debate_id,
            selected_candidate_id=winner.candidate_id,
            rejection_reasons=rejection_reasons,
            merge_notes=(
                f"[fallback] Selected {winner.proposer_role} proposal for {winner.target_scope}"
            ),
            confidence=0.5,
            escalation_required=False,
            decided_at=now,
        )
