"""Iteration Kernel — self-iteration meta-program lifecycle.

Manages the full lifecycle of a self-improvement iteration: admission,
state transitions through research/spec/execute/verify/reconcile phases,
promotion gating, lesson extraction, and next-seed generation.

The IterationKernel is the state machine that drives the self-iteration
pipeline. It validates transitions, enforces promotion gates, and
produces durable lesson packs that feed back into future iterations.

Self-iteration is NOT a second architecture — it is a Meta-Program
running on Hermit's own task OS. The Iteration Kernel provides
admission control, budgeting, a strict state machine, and a
promotion gate that requires benchmark + replay + reconciliation
before any change is promoted to system capability.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog

from hermit.kernel.execution.self_modify._metadata_utils import parse_metadata

__all__ = [
    "ITERATION_TRANSITIONS",
    "MAX_SEED_CHAIN_DEPTH",
    "AdmissionError",
    "InvalidTransitionError",
    "IterationKernel",
    "IterationLessonPack",
    "IterationSpec",
    "IterationState",
    "IterationVerdict",
    "PolicyRejectionError",
]

logger = structlog.get_logger()

# Default risk bands that are allowed for self-iteration.
_ALLOWED_RISK_BANDS: set[str] = {"low", "medium", "high"}

# Kernel paths that are protected from self-modification.
_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "src/hermit/kernel/policy/",
    "src/hermit/kernel/verification/",
)

# Maximum depth for seed chains (A → B → C → ...).  Prevents unbounded
# or circular self-iteration loops.
MAX_SEED_CHAIN_DEPTH = 10


class IterationState(StrEnum):
    """Lifecycle states for a self-improvement iteration."""

    draft = "draft"
    admitted = "admitted"
    researching = "researching"
    specifying = "specifying"
    executing = "executing"
    verifying = "verifying"
    reconciling = "reconciling"
    accepted = "accepted"
    pr_created = "pr_created"
    merge_approved = "merge_approved"
    rejected = "rejected"
    parked = "parked"


# Valid state transitions — enforced by IterationKernel.transition().
ITERATION_TRANSITIONS: dict[IterationState, set[IterationState]] = {
    IterationState.draft: {IterationState.admitted, IterationState.rejected},
    IterationState.admitted: {IterationState.researching, IterationState.rejected},
    IterationState.researching: {IterationState.specifying, IterationState.parked},
    IterationState.specifying: {IterationState.executing, IterationState.parked},
    IterationState.executing: {IterationState.verifying, IterationState.parked},
    IterationState.verifying: {IterationState.reconciling, IterationState.parked},
    IterationState.reconciling: {IterationState.accepted, IterationState.rejected},
    IterationState.accepted: {IterationState.pr_created, IterationState.rejected},
    IterationState.pr_created: {IterationState.merge_approved, IterationState.rejected},
    IterationState.merge_approved: set(),  # terminal — merge done
    IterationState.parked: {
        IterationState.researching,
        IterationState.specifying,
        IterationState.executing,
        IterationState.verifying,
        IterationState.reconciling,
    },
}

# Terminal states — no further transitions allowed.
_TERMINAL_STATES: set[IterationState] = {
    IterationState.merge_approved,
    IterationState.rejected,
}


class AdmissionError(ValueError):
    """Raised when an IterationSpec fails admission validation."""


class PolicyRejectionError(RuntimeError):
    """Raised when a policy_check callback rejects admission."""


def _now_ts() -> float:
    return datetime.now(UTC).timestamp()


@dataclass
class IterationSpec:
    """Specification for a self-improvement iteration."""

    spec_id: str
    goal: str
    constraints: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    change_units: list[str] = field(default_factory=list)
    eval_requirements: dict = field(default_factory=dict)
    risk_budget: dict = field(default_factory=dict)
    max_rounds: int = 3
    parent_iteration_id: str | None = None
    created_at: float = field(default_factory=_now_ts)


@dataclass
class IterationVerdict:
    """Outcome of a completed iteration reconciliation."""

    verdict_id: str
    iteration_id: str
    spec_id: str
    result: str  # "accepted" | "rejected" | "accepted_with_followups"
    benchmark_results: dict = field(default_factory=dict)
    reconciliation_summary: str = ""
    lessons: list[str] = field(default_factory=list)
    next_seed: str | None = None


@dataclass
class IterationLessonPack:
    """Lessons extracted from a completed iteration for future reuse."""

    lesson_id: str
    iteration_id: str
    playbook_updates: list[str] = field(default_factory=list)
    template_updates: list[str] = field(default_factory=list)
    pattern_updates: list[str] = field(default_factory=list)
    evidence_refs: list[str | None] = field(default_factory=list)
    created_at: float = field(default_factory=_now_ts)


class InvalidTransitionError(ValueError):
    """Raised when a state transition violates ITERATION_TRANSITIONS."""


class IterationKernel:
    """State machine and lifecycle manager for self-improvement iterations.

    Wraps a store (any object providing spec_backlog and iteration_lessons
    CRUD — typically a KernelStore with SelfIterateStoreMixin) to manage
    iteration admission, state transitions, promotion gating, lesson
    extraction, and next-seed generation.
    """

    def __init__(self, store: object) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Admission
    # ------------------------------------------------------------------

    def validate_admission(self, spec: IterationSpec) -> list[str]:
        """Validate an IterationSpec against admission criteria.

        Admission requires (per spec):
        1. scope clear — goal is non-empty
        2. success_criteria defined — at least one criterion
        3. eval_requirements present — benchmark exists
        4. risk_budget within allowed band
        5. change_units specified — rollback scope exists

        Returns a list of validation error strings. Empty list means the spec
        passes all admission checks.
        """
        errors: list[str] = []

        # 1. Scope must be clear — non-empty goal.
        if not spec.goal or not spec.goal.strip():
            errors.append("goal must be non-empty (scope unclear)")

        # 2. Success criteria must be defined — at least one criterion.
        if not spec.success_criteria:
            errors.append("success_criteria must contain at least one criterion")

        # 3. Benchmark must exist — eval_requirements non-empty.
        if not spec.eval_requirements:
            errors.append("eval_requirements must be non-empty (benchmark must exist)")

        # 4. Risk band must be allowed.
        risk_band = spec.risk_budget.get("band", "")
        if risk_band and risk_band not in _ALLOWED_RISK_BANDS:
            errors.append(
                f"risk_budget.band '{risk_band}' not in allowed bands: "
                f"{sorted(_ALLOWED_RISK_BANDS)}"
            )

        # 5. Rollback scope must exist — change_units non-empty.
        if not spec.change_units:
            errors.append("change_units must be non-empty (rollback scope required)")

        # 6. Self-modification safety — forbid changes to policy/verification engine.
        for unit in spec.change_units:
            for prefix in _FORBIDDEN_PREFIXES:
                if unit.startswith(prefix) or f"/{prefix}" in unit:
                    errors.append(
                        f"change_unit '{unit}' targets protected kernel path '{prefix}' "
                        f"— self-modification of policy/verification engine is forbidden"
                    )

        return errors

    def admit_iteration(
        self,
        spec: IterationSpec,
        *,
        strict: bool = False,
        policy_check: Callable[[dict], dict | None] | None = None,
    ) -> str:
        """Validate and admit a new iteration spec.

        Creates a spec_backlog entry in 'draft' state, validates admission
        criteria, then transitions to 'admitted'. Returns the iteration_id.

        When strict=True, all admission checks must pass or AdmissionError
        is raised. When strict=False (default, backward-compatible), only
        the goal-non-empty check is enforced; other warnings are logged
        but do not block admission.

        When *policy_check* is provided, it is called with a dict containing
        ``goal``, ``risk_budget``, and ``trust_zone`` (derived from
        risk_budget). The callback must return None to approve, or a dict
        with at least a ``reason`` key to reject. If rejected,
        PolicyRejectionError is raised and no store mutation occurs.

        Raises AdmissionError if strict=True and validation fails.
        Raises PolicyRejectionError if policy_check returns a rejection.
        Raises ValueError if the spec has no goal (always enforced).
        """
        # Always enforce non-empty goal.
        if not spec.goal or not spec.goal.strip():
            raise ValueError("IterationSpec.goal must be non-empty")

        # Detect circular seed chains and enforce max depth.
        if spec.parent_iteration_id is not None:
            self._check_seed_chain(spec.parent_iteration_id, spec.spec_id)

        # Run full admission validation.
        admission_errors = self.validate_admission(spec)

        if strict and admission_errors:
            raise AdmissionError("Iteration admission failed: " + "; ".join(admission_errors))
        elif admission_errors:
            logger.warning(
                "iteration_kernel.admission_warnings",
                spec_id=spec.spec_id,
                warnings=admission_errors,
            )

        # --- Policy gate (callback-based, no hard coupling) ---
        if policy_check is not None:
            trust_zone = spec.risk_budget.get("trust_zone", "normal")
            check_input = {
                "goal": spec.goal,
                "risk_budget": spec.risk_budget,
                "trust_zone": trust_zone,
            }
            rejection = policy_check(check_input)
            if rejection is not None:
                reason = rejection.get("reason", "policy check rejected admission")
                logger.warning(
                    "iteration_kernel.policy_rejected",
                    spec_id=spec.spec_id,
                    reason=reason,
                )
                raise PolicyRejectionError(reason)

        iteration_id = f"iter-{uuid.uuid4().hex[:12]}"

        metadata = {
            "constraints": spec.constraints,
            "success_criteria": spec.success_criteria,
            "change_units": spec.change_units,
            "eval_requirements": spec.eval_requirements,
            "risk_budget": spec.risk_budget,
            "max_rounds": spec.max_rounds,
            "iteration_id": iteration_id,
            "parent_iteration_id": spec.parent_iteration_id,
            "state": IterationState.draft.value,
        }

        # Create in 'draft' state first (the initial state per the state machine).
        self._store.create_spec_entry(
            spec_id=spec.spec_id,
            goal=spec.goal,
            priority="normal",
            source="self-iterate",
            metadata=metadata,
        )
        self._store.update_spec_status(
            spec.spec_id,
            IterationState.draft.value,
            metadata=metadata,
        )

        # Transition from draft -> admitted (follows the state machine).
        metadata["state"] = IterationState.admitted.value
        self._store.update_spec_status(
            spec.spec_id,
            IterationState.admitted.value,
            metadata=metadata,
        )

        logger.info(
            "iteration_kernel.admitted",
            iteration_id=iteration_id,
            spec_id=spec.spec_id,
            goal=spec.goal[:80],
            strict=strict,
            admission_warnings=len(admission_errors),
        )
        return iteration_id

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def get_state(self, iteration_id: str) -> IterationState:
        """Return the current state of an iteration.

        Raises KeyError if the iteration_id is not found.
        """
        entry = self.find_entry(iteration_id)
        if entry is None:
            raise KeyError(f"Iteration not found: {iteration_id}")
        return IterationState(entry["status"])

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def transition(self, iteration_id: str, new_state: IterationState) -> bool:
        """Advance an iteration to *new_state*.

        Validates the transition against ITERATION_TRANSITIONS. Returns True
        if the transition succeeded, False if the store update failed.

        Raises InvalidTransitionError if the transition is not allowed.
        Raises KeyError if the iteration is not found.
        """
        entry = self.find_entry(iteration_id)
        if entry is None:
            raise KeyError(f"Iteration not found: {iteration_id}")

        current = IterationState(entry["status"])

        if current in _TERMINAL_STATES:
            raise InvalidTransitionError(f"Cannot transition from terminal state {current.value}")

        allowed = ITERATION_TRANSITIONS.get(current, set())
        if new_state not in allowed:
            raise InvalidTransitionError(
                f"Invalid transition: {current.value} -> {new_state.value}"
            )

        # Update the metadata state tracking too.
        raw_meta = entry.get("metadata")
        meta = parse_metadata(raw_meta)
        meta["state"] = new_state.value

        updated = self._store.update_spec_status(
            entry["spec_id"],
            new_state.value,
            expected_status=current.value,
            metadata=meta,
        )

        if updated:
            logger.info(
                "iteration_kernel.transitioned",
                iteration_id=iteration_id,
                from_state=current.value,
                to_state=new_state.value,
            )

        return updated

    # ------------------------------------------------------------------
    # Promotion gate
    # ------------------------------------------------------------------

    def check_promotion_gate(self, iteration_id: str) -> bool:
        """Check whether an iteration is eligible for promotion to 'accepted'.

        Promotion requires (per spec — Iteration Promotion Gate):
        1. Current state is 'reconciling'.
        2. benchmark_results present with at least one result (benchmark passes).
        3. Non-empty reconciliation_summary (reconcile satisfied).
        4. replay_stable is True (replay stability verified).
        5. No high-risk unexplained drift (unexplained_drift is empty or absent).

        Returns True if the iteration passes the gate, False otherwise.
        Raises KeyError if the iteration is not found.
        """
        entry = self.find_entry(iteration_id)
        if entry is None:
            raise KeyError(f"Iteration not found: {iteration_id}")

        current = IterationState(entry["status"])
        if current != IterationState.reconciling:
            return False

        raw_meta = entry.get("metadata")
        meta = parse_metadata(raw_meta)
        if not meta:
            return False

        # 1. Benchmark results must be present and non-empty.
        benchmark = meta.get("benchmark_results")
        if not benchmark or not isinstance(benchmark, dict):
            return False

        # 2. Reconciliation summary must be non-empty.
        summary = meta.get("reconciliation_summary")
        if not summary or not isinstance(summary, str) or not summary.strip():
            return False

        # 3. Replay must be stable — explicit flag required.
        replay_stable = meta.get("replay_stable")
        if replay_stable is not True:
            return False

        # 4. No high-risk unexplained drift.
        unexplained_drift = meta.get("unexplained_drift")
        return not (isinstance(unexplained_drift, list) and len(unexplained_drift) > 0)

    # ------------------------------------------------------------------
    # Lesson extraction
    # ------------------------------------------------------------------

    def extract_lessons(self, iteration_id: str) -> IterationLessonPack:
        """Extract lessons from a completed iteration.

        Queries the store's iteration_lessons table for all lessons associated
        with this iteration, then categorizes them into playbook, template,
        and pattern updates.  Each update string is enriched with its
        ``evidence_ref`` when available.

        Raises KeyError if the iteration is not found.
        """
        entry = self.find_entry(iteration_id)
        if entry is None:
            raise KeyError(f"Iteration not found: {iteration_id}")

        lessons = self._store.list_lessons(iteration_ids=[iteration_id])

        playbook_updates: list[str] = []
        template_updates: list[str] = []
        pattern_updates: list[str] = []
        evidence_refs: list[str | None] = []

        for lesson in lessons:
            summary = lesson.get("summary", "")
            category = lesson.get("category", "")
            evidence_ref = lesson.get("evidence_ref")
            if category in ("playbook", "process", "workflow"):
                playbook_updates.append(summary)
            elif category in ("template", "scaffold", "boilerplate"):
                template_updates.append(summary)
            elif category in ("pattern", "architecture", "design"):
                pattern_updates.append(summary)
            else:
                # Default: treat uncategorized lessons as pattern updates.
                logger.warning(
                    "iteration_lesson_uncategorized_fallback",
                    category=category,
                    summary=summary[:100],
                    iteration_id=iteration_id,
                )
                pattern_updates.append(summary)
            evidence_refs.append(evidence_ref)

        lesson_pack = IterationLessonPack(
            lesson_id=f"lpack-{uuid.uuid4().hex[:12]}",
            iteration_id=iteration_id,
            playbook_updates=playbook_updates,
            template_updates=template_updates,
            pattern_updates=pattern_updates,
            evidence_refs=evidence_refs,
        )

        logger.info(
            "iteration_kernel.lessons_extracted",
            iteration_id=iteration_id,
            playbook_count=len(playbook_updates),
            template_count=len(template_updates),
            pattern_count=len(pattern_updates),
        )

        return lesson_pack

    # ------------------------------------------------------------------
    # Next-seed generation
    # ------------------------------------------------------------------

    def generate_next_seed(self, iteration_id: str) -> IterationSpec | None:
        """Generate a follow-up iteration spec from a completed iteration.

        If the iteration's metadata contains a 'next_seed' goal, produces
        a new IterationSpec seeded from the completed iteration's lessons
        and constraints. Returns None if no follow-up is indicated.

        Raises KeyError if the iteration is not found.
        """
        entry = self._find_entry(iteration_id)
        if entry is None:
            raise KeyError(f"Iteration not found: {iteration_id}")

        raw_meta = entry.get("metadata")
        meta = parse_metadata(raw_meta)
        if not meta:
            return None

        next_goal = meta.get("next_seed")
        if not next_goal or not isinstance(next_goal, str) or not next_goal.strip():
            return None

        # Carry forward constraints and success criteria from parent.
        parent_constraints = meta.get("constraints", [])
        parent_criteria = meta.get("success_criteria", [])

        seed = IterationSpec(
            spec_id=f"spec-{uuid.uuid4().hex[:12]}",
            goal=next_goal.strip(),
            constraints=list(parent_constraints),
            success_criteria=list(parent_criteria),
            max_rounds=meta.get("max_rounds", 3),
            parent_iteration_id=iteration_id,
        )

        logger.info(
            "iteration_kernel.seed_generated",
            iteration_id=iteration_id,
            seed_spec_id=seed.spec_id,
            seed_goal=seed.goal[:80],
        )

        return seed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_seed_chain(self, parent_iteration_id: str, new_spec_id: str) -> None:
        """Trace the parent chain to detect cycles and enforce depth limit.

        Walks from *parent_iteration_id* backwards through
        ``metadata.parent_iteration_id`` links.  Raises :class:`AdmissionError`
        if the chain exceeds :data:`MAX_SEED_CHAIN_DEPTH` or if *new_spec_id*
        already appears in the ancestry (circular chain).
        """
        visited: set[str] = {new_spec_id}
        current = parent_iteration_id
        depth = 1

        while current is not None:
            if depth > MAX_SEED_CHAIN_DEPTH:
                raise AdmissionError(
                    f"Seed chain depth ({depth}) exceeds MAX_SEED_CHAIN_DEPTH "
                    f"({MAX_SEED_CHAIN_DEPTH})"
                )
            if current in visited:
                raise AdmissionError(
                    f"Circular seed chain detected: '{current}' already in ancestry"
                )
            visited.add(current)

            # Look up the entry for 'current' to find its parent.
            entry = self.find_entry(current)
            if entry is None:
                # Parent not found — chain is broken, no cycle.
                break

            raw_meta = entry.get("metadata")
            meta = parse_metadata(raw_meta)
            if not meta:
                break

            current = meta.get("parent_iteration_id")
            depth += 1

    def find_entry(self, iteration_id: str) -> dict[str, Any] | None:
        """Find a spec_backlog entry by iteration_id stored in metadata.

        Searches for entries where metadata.iteration_id matches. Falls back
        to using iteration_id as spec_id for simpler lookups.
        """
        # First: try direct spec_id lookup (fast path).
        entry = self._store.get_spec_entry(iteration_id)
        if entry is not None:
            return entry

        # Second: scan for metadata.iteration_id match.
        entries = self._store.list_spec_backlog(limit=500)
        for e in entries:
            raw = e.get("metadata")
            meta = parse_metadata(raw)
            if not meta:
                continue
            if meta.get("iteration_id") == iteration_id:
                return e

        return None

    def _find_entry(self, iteration_id: str) -> dict[str, Any] | None:
        """Backward-compatible alias for internal callers.

        Kept for compatibility until external call sites are migrated.
        """
        return self.find_entry(iteration_id)
