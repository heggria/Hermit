"""Layered invariant engine for the Trace-Contract-Driven Assurance System.

Provides built-in invariant checkers for scheduler, state-machine, isolation,
governance, restart, and trace-level properties.  Each checker takes a list of
TraceEnvelope records and returns zero or more InvariantViolation instances.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

import structlog

from hermit.kernel.task.state.transitions import (
    VALID_ATTEMPT_TRANSITIONS,
    VALID_TASK_TRANSITIONS,
)
from hermit.kernel.verification.assurance.models import (
    InvariantSpec,
    InvariantViolation,
    TraceEnvelope,
    _id,
)

logger = structlog.get_logger()

# Type alias for checker functions.
InvariantChecker = Callable[[list[TraceEnvelope]], list[InvariantViolation]]

# ---------------------------------------------------------------------------
# Event-type constants used by checkers
# ---------------------------------------------------------------------------

_TASK_STATE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "task.queued",
        "task.running",
        "task.blocked",
        "task.completed",
        "task.failed",
        "task.cancelled",
        "task.paused",
        "task.budget_exceeded",
        "task.needs_attention",
        "task.reconciling",
        "task.planning_ready",
    }
)

# task.created is the birth event — it sets the initial state but does not
# participate in transition-legality checks (the first explicit state event
# after creation is validated from the initial state it establishes).
_TASK_INIT_EVENT = "task.created"

_ATTEMPT_STATE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "step_attempt.ready",
        "step_attempt.running",
        "step_attempt.waiting",
        "step_attempt.dispatching",
        "step_attempt.contracting",
        "step_attempt.preflighting",
        "step_attempt.observing",
        "step_attempt.reconciling",
        "step_attempt.policy_pending",
        "step_attempt.awaiting_approval",
        "step_attempt.awaiting_plan_confirmation",
        "step_attempt.verification_blocked",
        "step_attempt.receipt_pending",
        "step_attempt.succeeded",
        "step_attempt.completed",
        "step_attempt.skipped",
        "step_attempt.failed",
        "step_attempt.superseded",
    }
)

# Mapping from event_type suffix to canonical task state string.
_EVENT_TO_TASK_STATE: dict[str, str] = {
    "task.created": "queued",
    "task.queued": "queued",
    "task.running": "running",
    "task.blocked": "blocked",
    "task.completed": "completed",
    "task.failed": "failed",
    "task.cancelled": "cancelled",
    "task.paused": "paused",
    "task.budget_exceeded": "budget_exceeded",
    "task.needs_attention": "needs_attention",
    "task.reconciling": "reconciling",
    "task.planning_ready": "planning_ready",
}

_EVENT_TO_ATTEMPT_STATE: dict[str, str] = {
    et: et.split(".", 1)[1] for et in _ATTEMPT_STATE_EVENT_TYPES
}


# ---------------------------------------------------------------------------
# Trace-slice helper
# ---------------------------------------------------------------------------


def _slice_around(event_seq: int, all_seqs: list[int], radius: int = 3) -> tuple[int, int]:
    """Return (start, end) event_seq bounds around *event_seq*."""
    start = max(min(all_seqs), event_seq - radius) if all_seqs else event_seq
    end = max(max(all_seqs), event_seq + radius) if all_seqs else event_seq
    return start, end


def _all_seqs(envelopes: list[TraceEnvelope]) -> list[int]:
    return [e.event_seq for e in envelopes]


# ---------------------------------------------------------------------------
# Built-in checkers
# ---------------------------------------------------------------------------


def _check_single_winner_per_task(envelopes: list[TraceEnvelope]) -> list[InvariantViolation]:
    """scheduler.single_winner_per_task — same step_attempt_id claimed by only one actor."""
    violations: list[InvariantViolation] = []
    seqs = _all_seqs(envelopes)

    # Map step_attempt_id -> set of (actor_id, event_seq)
    attempt_actors: dict[str, list[tuple[str, TraceEnvelope]]] = defaultdict(list)
    for env in envelopes:
        if env.step_attempt_id and env.actor_id and env.event_type == "dispatch.claimed":
            attempt_actors[env.step_attempt_id].append((env.actor_id, env))

    for attempt_id, claims in attempt_actors.items():
        unique_actors = {actor for actor, _ in claims}
        if len(unique_actors) > 1:
            # Violation: multiple actors claimed the same attempt
            first_env = claims[0][1]
            start, end = _slice_around(first_env.event_seq, seqs)
            violations.append(
                InvariantViolation(
                    violation_id=_id("inv-viol"),
                    invariant_id="scheduler.single_winner_per_task",
                    severity="blocker",
                    event_id=first_env.trace_id,
                    task_id=first_env.task_id,
                    step_attempt_id=attempt_id,
                    evidence={
                        "step_attempt_id": attempt_id,
                        "actors": sorted(unique_actors),
                        "claim_count": len(claims),
                    },
                    trace_slice_start=start,
                    trace_slice_end=end,
                )
            )

    return violations


def _check_total_order_per_task(envelopes: list[TraceEnvelope]) -> list[InvariantViolation]:
    """scheduler.total_order_per_task — event_seq is monotonically increasing per task."""
    violations: list[InvariantViolation] = []
    seqs = _all_seqs(envelopes)

    task_last_seq: dict[str, int] = {}
    for env in envelopes:
        prev = task_last_seq.get(env.task_id)
        if prev is not None and env.event_seq <= prev:
            start, end = _slice_around(env.event_seq, seqs)
            violations.append(
                InvariantViolation(
                    violation_id=_id("inv-viol"),
                    invariant_id="scheduler.total_order_per_task",
                    severity="blocker",
                    event_id=env.trace_id,
                    task_id=env.task_id,
                    evidence={
                        "previous_seq": prev,
                        "current_seq": env.event_seq,
                        "event_type": env.event_type,
                    },
                    trace_slice_start=start,
                    trace_slice_end=end,
                )
            )
        task_last_seq[env.task_id] = env.event_seq

    return violations


def _check_task_transition_legality(
    envelopes: list[TraceEnvelope],
) -> list[InvariantViolation]:
    """state.task_transition_legality — task states follow valid transitions."""
    violations: list[InvariantViolation] = []
    seqs = _all_seqs(envelopes)

    # Track last known state per task_id.
    # task.created registers the task but is not a state-machine transition —
    # transition checking starts from the first explicit state event (task.queued,
    # task.running, etc.).
    task_state: dict[str, str] = {}
    for env in envelopes:
        if env.event_type == _TASK_INIT_EVENT:
            # Birth event — do not set state; the first explicit state event
            # will be accepted unconditionally.
            continue

        if env.event_type not in _TASK_STATE_EVENT_TYPES:
            continue
        new_state = _EVENT_TO_TASK_STATE.get(env.event_type)
        if new_state is None:
            continue

        old_state = task_state.get(env.task_id)
        if old_state is not None:
            # Validate transition using the kernel transition table
            from hermit.kernel.task.state.enums import TaskState

            try:
                old_ts = TaskState(old_state)
                new_ts = TaskState(new_state)
            except ValueError:
                # Unknown state — flag as violation
                start, end = _slice_around(env.event_seq, seqs)
                violations.append(
                    InvariantViolation(
                        violation_id=_id("inv-viol"),
                        invariant_id="state.task_transition_legality",
                        severity="blocker",
                        event_id=env.trace_id,
                        task_id=env.task_id,
                        evidence={
                            "old_state": old_state,
                            "new_state": new_state,
                            "event_type": env.event_type,
                        },
                        trace_slice_start=start,
                        trace_slice_end=end,
                    )
                )
                task_state[env.task_id] = new_state
                continue

            allowed = VALID_TASK_TRANSITIONS.get(old_ts, set())
            if new_ts not in allowed:
                start, end = _slice_around(env.event_seq, seqs)
                violations.append(
                    InvariantViolation(
                        violation_id=_id("inv-viol"),
                        invariant_id="state.task_transition_legality",
                        severity="blocker",
                        event_id=env.trace_id,
                        task_id=env.task_id,
                        evidence={
                            "old_state": old_state,
                            "new_state": new_state,
                            "event_type": env.event_type,
                        },
                        trace_slice_start=start,
                        trace_slice_end=end,
                    )
                )

        task_state[env.task_id] = new_state

    return violations


def _check_step_attempt_transition_legality(
    envelopes: list[TraceEnvelope],
) -> list[InvariantViolation]:
    """state.step_attempt_transition_legality — step attempt states follow valid transitions."""
    violations: list[InvariantViolation] = []
    seqs = _all_seqs(envelopes)

    attempt_state: dict[str, str] = {}
    for env in envelopes:
        if env.event_type not in _ATTEMPT_STATE_EVENT_TYPES:
            continue
        new_state = _EVENT_TO_ATTEMPT_STATE.get(env.event_type)
        if new_state is None or env.step_attempt_id is None:
            continue

        old_state = attempt_state.get(env.step_attempt_id)
        if old_state is not None:
            from hermit.kernel.task.state.enums import StepAttemptState

            try:
                old_sa = StepAttemptState(old_state)
                new_sa = StepAttemptState(new_state)
            except ValueError:
                start, end = _slice_around(env.event_seq, seqs)
                violations.append(
                    InvariantViolation(
                        violation_id=_id("inv-viol"),
                        invariant_id="state.step_attempt_transition_legality",
                        severity="blocker",
                        event_id=env.trace_id,
                        task_id=env.task_id,
                        step_attempt_id=env.step_attempt_id,
                        evidence={
                            "old_state": old_state,
                            "new_state": new_state,
                            "event_type": env.event_type,
                        },
                        trace_slice_start=start,
                        trace_slice_end=end,
                    )
                )
                attempt_state[env.step_attempt_id] = new_state
                continue

            allowed = VALID_ATTEMPT_TRANSITIONS.get(old_sa, set())
            if new_sa not in allowed:
                start, end = _slice_around(env.event_seq, seqs)
                violations.append(
                    InvariantViolation(
                        violation_id=_id("inv-viol"),
                        invariant_id="state.step_attempt_transition_legality",
                        severity="blocker",
                        event_id=env.trace_id,
                        task_id=env.task_id,
                        step_attempt_id=env.step_attempt_id,
                        evidence={
                            "old_state": old_state,
                            "new_state": new_state,
                            "event_type": env.event_type,
                        },
                        trace_slice_start=start,
                        trace_slice_end=end,
                    )
                )

        attempt_state[env.step_attempt_id] = new_state

    return violations


def _check_workspace_lease_exclusive(
    envelopes: list[TraceEnvelope],
) -> list[InvariantViolation]:
    """isolation.workspace_lease_exclusive — same workspace has only one mutable holder per lease."""
    violations: list[InvariantViolation] = []
    seqs = _all_seqs(envelopes)

    # Track active leases per workspace: workspace_id -> list of (lease_ref, envelope)
    active_leases: dict[str, list[tuple[str, TraceEnvelope]]] = defaultdict(list)

    for env in envelopes:
        workspace_id = env.payload.get("workspace_id")
        if workspace_id is None:
            continue

        if env.event_type == "lease.acquired" and env.lease_ref:
            active_leases[workspace_id].append((env.lease_ref, env))

            # Check if multiple active leases exist
            if len(active_leases[workspace_id]) > 1:
                start, end = _slice_around(env.event_seq, seqs)
                lease_refs = [lr for lr, _ in active_leases[workspace_id]]
                violations.append(
                    InvariantViolation(
                        violation_id=_id("inv-viol"),
                        invariant_id="isolation.workspace_lease_exclusive",
                        severity="blocker",
                        event_id=env.trace_id,
                        task_id=env.task_id,
                        evidence={
                            "workspace_id": workspace_id,
                            "active_lease_refs": lease_refs,
                            "concurrent_count": len(lease_refs),
                        },
                        trace_slice_start=start,
                        trace_slice_end=end,
                    )
                )

        elif env.event_type == "lease.released" and env.lease_ref:
            if workspace_id in active_leases:
                active_leases[workspace_id] = [
                    (lr, e) for lr, e in active_leases[workspace_id] if lr != env.lease_ref
                ]

    return violations


def _check_authority_chain_complete(
    envelopes: list[TraceEnvelope],
) -> list[InvariantViolation]:
    """governance.authority_chain_complete — tool_call events have decision+grant+lease+receipt refs."""
    violations: list[InvariantViolation] = []
    seqs = _all_seqs(envelopes)

    for env in envelopes:
        if env.event_type != "tool_call.start":
            continue

        missing: list[str] = []
        if not env.decision_ref:
            missing.append("decision_ref")
        if not env.grant_ref:
            missing.append("grant_ref")
        if not env.lease_ref:
            missing.append("lease_ref")

        if missing:
            start, end = _slice_around(env.event_seq, seqs)
            violations.append(
                InvariantViolation(
                    violation_id=_id("inv-viol"),
                    invariant_id="governance.authority_chain_complete",
                    severity="blocker",
                    event_id=env.trace_id,
                    task_id=env.task_id,
                    step_attempt_id=env.step_attempt_id,
                    evidence={
                        "missing_refs": missing,
                        "event_type": env.event_type,
                        "step_id": env.step_id,
                    },
                    trace_slice_start=start,
                    trace_slice_end=end,
                )
            )

    return violations


def _check_side_effect_authorized(
    envelopes: list[TraceEnvelope],
) -> list[InvariantViolation]:
    """governance.side_effect_authorized — tool_call.start has approval_ref or grant_ref."""
    violations: list[InvariantViolation] = []
    seqs = _all_seqs(envelopes)

    for env in envelopes:
        if env.event_type != "tool_call.start":
            continue

        if not env.approval_ref and not env.grant_ref:
            start, end = _slice_around(env.event_seq, seqs)
            violations.append(
                InvariantViolation(
                    violation_id=_id("inv-viol"),
                    invariant_id="governance.side_effect_authorized",
                    severity="blocker",
                    event_id=env.trace_id,
                    task_id=env.task_id,
                    step_attempt_id=env.step_attempt_id,
                    evidence={
                        "event_type": env.event_type,
                        "has_approval_ref": bool(env.approval_ref),
                        "has_grant_ref": bool(env.grant_ref),
                        "step_id": env.step_id,
                    },
                    trace_slice_start=start,
                    trace_slice_end=end,
                )
            )

    return violations


def _check_receipt_for_mutation(
    envelopes: list[TraceEnvelope],
) -> list[InvariantViolation]:
    """governance.receipt_for_mutation — tool_call.start is followed by receipt.issued with matching step."""
    violations: list[InvariantViolation] = []
    seqs = _all_seqs(envelopes)

    # Collect tool_call.start events per step_attempt_id
    tool_starts: dict[str, TraceEnvelope] = {}
    receipted_attempts: set[str] = set()

    for env in envelopes:
        if env.event_type == "tool_call.start" and env.step_attempt_id:
            tool_starts[env.step_attempt_id] = env
        elif env.event_type == "receipt.issued" and env.step_attempt_id:
            receipted_attempts.add(env.step_attempt_id)

    for attempt_id, start_env in tool_starts.items():
        if attempt_id not in receipted_attempts:
            start, end = _slice_around(start_env.event_seq, seqs)
            violations.append(
                InvariantViolation(
                    violation_id=_id("inv-viol"),
                    invariant_id="governance.receipt_for_mutation",
                    severity="high",
                    event_id=start_env.trace_id,
                    task_id=start_env.task_id,
                    step_attempt_id=attempt_id,
                    evidence={
                        "step_attempt_id": attempt_id,
                        "tool_call_seq": start_env.event_seq,
                        "step_id": start_env.step_id,
                    },
                    trace_slice_start=start,
                    trace_slice_end=end,
                )
            )

    return violations


def _check_idempotent_reentry(
    envelopes: list[TraceEnvelope],
) -> list[InvariantViolation]:
    """restart.idempotent_reentry — no duplicate receipt_ref values across restart boundaries."""
    violations: list[InvariantViolation] = []
    seqs = _all_seqs(envelopes)

    seen_receipts: dict[str, TraceEnvelope] = {}
    for env in envelopes:
        if not env.receipt_ref:
            continue

        if env.receipt_ref in seen_receipts:
            prev = seen_receipts[env.receipt_ref]
            # Only flag if across different restart epochs
            if env.restart_epoch != prev.restart_epoch:
                start, end = _slice_around(env.event_seq, seqs)
                violations.append(
                    InvariantViolation(
                        violation_id=_id("inv-viol"),
                        invariant_id="restart.idempotent_reentry",
                        severity="blocker",
                        event_id=env.trace_id,
                        task_id=env.task_id,
                        step_attempt_id=env.step_attempt_id,
                        evidence={
                            "receipt_ref": env.receipt_ref,
                            "first_epoch": prev.restart_epoch,
                            "duplicate_epoch": env.restart_epoch,
                            "first_seq": prev.event_seq,
                            "duplicate_seq": env.event_seq,
                        },
                        trace_slice_start=start,
                        trace_slice_end=end,
                    )
                )
        else:
            seen_receipts[env.receipt_ref] = env

    return violations


def _check_hash_chain_continuity(
    envelopes: list[TraceEnvelope],
) -> list[InvariantViolation]:
    """trace.hash_chain_continuity — event_seq has no gaps within a run."""
    violations: list[InvariantViolation] = []
    seqs = _all_seqs(envelopes)

    # Group envelopes by run_id and check for gaps
    run_seqs: dict[str, list[TraceEnvelope]] = defaultdict(list)
    for env in envelopes:
        run_seqs[env.run_id].append(env)

    for run_id, run_envs in run_seqs.items():
        sorted_envs = sorted(run_envs, key=lambda e: e.event_seq)
        for i in range(1, len(sorted_envs)):
            prev = sorted_envs[i - 1]
            curr = sorted_envs[i]
            expected = prev.event_seq + 1
            if curr.event_seq != expected:
                start, end = _slice_around(curr.event_seq, seqs)
                violations.append(
                    InvariantViolation(
                        violation_id=_id("inv-viol"),
                        invariant_id="trace.hash_chain_continuity",
                        severity="high",
                        event_id=curr.trace_id,
                        task_id=curr.task_id,
                        evidence={
                            "run_id": run_id,
                            "expected_seq": expected,
                            "actual_seq": curr.event_seq,
                            "gap_size": curr.event_seq - expected,
                            "previous_seq": prev.event_seq,
                        },
                        trace_slice_start=start,
                        trace_slice_end=end,
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# Builtin spec definitions
# ---------------------------------------------------------------------------

_BUILTIN_SPECS: list[tuple[InvariantSpec, InvariantChecker]] = [
    (
        InvariantSpec(
            invariant_id="scheduler.single_winner_per_task",
            scope="scheduler",
            detection_method="claim_projection",
            severity="blocker",
            evidence_fields=["step_attempt_id", "actors", "claim_count"],
            remediation_hint="Check dispatch deduplication logic",
        ),
        _check_single_winner_per_task,
    ),
    (
        InvariantSpec(
            invariant_id="scheduler.total_order_per_task",
            scope="scheduler",
            detection_method="sequence_scan",
            severity="blocker",
            evidence_fields=["previous_seq", "current_seq", "event_type"],
            remediation_hint="Check event sequencing and ledger append order",
        ),
        _check_total_order_per_task,
    ),
    (
        InvariantSpec(
            invariant_id="state.task_transition_legality",
            scope="task_state_machine",
            detection_method="state_projection",
            severity="blocker",
            evidence_fields=["task_id", "old_state", "new_state"],
            remediation_hint="Check task controller and state validator",
        ),
        _check_task_transition_legality,
    ),
    (
        InvariantSpec(
            invariant_id="state.step_attempt_transition_legality",
            scope="step_attempt_state_machine",
            detection_method="state_projection",
            severity="blocker",
            evidence_fields=["step_attempt_id", "old_state", "new_state"],
            remediation_hint="Check step attempt state machine and transition guards",
        ),
        _check_step_attempt_transition_legality,
    ),
    (
        InvariantSpec(
            invariant_id="isolation.workspace_lease_exclusive",
            scope="isolation",
            detection_method="lease_projection",
            severity="blocker",
            evidence_fields=["workspace_id", "active_lease_refs", "concurrent_count"],
            remediation_hint="Check WorkspaceLeaseService for concurrent lease prevention",
        ),
        _check_workspace_lease_exclusive,
    ),
    (
        InvariantSpec(
            invariant_id="governance.authority_chain_complete",
            scope="governance",
            detection_method="ref_completeness",
            severity="blocker",
            evidence_fields=["missing_refs", "event_type", "step_id"],
            remediation_hint="Ensure all tool calls flow through governed execution pipeline",
        ),
        _check_authority_chain_complete,
    ),
    (
        InvariantSpec(
            invariant_id="governance.side_effect_authorized",
            scope="governance",
            detection_method="ref_presence",
            severity="blocker",
            evidence_fields=["event_type", "has_approval_ref", "has_grant_ref"],
            remediation_hint="Ensure tool_call.start events carry approval_ref or grant_ref",
        ),
        _check_side_effect_authorized,
    ),
    (
        InvariantSpec(
            invariant_id="governance.receipt_for_mutation",
            scope="governance",
            detection_method="event_pairing",
            severity="high",
            evidence_fields=["step_attempt_id", "tool_call_seq", "step_id"],
            remediation_hint="Ensure receipt.issued follows every tool_call.start",
        ),
        _check_receipt_for_mutation,
    ),
    (
        InvariantSpec(
            invariant_id="restart.idempotent_reentry",
            scope="restart",
            detection_method="receipt_dedup",
            severity="blocker",
            evidence_fields=[
                "receipt_ref",
                "first_epoch",
                "duplicate_epoch",
            ],
            remediation_hint="Check restart-epoch deduplication in receipt service",
        ),
        _check_idempotent_reentry,
    ),
    (
        InvariantSpec(
            invariant_id="trace.hash_chain_continuity",
            scope="trace",
            detection_method="sequence_scan",
            severity="high",
            evidence_fields=["run_id", "expected_seq", "actual_seq", "gap_size"],
            remediation_hint="Check event sequencing; possible event loss or reorder",
        ),
        _check_hash_chain_continuity,
    ),
]


# ---------------------------------------------------------------------------
# InvariantEngine
# ---------------------------------------------------------------------------


class InvariantEngine:
    """Registry and executor for trace invariant checkers.

    Pre-registers 10 built-in invariants covering scheduler, state-machine,
    isolation, governance, restart, and trace-level properties.  Custom
    invariants can be added via :meth:`register`.
    """

    def __init__(self) -> None:
        self._invariants: dict[str, InvariantSpec] = {}
        self._checkers: dict[str, InvariantChecker] = {}
        self._register_builtins()

    # -- public API --------------------------------------------------------

    def register(
        self,
        spec: InvariantSpec,
        checker: Callable[[list[TraceEnvelope]], list[InvariantViolation]],
    ) -> None:
        """Register a new invariant spec and its checker function."""
        if spec.invariant_id in self._invariants:
            logger.warning(
                "invariant_overwrite",
                invariant_id=spec.invariant_id,
            )
        self._invariants[spec.invariant_id] = spec
        self._checkers[spec.invariant_id] = checker

    def check(
        self,
        envelopes: list[TraceEnvelope],
        *,
        task_id: str | None = None,
    ) -> list[InvariantViolation]:
        """Run all registered invariants and return violations sorted by event_seq."""
        if task_id is not None:
            envelopes = [e for e in envelopes if e.task_id == task_id]

        all_violations: list[InvariantViolation] = []
        for inv_id, checker in self._checkers.items():
            try:
                violations = checker(envelopes)
                all_violations.extend(violations)
            except Exception:
                logger.exception("invariant_checker_error", invariant_id=inv_id)

        # Sort by trace_slice_start (proxy for event_seq of violation)
        all_violations.sort(key=lambda v: v.trace_slice_start)
        return all_violations

    def check_single(
        self,
        invariant_id: str,
        envelopes: list[TraceEnvelope],
    ) -> list[InvariantViolation]:
        """Run a single invariant checker by ID."""
        checker = self._checkers.get(invariant_id)
        if checker is None:
            raise KeyError(f"Unknown invariant: {invariant_id!r}")
        return checker(envelopes)

    def first_violation(
        self,
        envelopes: list[TraceEnvelope],
    ) -> InvariantViolation | None:
        """Return the earliest violation across all invariants, or None."""
        violations = self.check(envelopes)
        return violations[0] if violations else None

    @property
    def invariant_ids(self) -> list[str]:
        """Return sorted list of registered invariant IDs."""
        return sorted(self._invariants.keys())

    # -- internals ---------------------------------------------------------

    def _register_builtins(self) -> None:
        """Register all built-in invariant checkers."""
        for spec, checker in _BUILTIN_SPECS:
            self.register(spec, checker)
