"""Unit tests for ContractExecutor — contract synthesis, admissibility, and lifecycle."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.execution.executor.contract_executor import ContractExecutor
from hermit.kernel.policy.guards.rules import POLICY_RULES_VERSION

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attempt_ctx(
    step_attempt_id: str = "sa-1",
    task_id: str = "task-1",
    step_id: str = "step-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        step_attempt_id=step_attempt_id,
        task_id=task_id,
        step_id=step_id,
    )


def _make_executor(**overrides: Any) -> tuple[ContractExecutor, dict[str, MagicMock]]:
    deps: dict[str, MagicMock] = {
        "store": MagicMock(),
        "artifact_store": MagicMock(),
        "execution_contracts": MagicMock(),
        "evidence_cases": MagicMock(),
        "authorization_plans": MagicMock(),
    }
    deps.update(overrides)
    executor = ContractExecutor(
        store=deps["store"],
        artifact_store=deps["artifact_store"],
        execution_contracts=deps["execution_contracts"],
        evidence_cases=deps["evidence_cases"],
        authorization_plans=deps["authorization_plans"],
    )
    return executor, deps


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def executor_and_deps() -> tuple[ContractExecutor, dict[str, MagicMock]]:
    return _make_executor()


# ---------------------------------------------------------------------------
# contract_refs
# ---------------------------------------------------------------------------


class TestContractRefs:
    """Tests for ContractExecutor.contract_refs."""

    def test_returns_refs_from_attempt(self, executor_and_deps: tuple) -> None:
        executor, deps = executor_and_deps
        attempt = SimpleNamespace(
            execution_contract_ref="ec-1",
            evidence_case_ref="ev-1",
            authorization_plan_ref="ap-1",
        )
        deps["store"].get_step_attempt.return_value = attempt
        ctx = _make_attempt_ctx()

        result = executor.contract_refs(ctx)

        assert result == ("ec-1", "ev-1", "ap-1")
        deps["store"].get_step_attempt.assert_called_once_with("sa-1")

    def test_returns_none_tuple_when_attempt_missing(self, executor_and_deps: tuple) -> None:
        executor, deps = executor_and_deps
        deps["store"].get_step_attempt.return_value = None
        ctx = _make_attempt_ctx()

        assert executor.contract_refs(ctx) == (None, None, None)

    def test_returns_partial_refs(self, executor_and_deps: tuple) -> None:
        executor, deps = executor_and_deps
        attempt = SimpleNamespace(
            execution_contract_ref="ec-1",
            evidence_case_ref=None,
            authorization_plan_ref="ap-1",
        )
        deps["store"].get_step_attempt.return_value = attempt
        ctx = _make_attempt_ctx()

        assert executor.contract_refs(ctx) == ("ec-1", None, "ap-1")


# ---------------------------------------------------------------------------
# load_contract_bundle
# ---------------------------------------------------------------------------


class TestLoadContractBundle:
    """Tests for ContractExecutor.load_contract_bundle."""

    def test_loads_all_three_objects(self, executor_and_deps: tuple) -> None:
        executor, deps = executor_and_deps
        attempt = SimpleNamespace(
            execution_contract_ref="ec-1",
            evidence_case_ref="ev-1",
            authorization_plan_ref="ap-1",
        )
        deps["store"].get_step_attempt.return_value = attempt
        contract_obj = SimpleNamespace(contract_id="ec-1")
        evidence_obj = SimpleNamespace(evidence_case_id="ev-1")
        auth_obj = SimpleNamespace(authorization_plan_id="ap-1")
        deps["store"].get_execution_contract.return_value = contract_obj
        deps["store"].get_evidence_case.return_value = evidence_obj
        deps["store"].get_authorization_plan.return_value = auth_obj

        ctx = _make_attempt_ctx()
        c, e, a = executor.load_contract_bundle(ctx)

        assert c is contract_obj
        assert e is evidence_obj
        assert a is auth_obj

    def test_returns_nones_when_attempt_missing(self, executor_and_deps: tuple) -> None:
        executor, deps = executor_and_deps
        deps["store"].get_step_attempt.return_value = None

        ctx = _make_attempt_ctx()
        c, e, a = executor.load_contract_bundle(ctx)

        assert c is None
        assert e is None
        assert a is None

    def test_returns_nones_when_refs_are_none(self, executor_and_deps: tuple) -> None:
        executor, deps = executor_and_deps
        attempt = SimpleNamespace(
            execution_contract_ref=None,
            evidence_case_ref=None,
            authorization_plan_ref=None,
        )
        deps["store"].get_step_attempt.return_value = attempt

        ctx = _make_attempt_ctx()
        c, e, a = executor.load_contract_bundle(ctx)

        assert c is None
        assert e is None
        assert a is None

    def test_handles_missing_store_methods_gracefully(self) -> None:
        """When store lacks get_execution_contract etc., returns None."""
        store = MagicMock(
            spec=[
                "get_step_attempt",
                "update_step_attempt",
                "append_event",
            ]
        )
        attempt = SimpleNamespace(
            execution_contract_ref="ec-1",
            evidence_case_ref="ev-1",
            authorization_plan_ref="ap-1",
        )
        store.get_step_attempt.return_value = attempt
        executor, _ = _make_executor(store=store)

        ctx = _make_attempt_ctx()
        c, e, a = executor.load_contract_bundle(ctx)

        assert c is None
        assert e is None
        assert a is None


# ---------------------------------------------------------------------------
# contract_expired (static)
# ---------------------------------------------------------------------------


class TestContractExpired:
    """Tests for ContractExecutor.contract_expired."""

    @pytest.mark.parametrize(
        "expiry_at,expected",
        [
            pytest.param(time.time() - 100, True, id="past-expiry"),
            pytest.param(time.time() + 3600, False, id="future-expiry"),
            pytest.param(None, False, id="no-expiry"),
            pytest.param("not-a-number", False, id="non-numeric-expiry"),
        ],
    )
    def test_contract_expired(self, expiry_at: object, expected: bool) -> None:
        contract = SimpleNamespace(expiry_at=expiry_at)
        assert ContractExecutor.contract_expired(contract) is expected

    def test_object_without_expiry_attr(self) -> None:
        contract = SimpleNamespace()
        assert ContractExecutor.contract_expired(contract) is False


# ---------------------------------------------------------------------------
# policy_version_drifted (static)
# ---------------------------------------------------------------------------


class TestPolicyVersionDrifted:
    """Tests for ContractExecutor.policy_version_drifted."""

    def test_current_version_not_drifted(self) -> None:
        attempt = SimpleNamespace(policy_version=POLICY_RULES_VERSION)
        assert ContractExecutor.policy_version_drifted(attempt) is False

    @pytest.mark.parametrize(
        "policy_version,expected",
        [
            pytest.param("old-version-1.0", True, id="old-version"),
            pytest.param("", False, id="empty"),
            pytest.param(None, False, id="none"),
            pytest.param("   ", False, id="whitespace"),
        ],
    )
    def test_drift_detection(self, policy_version: object, expected: bool) -> None:
        attempt = SimpleNamespace(policy_version=policy_version)
        assert ContractExecutor.policy_version_drifted(attempt) is expected

    def test_object_without_policy_version_attr(self) -> None:
        attempt = SimpleNamespace()
        assert ContractExecutor.policy_version_drifted(attempt) is False


# ---------------------------------------------------------------------------
# synthesize_contract_loop
# ---------------------------------------------------------------------------


class TestSynthesizeContractLoop:
    """Tests for the full contract synthesis loop."""

    def _setup_loop(
        self,
        auth_status: str = "preflighted",
        evidence_status: str = "sufficient",
    ) -> tuple[ContractExecutor, dict[str, MagicMock], SimpleNamespace]:
        executor, deps = _make_executor()
        ctx = _make_attempt_ctx()

        contract = SimpleNamespace(contract_id="ec-synth-1")
        evidence_case = SimpleNamespace(
            evidence_case_id="ev-synth-1",
            status=evidence_status,
        )
        authorization_plan = SimpleNamespace(
            authorization_plan_id="ap-synth-1",
            status=auth_status,
        )
        attempt_record = SimpleNamespace(context_pack_ref="cp-1", context={})

        deps["store"].get_step_attempt.return_value = attempt_record
        deps["execution_contracts"].synthesize_default.return_value = (
            contract,
            "artifact-ec-1",
        )
        deps["evidence_cases"].compile_for_contract.return_value = (
            evidence_case,
            "artifact-ev-1",
        )
        deps["authorization_plans"].preflight.return_value = (
            authorization_plan,
            "artifact-ap-1",
        )

        return executor, deps, ctx

    def test_authorized_status_when_preflighted_and_sufficient(self) -> None:
        executor, deps, ctx = self._setup_loop(
            auth_status="preflighted",
            evidence_status="sufficient",
        )
        tool = SimpleNamespace(name="write_file")
        action_request = SimpleNamespace(tool_name="write_file")
        policy = SimpleNamespace(decision="allow")

        c, e, a = executor.synthesize_contract_loop(
            attempt_ctx=ctx,
            tool=tool,
            action_request=action_request,
            policy=policy,
            action_request_ref="ar-1",
            policy_result_ref="pr-1",
            preview_artifact="pa-1",
            witness_ref="wr-1",
        )

        assert c.contract_id == "ec-synth-1"
        assert e.evidence_case_id == "ev-synth-1"
        assert a.authorization_plan_id == "ap-synth-1"

        deps["store"].update_execution_contract.assert_called_once()
        update_kwargs = deps["store"].update_execution_contract.call_args
        assert update_kwargs[1]["status"] == "authorized"

    def test_approval_pending_when_awaiting_approval(self) -> None:
        executor, deps, ctx = self._setup_loop(auth_status="awaiting_approval")
        tool = SimpleNamespace(name="write_file")

        executor.synthesize_contract_loop(
            attempt_ctx=ctx,
            tool=tool,
            action_request=SimpleNamespace(tool_name="write_file"),
            policy=SimpleNamespace(decision="allow"),
            action_request_ref=None,
            policy_result_ref=None,
            preview_artifact=None,
            witness_ref=None,
        )

        update_kwargs = deps["store"].update_execution_contract.call_args
        assert update_kwargs[1]["status"] == "approval_pending"

    def test_abandoned_when_blocked(self) -> None:
        executor, deps, ctx = self._setup_loop(auth_status="blocked")
        tool = SimpleNamespace(name="write_file")

        executor.synthesize_contract_loop(
            attempt_ctx=ctx,
            tool=tool,
            action_request=SimpleNamespace(tool_name="write_file"),
            policy=SimpleNamespace(decision="allow"),
            action_request_ref=None,
            policy_result_ref=None,
            preview_artifact=None,
            witness_ref=None,
        )

        update_kwargs = deps["store"].update_execution_contract.call_args
        assert update_kwargs[1]["status"] == "abandoned"

    def test_admissibility_pending_when_evidence_insufficient(self) -> None:
        executor, deps, ctx = self._setup_loop(
            auth_status="preflighted",
            evidence_status="insufficient",
        )
        tool = SimpleNamespace(name="write_file")

        executor.synthesize_contract_loop(
            attempt_ctx=ctx,
            tool=tool,
            action_request=SimpleNamespace(tool_name="write_file"),
            policy=SimpleNamespace(decision="allow"),
            action_request_ref=None,
            policy_result_ref=None,
            preview_artifact=None,
            witness_ref=None,
        )

        update_kwargs = deps["store"].update_execution_contract.call_args
        assert update_kwargs[1]["status"] == "admissibility_pending"

    def test_phase_transitions_emitted(self) -> None:
        executor, deps, ctx = self._setup_loop()
        tool = SimpleNamespace(name="write_file")

        executor.synthesize_contract_loop(
            attempt_ctx=ctx,
            tool=tool,
            action_request=SimpleNamespace(tool_name="write_file"),
            policy=SimpleNamespace(decision="allow"),
            action_request_ref=None,
            policy_result_ref=None,
            preview_artifact=None,
            witness_ref=None,
        )

        update_calls = deps["store"].update_step_attempt.call_args_list
        statuses = [c[1].get("status") for c in update_calls if "status" in c[1]]
        assert "contracting" in statuses
        assert "preflighting" in statuses

    def test_contract_bundle_refs_set(self) -> None:
        executor, deps, ctx = self._setup_loop()
        tool = SimpleNamespace(name="write_file")

        executor.synthesize_contract_loop(
            attempt_ctx=ctx,
            tool=tool,
            action_request=SimpleNamespace(tool_name="write_file"),
            policy=SimpleNamespace(decision="allow"),
            action_request_ref=None,
            policy_result_ref=None,
            preview_artifact=None,
            witness_ref=None,
        )

        update_kwargs = deps["store"].update_execution_contract.call_args[1]
        assert update_kwargs["evidence_case_ref"] == "ev-synth-1"
        assert update_kwargs["authorization_plan_ref"] == "ap-synth-1"


# ---------------------------------------------------------------------------
# admissibility_resolution (static)
# ---------------------------------------------------------------------------


class TestAdmissibilityResolution:
    """Tests for ContractExecutor.admissibility_resolution."""

    def test_gather_more_evidence_when_insufficient(self) -> None:
        evidence = SimpleNamespace(status="insufficient")
        auth = SimpleNamespace(status="preflighted")
        assert ContractExecutor.admissibility_resolution(evidence, auth) == "gather_more_evidence"

    def test_request_authority_when_blocked(self) -> None:
        evidence = SimpleNamespace(status="sufficient")
        auth = SimpleNamespace(status="blocked")
        assert ContractExecutor.admissibility_resolution(evidence, auth) == "request_authority"

    def test_none_when_both_ok(self) -> None:
        evidence = SimpleNamespace(status="sufficient")
        auth = SimpleNamespace(status="preflighted")
        assert ContractExecutor.admissibility_resolution(evidence, auth) is None

    def test_evidence_takes_priority_over_blocked_auth(self) -> None:
        """When evidence is insufficient AND auth is blocked, evidence check wins."""
        evidence = SimpleNamespace(status="insufficient")
        auth = SimpleNamespace(status="blocked")
        assert ContractExecutor.admissibility_resolution(evidence, auth) == "gather_more_evidence"

    def test_none_status_treated_as_insufficient(self) -> None:
        evidence = SimpleNamespace(status=None)
        auth = SimpleNamespace(status="preflighted")
        assert ContractExecutor.admissibility_resolution(evidence, auth) == "gather_more_evidence"

    def test_empty_string_status_treated_as_insufficient(self) -> None:
        evidence = SimpleNamespace(status="")
        auth = SimpleNamespace(status="preflighted")
        assert ContractExecutor.admissibility_resolution(evidence, auth) == "gather_more_evidence"


# ---------------------------------------------------------------------------
# _set_attempt_phase (internal helper)
# ---------------------------------------------------------------------------


class TestSetAttemptPhase:
    """Tests for the set_attempt_phase helper."""

    def test_phase_change_emits_event(self) -> None:
        executor, deps = _make_executor()
        attempt = SimpleNamespace(context={})
        deps["store"].get_step_attempt.return_value = attempt
        ctx = _make_attempt_ctx()

        executor.set_attempt_phase(ctx, "contracting", reason="test_reason")

        deps["store"].append_event.assert_called_once()
        event_kwargs = deps["store"].append_event.call_args[1]
        assert event_kwargs["event_type"] == "step_attempt.phase_changed"
        assert event_kwargs["payload"]["phase"] == "contracting"
        assert event_kwargs["payload"]["reason"] == "test_reason"

    def test_no_op_when_phase_unchanged(self) -> None:
        executor, deps = _make_executor()
        attempt = SimpleNamespace(context={"phase": "contracting"})
        deps["store"].get_step_attempt.return_value = attempt
        ctx = _make_attempt_ctx()

        executor.set_attempt_phase(ctx, "contracting")

        deps["store"].append_event.assert_not_called()
        # Only the get_step_attempt call should have happened, no update
        deps["store"].update_step_attempt.assert_not_called()

    def test_no_op_when_attempt_missing(self) -> None:
        executor, deps = _make_executor()
        deps["store"].get_step_attempt.return_value = None
        ctx = _make_attempt_ctx()

        executor.set_attempt_phase(ctx, "contracting")

        deps["store"].append_event.assert_not_called()

    def test_previous_phase_tracked_in_payload(self) -> None:
        executor, deps = _make_executor()
        attempt = SimpleNamespace(context={"phase": "evaluating"})
        deps["store"].get_step_attempt.return_value = attempt
        ctx = _make_attempt_ctx()

        executor.set_attempt_phase(ctx, "contracting")

        payload = deps["store"].append_event.call_args[1]["payload"]
        assert payload["previous_phase"] == "evaluating"
        assert payload["phase"] == "contracting"
