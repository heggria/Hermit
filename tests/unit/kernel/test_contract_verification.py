"""Tests for ExecutionContractRecord verification_requirements and task_family fields.

Verifies that:
1. enrich_verification_requirements generates correct dicts for each risk level
2. task_family is correctly inferred from action_class
3. New fields are persisted and retrieved from the store
4. Existing contracts without the new fields still load correctly
5. Benchmark profiles map correctly from task_family
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.execution.controller.execution_contracts import (
    ExecutionContractService,
)
from hermit.kernel.ledger.journal.store import KernelStore


def _make_store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


# ---------------------------------------------------------------------------
# enrich_verification_requirements
# ---------------------------------------------------------------------------


class TestEnrichVerificationRequirements:
    def test_low_risk_all_forbidden(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements(
            risk_level="low",
        )
        assert result["functional"] == "required"
        assert result["governance_bench"] == "forbidden"
        assert result["performance_bench"] == "forbidden"
        assert result["rollback_check"] == "forbidden"
        assert result["reconciliation_mode"] == "light"

    def test_medium_risk_all_optional(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements(
            risk_level="medium",
        )
        assert result["functional"] == "required"
        assert result["governance_bench"] == "optional"
        assert result["performance_bench"] == "optional"
        assert result["rollback_check"] == "optional"
        assert result["reconciliation_mode"] == "standard"

    def test_high_risk_all_required(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements(
            risk_level="high",
        )
        assert result["functional"] == "required"
        assert result["governance_bench"] == "required"
        assert result["performance_bench"] == "required"
        assert result["rollback_check"] == "required"
        assert result["reconciliation_mode"] == "strict"

    def test_critical_risk_same_as_high(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements(
            risk_level="critical",
        )
        assert result["governance_bench"] == "required"
        assert result["performance_bench"] == "required"
        assert result["rollback_check"] == "required"
        assert result["reconciliation_mode"] == "strict"

    def test_default_risk_is_low(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements()
        assert result["governance_bench"] == "forbidden"
        assert result["reconciliation_mode"] == "light"

    def test_benchmark_profile_governance_mutation(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation",
            risk_level="high",
        )
        assert result["benchmark_profile"] == "trustloop_governance"

    def test_benchmark_profile_runtime_perf(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements(
            task_family="runtime_perf",
            risk_level="medium",
        )
        assert result["benchmark_profile"] == "runtime_perf"

    def test_benchmark_profile_surface_integration(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements(
            task_family="surface_integration",
            risk_level="low",
        )
        assert result["benchmark_profile"] == "integration_regression"

    def test_benchmark_profile_learning_template(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements(
            task_family="learning_template",
            risk_level="low",
        )
        assert result["benchmark_profile"] == "template_quality"

    def test_benchmark_profile_none_when_no_family(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements(
            task_family=None,
            risk_level="high",
        )
        assert result["benchmark_profile"] == "none"

    def test_thresholds_ref_always_none(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation",
            risk_level="high",
        )
        assert result["thresholds_ref"] is None


# ---------------------------------------------------------------------------
# _infer_task_family
# ---------------------------------------------------------------------------


class TestInferTaskFamily:
    def test_write_local_is_governance_mutation(self) -> None:
        assert ExecutionContractService._infer_task_family("write_local") == "governance_mutation"

    def test_execute_local_is_governance_mutation(self) -> None:
        assert ExecutionContractService._infer_task_family("execute_local") == "governance_mutation"

    def test_delete_remote_is_governance_mutation(self) -> None:
        assert ExecutionContractService._infer_task_family("delete_remote") == "governance_mutation"

    def test_network_request_is_surface_integration(self) -> None:
        assert (
            ExecutionContractService._infer_task_family("network_request") == "surface_integration"
        )

    def test_read_remote_is_surface_integration(self) -> None:
        assert ExecutionContractService._infer_task_family("read_remote") == "surface_integration"

    def test_unknown_action_class_returns_none(self) -> None:
        assert ExecutionContractService._infer_task_family("read_local") is None

    def test_empty_action_class_returns_none(self) -> None:
        assert ExecutionContractService._infer_task_family("") is None


# ---------------------------------------------------------------------------
# Store persistence round-trip
# ---------------------------------------------------------------------------


class TestContractVerificationStore:
    def test_fields_persisted_and_retrieved(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        verification_req = {
            "functional": "required",
            "governance_bench": "required",
            "performance_bench": "optional",
            "rollback_check": "forbidden",
            "reconciliation_mode": "strict",
            "benchmark_profile": "trustloop_governance",
            "thresholds_ref": None,
        }
        contract = store.create_execution_contract(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            objective="test objective",
            task_family="governance_mutation",
            verification_requirements=verification_req,
        )
        assert contract.task_family == "governance_mutation"
        assert contract.verification_requirements is not None
        assert contract.verification_requirements["functional"] == "required"
        assert contract.verification_requirements["governance_bench"] == "required"
        assert contract.verification_requirements["benchmark_profile"] == "trustloop_governance"

        # Re-fetch from store
        fetched = store.get_execution_contract(contract.contract_id)
        assert fetched is not None
        assert fetched.task_family == "governance_mutation"
        assert fetched.verification_requirements == verification_req

    def test_fields_default_to_none(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        contract = store.create_execution_contract(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            objective="test objective",
        )
        assert contract.task_family is None
        assert contract.verification_requirements is None

    def test_list_contracts_includes_new_fields(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.create_execution_contract(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            objective="obj1",
            task_family="runtime_perf",
            verification_requirements={"functional": "required", "governance_bench": "optional"},
        )
        store.create_execution_contract(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-2",
            objective="obj2",
        )
        contracts = store.list_execution_contracts(task_id="task-1")
        assert len(contracts) == 2
        families = {c.task_family for c in contracts}
        assert "runtime_perf" in families
        assert None in families

    def test_task_family_only_without_verification_requirements(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        contract = store.create_execution_contract(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            objective="test",
            task_family="surface_integration",
        )
        assert contract.task_family == "surface_integration"
        assert contract.verification_requirements is None


# ---------------------------------------------------------------------------
# Spec compliance: verification_requirements all fields present
# ---------------------------------------------------------------------------


class TestVerificationRequirementsSpecCompliance:
    """Verify the 7 spec-required keys are present in every enriched dict."""

    _REQUIRED_KEYS = {
        "functional",
        "governance_bench",
        "performance_bench",
        "rollback_check",
        "reconciliation_mode",
        "benchmark_profile",
        "thresholds_ref",
    }

    def test_all_spec_keys_present_low_risk(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements(
            risk_level="low",
        )
        assert set(result.keys()) >= self._REQUIRED_KEYS

    def test_all_spec_keys_present_high_risk(self) -> None:
        result = ExecutionContractService.enrich_verification_requirements(
            risk_level="high",
            task_family="governance_mutation",
        )
        assert set(result.keys()) >= self._REQUIRED_KEYS

    def test_functional_is_string_not_bool(self) -> None:
        """Spec requires functional to be 'required'|'optional'|'forbidden', not bool."""
        for risk in ("low", "medium", "high", "critical"):
            result = ExecutionContractService.enrich_verification_requirements(
                risk_level=risk,
            )
            assert isinstance(result["functional"], str), (
                f"functional must be str, got {type(result['functional'])} for risk={risk}"
            )

    def test_all_verification_lane_values_are_strings(self) -> None:
        """All verification lane values must be 'required'|'optional'|'forbidden'."""
        valid_values = {"required", "optional", "forbidden"}
        for risk in ("low", "medium", "high", "critical"):
            result = ExecutionContractService.enrich_verification_requirements(
                risk_level=risk,
            )
            for lane in ("functional", "governance_bench", "performance_bench", "rollback_check"):
                assert result[lane] in valid_values, (
                    f"{lane}={result[lane]} is not a valid verification lane value for risk={risk}"
                )


# ---------------------------------------------------------------------------
# _merge_strictest
# ---------------------------------------------------------------------------


class TestMergeStrictest:
    """Verify that _merge_strictest always picks the stricter value per field."""

    def test_required_beats_optional(self) -> None:
        prev = {
            "governance_bench": "required",
            "performance_bench": "optional",
            "rollback_check": "forbidden",
            "functional": "required",
            "reconciliation_mode": "strict",
        }
        cur = {
            "governance_bench": "optional",
            "performance_bench": "optional",
            "rollback_check": "optional",
            "functional": "required",
            "reconciliation_mode": "light",
        }
        merged = ExecutionContractService._merge_strictest(prev, cur)
        assert merged["governance_bench"] == "required"
        assert merged["performance_bench"] == "optional"
        assert merged["rollback_check"] == "optional"
        assert merged["reconciliation_mode"] == "strict"

    def test_forbidden_never_weakens_required(self) -> None:
        prev = {
            "governance_bench": "required",
            "performance_bench": "required",
            "rollback_check": "required",
            "functional": "required",
            "reconciliation_mode": "strict",
        }
        cur = {
            "governance_bench": "forbidden",
            "performance_bench": "forbidden",
            "rollback_check": "forbidden",
            "functional": "forbidden",
            "reconciliation_mode": "light",
        }
        merged = ExecutionContractService._merge_strictest(prev, cur)
        assert merged["governance_bench"] == "required"
        assert merged["performance_bench"] == "required"
        assert merged["rollback_check"] == "required"
        assert merged["functional"] == "required"
        assert merged["reconciliation_mode"] == "strict"

    def test_current_wins_when_stricter(self) -> None:
        prev = {
            "governance_bench": "forbidden",
            "performance_bench": "optional",
            "rollback_check": "forbidden",
            "functional": "optional",
            "reconciliation_mode": "light",
        }
        cur = {
            "governance_bench": "required",
            "performance_bench": "required",
            "rollback_check": "optional",
            "functional": "required",
            "reconciliation_mode": "strict",
        }
        merged = ExecutionContractService._merge_strictest(prev, cur)
        assert merged["governance_bench"] == "required"
        assert merged["performance_bench"] == "required"
        assert merged["rollback_check"] == "optional"
        assert merged["functional"] == "required"
        assert merged["reconciliation_mode"] == "strict"

    def test_reconciliation_standard_beats_light(self) -> None:
        prev = {
            "governance_bench": "forbidden",
            "performance_bench": "forbidden",
            "rollback_check": "forbidden",
            "functional": "required",
            "reconciliation_mode": "standard",
        }
        cur = {
            "governance_bench": "forbidden",
            "performance_bench": "forbidden",
            "rollback_check": "forbidden",
            "functional": "required",
            "reconciliation_mode": "light",
        }
        merged = ExecutionContractService._merge_strictest(prev, cur)
        assert merged["reconciliation_mode"] == "standard"

    def test_non_lane_fields_from_current(self) -> None:
        prev = {
            "governance_bench": "required",
            "performance_bench": "required",
            "rollback_check": "required",
            "functional": "required",
            "reconciliation_mode": "strict",
            "benchmark_profile": "old_profile",
            "thresholds_ref": "old_ref",
        }
        cur = {
            "governance_bench": "forbidden",
            "performance_bench": "forbidden",
            "rollback_check": "forbidden",
            "functional": "required",
            "reconciliation_mode": "light",
            "benchmark_profile": "new_profile",
            "thresholds_ref": None,
        }
        merged = ExecutionContractService._merge_strictest(prev, cur)
        # Non-lane fields come from current
        assert merged["benchmark_profile"] == "new_profile"
        assert merged["thresholds_ref"] is None


# ---------------------------------------------------------------------------
# Contract freeze: verification_requirements never weaken on re-synthesis
# ---------------------------------------------------------------------------


class TestContractVerificationFreeze:
    """Verify that re-synthesis never weakens verification_requirements."""

    def test_resynthesis_preserves_stricter_requirements(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Simulate a previous contract with high-risk requirements
        store.create_execution_contract(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            objective="previous contract",
            verification_requirements={
                "functional": "required",
                "governance_bench": "required",
                "performance_bench": "required",
                "rollback_check": "required",
                "reconciliation_mode": "strict",
                "benchmark_profile": "trustloop_governance",
                "thresholds_ref": None,
            },
        )
        # Create a new contract with low-risk for the same step_attempt
        # Using the store directly since we want to test list_execution_contracts
        contracts = store.list_execution_contracts(step_attempt_id="attempt-1")
        assert len(contracts) == 1
        prev = contracts[0]
        assert prev.verification_requirements is not None
        assert prev.verification_requirements["governance_bench"] == "required"

        # Simulate what synthesize_default does: generate low-risk requirements
        low_risk_req = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation",
            risk_level="low",
        )
        assert low_risk_req["governance_bench"] == "forbidden"

        # Merge should preserve the stricter (previous) values
        merged = ExecutionContractService._merge_strictest(
            prev.verification_requirements,
            low_risk_req,
        )
        assert merged["governance_bench"] == "required"
        assert merged["performance_bench"] == "required"
        assert merged["rollback_check"] == "required"
        assert merged["reconciliation_mode"] == "strict"
