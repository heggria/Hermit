"""LLM-driven critique generator using kernel WorkerPool for admission control.

Dispatches parallel LLM reviewers gated by WorkerRole.reviewer slots.
Each critique batch is tracked as a Step + StepAttempt for governance audit.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import structlog

from hermit.kernel.execution.competition.deliberation import (
    CandidateProposal,
    CritiqueRecord,
)
from hermit.kernel.execution.workers.models import WorkerRole

if TYPE_CHECKING:
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.execution.workers.pool import WorkerPoolManager
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.provider_host.shared.contracts import Provider

__all__ = ["CriticRole", "CritiqueGenerator"]

log = structlog.get_logger()

_FUTURES_TIMEOUT_SECONDS = 120
_DEFAULT_MAX_TOKENS = 4096


@dataclass(frozen=True)
class CriticRole:
    """A reviewer perspective for critiquing proposals."""

    role: str
    system_prompt: str
    model: str | None = None


_DEFAULT_CRITIC_ROLES: tuple[CriticRole, ...] = (
    CriticRole(
        role="security_reviewer",
        system_prompt=(
            "You are a security-focused code reviewer.  Evaluate each proposal "
            "for security vulnerabilities, access control issues, data exposure "
            "risks, and injection vectors.  Flag anything that could lead to a "
            "security incident."
        ),
    ),
    CriticRole(
        role="correctness_reviewer",
        system_prompt=(
            "You are a correctness-focused reviewer.  Evaluate each proposal "
            "for logical errors, edge cases, data integrity issues, and "
            "specification violations.  Ensure the proposal actually solves "
            "the stated problem without introducing regressions."
        ),
    ),
    CriticRole(
        role="feasibility_reviewer",
        system_prompt=(
            "You are a feasibility-focused reviewer.  Evaluate each proposal "
            "for implementation complexity, dependency risks, resource "
            "requirements, and timeline realism.  Flag proposals that are "
            "over-scoped or under-estimated."
        ),
    ),
)


def _parse_critiques_response(raw_text: str) -> list[dict[str, Any]]:
    """Parse an LLM response into a list of critique dicts."""
    text = raw_text.strip()
    if text.startswith("```"):
        first_nl = text.index("\n") if "\n" in text else len(text)
        text = text[first_nl + 1 :]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    text = text.strip()

    try:
        parsed: Any = json.loads(text)
        if isinstance(parsed, list):
            return [
                cast(dict[str, Any], item)
                for item in cast(list[Any], parsed)
                if isinstance(item, dict)
            ]
        if isinstance(parsed, dict):
            return [cast(dict[str, Any], parsed)]
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            fallback: Any = json.loads(text[start : end + 1])
            if isinstance(fallback, list):
                return [
                    cast(dict[str, Any], item)
                    for item in cast(list[Any], fallback)
                    if isinstance(item, dict)
                ]
        except json.JSONDecodeError:
            pass
    return []


class CritiqueGenerator:
    """Generates critiques using pool-gated parallel LLM reviewers.

    Each reviewer claims a ``WorkerRole.reviewer`` slot and creates a
    kernel Step for governance audit.
    """

    def __init__(
        self,
        provider_factory: Callable[[], Provider],
        *,
        default_model: str,
        max_workers: int = 3,
        critic_roles: tuple[CriticRole, ...] | None = None,
    ) -> None:
        self._provider_factory = provider_factory
        self._default_model = default_model
        self._max_workers = max_workers
        self._critic_roles = critic_roles or _DEFAULT_CRITIC_ROLES

    def generate_critiques(
        self,
        proposals: list[CandidateProposal],
        context: dict[str, Any],
        *,
        task_id: str,
        debate_id: str,
        pool: WorkerPoolManager,
        store: KernelStore,
        artifact_store: ArtifactStore,
    ) -> list[CritiqueRecord]:
        """Generate critiques in parallel — one LLM call per role, pool-gated."""
        if not proposals:
            return []

        log.info(
            "llm_critic.generating",
            proposal_count=len(proposals),
            critic_count=len(self._critic_roles),
        )

        all_critiques: list[CritiqueRecord] = []

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self._max_workers,
        ) as executor:
            futures: dict[concurrent.futures.Future[list[CritiqueRecord]], str] = {}
            for critic_role in self._critic_roles:
                future = executor.submit(
                    self._review_pooled,
                    critic_role,
                    proposals,
                    context,
                    task_id=task_id,
                    debate_id=debate_id,
                    pool=pool,
                    store=store,
                    artifact_store=artifact_store,
                )
                futures[future] = critic_role.role

            done, not_done = concurrent.futures.wait(
                futures.keys(),
                timeout=_FUTURES_TIMEOUT_SECONDS,
            )

            for future in done:
                role = futures[future]
                try:
                    critiques = future.result()
                    all_critiques.extend(critiques)
                    log.debug(
                        "llm_critic.role_completed",
                        role=role,
                        critique_count=len(critiques),
                    )
                except Exception:
                    log.warning("llm_critic.role_failed", role=role, exc_info=True)

            for future in not_done:
                role = futures[future]
                future.cancel()
                log.warning("llm_critic.role_timeout", role=role)

        log.info("llm_critic.completed", total_critiques=len(all_critiques))
        return all_critiques

    def _review_pooled(
        self,
        critic_role: CriticRole,
        proposals: list[CandidateProposal],
        context: dict[str, Any],
        *,
        task_id: str,
        debate_id: str,
        pool: WorkerPoolManager,
        store: KernelStore,
        artifact_store: ArtifactStore,
    ) -> list[CritiqueRecord]:
        """Claim a reviewer slot, create a step, run LLM, release slot."""
        slot = pool.claim_slot(
            WorkerRole.reviewer,
            supervisor_id=task_id,
            workspace=debate_id,
            module="deliberation_critique",
        )
        if slot is None:
            log.warning(
                "llm_critic.slot_unavailable",
                debate_id=debate_id,
                role=critic_role.role,
            )
            return []

        step = store.create_step(
            task_id=task_id,
            kind="review",
            title=f"deliberation_critique:{critic_role.role}",
        )
        attempt = store.create_step_attempt(
            task_id=task_id,
            step_id=step.step_id,
            executor_mode="deliberation_critic",
        )

        try:
            critiques = self._review_all_proposals(critic_role, proposals, context)

            artifact_payload = {
                "artifact_type": "deliberation_llm_critique_batch",
                "debate_id": debate_id,
                "step_id": step.step_id,
                "attempt_id": attempt.step_attempt_id,
                "slot_id": slot.slot_id,
                "critic_role": critic_role.role,
                "critique_count": len(critiques),
                "target_ids": [c.target_candidate_id for c in critiques],
            }
            artifact_store.store_json(artifact_payload)

            store.update_step_attempt(attempt.step_attempt_id, status="succeeded")
            store.update_step(step.step_id, status="completed")
            return critiques
        except Exception:
            store.update_step_attempt(attempt.step_attempt_id, status="failed")
            store.update_step(step.step_id, status="failed")
            raise
        finally:
            pool.release_slot(slot.slot_id)

    def _review_all_proposals(
        self,
        critic_role: CriticRole,
        proposals: list[CandidateProposal],
        context: dict[str, Any],
    ) -> list[CritiqueRecord]:
        """One critic role reviews all proposals in a single LLM call."""
        from hermit.runtime.provider_host.shared.contracts import ProviderRequest

        provider = self._provider_factory()
        model = critic_role.model or self._default_model

        proposals_section = ""
        for p in proposals:
            proposals_section += (
                f"\n### Candidate: {p.candidate_id} (by {p.proposer_role})\n"
                f"**Plan:** {p.plan_summary}\n"
                f"**Scope:** {json.dumps(p.contract_draft, default=str)}\n"
                f"**Cost:** {p.expected_cost} | **Risk:** {p.expected_risk} | "
                f"**Reward:** {p.expected_reward}\n"
            )

        context_json = json.dumps(context, indent=2, default=str)
        user_prompt = (
            f"## Proposals to Review\n{proposals_section}\n\n"
            f"## Context\n```json\n{context_json}\n```\n\n"
            "For each issue found, return a JSON array of critique objects:\n"
            '- "target_candidate_id": the candidate_id of the proposal\n'
            '- "issue_type": category of issue\n'
            '- "severity": "low" | "medium" | "high" | "critical"\n'
            '- "evidence_refs": list of evidence references (strings)\n'
            '- "suggested_fix": how to address the issue\n\n'
            "If no issues are found, return an empty array [].\n"
            "Return ONLY a JSON array, no other text."
        )

        request = ProviderRequest(
            model=model,
            max_tokens=_DEFAULT_MAX_TOKENS,
            system_prompt=critic_role.system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        response = provider.generate(request)

        raw_text = ""
        for block in response.content:
            if hasattr(block, "get") and block.get("type") == "text":
                raw_text += block.get("text", "")

        parsed_items = _parse_critiques_response(raw_text)

        critiques: list[CritiqueRecord] = []
        now = time.time()
        valid_ids = {p.candidate_id for p in proposals}
        for item in parsed_items:
            target_id = str(item.get("target_candidate_id", ""))
            if target_id not in valid_ids:
                log.debug(
                    "llm_critic.invalid_target",
                    role=critic_role.role,
                    target=target_id,
                )
                continue

            severity = str(item.get("severity", "medium"))
            if severity not in {"low", "medium", "high", "critical"}:
                severity = "medium"

            critiques.append(
                CritiqueRecord(
                    critique_id=f"llm_crit_{uuid.uuid4().hex[:12]}",
                    target_candidate_id=target_id,
                    critic_role=critic_role.role,
                    issue_type=str(item.get("issue_type", "general")),
                    severity=severity,
                    evidence_refs=[str(r) for r in item.get("evidence_refs", [])],
                    suggested_fix=str(item.get("suggested_fix", "")),
                    created_at=now,
                )
            )

        return critiques
