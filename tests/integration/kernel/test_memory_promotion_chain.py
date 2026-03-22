"""Memory promotion chain integration test.

Full chain:
  execute task → reconcile(satisfied) → create belief → classify →
  promote to durable memory → verify reconciliation_ref →
  verify memory write receipt → verify enrichment

Also tests:
  violated reconciliation → attempt promotion → blocked
"""

from __future__ import annotations

import time

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.controller.template_learner import ContractTemplateLearner
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.receipts.receipts import ReceiptService


def _make_store(tmp_path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


def _create_full_chain(store: KernelStore, suffix: str = "1") -> dict:
    """Create task -> step -> attempt -> contract -> receipt -> reconciliation."""
    conv = store.ensure_conversation(f"conv_mem_{suffix}", source_channel="test")
    task = store.create_task(
        conversation_id=conv.conversation_id,
        title=f"Memory promotion test {suffix}",
        goal="Test memory promotion lifecycle",
        source_channel="test",
        status="running",
    )
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        attempt=1,
        context={"workspace_root": "/tmp/ws", "execution_mode": "run"},
    )
    contract = store.create_execution_contract(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        objective="bash: execute_command",
        expected_effects=["command:test"],
        success_criteria={
            "tool_name": "bash",
            "action_class": "execute_command",
        },
        status="active",
        risk_budget={"risk_level": "medium"},
    )
    return {
        "conversation": conv,
        "task": task,
        "step": step,
        "attempt": attempt,
        "contract": contract,
    }


class TestMemoryPromotionChain:
    """Exercise GC3+GC4: reconciliation → belief → memory promotion."""

    def test_satisfied_reconciliation_enables_memory_promotion(self, tmp_path) -> None:
        """Satisfied reconciliation should allow belief -> memory promotion."""
        store = _make_store(tmp_path)
        artifact_store = ArtifactStore(tmp_path / "artifacts")
        chain = _create_full_chain(store, "sat_promo")

        # Issue receipt
        receipt_svc = ReceiptService(store, artifact_store)
        receipt_id = receipt_svc.issue(
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            step_attempt_id=chain["attempt"].step_attempt_id,
            action_type="execute_command",
            input_refs=[],
            environment_ref=None,
            policy_result={"verdict": "approved"},
            approval_ref=None,
            output_refs=[],
            result_summary="Command succeeded",
            result_code="succeeded",
            contract_ref=chain["contract"].contract_id,
        )

        # Create reconciliation (satisfied)
        reconciliation = store.create_reconciliation(
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            step_attempt_id=chain["attempt"].step_attempt_id,
            contract_ref=chain["contract"].contract_id,
            receipt_refs=[receipt_id],
            observed_output_refs=[],
            intended_effect_summary="Execute command",
            authorized_effect_summary="Execute command in workspace",
            observed_effect_summary="Command completed successfully",
            receipted_effect_summary="Command executed",
            result_class="satisfied",
            confidence_delta=0.2,
            recommended_resolution="promote_learning",
        )

        # Create belief based on reconciliation
        belief = store.create_belief(
            task_id=chain["task"].task_id,
            conversation_id=chain["conversation"].conversation_id,
            scope_kind="workspace",
            scope_ref="/tmp/ws",
            category="tech_decision",
            claim_text="bash commands execute reliably in this workspace",
            confidence=0.8,
            trust_tier="observed",
            evidence_refs=[reconciliation.reconciliation_id, receipt_id],
            evidence_case_ref=None,
            promotion_candidate=True,
        )

        assert belief is not None
        assert belief.status == "active"
        assert belief.promotion_candidate is True

        # Promote belief to durable memory
        memory = store.create_memory_record(
            task_id=chain["task"].task_id,
            conversation_id=chain["conversation"].conversation_id,
            category="tech_decision",
            claim_text="bash commands execute reliably in this workspace",
            scope_kind="workspace",
            scope_ref="/tmp/ws",
            promotion_reason="satisfied_reconciliation",
            retention_class="durable_fact",
            status="active",
            confidence=0.8,
            trust_tier="durable",
            evidence_refs=[reconciliation.reconciliation_id, receipt_id],
            memory_kind="durable_fact",
            learned_from_reconciliation_ref=reconciliation.reconciliation_id,
            source_belief_ref=belief.belief_id,
            validation_basis=f"reconciliation:{reconciliation.reconciliation_id}",
            last_validated_at=time.time(),
        )

        assert memory is not None
        assert memory.status == "active"
        assert memory.memory_kind == "durable_fact"
        assert memory.learned_from_reconciliation_ref == reconciliation.reconciliation_id
        assert memory.source_belief_ref == belief.belief_id

        # Verify reconciliation_ref is traceable
        assert reconciliation.reconciliation_id in memory.evidence_refs
        assert receipt_id in memory.evidence_refs

        # Update belief to reference the promoted memory
        store.update_belief(
            belief.belief_id,
            memory_ref=memory.memory_id,
            promotion_candidate=False,
        )

        updated_belief = store.get_belief(belief.belief_id)
        assert updated_belief is not None
        assert updated_belief.memory_ref == memory.memory_id
        assert updated_belief.promotion_candidate is False

    def test_violated_reconciliation_blocks_promotion(self, tmp_path) -> None:
        """Violated reconciliation should produce beliefs that are NOT promotion candidates."""
        store = _make_store(tmp_path)
        chain = _create_full_chain(store, "violated_promo")

        # Create reconciliation (violated)
        reconciliation = store.create_reconciliation(
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            step_attempt_id=chain["attempt"].step_attempt_id,
            contract_ref=chain["contract"].contract_id,
            receipt_refs=[],
            observed_output_refs=[],
            intended_effect_summary="Execute command",
            authorized_effect_summary="Execute command in workspace",
            observed_effect_summary="Command failed - file not found",
            receipted_effect_summary="Command failed",
            result_class="violated",
            confidence_delta=-0.3,
            recommended_resolution="gather_more_evidence",
        )

        # Create belief from violated reconciliation - marked as NOT promotion candidate
        belief = store.create_belief(
            task_id=chain["task"].task_id,
            conversation_id=chain["conversation"].conversation_id,
            scope_kind="workspace",
            scope_ref="/tmp/ws",
            category="tech_decision",
            claim_text="bash command failed in workspace",
            confidence=0.3,
            trust_tier="observed",
            evidence_refs=[reconciliation.reconciliation_id],
            promotion_candidate=False,  # Violated -> no promotion
        )

        assert belief is not None
        assert belief.promotion_candidate is False

        # Verify the belief cannot be promoted via template learner
        learner = ContractTemplateLearner(store)
        template = learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=chain["contract"],
        )
        # Template learning should return None for violated reconciliation
        assert template is None

    def test_memory_enrichment_with_lineage(self, tmp_path) -> None:
        """Memory records should carry full lineage back to the reconciliation."""
        store = _make_store(tmp_path)
        ArtifactStore(tmp_path / "artifacts")
        chain = _create_full_chain(store, "enrich")

        # Create reconciliation
        reconciliation = store.create_reconciliation(
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            step_attempt_id=chain["attempt"].step_attempt_id,
            contract_ref=chain["contract"].contract_id,
            receipt_refs=[],
            observed_output_refs=[],
            intended_effect_summary="test",
            authorized_effect_summary="test",
            observed_effect_summary="test",
            receipted_effect_summary="test",
            result_class="satisfied",
            confidence_delta=0.2,
            recommended_resolution="promote_learning",
        )

        # Create memory with full enrichment
        memory = store.create_memory_record(
            task_id=chain["task"].task_id,
            conversation_id=None,
            category="tech_decision",
            claim_text="Workspace uses standard bash",
            structured_assertion={
                "subject": "workspace",
                "predicate": "uses",
                "object": "standard bash",
                "source_type": "reconciliation",
            },
            scope_kind="workspace",
            scope_ref="/tmp/ws",
            promotion_reason="satisfied_reconciliation",
            retention_class="durable_fact",
            status="active",
            confidence=0.85,
            trust_tier="durable",
            evidence_refs=[
                reconciliation.reconciliation_id,
                chain["contract"].contract_id,
            ],
            memory_kind="durable_fact",
            learned_from_reconciliation_ref=reconciliation.reconciliation_id,
            validation_basis=f"reconciliation:{reconciliation.reconciliation_id}",
            last_validated_at=time.time(),
        )

        # Verify enrichment
        fetched = store.get_memory_record(memory.memory_id)
        assert fetched is not None
        assert fetched.learned_from_reconciliation_ref == reconciliation.reconciliation_id
        assert fetched.validation_basis == f"reconciliation:{reconciliation.reconciliation_id}"
        assert fetched.last_validated_at is not None
        assert fetched.structured_assertion is not None
        assert fetched.structured_assertion["subject"] == "workspace"

        # Verify lineage: memory -> reconciliation -> contract -> task
        recon = store.get_reconciliation(fetched.learned_from_reconciliation_ref)
        assert recon is not None
        assert recon.contract_ref == chain["contract"].contract_id

        contract = store.get_execution_contract(recon.contract_ref)
        assert contract is not None
        assert contract.task_id == chain["task"].task_id

    def test_memory_receipt_is_issued_on_creation(self, tmp_path) -> None:
        """When a memory record is created, the store should emit a memory.recorded event."""
        store = _make_store(tmp_path)
        chain = _create_full_chain(store, "receipt")

        memory = store.create_memory_record(
            task_id=chain["task"].task_id,
            conversation_id=None,
            category="tech_decision",
            claim_text="Test memory receipt",
            status="active",
            confidence=0.8,
            trust_tier="durable",
            evidence_refs=[],
            memory_kind="durable_fact",
        )

        # Verify the memory.recorded event exists
        events = store._rows(
            "SELECT * FROM events WHERE entity_type = 'memory_record' AND entity_id = ?",
            (memory.memory_id,),
        )
        assert len(events) > 0
        event_types = [str(e["event_type"]) for e in events]
        assert "memory.recorded" in event_types

    def test_belief_to_memory_cross_reference_integrity(self, tmp_path) -> None:
        """Verify belief -> memory -> reconciliation cross-reference chain is intact."""
        store = _make_store(tmp_path)
        chain = _create_full_chain(store, "xref")

        reconciliation = store.create_reconciliation(
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            step_attempt_id=chain["attempt"].step_attempt_id,
            contract_ref=chain["contract"].contract_id,
            receipt_refs=[],
            observed_output_refs=[],
            intended_effect_summary="test",
            authorized_effect_summary="test",
            observed_effect_summary="test",
            receipted_effect_summary="test",
            result_class="satisfied",
            confidence_delta=0.1,
            recommended_resolution="promote_learning",
        )

        belief = store.create_belief(
            task_id=chain["task"].task_id,
            conversation_id=None,
            scope_kind="global",
            scope_ref="global",
            category="tech_decision",
            claim_text="Cross-ref test",
            confidence=0.7,
            trust_tier="observed",
            evidence_refs=[reconciliation.reconciliation_id],
        )

        memory = store.create_memory_record(
            task_id=chain["task"].task_id,
            conversation_id=None,
            category="tech_decision",
            claim_text="Cross-ref test",
            status="active",
            confidence=0.8,
            trust_tier="durable",
            evidence_refs=[reconciliation.reconciliation_id],
            memory_kind="durable_fact",
            source_belief_ref=belief.belief_id,
            learned_from_reconciliation_ref=reconciliation.reconciliation_id,
        )

        store.update_belief(
            belief.belief_id,
            memory_ref=memory.memory_id,
        )

        # Forward: belief -> memory
        updated_belief = store.get_belief(belief.belief_id)
        assert updated_belief is not None
        assert updated_belief.memory_ref == memory.memory_id

        # Backward: memory -> belief
        fetched_memory = store.get_memory_record(memory.memory_id)
        assert fetched_memory is not None
        assert fetched_memory.source_belief_ref == belief.belief_id

        # Lineage: memory -> reconciliation -> contract
        recon = store.get_reconciliation(fetched_memory.learned_from_reconciliation_ref)
        assert recon is not None
        assert recon.contract_ref == chain["contract"].contract_id

        contract = store.get_execution_contract(recon.contract_ref)
        assert contract is not None
        assert contract.task_id == chain["task"].task_id

    def test_template_learning_from_promoted_memory_chain(self, tmp_path) -> None:
        """Template learner creates contract_template memory linked to reconciliation."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)
        chain = _create_full_chain(store, "tpl_learn")

        reconciliation = store.create_reconciliation(
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            step_attempt_id=chain["attempt"].step_attempt_id,
            contract_ref=chain["contract"].contract_id,
            receipt_refs=[],
            observed_output_refs=[],
            intended_effect_summary="test",
            authorized_effect_summary="test",
            observed_effect_summary="test",
            receipted_effect_summary="test",
            result_class="satisfied",
            confidence_delta=0.2,
            recommended_resolution="promote_learning",
        )

        template = learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=chain["contract"],
        )
        assert template is not None
        assert template.memory_kind == "contract_template"
        assert template.learned_from_reconciliation_ref == reconciliation.reconciliation_id

        # The template memory should trace back to the contract
        sa = dict(template.structured_assertion or {})
        assert sa["source_contract_ref"] == chain["contract"].contract_id
        assert reconciliation.reconciliation_id in template.evidence_refs
