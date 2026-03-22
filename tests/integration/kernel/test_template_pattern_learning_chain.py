"""Template pattern learning chain integration test.

Exercises the ContractTemplateLearner lifecycle:
  1. Create task with specific action_class
  2. Execute and reconcile as "satisfied"
  3. Verify template created (invocation_count=1)
  4. Execute SAME action_class again -> satisfied
  5. Verify template reinforced (invocation_count=2)
  6. Execute SAME action_class -> violated reconciliation
  7. Verify template degraded (success_rate drops)
"""

from __future__ import annotations

import pytest

from hermit.kernel.execution.controller.template_learner import ContractTemplateLearner
from hermit.kernel.ledger.journal.store import KernelStore


def _make_store(tmp_path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


def _create_task_chain(store: KernelStore, suffix: str = "1") -> dict:
    """Create task -> step -> attempt -> contract -> reconciliation for testing."""
    conv = store.ensure_conversation(f"conv_tpl_{suffix}", source_channel="test")
    task = store.create_task(
        conversation_id=conv.conversation_id,
        title=f"Template learning test {suffix}",
        goal="Test template learning",
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
        proposed_action_refs=[],
        expected_effects=["command:echo test"],
        success_criteria={
            "tool_name": "bash",
            "action_class": "execute_command",
            "requires_receipt": True,
        },
        reversibility_class="compensatable",
        required_receipt_classes=["execute_command"],
        drift_budget={"resource_scopes": ["/tmp"], "outside_workspace": False},
        status="active",
        risk_budget={"risk_level": "medium"},
        action_contract_refs=["execute_command"],
    )
    return {
        "task": task,
        "step": step,
        "attempt": attempt,
        "contract": contract,
    }


def _create_reconciliation(store: KernelStore, chain: dict, result_class: str):
    """Create a reconciliation for the given chain."""
    return store.create_reconciliation(
        task_id=chain["task"].task_id,
        step_id=chain["step"].step_id,
        step_attempt_id=chain["attempt"].step_attempt_id,
        contract_ref=chain["contract"].contract_id,
        receipt_refs=[],
        observed_output_refs=[],
        intended_effect_summary="Execute echo test",
        authorized_effect_summary="Execute echo test",
        observed_effect_summary="Command completed",
        receipted_effect_summary="Command executed",
        result_class=result_class,
        confidence_delta=0.2 if result_class == "satisfied" else -0.3,
        recommended_resolution=(
            "promote_learning" if result_class == "satisfied" else "gather_more_evidence"
        ),
    )


class TestTemplatePatternLearningChain:
    """Exercise template learning lifecycle: create -> reinforce -> degrade."""

    def test_satisfied_reconciliation_creates_template(self, tmp_path) -> None:
        """A satisfied reconciliation should create a contract_template memory record."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        chain = _create_task_chain(store, "create")
        reconciliation = _create_reconciliation(store, chain, "satisfied")

        # Learn from satisfied reconciliation
        template_memory = learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=chain["contract"],
        )

        assert template_memory is not None
        assert template_memory.memory_kind == "contract_template"
        assert template_memory.status == "active"

        sa = dict(template_memory.structured_assertion or {})
        assert sa["action_class"] == "execute_command"
        assert sa["tool_name"] == "bash"
        assert sa["invocation_count"] == 1
        assert sa["success_count"] == 1
        assert sa["success_rate"] == 1.0
        assert sa["source_contract_ref"] == chain["contract"].contract_id

    def test_second_satisfied_reinforces_template(self, tmp_path) -> None:
        """A second satisfied reconciliation for the same action should reinforce the template."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        # First satisfied reconciliation -> create template
        chain1 = _create_task_chain(store, "reinforce_1")
        recon1 = _create_reconciliation(store, chain1, "satisfied")
        template1 = learner.learn_from_reconciliation(
            reconciliation=recon1,
            contract=chain1["contract"],
        )
        assert template1 is not None

        # Second satisfied reconciliation -> reinforce template
        chain2 = _create_task_chain(store, "reinforce_2")
        recon2 = _create_reconciliation(store, chain2, "satisfied")
        template2 = learner.learn_from_reconciliation(
            reconciliation=recon2,
            contract=chain2["contract"],
        )

        assert template2 is not None
        # Should be the same memory record (reinforced, not new)
        assert template2.memory_id == template1.memory_id

        # Re-fetch to see updated structured_assertion
        refreshed = store.get_memory_record(template2.memory_id)
        assert refreshed is not None
        sa = dict(refreshed.structured_assertion or {})
        assert sa["invocation_count"] == 2
        assert sa["success_count"] == 2
        assert sa["success_rate"] == 1.0

    def test_violated_reconciliation_does_not_create_template(self, tmp_path) -> None:
        """A violated reconciliation should NOT create a new template."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        chain = _create_task_chain(store, "violated")
        recon = _create_reconciliation(store, chain, "violated")

        template = learner.learn_from_reconciliation(
            reconciliation=recon,
            contract=chain["contract"],
        )
        assert template is None

    def test_degradation_after_violation(self, tmp_path) -> None:
        """After a template is created, a violation should degrade it."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        # Create a template via satisfied reconciliation
        chain1 = _create_task_chain(store, "degrade_1")
        recon1 = _create_reconciliation(store, chain1, "satisfied")
        template = learner.learn_from_reconciliation(
            reconciliation=recon1,
            contract=chain1["contract"],
        )
        assert template is not None

        # Degrade via violation
        invalidated = learner.degrade_templates_for_violation(recon1.reconciliation_id)

        # With only 1 invocation and success_rate still high, template should NOT be
        # invalidated yet (requires invocation_count >= 5 and success_rate < 0.3)
        assert len(invalidated) == 0

        # Verify failure was recorded though
        refreshed = store.get_memory_record(template.memory_id)
        assert refreshed is not None
        sa = dict(refreshed.structured_assertion or {})
        assert sa["failure_count"] == 1
        assert refreshed.status == "active"  # Still active

    def test_template_outcome_tracking_with_success_rate_drop(self, tmp_path) -> None:
        """record_template_outcome should update success_rate correctly."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        # Create template
        chain = _create_task_chain(store, "outcome_1")
        recon = _create_reconciliation(store, chain, "satisfied")
        template = learner.learn_from_reconciliation(
            reconciliation=recon,
            contract=chain["contract"],
        )
        assert template is not None

        # Record several outcomes using the source_contract_ref
        source_ref = chain["contract"].contract_id

        # 4 more invocations: 2 satisfied, 2 violated
        learner.record_template_outcome(
            template_ref=source_ref,
            result_class="satisfied",
            task_id=chain["task"].task_id,
        )
        learner.record_template_outcome(
            template_ref=source_ref,
            result_class="violated",
            task_id=chain["task"].task_id,
        )
        learner.record_template_outcome(
            template_ref=source_ref,
            result_class="violated",
            task_id=chain["task"].task_id,
        )
        learner.record_template_outcome(
            template_ref=source_ref,
            result_class="violated",
            task_id=chain["task"].task_id,
        )

        # Now: invocation_count=5 (1 initial + 4 recorded),
        # success_count=2 (1 initial + 1 recorded),
        # failure_count=3
        # success_rate = 2/5 = 0.4
        refreshed = store.get_memory_record(template.memory_id)
        assert refreshed is not None
        sa = dict(refreshed.structured_assertion or {})
        assert sa["invocation_count"] == 5
        assert sa["success_count"] == 2
        assert sa["failure_count"] == 3
        # 0.4 > 0.3, so not yet auto-invalidated
        assert refreshed.status == "active"

        # One more violation pushes success_rate below 0.3 -> auto-invalidate
        learner.record_template_outcome(
            template_ref=source_ref,
            result_class="violated",
            task_id=chain["task"].task_id,
        )

        refreshed = store.get_memory_record(template.memory_id)
        assert refreshed is not None
        sa = dict(refreshed.structured_assertion or {})
        # Now: invocation=6, success=2, failure=4, rate=2/6=0.333...
        # Still above 0.3, check actual
        # 2/6 = 0.333... > 0.3, still active
        # We need to push it lower
        learner.record_template_outcome(
            template_ref=source_ref,
            result_class="violated",
            task_id=chain["task"].task_id,
        )

        refreshed = store.get_memory_record(template.memory_id)
        assert refreshed is not None
        sa = dict(refreshed.structured_assertion or {})
        # Now: invocation=7, success=2, failure=5, rate=2/7=0.285... < 0.3
        assert sa["invocation_count"] >= 5
        assert sa["success_rate"] < 0.3
        assert refreshed.status == "invalidated"

    def test_full_template_lifecycle_create_reinforce_degrade(self, tmp_path) -> None:
        """Full lifecycle: create -> reinforce (invocation_count=2) -> record violation."""
        store = _make_store(tmp_path)
        learner = ContractTemplateLearner(store)

        # Step 1: Create template from satisfied reconciliation
        chain1 = _create_task_chain(store, "lifecycle_1")
        recon1 = _create_reconciliation(store, chain1, "satisfied")
        template = learner.learn_from_reconciliation(
            reconciliation=recon1,
            contract=chain1["contract"],
        )
        assert template is not None
        sa = dict(template.structured_assertion or {})
        assert sa["invocation_count"] == 1

        # Step 2: Reinforce from second satisfied reconciliation
        chain2 = _create_task_chain(store, "lifecycle_2")
        recon2 = _create_reconciliation(store, chain2, "satisfied")
        reinforced = learner.learn_from_reconciliation(
            reconciliation=recon2,
            contract=chain2["contract"],
        )
        assert reinforced is not None
        assert reinforced.memory_id == template.memory_id

        refreshed = store.get_memory_record(template.memory_id)
        assert refreshed is not None
        sa = dict(refreshed.structured_assertion or {})
        assert sa["invocation_count"] == 2
        assert sa["success_count"] == 2
        assert sa["success_rate"] == 1.0

        # Step 3: Record violation via outcome tracking
        learner.record_template_outcome(
            template_ref=chain1["contract"].contract_id,
            result_class="violated",
            task_id=chain1["task"].task_id,
        )

        refreshed = store.get_memory_record(template.memory_id)
        assert refreshed is not None
        sa = dict(refreshed.structured_assertion or {})
        assert sa["invocation_count"] == 3
        assert sa["success_count"] == 2
        assert sa["failure_count"] == 1
        # success_rate = 2/3 ≈ 0.667
        assert sa["success_rate"] == pytest.approx(2 / 3, rel=0.01)
        # Not yet invalidated (need >= 5 invocations and < 0.3)
        assert refreshed.status == "active"
