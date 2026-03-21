"""Integration tests: ExecutionContract enrichment → verification_requirements → freeze on re-synthesis.

Covers the full chain from risk-level-based enrichment through task family inference,
strictness merging (freeze semantics), store round-trip, and artifact payload synthesis.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.controller.execution_contracts import (
    ExecutionContractService,
)
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> tuple[KernelStore, ArtifactStore, TaskController]:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    return store, artifacts, controller


def _start_task(controller: TaskController, workspace: str):
    return controller.start_task(
        conversation_id="contract-chain-test",
        goal="contract chain integration",
        source_channel="chat",
        kind="respond",
        workspace_root=workspace,
    )


# ---------------------------------------------------------------------------
# 1. Enrichment by risk level
# ---------------------------------------------------------------------------


class TestEnrichmentByRiskLevel:
    """enrich_verification_requirements must map risk_level to bench requirement levels."""

    def test_high_risk_requires_all(self) -> None:
        reqs = ExecutionContractService.enrich_verification_requirements(risk_level="high")
        assert reqs["governance_bench"] == "required"
        assert reqs["performance_bench"] == "required"
        assert reqs["rollback_check"] == "required"
        assert reqs["reconciliation_mode"] == "strict"

    def test_critical_risk_same_as_high(self) -> None:
        reqs = ExecutionContractService.enrich_verification_requirements(risk_level="critical")
        assert reqs["governance_bench"] == "required"
        assert reqs["performance_bench"] == "required"
        assert reqs["rollback_check"] == "required"
        assert reqs["reconciliation_mode"] == "strict"

    def test_medium_risk_optional(self) -> None:
        reqs = ExecutionContractService.enrich_verification_requirements(risk_level="medium")
        assert reqs["governance_bench"] == "optional"
        assert reqs["performance_bench"] == "optional"
        assert reqs["rollback_check"] == "optional"
        assert reqs["reconciliation_mode"] == "standard"

    def test_low_risk_forbidden(self) -> None:
        reqs = ExecutionContractService.enrich_verification_requirements(risk_level="low")
        assert reqs["governance_bench"] == "forbidden"
        assert reqs["performance_bench"] == "forbidden"
        assert reqs["rollback_check"] == "forbidden"
        assert reqs["reconciliation_mode"] == "light"

    def test_functional_always_required(self) -> None:
        """The 'functional' lane is always required regardless of risk level."""
        for risk in ("low", "medium", "high", "critical"):
            reqs = ExecutionContractService.enrich_verification_requirements(risk_level=risk)
            assert reqs["functional"] == "required", f"functional should be required at {risk}"


# ---------------------------------------------------------------------------
# 2. Task family inference
# ---------------------------------------------------------------------------


class TestTaskFamilyInference:
    """_infer_task_family derives task family from action_class."""

    def test_write_local_is_governance_mutation(self) -> None:
        """write_local is in _GOVERNANCE_MUTATION_CLASSES -> governance_mutation."""
        result = ExecutionContractService._infer_task_family("write_local")
        assert result == "governance_mutation"

    def test_write_remote_is_governance_mutation(self) -> None:
        result = ExecutionContractService._infer_task_family("write_remote")
        assert result == "governance_mutation"

    def test_execute_local_is_governance_mutation(self) -> None:
        result = ExecutionContractService._infer_task_family("execute_local")
        assert result == "governance_mutation"

    def test_delete_local_is_governance_mutation(self) -> None:
        result = ExecutionContractService._infer_task_family("delete_local")
        assert result == "governance_mutation"

    def test_network_request_is_surface_integration(self) -> None:
        result = ExecutionContractService._infer_task_family("network_request")
        assert result == "surface_integration"

    def test_read_remote_is_surface_integration(self) -> None:
        result = ExecutionContractService._infer_task_family("read_remote")
        assert result == "surface_integration"

    def test_approval_resolution_returns_none(self) -> None:
        """approval_resolution is not in any classification set -> None."""
        result = ExecutionContractService._infer_task_family("approval_resolution")
        assert result is None

    def test_network_write_returns_none(self) -> None:
        """network_write is not in any classification set -> None."""
        result = ExecutionContractService._infer_task_family("network_write")
        assert result is None

    def test_unknown_action_returns_none(self) -> None:
        result = ExecutionContractService._infer_task_family("totally_unknown")
        assert result is None

    def test_benchmark_profile_from_governance_mutation(self) -> None:
        """governance_mutation family -> trustloop_governance benchmark profile."""
        reqs = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation",
            risk_level="high",
        )
        assert reqs["benchmark_profile"] == "trustloop_governance"

    def test_benchmark_profile_from_runtime_perf(self) -> None:
        """runtime_perf family -> runtime_perf benchmark profile."""
        reqs = ExecutionContractService.enrich_verification_requirements(
            task_family="runtime_perf",
            risk_level="high",
        )
        assert reqs["benchmark_profile"] == "runtime_perf"

    def test_benchmark_profile_from_surface_integration(self) -> None:
        """surface_integration family -> integration_regression benchmark profile."""
        reqs = ExecutionContractService.enrich_verification_requirements(
            task_family="surface_integration",
            risk_level="high",
        )
        assert reqs["benchmark_profile"] == "integration_regression"

    def test_benchmark_profile_from_learning_template(self) -> None:
        """learning_template family -> template_quality benchmark profile."""
        reqs = ExecutionContractService.enrich_verification_requirements(
            task_family="learning_template",
            risk_level="high",
        )
        assert reqs["benchmark_profile"] == "template_quality"

    def test_benchmark_profile_none_family(self) -> None:
        """None task family -> 'none' benchmark profile."""
        reqs = ExecutionContractService.enrich_verification_requirements(
            task_family=None,
            risk_level="high",
        )
        assert reqs["benchmark_profile"] == "none"


# ---------------------------------------------------------------------------
# 3. Merge strictest (freeze semantics)
# ---------------------------------------------------------------------------


class TestMergeStrictest:
    """_merge_strictest keeps the stricter value per field across contracts."""

    def test_required_plus_optional_stays_required(self) -> None:
        previous = {"governance_bench": "required", "performance_bench": "required"}
        current = {"governance_bench": "optional", "performance_bench": "optional"}
        merged = ExecutionContractService._merge_strictest(previous, current)
        assert merged["governance_bench"] == "required"
        assert merged["performance_bench"] == "required"

    def test_optional_plus_required_becomes_required(self) -> None:
        previous = {"governance_bench": "optional", "performance_bench": "optional"}
        current = {"governance_bench": "required", "performance_bench": "required"}
        merged = ExecutionContractService._merge_strictest(previous, current)
        assert merged["governance_bench"] == "required"
        assert merged["performance_bench"] == "required"

    def test_forbidden_plus_required_becomes_required(self) -> None:
        previous = {"governance_bench": "forbidden", "performance_bench": "forbidden"}
        current = {"governance_bench": "required", "performance_bench": "required"}
        merged = ExecutionContractService._merge_strictest(previous, current)
        assert merged["governance_bench"] == "required"
        assert merged["performance_bench"] == "required"

    def test_forbidden_plus_optional_becomes_optional(self) -> None:
        previous = {"governance_bench": "forbidden"}
        current = {"governance_bench": "optional"}
        merged = ExecutionContractService._merge_strictest(previous, current)
        assert merged["governance_bench"] == "optional"

    def test_reconciliation_strict_plus_light_stays_strict(self) -> None:
        previous = {"reconciliation_mode": "strict"}
        current = {"reconciliation_mode": "light"}
        merged = ExecutionContractService._merge_strictest(previous, current)
        assert merged["reconciliation_mode"] == "strict"

    def test_reconciliation_light_plus_strict_becomes_strict(self) -> None:
        previous = {"reconciliation_mode": "light"}
        current = {"reconciliation_mode": "strict"}
        merged = ExecutionContractService._merge_strictest(previous, current)
        assert merged["reconciliation_mode"] == "strict"

    def test_reconciliation_standard_plus_strict_becomes_strict(self) -> None:
        previous = {"reconciliation_mode": "standard"}
        current = {"reconciliation_mode": "strict"}
        merged = ExecutionContractService._merge_strictest(previous, current)
        assert merged["reconciliation_mode"] == "strict"

    def test_functional_stays_required(self) -> None:
        """functional is always 'required' — merge should preserve it."""
        previous = {"functional": "required"}
        current = {"functional": "required"}
        merged = ExecutionContractService._merge_strictest(previous, current)
        assert merged["functional"] == "required"

    def test_rollback_check_escalates(self) -> None:
        previous = {"rollback_check": "optional"}
        current = {"rollback_check": "required"}
        merged = ExecutionContractService._merge_strictest(previous, current)
        assert merged["rollback_check"] == "required"

    def test_full_merge_scenario(self) -> None:
        """Simulate a real scenario: high-risk contract followed by low-risk re-synthesis."""
        previous = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation", risk_level="high"
        )
        current = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation", risk_level="low"
        )
        merged = ExecutionContractService._merge_strictest(previous, current)

        # The freeze semantics mean the high-risk requirements are preserved.
        assert merged["governance_bench"] == "required"
        assert merged["performance_bench"] == "required"
        assert merged["rollback_check"] == "required"
        assert merged["reconciliation_mode"] == "strict"
        assert merged["functional"] == "required"


# ---------------------------------------------------------------------------
# 4. Contract store round-trip
# ---------------------------------------------------------------------------


class TestContractStoreRoundTrip:
    """Create a contract with verification_requirements via store, read back, verify."""

    def test_round_trip_preserves_all_fields(self, tmp_path: Path) -> None:
        store, _artifacts, controller = _make_store(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        ctx = _start_task(controller, str(workspace))

        verification_requirements: dict[str, Any] = {
            "functional": "required",
            "governance_bench": "required",
            "performance_bench": "optional",
            "rollback_check": "required",
            "reconciliation_mode": "strict",
            "benchmark_profile": "trustloop_governance",
            "thresholds_ref": "bench://governance/v1",
        }

        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="round-trip test",
            status="admissibility_pending",
            task_family="governance_mutation",
            verification_requirements=verification_requirements,
        )

        # Read back via get.
        loaded = store.get_execution_contract(contract.contract_id)
        assert loaded is not None
        assert loaded.task_family == "governance_mutation"
        assert loaded.verification_requirements is not None

        vr = loaded.verification_requirements
        assert vr["functional"] == "required"
        assert vr["governance_bench"] == "required"
        assert vr["performance_bench"] == "optional"
        assert vr["rollback_check"] == "required"
        assert vr["reconciliation_mode"] == "strict"
        assert vr["benchmark_profile"] == "trustloop_governance"
        assert vr["thresholds_ref"] == "bench://governance/v1"

    def test_round_trip_via_list(self, tmp_path: Path) -> None:
        store, _artifacts, controller = _make_store(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        ctx = _start_task(controller, str(workspace))

        verification_requirements: dict[str, Any] = {
            "functional": "required",
            "governance_bench": "optional",
            "performance_bench": "forbidden",
            "rollback_check": "optional",
            "reconciliation_mode": "standard",
            "benchmark_profile": "runtime_perf",
            "thresholds_ref": None,
        }

        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="round-trip list test",
            status="admissibility_pending",
            task_family="runtime_perf",
            verification_requirements=verification_requirements,
        )

        # Read back via list.
        contracts = store.list_execution_contracts(
            step_attempt_id=ctx.step_attempt_id,
        )
        assert len(contracts) >= 1
        loaded = next(c for c in contracts if c.contract_id == contract.contract_id)
        assert loaded.task_family == "runtime_perf"
        assert loaded.verification_requirements is not None
        assert loaded.verification_requirements["benchmark_profile"] == "runtime_perf"

    def test_round_trip_none_verification_requirements(self, tmp_path: Path) -> None:
        """A contract created without verification_requirements should read back as None."""
        store, _artifacts, controller = _make_store(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        ctx = _start_task(controller, str(workspace))

        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="no vr test",
            status="draft",
        )

        loaded = store.get_execution_contract(contract.contract_id)
        assert loaded is not None
        assert loaded.verification_requirements is None

    def test_round_trip_preserves_task_family(self, tmp_path: Path) -> None:
        """Verify task_family survives serialization round-trip."""
        store, _artifacts, controller = _make_store(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        ctx = _start_task(controller, str(workspace))

        for family in ("governance_mutation", "runtime_perf", "surface_integration", None):
            contract = store.create_execution_contract(
                task_id=ctx.task_id,
                step_id=ctx.step_id,
                step_attempt_id=ctx.step_attempt_id,
                objective=f"family-{family} test",
                status="draft",
                task_family=family,
            )
            loaded = store.get_execution_contract(contract.contract_id)
            assert loaded is not None
            assert loaded.task_family == family, (
                f"Expected task_family={family!r}, got {loaded.task_family!r}"
            )


# ---------------------------------------------------------------------------
# 5. Artifact payload
# ---------------------------------------------------------------------------


class TestArtifactPayload:
    """Verify synthesized contract artifact includes required fields."""

    def test_artifact_includes_scope_constraints_acceptance_rollback(self, tmp_path: Path) -> None:
        store, artifacts, controller = _make_store(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        ctx = _start_task(controller, str(workspace))

        svc = ExecutionContractService(store, artifacts)

        # Build a minimal contract payload the same way synthesize_default would.
        drift_budget: dict[str, Any] = {
            "resource_scopes": [str(workspace)],
            "outside_workspace": False,
            "requires_witness": True,
        }
        success_criteria: dict[str, Any] = {
            "tool_name": "write_file",
            "action_class": "write_local",
            "requires_receipt": True,
        }
        risk_budget: dict[str, Any] = {
            "risk_level": "high",
            "approval_required": True,
        }
        verification_requirements = svc.enrich_verification_requirements(
            task_family="governance_mutation", risk_level="high"
        )

        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="write_file: write_local",
            status="admissibility_pending",
            success_criteria=success_criteria,
            drift_budget=drift_budget,
            risk_budget=risk_budget,
            rollback_expectation="file_restore",
            task_family="governance_mutation",
            verification_requirements=verification_requirements,
        )

        # Simulate the artifact payload as produced by _store_artifact.
        artifact_payload = {
            "contract_id": contract.contract_id,
            "objective": contract.objective,
            "scope": {
                "resource_scopes": list(drift_budget.get("resource_scopes", [])),
                "outside_workspace": drift_budget.get("outside_workspace", False),
            },
            "constraints": {
                "risk_level": contract.risk_budget.get("risk_level", "low"),
                "approval_required": contract.risk_budget.get("approval_required", False),
                "requires_witness": drift_budget.get("requires_witness", False),
            },
            "acceptance": contract.success_criteria,
            "verification_requirements": contract.verification_requirements,
            "rollback_hint": contract.rollback_expectation,
            "expected_effects": contract.expected_effects,
            "required_receipt_classes": contract.required_receipt_classes,
            "risk_budget": contract.risk_budget,
            "drift_budget": contract.drift_budget,
            "reversibility_class": contract.reversibility_class,
            "operator_summary": contract.operator_summary,
            "task_family": contract.task_family,
        }

        # Verify the four required top-level keys.
        assert "scope" in artifact_payload
        assert "constraints" in artifact_payload
        assert "acceptance" in artifact_payload
        assert "rollback_hint" in artifact_payload

        # Verify scope contents.
        assert artifact_payload["scope"]["resource_scopes"] == [str(workspace)]
        assert artifact_payload["scope"]["outside_workspace"] is False

        # Verify constraints contents.
        assert artifact_payload["constraints"]["risk_level"] == "high"
        assert artifact_payload["constraints"]["approval_required"] is True
        assert artifact_payload["constraints"]["requires_witness"] is True

        # Verify acceptance contents.
        assert artifact_payload["acceptance"]["tool_name"] == "write_file"
        assert artifact_payload["acceptance"]["action_class"] == "write_local"

        # Verify rollback_hint.
        assert artifact_payload["rollback_hint"] == "file_restore"

        # Verify verification_requirements is present.
        assert artifact_payload["verification_requirements"] is not None
        assert artifact_payload["verification_requirements"]["governance_bench"] == "required"

        # Verify task_family.
        assert artifact_payload["task_family"] == "governance_mutation"

    def test_artifact_stored_and_retrievable(self, tmp_path: Path) -> None:
        """Verify an artifact payload can actually be stored via ArtifactStore."""
        _store, artifacts, _controller = _make_store(tmp_path)

        payload: dict[str, Any] = {
            "contract_id": "contract_test_001",
            "scope": {"resource_scopes": ["/tmp/test"], "outside_workspace": False},
            "constraints": {"risk_level": "medium", "approval_required": False},
            "acceptance": {"tool_name": "bash", "requires_receipt": True},
            "rollback_hint": "manual_or_followup",
        }

        uri, content_hash = artifacts.store_json(payload)
        assert uri is not None
        assert content_hash is not None
        assert len(content_hash) > 0


# ---------------------------------------------------------------------------
# 6. All 7 VerificationRequirements fields
# ---------------------------------------------------------------------------


class TestAllVerificationRequirementsFields:
    """Verify all 7 fields are present and correctly typed."""

    EXPECTED_FIELDS = {
        "functional",
        "governance_bench",
        "performance_bench",
        "rollback_check",
        "reconciliation_mode",
        "benchmark_profile",
        "thresholds_ref",
    }

    @pytest.mark.parametrize("risk_level", ["low", "medium", "high", "critical"])
    def test_all_fields_present(self, risk_level: str) -> None:
        reqs = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation", risk_level=risk_level
        )
        for field_name in self.EXPECTED_FIELDS:
            assert field_name in reqs, f"Missing field: {field_name} at risk={risk_level}"

    @pytest.mark.parametrize("risk_level", ["low", "medium", "high", "critical"])
    def test_lane_fields_are_strings(self, risk_level: str) -> None:
        reqs = ExecutionContractService.enrich_verification_requirements(risk_level=risk_level)
        for lane in ("functional", "governance_bench", "performance_bench", "rollback_check"):
            assert isinstance(reqs[lane], str), f"{lane} should be str, got {type(reqs[lane])}"
            assert reqs[lane] in {"required", "optional", "forbidden"}, (
                f"{lane}={reqs[lane]!r} is not a valid lane value"
            )

    @pytest.mark.parametrize("risk_level", ["low", "medium", "high", "critical"])
    def test_reconciliation_mode_is_string(self, risk_level: str) -> None:
        reqs = ExecutionContractService.enrich_verification_requirements(risk_level=risk_level)
        assert isinstance(reqs["reconciliation_mode"], str)
        assert reqs["reconciliation_mode"] in {"light", "standard", "strict"}

    def test_benchmark_profile_is_string(self) -> None:
        reqs = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation", risk_level="high"
        )
        assert isinstance(reqs["benchmark_profile"], str)

    def test_thresholds_ref_is_none_by_default(self) -> None:
        reqs = ExecutionContractService.enrich_verification_requirements(risk_level="high")
        assert reqs["thresholds_ref"] is None

    def test_round_trip_all_7_fields(self, tmp_path: Path) -> None:
        """Store and retrieve a contract with all 7 verification_requirements fields."""
        store, _artifacts, controller = _make_store(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        ctx = _start_task(controller, str(workspace))

        reqs = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation", risk_level="high"
        )
        # Set thresholds_ref to a non-None value for this test.
        reqs["thresholds_ref"] = "bench://governance/thresholds/v2"

        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="all-7-fields test",
            status="admissibility_pending",
            task_family="governance_mutation",
            verification_requirements=reqs,
        )

        loaded = store.get_execution_contract(contract.contract_id)
        assert loaded is not None
        assert loaded.verification_requirements is not None

        vr = loaded.verification_requirements
        assert vr["functional"] == "required"
        assert vr["governance_bench"] == "required"
        assert vr["performance_bench"] == "required"
        assert vr["rollback_check"] == "required"
        assert vr["reconciliation_mode"] == "strict"
        assert vr["benchmark_profile"] == "trustloop_governance"
        assert vr["thresholds_ref"] == "bench://governance/thresholds/v2"

        # Verify count of fields.
        assert len(self.EXPECTED_FIELDS - set(vr.keys())) == 0, (
            f"Missing fields after round-trip: {self.EXPECTED_FIELDS - set(vr.keys())}"
        )
