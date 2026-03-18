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
