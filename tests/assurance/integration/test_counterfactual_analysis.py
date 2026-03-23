"""Integration tests: counterfactual analysis and replay.

Validates that counterfactual replay can identify root causes by
mutating traces and observing how violations change.
"""

from __future__ import annotations

import hashlib
import time
import uuid

from dataclasses import replace

from hermit.kernel.verification.assurance.contracts import AssuranceContractEngine
from hermit.kernel.verification.assurance.invariants import InvariantEngine
from hermit.kernel.verification.assurance.models import (
    ContractViolation,
    CounterfactualMutation,
    EvidenceRetention,
    InvariantViolation,
    TraceEnvelope,
)
from hermit.kernel.verification.assurance.replay import ReplayService
from tests.assurance.conftest import make_envelope, make_governed_trace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _make_denied_trace(
    *,
    run_id: str = "run-denied",
    task_id: str = "task-denied",
) -> list[TraceEnvelope]:
    """Create a trace where approval is denied instead of granted.

    The tool_call.start still proceeds (simulating a governance violation
    scenario), so the trace has: task.created -> approval.requested ->
    approval.denied -> tool_call.start -> receipt.issued -> task.completed.
    """
    now = time.time()
    approval_ref = _uid("approval")
    decision_ref = _uid("decision")
    grant_ref = _uid("grant")
    lease_ref = _uid("lease")
    receipt_ref = _uid("receipt")

    return [
        make_envelope(
            run_id=run_id, task_id=task_id, event_type="task.created",
            event_seq=0, wallclock_at=now,
        ),
        make_envelope(
            run_id=run_id, task_id=task_id, event_type="approval.requested",
            event_seq=1, wallclock_at=now + 0.001,
            step_id="step-0", step_attempt_id="attempt-0",
            approval_ref=approval_ref,
        ),
        make_envelope(
            run_id=run_id, task_id=task_id, event_type="approval.denied",
            event_seq=2, wallclock_at=now + 0.002,
            step_id="step-0", step_attempt_id="attempt-0",
            approval_ref=approval_ref, decision_ref=decision_ref,
        ),
        make_envelope(
            run_id=run_id, task_id=task_id, event_type="tool_call.start",
            event_seq=3, wallclock_at=now + 0.003,
            step_id="step-0", step_attempt_id="attempt-0",
            grant_ref=grant_ref, lease_ref=lease_ref,
            decision_ref=decision_ref, approval_ref=approval_ref,
        ),
        make_envelope(
            run_id=run_id, task_id=task_id, event_type="receipt.issued",
            event_seq=4, wallclock_at=now + 0.004,
            step_id="step-0", step_attempt_id="attempt-0",
            receipt_ref=receipt_ref, grant_ref=grant_ref,
            lease_ref=lease_ref, decision_ref=decision_ref,
        ),
        make_envelope(
            run_id=run_id, task_id=task_id, event_type="task.completed",
            event_seq=5, wallclock_at=now + 0.005,
        ),
    ]


def _collect_violation_ids(
    envelopes: list[TraceEnvelope],
    *,
    invariant_engine: InvariantEngine | None = None,
    contract_engine: AssuranceContractEngine | None = None,
) -> tuple[set[str], set[str]]:
    """Run assurance checks and return (contract_ids, invariant_ids) with violations."""
    ie = invariant_engine or InvariantEngine()
    ce = contract_engine or AssuranceContractEngine()

    inv_violations = ie.check(envelopes)
    contract_violations = ce.evaluate_post_run(envelopes)

    # Also run runtime checks with prior context for approval.gating
    prior: list[TraceEnvelope] = []
    for env in envelopes:
        per_env = ce.evaluate_runtime(env, context={"prior_envelopes": prior})
        contract_violations.extend(per_env)
        prior.append(env)

    contract_ids = {v.contract_id for v in contract_violations}
    invariant_ids = {v.invariant_id for v in inv_violations}
    return contract_ids, invariant_ids


def _has_any_blocker_or_high(
    envelopes: list[TraceEnvelope],
    *,
    invariant_engine: InvariantEngine | None = None,
    contract_engine: AssuranceContractEngine | None = None,
) -> bool:
    """Return True if the trace has any blocker or high severity violations."""
    ie = invariant_engine or InvariantEngine()
    ce = contract_engine or AssuranceContractEngine()

    violations: list[ContractViolation | InvariantViolation] = []
    violations.extend(ie.check(envelopes))
    violations.extend(ce.evaluate_post_run(envelopes))

    prior: list[TraceEnvelope] = []
    for env in envelopes:
        violations.extend(ce.evaluate_runtime(env, context={"prior_envelopes": prior}))
        prior.append(env)

    return any(v.severity in ("blocker", "high") for v in violations)


# ---------------------------------------------------------------------------
# TestToggleApproval
# ---------------------------------------------------------------------------


class TestToggleApproval:
    """Toggle approval.granted <-> approval.denied."""

    def test_denying_approval_introduces_violation(self) -> None:
        """Clean trace -> toggle approval to denied -> new violations appear."""
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()
        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="toggle-deny",
        )

        # Find the approval.granted envelope
        granted_env = next(e for e in envelopes if e.event_type == "approval.granted")

        # Verify the original trace is clean
        contract_ids_before, _ = _collect_violation_ids(envelopes)
        assert "approval.gating" not in contract_ids_before

        # Toggle approval.granted -> approval.denied
        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="toggle_approval",
                target_ref=granted_env.trace_id,
                description="Toggle approval.granted -> approval.denied",
            ),
        ]

        result = replay_service.counterfactual(entry, envelopes, mutations)
        assert result.diff_summary["diverged"] >= 1

        # Reconstruct the mutated trace and check for violations
        mutated = list(envelopes)
        mutated = replay_service._apply_mutation(mutated, mutations[0])

        # The mutated trace should have the approval.gating violation
        contract_ids_after, _ = _collect_violation_ids(mutated)
        assert "approval.gating" in contract_ids_after

    def test_granting_denied_approval_removes_violation(self) -> None:
        """Bad trace (approval.denied) -> toggle to granted -> violations disappear."""
        envelopes = _make_denied_trace()
        replay_service = ReplayService()
        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="toggle-grant",
        )

        # Verify original trace has approval.gating violation
        contract_ids_before, _ = _collect_violation_ids(envelopes)
        assert "approval.gating" in contract_ids_before

        # Find the approval.denied envelope
        denied_env = next(e for e in envelopes if e.event_type == "approval.denied")

        # Toggle approval.denied -> approval.granted
        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="toggle_approval",
                target_ref=denied_env.trace_id,
                description="Toggle approval.denied -> approval.granted",
            ),
        ]

        result = replay_service.counterfactual(entry, envelopes, mutations)
        assert result.diff_summary["diverged"] >= 1

        # Reconstruct mutated trace
        mutated = list(envelopes)
        mutated = replay_service._apply_mutation(mutated, mutations[0])

        # Verify the toggled event is now approval.granted
        toggled = next(e for e in mutated if e.trace_id == denied_env.trace_id)
        assert toggled.event_type == "approval.granted"

        # The mutated trace should NOT have the approval.gating violation
        contract_ids_after, _ = _collect_violation_ids(mutated)
        assert "approval.gating" not in contract_ids_after

    def test_identifies_approval_as_root_cause(self) -> None:
        """Counterfactual proves approval is the root cause of downstream failures.

        If toggling a single event (approval) eliminates the violation while
        all other events remain unchanged, that event is the root cause.
        """
        envelopes = _make_denied_trace()
        replay_service = ReplayService()
        ie = InvariantEngine()
        ce = AssuranceContractEngine()

        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="root-cause",
        )

        # Original has violations
        original_contract_ids, original_inv_ids = _collect_violation_ids(
            envelopes, invariant_engine=ie, contract_engine=ce,
        )
        assert "approval.gating" in original_contract_ids

        # Apply counterfactual: toggle the denied approval
        denied_env = next(e for e in envelopes if e.event_type == "approval.denied")
        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="toggle_approval",
                target_ref=denied_env.trace_id,
            ),
        ]

        # Use counterfactual_with_assurance for full checking
        cf_result = replay_service.counterfactual_with_assurance(
            entry, envelopes, mutations,
            invariant_engine=ie, contract_engine=ce,
        )

        # The counterfactual result should have fewer violations than original
        # because toggling approval.denied -> approval.granted fixes approval.gating
        cf_contract_ids = {
            v.contract_id
            for v in cf_result.contract_violations
            if isinstance(v, ContractViolation)
        }
        assert "approval.gating" not in cf_contract_ids

        # Only 1 mutation was needed, and it was on the approval event
        assert len(cf_result.mutations) == 1
        assert cf_result.mutations[0].mutation_type == "toggle_approval"

        # The diff shows exactly one diverged event (the toggled approval)
        assert cf_result.diff_summary["diverged"] == 1


# ---------------------------------------------------------------------------
# TestDropEvent
# ---------------------------------------------------------------------------


class TestDropEvent:
    """Remove events from trace."""

    def test_dropping_receipt_causes_violation(self) -> None:
        """Drop receipt.issued -> receipt_for_mutation invariant violation appears."""
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()
        ie = InvariantEngine()

        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="drop-receipt",
        )

        # Verify original has no receipt_for_mutation violation
        original_violations = ie.check(envelopes)
        original_inv_ids = {v.invariant_id for v in original_violations}
        assert "governance.receipt_for_mutation" not in original_inv_ids

        # Find and drop the receipt.issued envelope
        receipt_env = next(e for e in envelopes if e.event_type == "receipt.issued")
        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="drop_event",
                target_ref=receipt_env.trace_id,
                description="Drop receipt.issued to break receipt linkage",
            ),
        ]

        result = replay_service.counterfactual(entry, envelopes, mutations)
        assert receipt_env.trace_id in result.diff_summary["missing"]

        # Check the mutated trace for invariant violations
        mutated = list(envelopes)
        mutated = replay_service._apply_mutation(mutated, mutations[0])

        mutated_violations = ie.check(mutated)
        mutated_inv_ids = {v.invariant_id for v in mutated_violations}

        # Dropping the receipt should cause governance.receipt_for_mutation
        assert "governance.receipt_for_mutation" in mutated_inv_ids

    def test_dropping_approval_causes_violation(self) -> None:
        """Drop approval.granted -> approval.gating contract violation."""
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()
        ce = AssuranceContractEngine()

        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="drop-approval",
        )

        # Verify original passes approval.gating
        contract_ids_before, _ = _collect_violation_ids(
            envelopes, contract_engine=ce,
        )
        assert "approval.gating" not in contract_ids_before

        # Find and drop the approval.granted envelope
        granted_env = next(e for e in envelopes if e.event_type == "approval.granted")
        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="drop_event",
                target_ref=granted_env.trace_id,
                description="Drop approval.granted to break gating",
            ),
        ]

        result = replay_service.counterfactual(entry, envelopes, mutations)
        assert granted_env.trace_id in result.diff_summary["missing"]

        # Check the mutated trace
        mutated = list(envelopes)
        mutated = replay_service._apply_mutation(mutated, mutations[0])

        contract_ids_after, _ = _collect_violation_ids(
            mutated, contract_engine=ce,
        )
        # Without approval.granted, the runtime check on tool_call.start will fail
        assert "approval.gating" in contract_ids_after


# ---------------------------------------------------------------------------
# TestReplaceEvent
# ---------------------------------------------------------------------------


class TestReplaceEvent:
    """Replace event content."""

    def test_replacing_grant_ref_with_none_causes_violation(self) -> None:
        """Replace tool_call.start's grant_ref with None -> authorization violation."""
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()
        ie = InvariantEngine()
        ce = AssuranceContractEngine()

        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="replace-grant",
        )

        # Verify original passes side_effect.authorization and authority_chain_complete
        contract_ids_before, inv_ids_before = _collect_violation_ids(
            envelopes, invariant_engine=ie, contract_engine=ce,
        )
        assert "side_effect.authorization" not in contract_ids_before
        assert "governance.authority_chain_complete" not in inv_ids_before

        # Find tool_call.start and replace its grant_ref with None
        tool_env = next(e for e in envelopes if e.event_type == "tool_call.start")
        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="replace_event",
                target_ref=tool_env.trace_id,
                replacement={"grant_ref": None},
                description="Remove grant_ref from tool_call.start",
            ),
        ]

        result = replay_service.counterfactual(entry, envelopes, mutations)
        assert result.diff_summary["diverged"] >= 1

        # Check the mutated trace
        mutated = list(envelopes)
        mutated = replay_service._apply_mutation(mutated, mutations[0])

        # Verify the grant_ref was removed
        mutated_tool = next(e for e in mutated if e.event_type == "tool_call.start")
        assert mutated_tool.grant_ref is None

        # Check for violations
        contract_ids_after, inv_ids_after = _collect_violation_ids(
            mutated, invariant_engine=ie, contract_engine=ce,
        )
        # side_effect.authorization requires grant_ref on tool_call.start
        assert "side_effect.authorization" in contract_ids_after
        # governance.authority_chain_complete also requires grant_ref
        assert "governance.authority_chain_complete" in inv_ids_after


# ---------------------------------------------------------------------------
# TestAdvanceRestartEpoch
# ---------------------------------------------------------------------------


class TestAdvanceRestartEpoch:
    """Simulate restart mid-execution."""

    def test_restart_epoch_advances_downstream(self) -> None:
        """Advance restart_epoch at tool_call.start -> all subsequent events
        have incremented epoch."""
        envelopes = make_governed_trace(num_steps=2)
        replay_service = ReplayService()

        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="restart-epoch",
        )

        # All original envelopes should have restart_epoch=0
        assert all(e.restart_epoch == 0 for e in envelopes)

        # Find the first tool_call.start
        tool_env = next(e for e in envelopes if e.event_type == "tool_call.start")
        tool_idx = next(
            i for i, e in enumerate(envelopes) if e.trace_id == tool_env.trace_id
        )

        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="advance_restart_epoch",
                target_ref=tool_env.trace_id,
                description="Simulate restart at tool_call.start",
            ),
        ]

        result = replay_service.counterfactual(entry, envelopes, mutations)

        # Apply mutation to get the mutated trace
        mutated = list(envelopes)
        mutated = replay_service._apply_mutation(mutated, mutations[0])

        # Events before the target should still be epoch 0
        for i in range(tool_idx):
            assert mutated[i].restart_epoch == 0, (
                f"Event at index {i} should still have epoch 0"
            )

        # Events from the target onward should have epoch 1
        for i in range(tool_idx, len(mutated)):
            assert mutated[i].restart_epoch == 1, (
                f"Event at index {i} should have epoch 1"
            )

        # The diff should show diverged events for all modified envelopes
        assert result.diff_summary["diverged"] == len(envelopes) - tool_idx

    def test_restart_does_not_duplicate_receipt(self) -> None:
        """After restart, ensure no duplicate receipt violations appear
        as long as receipt_refs are unique."""
        envelopes = make_governed_trace(num_steps=2)
        replay_service = ReplayService()
        ie = InvariantEngine()

        # Find the first tool_call.start
        tool_env = next(e for e in envelopes if e.event_type == "tool_call.start")

        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="advance_restart_epoch",
                target_ref=tool_env.trace_id,
            ),
        ]

        mutated = list(envelopes)
        mutated = replay_service._apply_mutation(mutated, mutations[0])

        # Check that the restart.idempotent_reentry invariant passes
        # because each receipt_ref is still unique (just at different epochs)
        violations = ie.check_single("restart.idempotent_reentry", mutated)
        assert len(violations) == 0, (
            "Unique receipt_refs across restart epochs should not trigger "
            "idempotent_reentry violations"
        )


# ---------------------------------------------------------------------------
# TestReplayDiff
# ---------------------------------------------------------------------------


class TestReplayDiff:
    """Verify diff_traces categorization."""

    def test_identical_traces_all_same(self) -> None:
        """Replay same trace -> all 'same', no divergences."""
        envelopes = make_governed_trace(num_steps=2)
        replay_service = ReplayService()

        diff = replay_service.diff_traces(envelopes, envelopes)

        assert diff["same"] == len(envelopes)
        assert diff["diverged"] == 0
        assert diff["missing"] == []
        assert diff["extra"] == []
        assert diff["reordered"] == 0
        assert diff["delayed"] == 0
        assert diff["propagated"] == 0

    def test_dropped_event_shows_missing(self) -> None:
        """Drop one event -> shows in 'missing' list."""
        envelopes = make_governed_trace(num_steps=2)
        replay_service = ReplayService()

        # Drop the third envelope (approval.granted for step-0)
        dropped_id = envelopes[2].trace_id
        replayed = [e for e in envelopes if e.trace_id != dropped_id]

        diff = replay_service.diff_traces(envelopes, replayed)

        assert diff["missing"] == [dropped_id]
        assert diff["same"] == len(envelopes) - 1
        assert diff["diverged"] == 0
        assert diff["extra"] == []

    def test_toggled_event_shows_diverged(self) -> None:
        """Toggle approval type -> shows as 'diverged'."""
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()

        # Find approval.granted and toggle it
        granted_env = next(e for e in envelopes if e.event_type == "approval.granted")
        replayed = [
            replace(e, event_type="approval.denied")
            if e.trace_id == granted_env.trace_id
            else e
            for e in envelopes
        ]

        diff = replay_service.diff_traces(envelopes, replayed)

        assert diff["diverged"] == 1
        assert diff["same"] == len(envelopes) - 1
        assert diff["missing"] == []
        assert diff["extra"] == []

    def test_extra_event_shows_extra(self) -> None:
        """Add an event not in original -> shows in 'extra' list."""
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()

        extra_env = make_envelope(
            run_id=envelopes[0].run_id,
            task_id=envelopes[0].task_id,
            event_type="recovery.started",
            event_seq=99,
        )
        replayed = envelopes + [extra_env]

        diff = replay_service.diff_traces(envelopes, replayed)

        assert extra_env.trace_id in diff["extra"]
        assert diff["same"] == len(envelopes)
        assert diff["missing"] == []

    def test_reordered_events_detected(self) -> None:
        """Swap two events -> shows reordered count."""
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()

        # Swap envelope at index 1 and index 2
        replayed = list(envelopes)
        replayed[1], replayed[2] = replayed[2], replayed[1]

        diff = replay_service.diff_traces(envelopes, replayed)

        # Both swapped events are at different positions
        assert diff["reordered"] >= 2

    def test_delayed_events_detected(self) -> None:
        """Later wallclock_at in replay -> shows delayed count."""
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()

        # Delay every event by 10 seconds
        replayed = [replace(e, wallclock_at=e.wallclock_at + 10.0) for e in envelopes]

        diff = replay_service.diff_traces(envelopes, replayed)

        assert diff["delayed"] == len(envelopes)

    def test_recovered_events_counted(self) -> None:
        """Replay with recovery events -> 'recovered' count."""
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()

        # Add recovery events to the replayed trace
        recovery_env = make_envelope(
            run_id=envelopes[0].run_id,
            task_id=envelopes[0].task_id,
            event_type="recovery.started",
            event_seq=50,
        )
        resolved_env = make_envelope(
            run_id=envelopes[0].run_id,
            task_id=envelopes[0].task_id,
            event_type="reconciliation.resolved",
            event_seq=51,
        )
        replayed = envelopes + [recovery_env, resolved_env]

        diff = replay_service.diff_traces(envelopes, replayed)

        assert diff["recovered"] == 2


# ---------------------------------------------------------------------------
# TestMultipleMutations
# ---------------------------------------------------------------------------


class TestMultipleMutations:
    """Apply multiple mutations in one counterfactual."""

    def test_combined_mutations_compound_violations(self) -> None:
        """Drop approval.granted + drop receipt.issued -> both violations appear."""
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()
        ie = InvariantEngine()
        ce = AssuranceContractEngine()

        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="combined",
        )

        # Verify original is clean
        assert not _has_any_blocker_or_high(
            envelopes, invariant_engine=ie, contract_engine=ce,
        )

        # Find the approval.granted and receipt.issued envelopes
        granted_env = next(e for e in envelopes if e.event_type == "approval.granted")
        receipt_env = next(e for e in envelopes if e.event_type == "receipt.issued")

        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="drop_event",
                target_ref=granted_env.trace_id,
                description="Drop approval.granted",
            ),
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="drop_event",
                target_ref=receipt_env.trace_id,
                description="Drop receipt.issued",
            ),
        ]

        # Apply both mutations
        result = replay_service.counterfactual(entry, envelopes, mutations)

        # Both should appear in missing
        assert granted_env.trace_id in result.diff_summary["missing"]
        assert receipt_env.trace_id in result.diff_summary["missing"]

        # Reconstruct mutated trace
        mutated = list(envelopes)
        for m in mutations:
            mutated = replay_service._apply_mutation(mutated, m)

        # Check for both categories of violations
        contract_ids, inv_ids = _collect_violation_ids(
            mutated, invariant_engine=ie, contract_engine=ce,
        )

        # approval.gating should fail (no approval.granted before tool_call.start)
        assert "approval.gating" in contract_ids
        # receipt_for_mutation should fail (tool_call.start without matching receipt)
        assert "governance.receipt_for_mutation" in inv_ids

    def test_mutation_order_matters(self) -> None:
        """Applying mutations in different order may produce different results.

        When a drop_event removes the target of a subsequent toggle_approval,
        the toggle becomes a no-op (target not found). Reversing the order
        means the toggle fires first, then the drop removes it.
        """
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()

        granted_env = next(e for e in envelopes if e.event_type == "approval.granted")

        # Order A: toggle first, then drop (toggle changes type, then event is removed)
        mutations_a = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="toggle_approval",
                target_ref=granted_env.trace_id,
            ),
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="drop_event",
                target_ref=granted_env.trace_id,
            ),
        ]

        # Order B: drop first, then toggle (drop removes event, toggle is no-op)
        mutations_b = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="drop_event",
                target_ref=granted_env.trace_id,
            ),
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="toggle_approval",
                target_ref=granted_env.trace_id,
            ),
        ]

        # Both should produce the same final result in this case (event removed),
        # but the intermediate states differ:
        # A: toggle fires (granted->denied), then drop removes the denied event
        # B: drop removes the granted event, toggle finds nothing (no-op)
        mutated_a = list(envelopes)
        for m in mutations_a:
            mutated_a = replay_service._apply_mutation(mutated_a, m)

        mutated_b = list(envelopes)
        for m in mutations_b:
            mutated_b = replay_service._apply_mutation(mutated_b, m)

        # Both orderings remove the approval event, so final trace length is the same
        assert len(mutated_a) == len(mutated_b)
        assert len(mutated_a) == len(envelopes) - 1

        # Verify neither trace contains the original approval event
        assert all(e.trace_id != granted_env.trace_id for e in mutated_a)
        assert all(e.trace_id != granted_env.trace_id for e in mutated_b)


# ---------------------------------------------------------------------------
# TestReplayCorpusManagement
# ---------------------------------------------------------------------------


class TestReplayCorpusManagement:
    """Ingest and replay from corpus."""

    def test_ingest_creates_entry_with_hash(self) -> None:
        """Ingesting a trace creates a ReplayEntry with a valid head hash."""
        envelopes = make_governed_trace(num_steps=2)
        replay_service = ReplayService()

        entry = replay_service.ingest(
            envelopes[0].run_id,
            envelopes,
            scenario_id="ingest-hash-test",
        )

        # Entry should have a non-empty entry_id and head hash
        assert entry.entry_id.startswith("replay-")
        assert entry.scenario_id == "ingest-hash-test"
        assert entry.run_id == envelopes[0].run_id
        assert entry.sanitized is False

        # Head hash should be SHA-256 of the last envelope's trace_id
        expected_hash = hashlib.sha256(
            envelopes[-1].trace_id.encode()
        ).hexdigest()
        assert entry.event_head_hash == expected_hash

        # Entry should be stored in the corpus
        assert entry.entry_id in replay_service._corpus

    def test_replay_validates_head_hash(self) -> None:
        """Replaying the same trace validates that head hashes match."""
        envelopes = make_governed_trace(num_steps=2)
        replay_service = ReplayService()

        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="hash-validate",
        )

        result = replay_service.replay(entry, envelopes)

        # Head hash should match since we replay the same trace
        assert result.diff_summary["head_hash_match"] is True
        assert result.diff_summary["schema_version_match"] is True

        # trace_path should contain all trace_ids
        assert result.trace_path == [e.trace_id for e in envelopes]

    def test_replay_detects_head_hash_mismatch(self) -> None:
        """Replaying a different trace produces a head hash mismatch."""
        envelopes = make_governed_trace(num_steps=2, run_id="run-orig")
        replay_service = ReplayService()

        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="hash-mismatch",
        )

        # Create a different trace (different trace_ids -> different head hash)
        different_envelopes = make_governed_trace(num_steps=2, run_id="run-diff")

        result = replay_service.replay(entry, different_envelopes)

        # Head hash should NOT match since the last envelope's trace_id differs
        assert result.diff_summary["head_hash_match"] is False

    def test_sanitize_removes_sensitive_fields(self) -> None:
        """Sanitize trace removes fields listed in retention policy."""
        now = time.time()
        envelopes = [
            make_envelope(
                run_id="run-sanitize",
                task_id="task-sanitize",
                event_type="tool_call.start",
                event_seq=0,
                wallclock_at=now,
                payload={
                    "prompt_text": "secret prompt content",
                    "secret_values": {"api_key": "sk-12345"},
                    "tool_name": "bash",
                    "action_class": "write_local",
                },
            ),
            make_envelope(
                run_id="run-sanitize",
                task_id="task-sanitize",
                event_type="receipt.issued",
                event_seq=1,
                wallclock_at=now + 0.001,
                payload={
                    "prompt_text": "another secret",
                    "result_code": "success",
                },
            ),
        ]

        replay_service = ReplayService()
        retention = EvidenceRetention(
            redact_fields=["prompt_text", "secret_values"],
        )

        sanitized = replay_service.sanitize_trace(envelopes, retention)

        # Sensitive fields should be removed from payloads
        for env in sanitized:
            assert "prompt_text" not in env.payload
            assert "secret_values" not in env.payload

        # Non-sensitive fields should be preserved
        assert sanitized[0].payload["tool_name"] == "bash"
        assert sanitized[0].payload["action_class"] == "write_local"
        assert sanitized[1].payload["result_code"] == "success"

        # Original envelopes should NOT be mutated
        assert "prompt_text" in envelopes[0].payload
        assert "secret_values" in envelopes[0].payload

    def test_ingest_with_sanitize(self) -> None:
        """Ingesting with sanitize=True uses the retention policy."""
        envelopes = [
            make_envelope(
                run_id="run-san-ingest",
                task_id="task-san-ingest",
                event_type="generic",
                event_seq=0,
                payload={"prompt_text": "sensitive", "safe_field": "ok"},
            ),
        ]

        replay_service = ReplayService()
        retention = EvidenceRetention(redact_fields=["prompt_text"])

        entry = replay_service.ingest(
            "run-san-ingest",
            envelopes,
            scenario_id="sanitize-ingest",
            sanitize=True,
            retention=retention,
        )

        assert entry.sanitized is True
        # Entry should still be created with correct hash
        expected_hash = hashlib.sha256(
            envelopes[-1].trace_id.encode()
        ).hexdigest()
        assert entry.event_head_hash == expected_hash

    def test_ingest_empty_trace_raises(self) -> None:
        """Ingesting an empty trace raises ValueError."""
        replay_service = ReplayService()
        try:
            replay_service.ingest("run-empty", [], scenario_id="empty")
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "empty" in str(exc).lower()

    def test_replay_empty_trace_raises(self) -> None:
        """Replaying an empty trace raises ValueError."""
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()
        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="replay-empty",
        )

        try:
            replay_service.replay(entry, [])
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "empty" in str(exc).lower()


# ---------------------------------------------------------------------------
# TestCounterfactualWithAssurance
# ---------------------------------------------------------------------------


class TestCounterfactualWithAssurance:
    """End-to-end counterfactual_with_assurance tests."""

    def test_clean_trace_counterfactual_remains_clean(self) -> None:
        """Applying a no-op mutation to a clean trace keeps it clean."""
        envelopes = make_governed_trace(num_steps=1)
        replay_service = ReplayService()
        ie = InvariantEngine()
        ce = AssuranceContractEngine()

        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="noop",
        )

        # Use a mutation targeting a non-existent trace_id (becomes no-op)
        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="drop_event",
                target_ref="nonexistent-trace-id",
                description="This targets nothing and is a no-op",
            ),
        ]

        result = replay_service.counterfactual_with_assurance(
            entry, envelopes, mutations,
            invariant_engine=ie, contract_engine=ce,
        )

        # No violations should be introduced by a no-op mutation
        # (the assurance checks should still pass on the unmodified trace)
        blocker_violations = [
            v for v in result.contract_violations
            if v.severity in ("blocker", "high")
        ]
        assert len(blocker_violations) == 0

    def test_drop_all_approvals_cascades_violations(self) -> None:
        """Dropping all approval.granted events causes cascading violations.

        counterfactual_with_assurance runs invariant checks and post_run contract
        checks on the mutated trace. Dropping approval.granted events introduces
        sequence gaps (trace.hash_chain_continuity invariant violation). We also
        verify that separately running runtime contract checks on the mutated
        trace reveals approval.gating violations.
        """
        envelopes = make_governed_trace(num_steps=2)
        replay_service = ReplayService()
        ie = InvariantEngine()
        ce = AssuranceContractEngine()

        entry = replay_service.ingest(
            envelopes[0].run_id, envelopes, scenario_id="cascade",
        )

        # Drop all approval.granted events
        granted_envs = [e for e in envelopes if e.event_type == "approval.granted"]
        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="drop_event",
                target_ref=env.trace_id,
                description=f"Drop approval.granted for {env.step_id}",
            )
            for env in granted_envs
        ]

        result = replay_service.counterfactual_with_assurance(
            entry, envelopes, mutations,
            invariant_engine=ie, contract_engine=ce,
        )

        # The diff should show the dropped events
        assert len(result.diff_summary["missing"]) == len(granted_envs)

        # counterfactual_with_assurance runs invariant + post_run checks.
        # Dropping events creates seq gaps -> trace.hash_chain_continuity
        violation_ids = {
            v.contract_id
            for v in result.contract_violations
            if isinstance(v, ContractViolation)
        }
        invariant_ids = {
            v.invariant_id
            for v in result.contract_violations
            if isinstance(v, InvariantViolation)  # type: ignore[arg-type]
        }
        # hash_chain_continuity catches the seq gap from dropped events
        assert "trace.hash_chain_continuity" in (violation_ids | invariant_ids)

        # Separately verify that runtime checks on the mutated trace
        # detect approval.gating violations
        mutated = list(envelopes)
        for m in mutations:
            mutated = replay_service._apply_mutation(mutated, m)

        contract_ids, _ = _collect_violation_ids(
            mutated, invariant_engine=ie, contract_engine=ce,
        )
        assert "approval.gating" in contract_ids
