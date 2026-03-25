"""Unit tests for InvariantEngine and built-in invariant checkers."""

from __future__ import annotations

import pytest

from hermit.kernel.verification.assurance.invariants import InvariantEngine
from hermit.kernel.verification.assurance.models import (
    InvariantSpec,
    InvariantViolation,
)
from tests.assurance.conftest import make_envelope, make_governed_trace

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    """InvariantEngine registration and lookup."""

    def test_builtins_registered(self) -> None:
        engine = InvariantEngine()
        ids = engine.invariant_ids
        assert len(ids) >= 10
        assert "scheduler.single_winner_per_task" in ids
        assert "state.task_transition_legality" in ids
        assert "governance.authority_chain_complete" in ids
        assert "trace.hash_chain_continuity" in ids

    def test_register_custom_invariant(self) -> None:
        engine = InvariantEngine()
        spec = InvariantSpec(
            invariant_id="custom.always_pass",
            scope="test",
            detection_method="noop",
            severity="low",
        )
        engine.register(spec, lambda envelopes: [])
        assert "custom.always_pass" in engine.invariant_ids

    def test_register_overwrite_replaces(self) -> None:
        engine = InvariantEngine()
        spec = InvariantSpec(invariant_id="custom.x", scope="test")
        engine.register(spec, lambda envelopes: [])
        # Overwrite with a checker that always returns one violation
        engine.register(
            spec,
            lambda envelopes: [
                InvariantViolation(
                    violation_id="v1",
                    invariant_id="custom.x",
                    severity="low",
                    event_id="e1",
                    task_id="t1",
                )
            ],
        )
        violations = engine.check_single("custom.x", [])
        assert len(violations) == 1


# ---------------------------------------------------------------------------
# Clean trace — no violations
# ---------------------------------------------------------------------------


class TestCleanTrace:
    """A well-formed governed trace should produce zero violations."""

    def test_governed_trace_clean(self) -> None:
        engine = InvariantEngine()
        trace = make_governed_trace(num_steps=3)
        violations = engine.check(trace)
        assert violations == []

    def test_empty_trace_clean(self) -> None:
        engine = InvariantEngine()
        violations = engine.check([])
        assert violations == []


# ---------------------------------------------------------------------------
# state.task_transition_legality
# ---------------------------------------------------------------------------


class TestTaskTransitionLegality:
    """Detect illegal task state transitions."""

    def test_legal_transition_no_violation(self) -> None:
        engine = InvariantEngine()
        envelopes = [
            make_envelope(event_type="task.queued", event_seq=0, task_id="t1"),
            make_envelope(event_type="task.running", event_seq=1, task_id="t1"),
            make_envelope(event_type="task.completed", event_seq=2, task_id="t1"),
        ]
        violations = engine.check_single("state.task_transition_legality", envelopes)
        assert violations == []

    def test_illegal_transition_detected(self) -> None:
        engine = InvariantEngine()
        # queued -> completed is illegal (must go through running)
        envelopes = [
            make_envelope(event_type="task.queued", event_seq=0, task_id="t1"),
            make_envelope(event_type="task.completed", event_seq=1, task_id="t1"),
        ]
        violations = engine.check_single("state.task_transition_legality", envelopes)
        assert len(violations) == 1
        v = violations[0]
        assert v.invariant_id == "state.task_transition_legality"
        assert v.evidence["old_state"] == "queued"
        assert v.evidence["new_state"] == "completed"

    def test_terminal_to_active_detected(self) -> None:
        engine = InvariantEngine()
        # completed -> running is illegal
        envelopes = [
            make_envelope(event_type="task.queued", event_seq=0, task_id="t1"),
            make_envelope(event_type="task.running", event_seq=1, task_id="t1"),
            make_envelope(event_type="task.completed", event_seq=2, task_id="t1"),
            make_envelope(event_type="task.running", event_seq=3, task_id="t1"),
        ]
        violations = engine.check_single("state.task_transition_legality", envelopes)
        assert len(violations) == 1
        assert violations[0].evidence["old_state"] == "completed"
        assert violations[0].evidence["new_state"] == "running"


# ---------------------------------------------------------------------------
# governance.authority_chain_complete
# ---------------------------------------------------------------------------


class TestAuthorityChainComplete:
    """tool_call.start must have decision_ref, grant_ref, lease_ref."""

    def test_complete_chain_no_violation(self) -> None:
        engine = InvariantEngine()
        trace = make_governed_trace(num_steps=1)
        violations = engine.check_single("governance.authority_chain_complete", trace)
        assert violations == []

    def test_missing_grant_ref(self) -> None:
        engine = InvariantEngine()
        envelopes = [
            make_envelope(
                event_type="tool_call.start",
                event_seq=0,
                task_id="t1",
                step_attempt_id="a1",
                decision_ref="dec-1",
                grant_ref=None,
                lease_ref="lease-1",
            ),
        ]
        violations = engine.check_single("governance.authority_chain_complete", envelopes)
        assert len(violations) == 1
        assert "grant_ref" in violations[0].evidence["missing_refs"]

    def test_missing_multiple_refs(self) -> None:
        engine = InvariantEngine()
        envelopes = [
            make_envelope(
                event_type="tool_call.start",
                event_seq=0,
                task_id="t1",
                step_attempt_id="a1",
                decision_ref=None,
                grant_ref=None,
                lease_ref=None,
            ),
        ]
        violations = engine.check_single("governance.authority_chain_complete", envelopes)
        assert len(violations) == 1
        missing = violations[0].evidence["missing_refs"]
        assert "decision_ref" in missing
        assert "grant_ref" in missing
        assert "lease_ref" in missing


# ---------------------------------------------------------------------------
# governance.side_effect_authorized
# ---------------------------------------------------------------------------


class TestSideEffectAuthorized:
    """tool_call.start must have approval_ref or grant_ref."""

    def test_authorized_no_violation(self) -> None:
        engine = InvariantEngine()
        trace = make_governed_trace(num_steps=1)
        violations = engine.check_single("governance.side_effect_authorized", trace)
        assert violations == []

    def test_unauthorized_tool_call(self) -> None:
        engine = InvariantEngine()
        envelopes = [
            make_envelope(
                event_type="tool_call.start",
                event_seq=0,
                task_id="t1",
                step_attempt_id="a1",
                approval_ref=None,
                grant_ref=None,
            ),
        ]
        violations = engine.check_single("governance.side_effect_authorized", envelopes)
        assert len(violations) == 1
        v = violations[0]
        assert v.invariant_id == "governance.side_effect_authorized"
        assert v.evidence["has_approval_ref"] is False
        assert v.evidence["has_grant_ref"] is False

    def test_grant_ref_alone_sufficient(self) -> None:
        engine = InvariantEngine()
        envelopes = [
            make_envelope(
                event_type="tool_call.start",
                event_seq=0,
                task_id="t1",
                step_attempt_id="a1",
                approval_ref=None,
                grant_ref="grant-1",
                decision_ref="dec-1",
                lease_ref="lease-1",
            ),
        ]
        violations = engine.check_single("governance.side_effect_authorized", envelopes)
        assert violations == []


# ---------------------------------------------------------------------------
# trace.hash_chain_continuity
# ---------------------------------------------------------------------------


class TestHashChainContinuity:
    """event_seq must have no gaps within a run."""

    def test_continuous_chain_no_violation(self) -> None:
        engine = InvariantEngine()
        trace = make_governed_trace(num_steps=2)
        violations = engine.check_single("trace.hash_chain_continuity", trace)
        assert violations == []

    def test_gap_detected(self) -> None:
        engine = InvariantEngine()
        envelopes = [
            make_envelope(event_type="a", event_seq=0, run_id="r1"),
            make_envelope(event_type="b", event_seq=1, run_id="r1"),
            # Gap: seq 2 is missing
            make_envelope(event_type="c", event_seq=3, run_id="r1"),
        ]
        violations = engine.check_single("trace.hash_chain_continuity", envelopes)
        assert len(violations) == 1
        v = violations[0]
        assert v.invariant_id == "trace.hash_chain_continuity"
        assert v.evidence["expected_seq"] == 2
        assert v.evidence["actual_seq"] == 3
        assert v.evidence["gap_size"] == 1

    def test_multiple_gaps(self) -> None:
        engine = InvariantEngine()
        envelopes = [
            make_envelope(event_type="a", event_seq=0, run_id="r1"),
            make_envelope(event_type="b", event_seq=3, run_id="r1"),
            make_envelope(event_type="c", event_seq=7, run_id="r1"),
        ]
        violations = engine.check_single("trace.hash_chain_continuity", envelopes)
        assert len(violations) == 2

    def test_separate_runs_checked_independently(self) -> None:
        engine = InvariantEngine()
        # run-a is continuous, run-b has a gap
        envelopes = [
            make_envelope(event_type="a", event_seq=0, run_id="run-a", task_id="t1"),
            make_envelope(event_type="b", event_seq=1, run_id="run-a", task_id="t1"),
            make_envelope(event_type="c", event_seq=0, run_id="run-b", task_id="t2"),
            make_envelope(event_type="d", event_seq=5, run_id="run-b", task_id="t2"),
        ]
        violations = engine.check_single("trace.hash_chain_continuity", envelopes)
        assert len(violations) == 1
        assert violations[0].evidence["run_id"] == "run-b"


# ---------------------------------------------------------------------------
# governance.receipt_for_mutation
# ---------------------------------------------------------------------------


class TestReceiptForMutation:
    """tool_call.start must be paired with receipt.issued."""

    def test_paired_no_violation(self) -> None:
        engine = InvariantEngine()
        trace = make_governed_trace(num_steps=2)
        violations = engine.check_single("governance.receipt_for_mutation", trace)
        assert violations == []

    def test_missing_receipt(self) -> None:
        engine = InvariantEngine()
        envelopes = [
            make_envelope(
                event_type="tool_call.start",
                event_seq=0,
                task_id="t1",
                step_attempt_id="a1",
                grant_ref="g1",
            ),
            # No receipt.issued for attempt a1
        ]
        violations = engine.check_single("governance.receipt_for_mutation", envelopes)
        assert len(violations) == 1
        assert violations[0].evidence["step_attempt_id"] == "a1"


# ---------------------------------------------------------------------------
# scheduler.total_order_per_task
# ---------------------------------------------------------------------------


class TestTotalOrderPerTask:
    """event_seq must be monotonically increasing per task."""

    def test_ordered_no_violation(self) -> None:
        engine = InvariantEngine()
        trace = make_governed_trace(num_steps=2)
        violations = engine.check_single("scheduler.total_order_per_task", trace)
        assert violations == []

    def test_out_of_order_detected(self) -> None:
        engine = InvariantEngine()
        envelopes = [
            make_envelope(event_type="a", event_seq=0, task_id="t1"),
            make_envelope(event_type="b", event_seq=2, task_id="t1"),
            make_envelope(event_type="c", event_seq=1, task_id="t1"),  # out of order
        ]
        violations = engine.check_single("scheduler.total_order_per_task", envelopes)
        assert len(violations) == 1
        assert violations[0].evidence["previous_seq"] == 2
        assert violations[0].evidence["current_seq"] == 1


# ---------------------------------------------------------------------------
# first_violation ordering
# ---------------------------------------------------------------------------


class TestFirstViolation:
    """first_violation returns the earliest by trace_slice_start."""

    def test_returns_none_for_clean_trace(self) -> None:
        engine = InvariantEngine()
        trace = make_governed_trace(num_steps=2)
        assert engine.first_violation(trace) is None

    def test_returns_earliest_violation(self) -> None:
        engine = InvariantEngine()
        # Create trace with a hash-chain gap and an unauthorized tool call.
        # The gap is at seq 3, the unauthorized call at seq 5.
        envelopes = [
            make_envelope(event_type="generic", event_seq=0, run_id="r1", task_id="t1"),
            make_envelope(event_type="generic", event_seq=1, run_id="r1", task_id="t1"),
            # Gap: seq 2 missing
            make_envelope(event_type="generic", event_seq=3, run_id="r1", task_id="t1"),
            make_envelope(event_type="generic", event_seq=4, run_id="r1", task_id="t1"),
            make_envelope(
                event_type="tool_call.start",
                event_seq=5,
                run_id="r1",
                task_id="t1",
                step_attempt_id="a1",
                approval_ref=None,
                grant_ref=None,
            ),
        ]
        v = engine.first_violation(envelopes)
        assert v is not None
        # The hash-chain gap at seq 3 should come before the auth violation at seq 5
        assert v.invariant_id == "trace.hash_chain_continuity"


# ---------------------------------------------------------------------------
# check_single with unknown invariant
# ---------------------------------------------------------------------------


class TestCheckSingle:
    """check_single raises KeyError for unknown invariant."""

    def test_unknown_invariant_raises(self) -> None:
        engine = InvariantEngine()
        with pytest.raises(KeyError, match="Unknown invariant"):
            engine.check_single("does.not.exist", [])


# ---------------------------------------------------------------------------
# check with task_id filter
# ---------------------------------------------------------------------------


class TestTaskIdFilter:
    """check(task_id=...) filters envelopes before checking."""

    def test_filter_isolates_task(self) -> None:
        engine = InvariantEngine()
        envelopes = [
            # t1 has a hash-chain gap
            make_envelope(event_type="a", event_seq=0, run_id="r1", task_id="t1"),
            make_envelope(event_type="b", event_seq=5, run_id="r1", task_id="t1"),
            # t2 is clean
            make_envelope(event_type="c", event_seq=0, run_id="r2", task_id="t2"),
            make_envelope(event_type="d", event_seq=1, run_id="r2", task_id="t2"),
        ]
        violations_t2 = engine.check(envelopes, task_id="t2")
        assert violations_t2 == []

        violations_t1 = engine.check(envelopes, task_id="t1")
        assert len(violations_t1) > 0


# ---------------------------------------------------------------------------
# restart.idempotent_reentry
# ---------------------------------------------------------------------------


class TestIdempotentReentry:
    """Duplicate receipt_ref across restart epochs is flagged."""

    def test_no_duplicates_clean(self) -> None:
        engine = InvariantEngine()
        envelopes = [
            make_envelope(
                event_type="receipt.issued",
                event_seq=0,
                receipt_ref="r1",
                restart_epoch=0,
            ),
            make_envelope(
                event_type="receipt.issued",
                event_seq=1,
                receipt_ref="r2",
                restart_epoch=1,
            ),
        ]
        violations = engine.check_single("restart.idempotent_reentry", envelopes)
        assert violations == []

    def test_duplicate_across_epochs(self) -> None:
        engine = InvariantEngine()
        envelopes = [
            make_envelope(
                event_type="receipt.issued",
                event_seq=0,
                receipt_ref="dup-receipt",
                restart_epoch=0,
            ),
            make_envelope(
                event_type="receipt.issued",
                event_seq=1,
                receipt_ref="dup-receipt",
                restart_epoch=1,
            ),
        ]
        violations = engine.check_single("restart.idempotent_reentry", envelopes)
        assert len(violations) == 1
        assert violations[0].evidence["receipt_ref"] == "dup-receipt"
        assert violations[0].evidence["first_epoch"] == 0
        assert violations[0].evidence["duplicate_epoch"] == 1
