"""Unit tests for AssuranceContractEngine."""

from __future__ import annotations

import time

import pytest

from hermit.kernel.verification.assurance.contracts import AssuranceContractEngine
from hermit.kernel.verification.assurance.models import TraceContractSpec, TraceEnvelope

# Import helpers from the shared conftest
from tests.assurance.conftest import make_envelope, make_governed_trace

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine() -> AssuranceContractEngine:
    return AssuranceContractEngine()


@pytest.fixture()
def good_trace() -> list[TraceEnvelope]:
    return make_governed_trace(num_steps=3)


@pytest.fixture()
def bare_engine() -> AssuranceContractEngine:
    """Engine with builtins cleared so we can test individual contracts."""
    eng = AssuranceContractEngine()
    eng._contracts.clear()
    return eng


# ===================================================================
# Predicate algebra
# ===================================================================


class TestPredicateAll:
    def test_all_true(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="a"), make_envelope(event_type="b")]
        pred = {"all": [{"exists": "a"}, {"exists": "b"}]}
        assert engine.evaluate_predicate(pred, envs) is True

    def test_all_one_false(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="a")]
        pred = {"all": [{"exists": "a"}, {"exists": "b"}]}
        assert engine.evaluate_predicate(pred, envs) is False


class TestPredicateAny:
    def test_any_one_true(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="b")]
        pred = {"any": [{"exists": "a"}, {"exists": "b"}]}
        assert engine.evaluate_predicate(pred, envs) is True

    def test_any_none_true(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="c")]
        pred = {"any": [{"exists": "a"}, {"exists": "b"}]}
        assert engine.evaluate_predicate(pred, envs) is False


class TestPredicateNot:
    def test_not_negates(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="a")]
        assert engine.evaluate_predicate({"not": {"exists": "b"}}, envs) is True
        assert engine.evaluate_predicate({"not": {"exists": "a"}}, envs) is False


class TestPredicateExists:
    def test_exists_found(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="task.created")]
        assert engine.evaluate_predicate({"exists": "task.created"}, envs) is True

    def test_exists_not_found(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="task.created")]
        assert engine.evaluate_predicate({"exists": "task.failed"}, envs) is False


class TestPredicateCount:
    def test_count_ge(self, engine: AssuranceContractEngine) -> None:
        envs = [
            make_envelope(event_type="tool_call.start", event_seq=i)
            for i in range(3)
        ]
        pred = {"count": {"event": "tool_call.start", "op": ">=", "value": 3}}
        assert engine.evaluate_predicate(pred, envs) is True

    def test_count_lt(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="tool_call.start")]
        pred = {"count": {"event": "tool_call.start", "op": "<", "value": 2}}
        assert engine.evaluate_predicate(pred, envs) is True

    def test_count_eq(self, engine: AssuranceContractEngine) -> None:
        envs = [
            make_envelope(event_type="receipt.issued", event_seq=i) for i in range(2)
        ]
        pred = {"count": {"event": "receipt.issued", "op": "==", "value": 2}}
        assert engine.evaluate_predicate(pred, envs) is True

    def test_count_le(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="x", event_seq=i) for i in range(5)]
        pred = {"count": {"event": "x", "op": "<=", "value": 5}}
        assert engine.evaluate_predicate(pred, envs) is True

    def test_count_gt(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="x")]
        pred = {"count": {"event": "x", "op": ">", "value": 0}}
        assert engine.evaluate_predicate(pred, envs) is True

    def test_count_fails(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="x")]
        pred = {"count": {"event": "x", "op": ">", "value": 5}}
        assert engine.evaluate_predicate(pred, envs) is False


class TestPredicateBefore:
    def test_before_correct_order(self, engine: AssuranceContractEngine) -> None:
        envs = [
            make_envelope(event_type="approval.granted", event_seq=1),
            make_envelope(event_type="tool_call.start", event_seq=2),
        ]
        pred = {"before": {"event1": "approval.granted", "event2": "tool_call.start"}}
        assert engine.evaluate_predicate(pred, envs) is True

    def test_before_wrong_order(self, engine: AssuranceContractEngine) -> None:
        envs = [
            make_envelope(event_type="tool_call.start", event_seq=1),
            make_envelope(event_type="approval.granted", event_seq=2),
        ]
        pred = {"before": {"event1": "approval.granted", "event2": "tool_call.start"}}
        assert engine.evaluate_predicate(pred, envs) is False

    def test_before_missing_event(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="tool_call.start", event_seq=1)]
        pred = {"before": {"event1": "approval.granted", "event2": "tool_call.start"}}
        assert engine.evaluate_predicate(pred, envs) is False


class TestPredicateAfter:
    def test_after_correct_order(self, engine: AssuranceContractEngine) -> None:
        envs = [
            make_envelope(event_type="tool_call.start", event_seq=1),
            make_envelope(event_type="receipt.issued", event_seq=2),
        ]
        pred = {"after": {"event1": "receipt.issued", "event2": "tool_call.start"}}
        assert engine.evaluate_predicate(pred, envs) is True

    def test_after_wrong_order(self, engine: AssuranceContractEngine) -> None:
        envs = [
            make_envelope(event_type="receipt.issued", event_seq=1),
            make_envelope(event_type="tool_call.start", event_seq=2),
        ]
        pred = {"after": {"event1": "receipt.issued", "event2": "tool_call.start"}}
        assert engine.evaluate_predicate(pred, envs) is False


class TestPredicateEq:
    def test_eq_match(self, engine: AssuranceContractEngine) -> None:
        env = make_envelope(event_type="tool_call.start", phase="execution")
        result = engine.evaluate_predicate(
            {"eq": {"field": "phase", "value": "execution"}}, [], current=env
        )
        assert result is True

    def test_eq_no_match(self, engine: AssuranceContractEngine) -> None:
        env = make_envelope(event_type="tool_call.start", phase="planning")
        result = engine.evaluate_predicate(
            {"eq": {"field": "phase", "value": "execution"}}, [], current=env
        )
        assert result is False

    def test_eq_no_current(self, engine: AssuranceContractEngine) -> None:
        result = engine.evaluate_predicate(
            {"eq": {"field": "phase", "value": "execution"}}, []
        )
        assert result is False


class TestPredicateInSet:
    def test_in_set_match(self, engine: AssuranceContractEngine) -> None:
        env = make_envelope(event_type="tool_call.start", phase="execution")
        result = engine.evaluate_predicate(
            {"in_set": {"field": "phase", "values": ["planning", "execution"]}},
            [],
            current=env,
        )
        assert result is True

    def test_in_set_no_match(self, engine: AssuranceContractEngine) -> None:
        env = make_envelope(event_type="tool_call.start", phase="review")
        result = engine.evaluate_predicate(
            {"in_set": {"field": "phase", "values": ["planning", "execution"]}},
            [],
            current=env,
        )
        assert result is False


class TestPredicateEmpty:
    def test_empty_predicate_passes(self, engine: AssuranceContractEngine) -> None:
        assert engine.evaluate_predicate({}, []) is True


class TestPredicateNested:
    def test_nested_combinators(self, engine: AssuranceContractEngine) -> None:
        envs = [
            make_envelope(event_type="task.created", event_seq=0),
            make_envelope(event_type="task.completed", event_seq=5),
        ]
        pred = {
            "all": [
                {"exists": "task.created"},
                {
                    "not": {
                        "any": [
                            {"exists": "task.cancelled"},
                        ]
                    }
                },
            ]
        }
        assert engine.evaluate_predicate(pred, envs) is True


# ===================================================================
# Mode filtering
# ===================================================================


class TestModeFiltering:
    def test_runtime_ignores_post_run_contracts(
        self, bare_engine: AssuranceContractEngine
    ) -> None:
        bare_engine.register(
            TraceContractSpec(
                contract_id="post_only",
                mode="post_run",
                severity="high",
                assert_expr={"exists": "never_happens"},
            )
        )
        env = make_envelope(event_type="tool_call.start")
        violations = bare_engine.evaluate_runtime(env)
        assert violations == []

    def test_post_run_ignores_runtime_contracts(
        self, bare_engine: AssuranceContractEngine
    ) -> None:
        bare_engine.register(
            TraceContractSpec(
                contract_id="runtime_only",
                mode="runtime",
                severity="high",
                assert_expr={"exists": "never_happens"},
            )
        )
        envs = [make_envelope(event_type="tool_call.start")]
        violations = bare_engine.evaluate_post_run(envs)
        assert violations == []

    def test_both_mode_checked_at_runtime(
        self, bare_engine: AssuranceContractEngine
    ) -> None:
        bare_engine.register(
            TraceContractSpec(
                contract_id="both_mode",
                mode="both",
                severity="high",
                assert_expr={"exists": "never_happens"},
            )
        )
        env = make_envelope(event_type="tool_call.start")
        violations = bare_engine.evaluate_runtime(env)
        assert len(violations) == 1
        assert violations[0].contract_id == "both_mode"

    def test_both_mode_checked_at_post_run(
        self, bare_engine: AssuranceContractEngine
    ) -> None:
        bare_engine.register(
            TraceContractSpec(
                contract_id="both_mode",
                mode="both",
                severity="high",
                assert_expr={"exists": "never_happens"},
            )
        )
        envs = [make_envelope(event_type="something")]
        violations = bare_engine.evaluate_post_run(envs)
        assert len(violations) == 1


# ===================================================================
# Built-in contracts — good traces
# ===================================================================


class TestBuiltinContractsGoodTrace:
    """Verify that a well-formed governed trace passes all built-in contracts."""

    def test_task_lifecycle_pass(
        self, engine: AssuranceContractEngine, good_trace: list[TraceEnvelope]
    ) -> None:
        violations = engine.evaluate_post_run(good_trace)
        lifecycle_violations = [
            v for v in violations if v.contract_id == "task.lifecycle"
        ]
        assert lifecycle_violations == []

    def test_receipt_linkage_pass(
        self, engine: AssuranceContractEngine, good_trace: list[TraceEnvelope]
    ) -> None:
        violations = engine.evaluate_post_run(good_trace)
        linkage_violations = [
            v for v in violations if v.contract_id == "receipt.linkage"
        ]
        assert linkage_violations == []

    def test_no_duplicate_execution_pass(
        self, engine: AssuranceContractEngine, good_trace: list[TraceEnvelope]
    ) -> None:
        violations = engine.evaluate_post_run(good_trace)
        dup_violations = [
            v for v in violations if v.contract_id == "no_duplicate_execution"
        ]
        assert dup_violations == []

    def test_bounded_stuck_pass(
        self, engine: AssuranceContractEngine, good_trace: list[TraceEnvelope]
    ) -> None:
        violations = engine.evaluate_post_run(good_trace)
        stuck_violations = [
            v for v in violations if v.contract_id == "bounded_stuck"
        ]
        assert stuck_violations == []

    def test_all_post_run_builtins_pass(
        self, engine: AssuranceContractEngine, good_trace: list[TraceEnvelope]
    ) -> None:
        violations = engine.evaluate_post_run(good_trace)
        assert violations == []

    def test_runtime_builtins_pass_governed_event(
        self, engine: AssuranceContractEngine, good_trace: list[TraceEnvelope]
    ) -> None:
        """Runtime contracts should pass for a proper tool_call.start with prior approvals."""
        prior = []
        for env in good_trace:
            if env.event_type == "tool_call.start":
                violations = engine.evaluate_runtime(
                    env, context={"prior_envelopes": prior}
                )
                runtime_violations = [
                    v
                    for v in violations
                    if v.contract_id
                    in (
                        "approval.gating",
                        "side_effect.authorization",
                        "workspace.isolation",
                    )
                ]
                assert runtime_violations == [], (
                    f"Unexpected violations for tool_call.start: {runtime_violations}"
                )
            prior.append(env)


# ===================================================================
# Built-in contracts — bad traces (violation detection)
# ===================================================================


class TestTaskLifecycleViolation:
    def test_missing_created(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="task.completed", event_seq=0)]
        violations = engine.evaluate_post_run(envs)
        ids = [v.contract_id for v in violations]
        assert "task.lifecycle" in ids

    def test_missing_terminal(self, engine: AssuranceContractEngine) -> None:
        envs = [make_envelope(event_type="task.created", event_seq=0)]
        violations = engine.evaluate_post_run(envs)
        ids = [v.contract_id for v in violations]
        assert "task.lifecycle" in ids

    def test_failed_also_satisfies(self, engine: AssuranceContractEngine) -> None:
        envs = [
            make_envelope(event_type="task.created", event_seq=0),
            make_envelope(event_type="task.failed", event_seq=1),
        ]
        violations = engine.evaluate_post_run(envs)
        lifecycle_violations = [
            v for v in violations if v.contract_id == "task.lifecycle"
        ]
        assert lifecycle_violations == []


class TestApprovalGatingViolation:
    def test_tool_call_without_prior_approval(
        self, engine: AssuranceContractEngine
    ) -> None:
        env = make_envelope(
            event_type="tool_call.start",
            grant_ref="g-1",
            lease_ref="l-1",
        )
        violations = engine.evaluate_runtime(env, context={"prior_envelopes": []})
        ids = [v.contract_id for v in violations]
        assert "approval.gating" in ids

    def test_tool_call_after_approval(
        self, engine: AssuranceContractEngine
    ) -> None:
        prior = [
            make_envelope(event_type="approval.granted", event_seq=0),
        ]
        env = make_envelope(
            event_type="tool_call.start",
            event_seq=1,
            grant_ref="g-1",
            lease_ref="l-1",
        )
        violations = engine.evaluate_runtime(env, context={"prior_envelopes": prior})
        gating_violations = [
            v for v in violations if v.contract_id == "approval.gating"
        ]
        assert gating_violations == []


class TestSideEffectAuthorizationViolation:
    def test_tool_call_without_grant_ref(
        self, engine: AssuranceContractEngine
    ) -> None:
        prior = [make_envelope(event_type="approval.granted", event_seq=0)]
        env = make_envelope(
            event_type="tool_call.start",
            event_seq=1,
            lease_ref="l-1",
        )
        violations = engine.evaluate_runtime(env, context={"prior_envelopes": prior})
        ids = [v.contract_id for v in violations]
        assert "side_effect.authorization" in ids

    def test_tool_call_with_grant_ref(
        self, engine: AssuranceContractEngine
    ) -> None:
        prior = [make_envelope(event_type="approval.granted", event_seq=0)]
        env = make_envelope(
            event_type="tool_call.start",
            event_seq=1,
            grant_ref="g-1",
            lease_ref="l-1",
        )
        violations = engine.evaluate_runtime(env, context={"prior_envelopes": prior})
        auth_violations = [
            v for v in violations if v.contract_id == "side_effect.authorization"
        ]
        assert auth_violations == []


class TestReceiptLinkageViolation:
    def test_receipt_missing_decision_ref(
        self, engine: AssuranceContractEngine
    ) -> None:
        envs = [
            make_envelope(event_type="task.created", event_seq=0),
            make_envelope(
                event_type="receipt.issued",
                event_seq=1,
                grant_ref="g-1",
                # no decision_ref
            ),
            make_envelope(event_type="task.completed", event_seq=2),
        ]
        violations = engine.evaluate_post_run(envs)
        ids = [v.contract_id for v in violations]
        assert "receipt.linkage" in ids

    def test_receipt_missing_grant_ref(
        self, engine: AssuranceContractEngine
    ) -> None:
        envs = [
            make_envelope(event_type="task.created", event_seq=0),
            make_envelope(
                event_type="receipt.issued",
                event_seq=1,
                decision_ref="d-1",
                # no grant_ref
            ),
            make_envelope(event_type="task.completed", event_seq=2),
        ]
        violations = engine.evaluate_post_run(envs)
        ids = [v.contract_id for v in violations]
        assert "receipt.linkage" in ids

    def test_receipt_fully_linked(self, engine: AssuranceContractEngine) -> None:
        envs = [
            make_envelope(event_type="task.created", event_seq=0),
            make_envelope(
                event_type="receipt.issued",
                event_seq=1,
                decision_ref="d-1",
                grant_ref="g-1",
            ),
            make_envelope(event_type="task.completed", event_seq=2),
        ]
        violations = engine.evaluate_post_run(envs)
        linkage_violations = [
            v for v in violations if v.contract_id == "receipt.linkage"
        ]
        assert linkage_violations == []


class TestNoDuplicateExecutionViolation:
    def test_duplicate_pair_detected(self, engine: AssuranceContractEngine) -> None:
        envs = [
            make_envelope(event_type="task.created", event_seq=0),
            make_envelope(
                event_type="receipt.issued",
                event_seq=1,
                step_attempt_id="sa-1",
                receipt_ref="r-1",
                decision_ref="d-1",
                grant_ref="g-1",
            ),
            make_envelope(
                event_type="receipt.issued",
                event_seq=2,
                step_attempt_id="sa-1",
                receipt_ref="r-1",
                decision_ref="d-1",
                grant_ref="g-1",
            ),
            make_envelope(event_type="task.completed", event_seq=3),
        ]
        violations = engine.evaluate_post_run(envs)
        ids = [v.contract_id for v in violations]
        assert "no_duplicate_execution" in ids

    def test_unique_pairs_pass(self, engine: AssuranceContractEngine) -> None:
        envs = [
            make_envelope(event_type="task.created", event_seq=0),
            make_envelope(
                event_type="receipt.issued",
                event_seq=1,
                step_attempt_id="sa-1",
                receipt_ref="r-1",
                decision_ref="d-1",
                grant_ref="g-1",
            ),
            make_envelope(
                event_type="receipt.issued",
                event_seq=2,
                step_attempt_id="sa-2",
                receipt_ref="r-2",
                decision_ref="d-2",
                grant_ref="g-2",
            ),
            make_envelope(event_type="task.completed", event_seq=3),
        ]
        violations = engine.evaluate_post_run(envs)
        dup_violations = [
            v for v in violations if v.contract_id == "no_duplicate_execution"
        ]
        assert dup_violations == []


class TestBoundedStuckViolation:
    def test_large_gap_detected(self, engine: AssuranceContractEngine) -> None:
        now = time.time()
        envs = [
            make_envelope(event_type="task.created", event_seq=0, wallclock_at=now),
            make_envelope(
                event_type="tool_call.start",
                event_seq=1,
                wallclock_at=now + 1.0,
                grant_ref="g-1",
                lease_ref="l-1",
            ),
            make_envelope(
                event_type="tool_call.start",
                event_seq=2,
                wallclock_at=now + 1000.0,  # 999s gap > 600s default TTL
                grant_ref="g-2",
                lease_ref="l-2",
            ),
            make_envelope(event_type="task.completed", event_seq=3, wallclock_at=now + 1001.0),
        ]
        violations = engine.evaluate_post_run(envs)
        ids = [v.contract_id for v in violations]
        assert "bounded_stuck" in ids

    def test_small_gap_passes(self, engine: AssuranceContractEngine) -> None:
        now = time.time()
        envs = [
            make_envelope(event_type="task.created", event_seq=0, wallclock_at=now),
            make_envelope(
                event_type="tool_call.start",
                event_seq=1,
                wallclock_at=now + 1.0,
                grant_ref="g-1",
                lease_ref="l-1",
            ),
            make_envelope(
                event_type="tool_call.start",
                event_seq=2,
                wallclock_at=now + 2.0,
                grant_ref="g-2",
                lease_ref="l-2",
            ),
            make_envelope(event_type="task.completed", event_seq=3, wallclock_at=now + 3.0),
        ]
        violations = engine.evaluate_post_run(envs)
        stuck_violations = [
            v for v in violations if v.contract_id == "bounded_stuck"
        ]
        assert stuck_violations == []


class TestWorkspaceIsolationViolation:
    def test_tool_call_without_lease_ref(
        self, engine: AssuranceContractEngine
    ) -> None:
        prior = [make_envelope(event_type="approval.granted", event_seq=0)]
        env = make_envelope(
            event_type="tool_call.start",
            event_seq=1,
            grant_ref="g-1",
            # no lease_ref
        )
        violations = engine.evaluate_runtime(env, context={"prior_envelopes": prior})
        ids = [v.contract_id for v in violations]
        assert "workspace.isolation" in ids

    def test_tool_call_with_lease_ref(
        self, engine: AssuranceContractEngine
    ) -> None:
        prior = [make_envelope(event_type="approval.granted", event_seq=0)]
        env = make_envelope(
            event_type="tool_call.start",
            event_seq=1,
            grant_ref="g-1",
            lease_ref="l-1",
        )
        violations = engine.evaluate_runtime(env, context={"prior_envelopes": prior})
        isolation_violations = [
            v for v in violations if v.contract_id == "workspace.isolation"
        ]
        assert isolation_violations == []


# ===================================================================
# Contract registration
# ===================================================================


class TestContractRegistration:
    def test_builtins_registered(self, engine: AssuranceContractEngine) -> None:
        expected = {
            "task.lifecycle",
            "approval.gating",
            "side_effect.authorization",
            "receipt.linkage",
            "no_duplicate_execution",
            "bounded_stuck",
            "workspace.isolation",
        }
        assert expected == set(engine._contracts.keys())

    def test_register_custom_contract(
        self, engine: AssuranceContractEngine
    ) -> None:
        spec = TraceContractSpec(
            contract_id="custom.test",
            mode="post_run",
            severity="low",
            assert_expr={"exists": "custom.event"},
        )
        engine.register(spec)
        assert "custom.test" in engine._contracts

    def test_register_replaces_existing(
        self, engine: AssuranceContractEngine
    ) -> None:
        spec = TraceContractSpec(
            contract_id="task.lifecycle",
            mode="post_run",
            severity="low",
            assert_expr={"exists": "something"},
        )
        engine.register(spec)
        assert engine._contracts["task.lifecycle"].severity == "low"


# ===================================================================
# Violation structure
# ===================================================================


class TestViolationStructure:
    def test_violation_has_correct_fields(
        self, engine: AssuranceContractEngine
    ) -> None:
        envs = [make_envelope(event_type="task.completed", event_seq=0)]
        violations = engine.evaluate_post_run(envs, task_id="task-abc")
        lifecycle_v = next(
            v for v in violations if v.contract_id == "task.lifecycle"
        )
        assert lifecycle_v.severity == "blocker"
        assert lifecycle_v.mode == "post_run"
        assert lifecycle_v.task_id == "task-abc"
        assert lifecycle_v.remediation_hint != ""

    def test_runtime_violation_captures_event_info(
        self, engine: AssuranceContractEngine
    ) -> None:
        env = make_envelope(event_type="tool_call.start", event_seq=42)
        violations = engine.evaluate_runtime(env, context={"prior_envelopes": []})
        assert len(violations) > 0
        v = violations[0]
        assert v.evidence["event_type"] == "tool_call.start"
        assert v.evidence["event_seq"] == 42
