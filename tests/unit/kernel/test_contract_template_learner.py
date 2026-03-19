"""Tests for contract template learning from reconciled outcomes (Criterion #8).

Verifies that:
1. Templates are learned from satisfied reconciliations
2. Templates are matched for similar subsequent actions (after promotion)
3. Duplicate templates boost the existing score instead of creating new records
4. Templates are degraded when their source reconciliation is violated
5. Template-conditioned contract selection works in the synthesis path
6. Promotion threshold prevents premature template matching
7. Low-confidence matches are skipped
8. apply_template produces pre-filled contract parameters
9. TemplateMatch provides confidence and reasons
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.controller.execution_contracts import ExecutionContractService
from hermit.kernel.execution.controller.template_learner import (
    _PROMOTION_THRESHOLD,
    ContractTemplateLearner,
    _action_fingerprint,
    _effects_similarity,
    _normalise_effect,
)
from hermit.kernel.execution.controller.template_models import (
    ContractTemplate,
    TemplateMatch,
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


def _promote_template(
    store: KernelStore,
    learner: ContractTemplateLearner,
    contract: ExecutionContractRecord,
    *,
    count: int = _PROMOTION_THRESHOLD,
) -> None:
    """Reinforce a template enough times to meet the promotion threshold."""
    for _ in range(count):
        recon = _make_reconciliation(store, contract_ref=contract.contract_id)
        learner.learn_from_reconciliation(reconciliation=recon, contract=contract)


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
        assert sa["success_count"] == 1
        assert sa["resource_scope_pattern"] == []
        assert sa["evidence_requirements"] == ["write_local"]

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
# Unit tests: promotion threshold
# ---------------------------------------------------------------------------


class TestPromotionThreshold:
    def test_template_not_matched_before_promotion(self, tmp_path: Path) -> None:
        """A template with fewer than _PROMOTION_THRESHOLD successes is not matched."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )
        # Only 1 reconciliation (below threshold of 3)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)

        template = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/readme.md"],
        )
        assert template is None

    def test_template_matched_after_promotion(self, tmp_path: Path) -> None:
        """A template with >= _PROMOTION_THRESHOLD successes is matched."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )
        _promote_template(store, learner, contract)

        template = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/readme.md"],
        )
        assert template is not None
        assert template.action_class == "write_local"
        assert template.tool_name == "write_file"

    def test_promotion_threshold_exact_boundary(self, tmp_path: Path) -> None:
        """Template becomes available exactly at _PROMOTION_THRESHOLD."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/file.py"],
        )

        # Reinforce to threshold - 1
        _promote_template(store, learner, contract, count=_PROMOTION_THRESHOLD - 1)
        assert (
            learner.find_matching_template(
                action_class="write_local",
                tool_name="write_file",
                expected_effects=["path:/other/file.py"],
            )
            is None
        )

        # One more pushes it over
        recon = _make_reconciliation(store, contract_ref=contract.contract_id)
        learner.learn_from_reconciliation(reconciliation=recon, contract=contract)
        assert (
            learner.find_matching_template(
                action_class="write_local",
                tool_name="write_file",
                expected_effects=["path:/other/file.py"],
            )
            is not None
        )


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
        _promote_template(store, learner, contract)

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
        _promote_template(store, learner, contract)

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
# Unit tests: TemplateMatch and match_template
# ---------------------------------------------------------------------------


class TestMatchTemplate:
    def test_match_template_returns_template_match(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )
        _promote_template(store, learner, contract)

        match = learner.match_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/readme.md"],
        )
        assert match is not None
        assert isinstance(match, TemplateMatch)
        assert match.confidence > 0.0
        assert match.template_ref != ""
        assert len(match.match_reasons) > 0
        assert match.template is not None
        assert match.template.action_class == "write_local"

    def test_match_template_returns_none_below_threshold(self, tmp_path: Path) -> None:
        """Low-confidence matches (below promotion threshold) are skipped."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )
        # Only 1 success, below promotion threshold
        recon = _make_reconciliation(store, contract_ref=contract.contract_id)
        learner.learn_from_reconciliation(reconciliation=recon, contract=contract)

        match = learner.match_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/readme.md"],
        )
        assert match is None

    def test_match_template_includes_reasons(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )
        _promote_template(store, learner, contract)

        match = learner.match_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/readme.md"],
        )
        assert match is not None
        reasons_str = " ".join(match.match_reasons)
        assert "action_class=write_local" in reasons_str
        assert "tool_name=write_file" in reasons_str
        assert "effects_similarity=" in reasons_str
        assert "success_count=" in reasons_str


# ---------------------------------------------------------------------------
# Unit tests: apply_template
# ---------------------------------------------------------------------------


class TestApplyTemplate:
    def test_apply_template_produces_contract_params(self, tmp_path: Path) -> None:
        template = ContractTemplate(
            action_class="write_local",
            tool_name="write_file",
            risk_level="high",
            reversibility_class="reversible",
            expected_effects=["path:/workspace/readme.md"],
            success_criteria={"tool_name": "write_file", "requires_receipt": True},
            drift_budget={"resource_scopes": ["/workspace"], "outside_workspace": False},
            evidence_requirements=["write_local"],
        )

        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)
        params = learner.apply_template(
            template,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/new/file.txt"],
        )

        assert params["reversibility_class"] == "reversible"
        assert params["risk_budget"]["risk_level"] == "high"
        assert params["required_receipt_classes"] == ["write_local"]
        assert params["expected_effects"] == ["path:/new/file.txt"]
        assert params["success_criteria"]["tool_name"] == "write_file"
        assert params["success_criteria"]["action_class"] == "write_local"
        assert params["selected_template_ref"] == ""

    def test_apply_template_uses_resource_scopes_fallback(self, tmp_path: Path) -> None:
        template = ContractTemplate(
            action_class="write_local",
            tool_name="write_file",
            risk_level="medium",
            reversibility_class="limited",
            drift_budget={},
            evidence_requirements=[],
        )

        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)
        params = learner.apply_template(
            template,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["action:write_local"],
            resource_scopes=["/fallback"],
        )

        assert params["drift_budget"]["resource_scopes"] == ["/fallback"]


# ---------------------------------------------------------------------------
# Unit tests: template degradation on violation
# ---------------------------------------------------------------------------


class TestTemplateDegradation:
    def test_degrade_invalidates_template_on_violation(self, tmp_path: Path) -> None:
        """Gradual degradation: single violation records failure but does not
        invalidate until invocation_count >= 5 and success_rate < 0.3."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=reconciliation, contract=contract)
        assert memory is not None

        # Single violation records failure but does NOT invalidate (invocation < 5)
        affected = learner.degrade_templates_for_violation(reconciliation.reconciliation_id)
        assert affected == []

        record = store.get_memory_record(memory.memory_id)
        assert record is not None
        assert record.status == "active"
        sa = dict(record.structured_assertion or {})
        assert sa["failure_count"] == 1

        # Build up to threshold: set invocation_count=5, success_count=0
        sa["invocation_count"] = 5
        sa["success_count"] = 0
        sa["failure_count"] = 4
        sa["success_rate"] = 0.0
        store.update_memory_record(memory.memory_id, structured_assertion=sa)

        # Now degradation should invalidate
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


# ---------------------------------------------------------------------------
# Unit tests: template models
# ---------------------------------------------------------------------------


class TestTemplateModels:
    def test_contract_template_defaults(self) -> None:
        tmpl = ContractTemplate(
            action_class="write_local",
            tool_name="write_file",
            risk_level="high",
            reversibility_class="reversible",
        )
        assert tmpl.expected_effects == []
        assert tmpl.success_criteria == {}
        assert tmpl.drift_budget == {}
        assert tmpl.source_contract_ref == ""
        assert tmpl.success_count == 1
        assert tmpl.last_used_at == 0.0
        assert tmpl.resource_scope_pattern == []
        assert tmpl.constraint_defaults == {}
        assert tmpl.evidence_requirements == []

    def test_template_match_defaults(self) -> None:
        match = TemplateMatch(template_ref="mem-1", confidence=0.9)
        assert match.match_reasons == []
        assert match.template is None


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

        # -- Phase 1: create a contract and promote it via multiple reconciliations --
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
        _promote_template(store, service.template_learner, contract1)

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

        # --- Simulate enough satisfied reconciliations to promote ---
        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/project/src/config.py"],
            risk_level="high",
        )
        _promote_template(store, learner, contract)

        # --- Template now available for same file in different directory ---
        template_after = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/project/config.py"],
        )
        assert template_after is not None
        assert template_after.action_class == "write_local"
        assert template_after.risk_level == "high"
        assert template_after.source_contract_ref == contract.contract_id

    def test_violated_reconciliation_invalidates_template(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        # Learn and promote a template
        contract = _make_contract(store, action_class="write_local", tool_name="write_file")
        _promote_template(store, learner, contract)

        # Verify template exists
        template = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["action:write_local"],
        )
        assert template is not None

        # Get the template memory and push invocation_count past threshold
        templates = [
            r
            for r in store.list_memory_records(status="active", limit=100)
            if r.memory_kind == "contract_template"
        ]
        assert len(templates) == 1
        recon_ref = templates[0].learned_from_reconciliation_ref
        assert recon_ref is not None

        # Set up stats so degradation crosses the invalidation threshold
        sa = dict(templates[0].structured_assertion or {})
        sa["invocation_count"] = 5
        sa["success_count"] = 0
        sa["failure_count"] = 4
        sa["success_rate"] = 0.0
        store.update_memory_record(templates[0].memory_id, structured_assertion=sa)

        learner.degrade_templates_for_violation(recon_ref)

        # Template should no longer be found (invalidated due to low success rate)
        template_after = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["action:write_local"],
        )
        assert template_after is None

    def test_multiple_learnings_reinforce_template(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/project/src/main.py"],
        )

        # Learn from enough reconciliations to promote
        _promote_template(store, learner, contract)

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


# ---------------------------------------------------------------------------
# Unit tests: workspace-scoped template isolation
# ---------------------------------------------------------------------------


class TestWorkspaceScopedTemplates:
    def test_learn_workspace_scoped_template(self, tmp_path: Path) -> None:
        """Template learned with workspace_root gets scope_kind='workspace'."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)

        ws = str(tmp_path / "project-a")
        memory = learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=contract,
            workspace_root=ws,
        )

        assert memory is not None
        assert memory.scope_kind == "workspace"
        assert memory.scope_ref == str(Path(ws).resolve())

    def test_workspace_isolation(self, tmp_path: Path) -> None:
        """Template learned in workspace A is not matched in workspace B."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        ws_a = str(tmp_path / "project-a")
        ws_b = str(tmp_path / "project-b")

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )
        # Promote in workspace A
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws_a
            )

        # Should match in workspace A
        template_a = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/readme.md"],
            workspace_root=ws_a,
        )
        assert template_a is not None

        # Should NOT match in workspace B (no workspace-scoped template there,
        # no global template either)
        template_b = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/readme.md"],
            workspace_root=ws_b,
        )
        assert template_b is None

    def test_global_fallback(self, tmp_path: Path) -> None:
        """Global templates still match when no workspace template exists."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )
        # Learn as global (no workspace_root)
        _promote_template(store, learner, contract)

        # Should match even from a specific workspace
        ws = str(tmp_path / "any-project")
        template = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/other/readme.md"],
            workspace_root=ws,
        )
        assert template is not None

    def test_workspace_priority(self, tmp_path: Path) -> None:
        """Workspace template preferred over global template for same fingerprint."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        ws = str(tmp_path / "project-a")
        resolved_ws = str(Path(ws).resolve())

        # Create a global template
        contract_global = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
            risk_level="medium",
        )
        _promote_template(store, learner, contract_global)

        # Create a workspace-scoped template with different contract (different risk)
        contract_ws = _make_contract(
            store,
            action_class="write_local",
            tool_name="patch_file",
            expected_effects=["path:/workspace/readme.md"],
            risk_level="high",
        )
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract_ws.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract_ws, workspace_root=ws
            )

        # Match in the workspace — should get workspace template (patch_file)
        template = learner.find_matching_template(
            action_class="write_local",
            tool_name="patch_file",
            expected_effects=["path:/other/readme.md"],
            workspace_root=ws,
        )
        assert template is not None
        assert template.workspace_ref == resolved_ws
        assert template.scope_kind == "workspace"

    def test_backward_compat_no_workspace(self, tmp_path: Path) -> None:
        """workspace_root='' falls back to global (existing behavior preserved)."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        reconciliation = _make_reconciliation(store, contract_ref=contract.contract_id)

        memory = learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=contract,
            workspace_root="",
        )

        assert memory is not None
        assert memory.scope_kind == "global"

        # Promote and verify matching works without workspace
        _promote_template(store, learner, contract)
        template = learner.find_matching_template(
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["action:write_local"],
        )
        assert template is not None


# ---------------------------------------------------------------------------
# Unit tests: cross-workspace promotion
# ---------------------------------------------------------------------------


class TestCrossWorkspacePromotion:
    def test_cross_workspace_promotion(self, tmp_path: Path) -> None:
        """Template promoted to global after appearing in 2+ workspaces with high success rate."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        ws_a = str(tmp_path / "project-a")
        ws_b = str(tmp_path / "project-b")

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )

        # Learn and promote in workspace A
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws_a
            )

        # Learn and promote in workspace B — this should trigger promotion
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws_b
            )

        # Check that a global template now exists
        global_records = store.list_memory_records(status="active", scope_kind="global", limit=100)
        global_templates = [r for r in global_records if r.memory_kind == "contract_template"]
        assert len(global_templates) >= 1

        promoted = global_templates[0]
        sa = dict(promoted.structured_assertion or {})
        assert sa.get("promotion_reason") == "cross_workspace_convergence"
        assert promoted.promotion_reason == "cross_workspace_convergence"

    def test_no_promotion_below_threshold(self, tmp_path: Path) -> None:
        """Promotion blocked when only 1 workspace has the template."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        ws_a = str(tmp_path / "project-a")

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )

        # Learn in only one workspace
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws_a
            )

        # No global template should exist
        global_records = store.list_memory_records(status="active", scope_kind="global", limit=100)
        global_templates = [r for r in global_records if r.memory_kind == "contract_template"]
        assert len(global_templates) == 0


# ---------------------------------------------------------------------------
# Unit tests: compute_policy_suggestion edge cases (lines 476-477)
# ---------------------------------------------------------------------------


class TestPolicySuggestionEdgeCases:
    def test_policy_suggestion_critical_risk_at_080_rate(self, tmp_path: Path) -> None:
        """0.80 <= success_rate < 0.95 with risk_level='critical' -> suggested='high'."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        template = ContractTemplate(
            action_class="execute_command",
            tool_name="bash",
            risk_level="critical",
            reversibility_class="irreversible",
            invocation_count=10,
            success_count=9,
            success_rate=0.90,
        )

        suggestion = learner.compute_policy_suggestion(template, risk_level="critical")
        assert suggestion is not None
        assert suggestion.suggested_risk_level == "high"
        assert suggestion.skip_approval_eligible is False
        assert "Moderate-confidence" in suggestion.reason

    def test_policy_suggestion_high_risk_at_080_rate(self, tmp_path: Path) -> None:
        """0.80 <= success_rate < 0.95 with risk_level='high' -> suggested='medium'."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        template = ContractTemplate(
            action_class="write_local",
            tool_name="write_file",
            risk_level="high",
            reversibility_class="reversible",
            invocation_count=10,
            success_count=9,
            success_rate=0.90,
        )

        suggestion = learner.compute_policy_suggestion(template, risk_level="high")
        assert suggestion is not None
        assert suggestion.suggested_risk_level == "medium"
        assert suggestion.skip_approval_eligible is False


# ---------------------------------------------------------------------------
# Unit tests: record_template_outcome (lines 506-560)
# ---------------------------------------------------------------------------


class TestRecordTemplateOutcome:
    def test_record_outcome_not_found(self, tmp_path: Path) -> None:
        """record_template_outcome is a no-op when the template ref doesn't exist."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        # Should not raise — guard exits early
        result = learner.record_template_outcome(
            template_ref="nonexistent-contract-ref",
            result_class="satisfied",
            task_id="task-1",
            step_id="step-1",
        )

        # Returns None and writes no template record
        assert result is None
        assert learner._find_template_by_source_contract_ref("nonexistent-contract-ref") is None

    def test_record_outcome_satisfied(self, tmp_path: Path) -> None:
        """Satisfied outcome increments success_count and invocation_count."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        recon = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=recon, contract=contract)
        assert memory is not None

        # Record satisfied outcome using the source_contract_ref
        learner.record_template_outcome(
            template_ref=contract.contract_id,
            result_class="satisfied",
            task_id="task-1",
            step_id="step-1",
        )

        updated = store.get_memory_record(memory.memory_id)
        assert updated is not None
        sa = dict(updated.structured_assertion or {})
        assert sa["invocation_count"] == 2
        assert sa["success_count"] == 2
        assert sa["failure_count"] == 0
        assert sa["success_rate"] == 1.0

    def test_record_outcome_violated(self, tmp_path: Path) -> None:
        """Violated outcome increments failure_count and sets last_failure_at."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        recon = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=recon, contract=contract)
        assert memory is not None

        learner.record_template_outcome(
            template_ref=contract.contract_id,
            result_class="violated",
            task_id="task-1",
            step_id="step-1",
        )

        updated = store.get_memory_record(memory.memory_id)
        assert updated is not None
        sa = dict(updated.structured_assertion or {})
        assert sa["invocation_count"] == 2
        assert sa["success_count"] == 1
        assert sa["failure_count"] == 1
        assert sa["last_failure_at"] is not None
        assert sa["success_rate"] == 0.5

    def test_record_outcome_ambiguous(self, tmp_path: Path) -> None:
        """Ambiguous outcome also counts as failure."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        recon = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=recon, contract=contract)
        assert memory is not None

        learner.record_template_outcome(
            template_ref=contract.contract_id,
            result_class="ambiguous",
        )

        updated = store.get_memory_record(memory.memory_id)
        assert updated is not None
        sa = dict(updated.structured_assertion or {})
        assert sa["failure_count"] == 1

    def test_record_outcome_auto_invalidation(self, tmp_path: Path) -> None:
        """Template auto-invalidated when invocation_count >= 5 and success_rate < 0.3."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        recon = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=recon, contract=contract)
        assert memory is not None

        # Set up stats near invalidation threshold
        sa = dict(memory.structured_assertion or {})
        sa["invocation_count"] = 4
        sa["success_count"] = 1
        sa["failure_count"] = 3
        sa["success_rate"] = 0.25
        store.update_memory_record(memory.memory_id, structured_assertion=sa)

        # One more violated outcome pushes to invocation_count=5, success_rate < 0.3
        learner.record_template_outcome(
            template_ref=contract.contract_id,
            result_class="violated",
            task_id="task-1",
            step_id="step-1",
        )

        updated = store.get_memory_record(memory.memory_id)
        assert updated is not None
        assert updated.status == "invalidated"
        assert "low_success_rate" in (updated.invalidation_reason or "")


# ---------------------------------------------------------------------------
# Unit tests: degrade_templates_for_violation edge case (line 591)
# ---------------------------------------------------------------------------


class TestDegradationEdgeCases:
    def test_degrade_with_zero_invocation_count(self, tmp_path: Path) -> None:
        """When invocation_count is 0, it gets set to 1 during degradation."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        recon = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=recon, contract=contract)
        assert memory is not None

        # Force invocation_count to 0
        sa = dict(memory.structured_assertion or {})
        sa["invocation_count"] = 0
        sa["success_count"] = 0
        store.update_memory_record(memory.memory_id, structured_assertion=sa)

        learner.degrade_templates_for_violation(recon.reconciliation_id)

        updated = store.get_memory_record(memory.memory_id)
        assert updated is not None
        sa_updated = dict(updated.structured_assertion or {})
        # invocation_count should be set to 1 (the fallback)
        assert sa_updated["invocation_count"] == 1
        assert sa_updated["failure_count"] == 1


# ---------------------------------------------------------------------------
# Unit tests: promote_to_global filtering (lines 637, 648, 651, 654)
# ---------------------------------------------------------------------------


class TestPromoteToGlobalFiltering:
    def test_promote_skips_non_template_memory_kinds(self, tmp_path: Path) -> None:
        """Non-contract_template records with global/workspace scope are ignored."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        ws_a = str(tmp_path / "project-a")
        ws_b = str(tmp_path / "project-b")

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )

        # Create a non-template memory record with global scope to test filtering
        store.create_memory_record(
            task_id="task-1",
            conversation_id=None,
            category="observation",
            claim_text="Not a template",
            structured_assertion={"fingerprint": "fake"},
            scope_kind="global",
            scope_ref="global",
            status="active",
            memory_kind="observation",
        )

        # Create workspace-scoped non-template record
        store.create_memory_record(
            task_id="task-1",
            conversation_id=None,
            category="observation",
            claim_text="Not a template either",
            structured_assertion={"fingerprint": "fake"},
            scope_kind="workspace",
            scope_ref=str(Path(ws_a).resolve()),
            status="active",
            memory_kind="observation",
        )

        # Learn in two workspaces to trigger promotion
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws_a
            )
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws_b
            )

        # Global template should exist (non-template records were filtered out)
        global_records = store.list_memory_records(status="active", scope_kind="global", limit=100)
        global_templates = [r for r in global_records if r.memory_kind == "contract_template"]
        assert len(global_templates) >= 1

    def test_promote_blocked_by_low_success_rate(self, tmp_path: Path) -> None:
        """Promotion blocked when any workspace template has success_rate < min_success_rate."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        ws_a = str(tmp_path / "project-a")
        ws_b = str(tmp_path / "project-b")

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )

        # Learn in workspace A with good stats
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws_a
            )

        # Learn in workspace B
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws_b
            )

        # Degrade workspace B's template success rate below threshold
        ws_records = store.list_memory_records(
            status="active",
            scope_kind="workspace",
            scope_ref=str(Path(ws_b).resolve()),
            limit=100,
        )
        ws_templates = [r for r in ws_records if r.memory_kind == "contract_template"]
        if ws_templates:
            sa = dict(ws_templates[0].structured_assertion or {})
            sa["success_rate"] = 0.5  # Below default min_success_rate of 0.8
            store.update_memory_record(ws_templates[0].memory_id, structured_assertion=sa)

        # Remove any existing global promoted template
        global_records = store.list_memory_records(status="active", scope_kind="global", limit=100)
        for r in global_records:
            if r.memory_kind == "contract_template":
                store.update_memory_record(r.memory_id, status="invalidated")

        fp = _action_fingerprint("write_local", "write_file", ["path:/workspace/readme.md"])
        result = learner.promote_to_global(fingerprint=fp)
        assert result is None

    def test_promote_already_exists_returns_none(self, tmp_path: Path) -> None:
        """If a global template with same fingerprint exists, promote returns None."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        ws_a = str(tmp_path / "project-a")
        ws_b = str(tmp_path / "project-b")

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )

        # Learn in two workspaces to trigger promotion
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws_a
            )
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws_b
            )

        # First promotion should have happened automatically. Try again — should return None.
        fp = _action_fingerprint("write_local", "write_file", ["path:/workspace/readme.md"])
        result = learner.promote_to_global(fingerprint=fp)
        assert result is None

    def test_promote_fingerprint_mismatch_skipped(self, tmp_path: Path) -> None:
        """Workspace templates with different fingerprints are not matched for promotion."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        ws_a = str(tmp_path / "project-a")

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )

        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws_a
            )

        # Try to promote with a different fingerprint
        result = learner.promote_to_global(fingerprint="nonexistent_fingerprint_abc")
        assert result is None


# ---------------------------------------------------------------------------
# Unit tests: _active_templates backward compat (lines 771-772)
# ---------------------------------------------------------------------------


class TestActiveTemplatesBackwardCompat:
    def test_active_templates_empty_scope_fallback(self, tmp_path: Path) -> None:
        """Templates with empty/None scope are still returned when workspace_root is empty."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        # Create a template with empty scope (simulating old records)
        store.create_memory_record(
            task_id="task-1",
            conversation_id=None,
            category="contract_template",
            claim_text="Legacy template",
            structured_assertion={
                "action_class": "write_local",
                "tool_name": "write_file",
                "fingerprint": "legacy_fp",
                "invocation_count": 5,
                "success_count": 5,
                "success_rate": 1.0,
                "expected_effects": ["action:write_local"],
            },
            scope_kind="",
            scope_ref="",
            status="active",
            memory_kind="contract_template",
        )

        # Without workspace_root, should find the legacy template
        templates = learner._active_templates(workspace_root="")
        template_fps = [dict(r.structured_assertion or {}).get("fingerprint") for r in templates]
        assert "legacy_fp" in template_fps


# ---------------------------------------------------------------------------
# Unit tests: _find_template_by_fingerprint workspace filtering (lines 788-791)
# ---------------------------------------------------------------------------


class TestFindTemplateByFingerprintWorkspaceFiltering:
    def test_skip_other_workspace_return_global(self, tmp_path: Path) -> None:
        """When searching with workspace_root, skip templates from other workspaces
        but accept global templates as fallback."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        ws_a = str(tmp_path / "project-a")
        ws_b = str(tmp_path / "project-b")

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )

        fp = _action_fingerprint("write_local", "write_file", ["path:/workspace/readme.md"])

        # Create a workspace-scoped template in workspace A
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws_a
            )

        # Searching from workspace B should NOT find workspace A's template
        result = learner._find_template_by_fingerprint(fp, workspace_root=ws_b)
        # Should be None since ws_a's template is not visible from ws_b
        # (no global template exists either)
        assert result is None

    def test_find_global_fallback_when_workspace_specified(self, tmp_path: Path) -> None:
        """When workspace is specified and no workspace-scoped match exists,
        a global template with matching fingerprint is returned."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        ws = str(tmp_path / "project-a")

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )

        fp = _action_fingerprint("write_local", "write_file", ["path:/workspace/readme.md"])

        # Create a global template (no workspace_root)
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=""
            )

        # Searching from a workspace should find the global template
        result = learner._find_template_by_fingerprint(fp, workspace_root=ws)
        assert result is not None
        assert result.scope_kind == "global"

    def test_workspace_scoped_match_preferred(self, tmp_path: Path) -> None:
        """When a workspace-scoped template matches, it is returned over global."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        ws = str(tmp_path / "project-a")
        resolved_ws = str(Path(ws).resolve())

        contract = _make_contract(
            store,
            action_class="write_local",
            tool_name="write_file",
            expected_effects=["path:/workspace/readme.md"],
        )

        fp = _action_fingerprint("write_local", "write_file", ["path:/workspace/readme.md"])

        # Create workspace-scoped template
        for _ in range(_PROMOTION_THRESHOLD):
            recon = _make_reconciliation(store, contract_ref=contract.contract_id)
            learner.learn_from_reconciliation(
                reconciliation=recon, contract=contract, workspace_root=ws
            )

        result = learner._find_template_by_fingerprint(fp, workspace_root=ws)
        assert result is not None
        assert result.scope_kind == "workspace"
        assert result.scope_ref == resolved_ws


# ---------------------------------------------------------------------------
# Unit tests: _find_template_by_source_contract_ref (lines 796-798)
# ---------------------------------------------------------------------------


class TestFindTemplateBySourceContractRef:
    def test_find_by_source_contract_ref(self, tmp_path: Path) -> None:
        """_find_template_by_source_contract_ref returns record with matching ref."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        contract = _make_contract(store)
        recon = _make_reconciliation(store, contract_ref=contract.contract_id)
        memory = learner.learn_from_reconciliation(reconciliation=recon, contract=contract)
        assert memory is not None

        result = learner._find_template_by_source_contract_ref(contract.contract_id)
        assert result is not None
        assert result.memory_id == memory.memory_id

    def test_find_by_source_contract_ref_not_found(self, tmp_path: Path) -> None:
        """Returns None when no template matches the source_contract_ref."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        result = learner._find_template_by_source_contract_ref("nonexistent-ref")
        assert result is None
