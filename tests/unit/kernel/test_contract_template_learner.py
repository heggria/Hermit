"""Tests for contract template learning from reconciled outcomes (Criterion #8).

Verifies that:
1. Templates are learned from satisfied reconciliations
2. Templates are matched for similar subsequent actions
3. Duplicate templates boost the existing score instead of creating new records
4. Templates are degraded when their source reconciliation is violated
5. Template-conditioned contract selection works in the synthesis path
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.controller.execution_contracts import ExecutionContractService
from hermit.kernel.execution.controller.template_learner import (
    ContractTemplateLearner,
    _action_fingerprint,
    _effects_similarity,
    _normalise_effect,
)
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import ExecutionContractRecord, ReconciliationRecord
from hermit.kernel.task.services.controller import TaskController

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


def _make_contract(
    store: KernelStore,
    *,
    task_id: str = "task-1",
    step_id: str = "step-1",
    step_attempt_id: str = "attempt-1",
    action_class: str = "write_local",
    tool_name: str = "write_file",
    risk_level: str = "high",
    expected_effects: list[str] | None = None,
) -> ExecutionContractRecord:
    return store.create_execution_contract(
        task_id=task_id,
        step_id=step_id,
        step_attempt_id=step_attempt_id,
        objective=f"{tool_name}: {action_class}",
        expected_effects=expected_effects or [f"action:{action_class}"],
        success_criteria={
            "tool_name": tool_name,
            "action_class": action_class,
            "requires_receipt": True,
        },
        reversibility_class="reversible",
        required_receipt_classes=[action_class],
        drift_budget={"resource_scopes": [], "outside_workspace": False},
        risk_budget={"risk_level": risk_level, "approval_required": False},
        status="satisfied",
        action_contract_refs=[action_class],
    )


def _make_reconciliation(
    store: KernelStore,
    *,
    task_id: str = "task-1",
    step_id: str = "step-1",
    step_attempt_id: str = "attempt-1",
    contract_ref: str,
    result_class: str = "satisfied",
) -> ReconciliationRecord:
    return store.create_reconciliation(
        task_id=task_id,
        step_id=step_id,
        step_attempt_id=step_attempt_id,
        contract_ref=contract_ref,
        receipt_refs=["receipt-1"],
        observed_output_refs=[],
        intended_effect_summary="Expected side effects.",
        authorized_effect_summary="Expected side effects.",
        observed_effect_summary="Outcome reconciled.",
        receipted_effect_summary="Outcome reconciled.",
        result_class=result_class,
        recommended_resolution="promote_learning" if result_class == "satisfied" else "park",
    )


# ---------------------------------------------------------------------------
# Unit tests: fingerprint and similarity helpers
# ---------------------------------------------------------------------------


class TestEffectNormalisation:
    def test_path_normalisation_strips_directory(self) -> None:
        assert _normalise_effect("path:/home/user/project/file.py") == "path:*/file.py"

    def test_non_path_effect_unchanged(self) -> None:
        assert _normalise_effect("action:write_local") == "action:write_local"
        assert _normalise_effect("host:api.example.com") == "host:api.example.com"

    def test_action_fingerprint_deterministic(self) -> None:
        fp1 = _action_fingerprint("write_local", "write_file", ["path:/a/b.py"])
        fp2 = _action_fingerprint("write_local", "write_file", ["path:/a/b.py"])
        assert fp1 == fp2

    def test_action_fingerprint_differs_for_different_tools(self) -> None:
        fp1 = _action_fingerprint("write_local", "write_file", ["action:write_local"])
        fp2 = _action_fingerprint("write_local", "patch_file", ["action:write_local"])
        assert fp1 != fp2

    def test_effects_similarity_identical(self) -> None:
        effects = ["path:/a/b.py", "action:write_local"]
        assert _effects_similarity(effects, effects) == 1.0

    def test_effects_similarity_empty(self) -> None:
        assert _effects_similarity([], []) == 1.0

    def test_effects_similarity_disjoint(self) -> None:
        assert _effects_similarity(["action:read_local"], ["action:write_local"]) == 0.0

    def test_effects_similarity_partial(self) -> None:
        a = ["action:write_local", "path:/a/file.py"]
        b = ["action:write_local", "host:api.example.com"]
        sim = _effects_similarity(a, b)
        assert 0.0 < sim < 1.0


# ---------------------------------------------------------------------------
# Unit tests: learning from reconciliation
# ---------------------------------------------------------------------------


class TestLearnFromReconciliation:
    def test_learn_creates_contract_template_memory(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)

        memory = learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=contract,
        )

        assert memory is not None
        assert memory.memory_kind == "contract_template"
        assert memory.category == "contract_template"
        assert memory.status == "active"
        assert memory.learned_from_reconciliation_ref == reconciliation.reconciliation_id
        assert "write_local" in memory.claim_text

        sa = memory.structured_assertion
        assert sa["action_class"] == "write_local"
        assert sa["tool_name"] == "write_file"
        assert sa["fingerprint"]

    def test_learn_skips_non_satisfied_reconciliation(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(
            store, contract_ref=contract.contract_id, result_class="violated"
        )

        memory = learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=contract,
        )
        assert memory is None

    def test_duplicate_fingerprint_boosts_existing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        recon1 = _make_reconciliation(store, contract_ref=contract.contract_id)
        recon2 = _make_reconciliation(store, contract_ref=contract.contract_id)

        mem1 = learner.learn_from_reconciliation(reconciliation=recon1, contract=contract)
        mem2 = learner.learn_from_reconciliation(reconciliation=recon2, contract=contract)

        # Should return the same record (boosted, not duplicated)
        assert mem1 is not None
        assert mem2 is not None
        assert mem1.memory_id == mem2.memory_id

        # Only one active contract_template should exist
        templates = [
            r
            for r in store.list_memory_records(status="active", limit=100)
            if r.memory_kind == "contract_template"
        ]
        assert len(templates) == 1


# ---------------------------------------------------------------------------
# Unit tests: template matching
# ---------------------------------------------------------------------------


class TestFindMatchingTemplate:
    def test_finds_matching_template(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)

        template = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/readme.md"],
        )
        assert template is not None
        assert template.action_class == "write_local"
        assert template.tool_name == "write_file"

    def test_no_match_for_different_action_class(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store, action_class="write_local")
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)

        template = learner.find_matching_template(
            action_class="execute_command",
            tool_name="bash",
            expected_effects=["action:execute_command"],
        )
        assert template is None

    def test_no_match_when_no_templates_exist(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        template = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["action:write_local"],
        )
        assert template is None


# ---------------------------------------------------------------------------
# Unit tests: template degradation on violation
# ---------------------------------------------------------------------------


class TestTemplateDegradation:
    def test_degrade_invalidates_template_after_enough_failures(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)
        assert memory is not None

        # Build up invocation_count to >= 5 with all failures
        sa = dict(memory.structured_assertion)
        sa["invocation_count"] = 5
        sa["success_count"] = 0
        sa["failure_count"] = 4
        sa["success_rate"] = 0.0
        store.update_memory_record(memory.memory_id, structured_assertion=sa)

        affected = learner.degrade_templates_for_violation(reconciliation.reconciliation_id)
        assert memory.memory_id in affected

        record = store.get_memory_record(memory.memory_id)
        assert record is not None
        assert record.status == "invalidated"

    def test_degrade_does_nothing_for_unrelated_reconciliation(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)

        affected = learner.degrade_templates_for_violation("unrelated-reconciliation-id")
        assert affected == []

    def test_single_violation_does_not_invalidate_template(self, tmp_path: Path) -> None:
        """With success_rate-based degradation, a single violation should NOT invalidate."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)
        assert memory is not None

        # One violation should not invalidate (invocation_count < 5)
        affected = learner.degrade_templates_for_violation(reconciliation.reconciliation_id)
        assert affected == []

        # Template should still be found
        record = store.get_memory_record(memory.memory_id)
        assert record is not None
        assert record.status == "active"
        sa = record.structured_assertion
        assert sa["failure_count"] == 1


# ---------------------------------------------------------------------------
# Unit tests: success rate tracking
# ---------------------------------------------------------------------------


class TestSuccessRateTracking:
    def test_record_template_outcome_tracks_success(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)
        assert memory is not None

        # Record 3 successful outcomes
        for _ in range(3):
            learner.record_template_outcome(
                template_ref=contract.contract_id,
                result_class="satisfied",
            )

        record = store.get_memory_record(memory.memory_id)
        assert record is not None
        sa = record.structured_assertion
        assert sa["invocation_count"] == 3
        assert sa["success_count"] == 3
        assert sa["failure_count"] == 0
        assert sa["success_rate"] == 1.0

    def test_record_template_outcome_tracks_failures(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)
        assert memory is not None

        # Record 1 success and 1 failure
        learner.record_template_outcome(template_ref=contract.contract_id, result_class="satisfied")
        learner.record_template_outcome(template_ref=contract.contract_id, result_class="violated")

        record = store.get_memory_record(memory.memory_id)
        assert record is not None
        sa = record.structured_assertion
        assert sa["invocation_count"] == 2
        assert sa["success_count"] == 1
        assert sa["failure_count"] == 1
        assert sa["success_rate"] == 0.5
        assert sa["last_failure_at"] is not None

    def test_auto_invalidation_on_low_success_rate(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)
        assert memory is not None

        # Record 5 failures, 1 success → success_rate = 1/6 ≈ 0.167 < 0.3
        learner.record_template_outcome(template_ref=contract.contract_id, result_class="satisfied")
        for _ in range(5):
            learner.record_template_outcome(
                template_ref=contract.contract_id, result_class="violated"
            )

        record = store.get_memory_record(memory.memory_id)
        assert record is not None
        assert record.status == "invalidated"
        assert "low_success_rate" in (record.invalidation_reason or "")

    def test_no_invalidation_with_good_success_rate(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)
        assert memory is not None

        # Record 4 successes, 1 failure → success_rate = 0.8, invocation_count = 5
        for _ in range(4):
            learner.record_template_outcome(
                template_ref=contract.contract_id, result_class="satisfied"
            )
        learner.record_template_outcome(template_ref=contract.contract_id, result_class="violated")

        record = store.get_memory_record(memory.memory_id)
        assert record is not None
        assert record.status == "active"

    def test_new_templates_have_tracking_fields(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)
        assert memory is not None

        sa = memory.structured_assertion
        assert sa["invocation_count"] == 0
        assert sa["success_count"] == 0
        assert sa["failure_count"] == 0
        assert sa["success_rate"] == 0.0
        assert sa["last_failure_at"] is None

    def test_find_matching_template_returns_tracking_fields(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(
            store,
            expected_effects=["path:/workspace/file.txt"],
        )
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)

        # Record some outcomes
        learner.record_template_outcome(template_ref=contract.contract_id, result_class="satisfied")
        learner.record_template_outcome(template_ref=contract.contract_id, result_class="satisfied")

        template = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/file.txt"],
        )
        assert template is not None
        assert template.invocation_count == 2
        assert template.success_count == 2
        assert template.success_rate == 1.0


# ---------------------------------------------------------------------------
# Integration: template improves subsequent contract selection
# ---------------------------------------------------------------------------


class TestTemplateConditionedContractSelection:
    """Verify that a learned template is referenced in subsequent contract synthesis."""

    def test_template_ref_set_on_subsequent_contract(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        service = ExecutionContractService(store, artifacts)
        controller = TaskController(store)

        # -- Phase 1: create a contract and satisfy it to learn a template --
        ctx1 = controller.start_task(
            conversation_id="chat-1",
            goal="write a file",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )
        contract1 = _make_contract(
            store,
            task_id=ctx1.task_id,
            step_id=ctx1.step_id,
            step_attempt_id=ctx1.step_attempt_id,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/output.txt"],
        )
        recon1 = _make_reconciliation(
            store,
            task_id=ctx1.task_id,
            step_id=ctx1.step_id,
            step_attempt_id=ctx1.step_attempt_id,
            contract_ref=contract1.contract_id,
        )
        service.template_learner.learn_from_reconciliation(
            reconciliation=recon1, contract=contract1
        )

        # -- Phase 2: synthesize a new contract for a similar action ----------
        ctx2 = controller.start_task(
            conversation_id="chat-2",
            goal="write another file",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )

        from hermit.kernel.policy.models.models import (
            ActionRequest,
            PolicyDecision,
            PolicyObligations,
        )
        from hermit.runtime.capability.registry.tools import ToolSpec

        tool = ToolSpec(
            name="write_file",
            description="Write a file",
            input_schema={},
            handler=lambda kw: None,
            action_class="write_local",
            risk_hint="high",
            requires_receipt=True,
        )
        action_request = ActionRequest(
            request_id="req-2",
            task_id=ctx2.task_id,
            step_attempt_id=ctx2.step_attempt_id,
            tool_name="write_file",
            action_class="write_local",
            resource_scopes=[],
            derived={"target_paths": ["/workspace/output.txt"]},
        )
        policy = PolicyDecision(
            verdict="allow",
            risk_level="high",
            action_class="write_local",
            obligations=PolicyObligations(),
        )

        new_contract, _artifact_ref = service.synthesize_default(
            attempt_ctx=ctx2,
            tool=tool,
            action_request=action_request,
            policy=policy,
            action_request_ref=None,
            witness_ref=None,
        )

        # The new contract should reference the learned template
        assert new_contract.selected_template_ref is not None

        # And the step_attempt should have the template ref
        attempt = store.get_step_attempt(ctx2.step_attempt_id)
        assert attempt is not None
        assert attempt.selected_contract_template_ref is not None

    def test_no_template_ref_when_no_templates_learned(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        service = ExecutionContractService(store, artifacts)
        controller = TaskController(store)

        ctx = controller.start_task(
            conversation_id="chat-clean",
            goal="first-time action",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )

        from hermit.kernel.policy.models.models import (
            ActionRequest,
            PolicyDecision,
            PolicyObligations,
        )
        from hermit.runtime.capability.registry.tools import ToolSpec

        tool = ToolSpec(
            name="bash",
            description="Run command",
            input_schema={},
            handler=lambda kw: None,
            action_class="execute_command",
            risk_hint="critical",
            requires_receipt=True,
        )
        action_request = ActionRequest(
            request_id="req-clean",
            task_id=ctx.task_id,
            step_attempt_id=ctx.step_attempt_id,
            tool_name="bash",
            action_class="execute_command",
            resource_scopes=[],
            derived={},
        )
        policy = PolicyDecision(
            verdict="allow",
            risk_level="critical",
            action_class="execute_command",
            obligations=PolicyObligations(),
        )

        new_contract, _ = service.synthesize_default(
            attempt_ctx=ctx,
            tool=tool,
            action_request=action_request,
            policy=policy,
            action_request_ref=None,
            witness_ref=None,
        )

        assert new_contract.selected_template_ref is None


# ---------------------------------------------------------------------------
# End-to-end: learned templates improve subsequent similar task decisions
# ---------------------------------------------------------------------------


class TestLearnedTemplatesImproveDecisions:
    """Core Criterion #8 assertion: learned templates from successful outcomes
    improve subsequent similar task decisions by providing a template reference
    and carrying forward successful parameters.
    """

    def test_full_learn_match_cycle(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        # --- First run: no templates, action executes without template guidance ---
        template_before = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/project/src/config.py"],
        )
        assert template_before is None

        # --- Simulate a satisfied reconciliation ---
        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/project/src/config.py"],
            risk_level="high",
        )
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        learned = learner.learn_from_reconciliation(
            reconciliation=reconciliation, contract=contract
        )
        assert learned is not None

        # --- Second run: template available for same file in different directory ---
        template_after = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/project/config.py"],
        )
        assert template_after is not None
        assert template_after.action_class == "write_local"
        assert template_after.risk_level == "high"
        assert template_after.source_contract_ref == contract.contract_id

    def test_violated_reconciliation_degrades_template(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        # Learn a template
        contract = _make_contract(store, action_class="write_local", tool_name="write_file")
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        learned = learner.learn_from_reconciliation(
            reconciliation=reconciliation, contract=contract
        )
        assert learned is not None

        # Verify template exists
        template = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["action:write_local"],
        )
        assert template is not None

        # Single violation records failure but does NOT invalidate (invocation < 5)
        learner.degrade_templates_for_violation(reconciliation.reconciliation_id)

        # Template should still be found (gradual degradation)
        template_after = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["action:write_local"],
        )
        assert template_after is not None
        assert template_after.failure_count == 1

        # Build up to 5 failures → should invalidate
        sa = dict(learned.structured_assertion)
        sa["invocation_count"] = 5
        sa["success_count"] = 0
        sa["failure_count"] = 4
        sa["success_rate"] = 0.0
        store.update_memory_record(learned.memory_id, structured_assertion=sa)

        learner.degrade_templates_for_violation(reconciliation.reconciliation_id)

        # Now template should be invalidated
        template_gone = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["action:write_local"],
        )
        assert template_gone is None

    def test_template_drift_budget_tightens_contract(self, tmp_path: Path) -> None:
        """Template drift_budget should tighten (not relax) the synthesized contract."""
        store = _make_store(tmp_path)
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        service = ExecutionContractService(store, artifacts)
        controller = TaskController(store)

        # Phase 1: learn a template with restrictive drift_budget
        ctx1 = controller.start_task(
            conversation_id="chat-drift-1",
            goal="write a file",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )
        contract1 = store.create_execution_contract(
            task_id=ctx1.task_id,
            step_id=ctx1.step_id,
            step_attempt_id=ctx1.step_attempt_id,
            objective="write_file: write_local",
            expected_effects=["path:/workspace/output.txt"],
            success_criteria={
                "tool_name": "write_file",
                "action_class": "write_local",
                "requires_receipt": True,
            },
            reversibility_class="reversible",
            required_receipt_classes=["write_local"],
            drift_budget={
                "resource_scopes": ["scope_a", "scope_b"],
                "outside_workspace": False,
                "requires_witness": True,
            },
            risk_budget={"risk_level": "high", "approval_required": False},
            status="satisfied",
            action_contract_refs=["write_local"],
        )
        recon1 = _make_reconciliation(
            store,
            task_id=ctx1.task_id,
            step_id=ctx1.step_id,
            step_attempt_id=ctx1.step_attempt_id,
            contract_ref=contract1.contract_id,
        )
        service.template_learner.learn_from_reconciliation(
            reconciliation=recon1, contract=contract1
        )

        # Phase 2: synthesize with broader request — template should tighten
        ctx2 = controller.start_task(
            conversation_id="chat-drift-2",
            goal="write another file",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )

        from hermit.kernel.policy.models.models import (
            ActionRequest,
            PolicyDecision,
            PolicyObligations,
        )
        from hermit.runtime.capability.registry.tools import ToolSpec

        tool = ToolSpec(
            name="write_file",
            description="Write a file",
            input_schema={},
            handler=lambda kw: None,
            action_class="write_local",
            risk_hint="high",
            requires_receipt=True,
        )
        action_request = ActionRequest(
            request_id="req-drift",
            task_id=ctx2.task_id,
            step_attempt_id=ctx2.step_attempt_id,
            tool_name="write_file",
            action_class="write_local",
            resource_scopes=["scope_a", "scope_c"],  # broader than template
            derived={
                "target_paths": ["/workspace/output.txt"],
                "outside_workspace": True,  # request says True
            },
        )
        policy = PolicyDecision(
            verdict="allow",
            risk_level="high",
            action_class="write_local",
            obligations=PolicyObligations(),
        )

        new_contract, _ = service.synthesize_default(
            attempt_ctx=ctx2,
            tool=tool,
            action_request=action_request,
            policy=policy,
            action_request_ref=None,
            witness_ref=None,
        )

        budget = new_contract.drift_budget
        # Template scopes intersected: only scope_a (in both)
        assert budget["resource_scopes"] == ["scope_a"]
        # Template outside_workspace=False overrides request True
        assert budget["outside_workspace"] is False
        # Template requires_witness=True overrides request False
        assert budget["requires_witness"] is True
        # contract_template.applied event should have been emitted
        events = store.list_events(task_id=ctx2.task_id, limit=50)
        applied_events = [e for e in events if e.get("event_type") == "contract_template.applied"]
        assert len(applied_events) == 1

    def test_multiple_learnings_reinforce_template(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/project/src/main.py"],
        )

        # Learn from three separate reconciliations
        for _i in range(3):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(reconciliation=recon, contract=contract)

        # Same file in different directory matches
        template = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/project/main.py"],
        )
        assert template is not None

        # Only one template record should exist (reinforced, not duplicated)
        templates = [
            r
            for r in store.list_memory_records(status="active", limit=100)
            if r.memory_kind == "contract_template"
        ]
        assert len(templates) == 1
