"""Governance profiles that map complexity bands to pipeline stage toggles.

A GovernanceProfile determines which governed execution stages are active
for a given task complexity level. This enables the executor to skip
expensive stages (witness capture, contract synthesis, deliberation,
reconciliation) for simple tasks while maintaining full governance for
complex or high-risk operations.

Safety invariants:
- ``policy_profile == "supervised"`` overrides all skips (full governance).
- Policy engine deny verdicts are never affected by complexity.
- Receipts are never fully skipped — at minimum ``summary`` mode is used.
"""

from __future__ import annotations

from dataclasses import dataclass

from hermit.kernel.policy.models.enums import ComplexityBand


@dataclass(frozen=True)
class GovernanceProfile:
    """Controls which governance stages the executor should run."""

    complexity_band: str
    skip_witness: bool = False
    skip_contract_synthesis: bool = False
    skip_deliberation: bool = False
    skip_reconciliation: bool = False
    receipt_mode: str = "full"  # "full" | "summary" | "none"


# Pre-defined profiles per complexity band.
# These encode the assumption that simpler tasks need less overhead.
# This mapping should be stress-tested periodically as model capabilities
# improve — see guard_effectiveness_report() in AnalyticsEngine.
_PROFILES: dict[str, GovernanceProfile] = {
    ComplexityBand.TRIVIAL: GovernanceProfile(
        complexity_band=ComplexityBand.TRIVIAL,
        skip_witness=True,
        skip_contract_synthesis=True,
        skip_deliberation=True,
        skip_reconciliation=True,
        receipt_mode="none",
    ),
    ComplexityBand.SIMPLE: GovernanceProfile(
        complexity_band=ComplexityBand.SIMPLE,
        skip_witness=True,
        skip_contract_synthesis=True,
        skip_deliberation=True,
        skip_reconciliation=False,
        receipt_mode="summary",
    ),
    ComplexityBand.MODERATE: GovernanceProfile(
        complexity_band=ComplexityBand.MODERATE,
        skip_witness=False,
        skip_contract_synthesis=False,
        skip_deliberation=True,
        skip_reconciliation=False,
        receipt_mode="full",
    ),
    ComplexityBand.COMPLEX: GovernanceProfile(
        complexity_band=ComplexityBand.COMPLEX,
        skip_witness=False,
        skip_contract_synthesis=False,
        skip_deliberation=False,
        skip_reconciliation=False,
        receipt_mode="full",
    ),
}

# Full governance profile — used when policy_profile is "supervised".
_FULL_GOVERNANCE = GovernanceProfile(
    complexity_band=ComplexityBand.COMPLEX,
    skip_witness=False,
    skip_contract_synthesis=False,
    skip_deliberation=False,
    skip_reconciliation=False,
    receipt_mode="full",
)


def resolve_governance_profile(
    complexity_band: str,
    policy_profile: str,
) -> GovernanceProfile:
    """Resolve the effective governance profile for a task.

    Safety rule: supervised policy profile always gets full governance,
    regardless of complexity band.
    """
    if policy_profile in ("supervised", "readonly"):
        return _FULL_GOVERNANCE

    return _PROFILES.get(complexity_band, _FULL_GOVERNANCE)
