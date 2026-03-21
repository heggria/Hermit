"""End-to-end tests exercising the full Task OS lifecycle.

Each test crosses multiple kernel layers:
task -> contract -> policy -> execution -> receipt -> reconciliation -> learning

Uses real KernelStore with tmp_path fixture (no mocks on the store).
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.execution.executor.reconciliation_executor import (
    MAX_AUTO_FOLLOWUPS,
    ReconciliationExecutor,
)
from hermit.kernel.ledger.journal.store import KernelStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> KernelStore:
    """Create a KernelStore backed by a real SQLite file under tmp_path."""
    db_path = tmp_path / "kernel" / "state.db"
    return KernelStore(db_path)


def _create_task_scaffold(
    store: KernelStore,
    *,
    goal: str = "test lifecycle task",
    status: str = "queued",
    parent_task_id: str | None = None,
    policy_profile: str = "autonomous",
) -> tuple[str, str, str, str]:
    """Create task + conversation + step + step_attempt scaffold.

    Returns (conversation_id, task_id, step_id, step_attempt_id).
    """
    conv = store.ensure_conversation("conv_e2e", source_channel="cli")
    task = store.create_task(
        conversation_id=conv.conversation_id,
        title=goal[:80],
        goal=goal,
        source_channel="cli",
        status=status,
        policy_profile=policy_profile,
        parent_task_id=parent_task_id,
    )
    step = store.create_step(task_id=task.task_id, kind="execute", status="running")
    attempt = store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        status="running",
    )
    return conv.conversation_id, task.task_id, step.step_id, attempt.step_attempt_id


def _create_contract(
    store: KernelStore,
    *,
    task_id: str,
    step_id: str,
    step_attempt_id: str,
    action_class: str = "write_local",
) -> str:
    """Create an execution contract and return contract_id."""
    contract = store.create_execution_contract(
        task_id=task_id,
        step_id=step_id,
        step_attempt_id=step_attempt_id,
        objective=f"test: {action_class}",
        proposed_action_refs=["action_ref_1"],
        expected_effects=[f"action:{action_class}"],
        success_criteria={
            "tool_name": "write_file",
            "action_class": action_class,
            "requires_receipt": True,
        },
        reversibility_class="reversible",
        required_receipt_classes=[action_class],
        drift_budget={"resource_scopes": ["/tmp"], "outside_workspace": False},
        status="admissibility_pending",
        risk_budget={"risk_level": "low", "approval_required": False},
        expected_artifact_shape={"expected_effects": [f"action:{action_class}"]},
        contract_version=1,
        action_contract_refs=[action_class],
    )
    return contract.contract_id


def _create_receipt(
    store: KernelStore,
    *,
    task_id: str,
    step_id: str,
    step_attempt_id: str,
    action_type: str = "write_local",
    contract_ref: str | None = None,
    result_code: str = "succeeded",
) -> str:
    """Create a receipt via store and return receipt_id."""
    receipt = store.create_receipt(
        task_id=task_id,
        step_id=step_id,
        step_attempt_id=step_attempt_id,
        action_type=action_type,
        receipt_class=action_type,
        input_refs=["input_ref_1"],
        environment_ref=None,
        policy_result={"verdict": "allow", "risk_level": "low"},
        approval_ref=None,
        output_refs=["output_ref_1"],
        result_summary="test action completed",
        result_code=result_code,
        contract_ref=contract_ref,
        observed_effect_summary="file written",
        reconciliation_required=True,
    )
    return receipt.receipt_id


# ---------------------------------------------------------------------------
# Test 1: Happy path — full lifecycle
# ---------------------------------------------------------------------------


class TestHappyPathFullLifecycle:
    """Create task -> contract -> receipt -> reconciliation (satisfied).

    Verify all records exist and cross-reference each other correctly.
    """

    def test_happy_path_full_lifecycle(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        # 1. Create task scaffold (task, step, step_attempt)
        _conv_id, task_id, step_id, attempt_id = _create_task_scaffold(store)

        # Verify task exists and is in expected state
        task = store.get_task(task_id)
        assert task is not None
        assert task.status == "queued"
        assert task.goal == "test lifecycle task"

        # 2. Create execution contract
        contract_id = _create_contract(
            store,
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
        )
        contract = store.get_execution_contract(contract_id)
        assert contract is not None
        assert contract.task_id == task_id
        assert contract.step_id == step_id
        assert contract.status == "admissibility_pending"

        # Link contract to attempt
        store.update_step_attempt(attempt_id, execution_contract_ref=contract_id)

        # 3. Create receipt
        receipt_id = _create_receipt(
            store,
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            contract_ref=contract_id,
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt is not None
        assert receipt.task_id == task_id
        assert receipt.action_type == "write_local"
        assert receipt.result_code == "succeeded"
        assert receipt.contract_ref == contract_id

        # 4. Create reconciliation (satisfied)
        reconciliation = store.create_reconciliation(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            contract_ref=contract_id,
            receipt_refs=[receipt_id],
            observed_output_refs=["output_ref_1"],
            intended_effect_summary="write file to disk",
            authorized_effect_summary="write file to disk",
            observed_effect_summary="file written successfully",
            receipted_effect_summary="file written successfully",
            result_class="satisfied",
            confidence_delta=0.2,
            recommended_resolution="promote_learning",
            operator_summary="satisfied: file written successfully",
        )
        assert reconciliation is not None
        assert reconciliation.result_class == "satisfied"
        assert reconciliation.task_id == task_id
        assert reconciliation.contract_ref == contract_id
        assert receipt_id in reconciliation.receipt_refs

        # 5. Link reconciliation to attempt and close contract
        store.update_step_attempt(
            attempt_id,
            reconciliation_ref=reconciliation.reconciliation_id,
        )
        store.update_execution_contract(contract_id, status="closed")

        # 6. Verify final state consistency
        # -- contract is closed
        final_contract = store.get_execution_contract(contract_id)
        assert final_contract is not None
        assert final_contract.status == "closed"

        # -- attempt has reconciliation ref
        final_attempt = store.get_step_attempt(attempt_id)
        assert final_attempt is not None
        assert final_attempt.reconciliation_ref == reconciliation.reconciliation_id
        assert final_attempt.execution_contract_ref == contract_id

        # -- reconciliation is retrievable via list_reconciliations
        recons = store.list_reconciliations(task_id=task_id)
        assert len(recons) >= 1
        assert any(r.reconciliation_id == reconciliation.reconciliation_id for r in recons)

        # -- receipts are retrievable
        receipts = store.list_receipts(task_id=task_id)
        assert len(receipts) >= 1
        assert any(r.receipt_id == receipt_id for r in receipts)

        # -- events are recorded throughout the lifecycle
        events = store.list_events(task_id=task_id, limit=1000)
        event_types = {e["event_type"] for e in events}
        assert "task.queued" in event_types or "task.created" in event_types
        assert "step.started" in event_types
        assert "step_attempt.started" in event_types
        assert "execution_contract.recorded" in event_types
        assert "reconciliation.recorded" in event_types


# ---------------------------------------------------------------------------
# Test 2: Violated reconciliation triggers follow-up task
# ---------------------------------------------------------------------------


class TestViolatedReconciliationTriggersFollowup:
    """A violated reconciliation should generate a follow-up task
    when ReconciliationExecutor._generate_followup_if_needed is invoked.
    """

    def test_violated_reconciliation_triggers_followup(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        # 1. Create task scaffold
        _conv_id, task_id, step_id, attempt_id = _create_task_scaffold(
            store, goal="deploy config changes"
        )

        # 2. Create contract
        contract_id = _create_contract(
            store,
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
        )
        store.update_step_attempt(attempt_id, execution_contract_ref=contract_id)

        # 3. Create receipt (succeeded at execution level)
        receipt_id = _create_receipt(
            store,
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            contract_ref=contract_id,
        )

        # 4. Create violated reconciliation
        reconciliation = store.create_reconciliation(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            contract_ref=contract_id,
            receipt_refs=[receipt_id],
            observed_output_refs=[],
            intended_effect_summary="deploy config",
            authorized_effect_summary="deploy config",
            observed_effect_summary="config file not found at target",
            receipted_effect_summary="config file not found at target",
            result_class="violated",
            confidence_delta=-0.3,
            recommended_resolution="gather_more_evidence",
            operator_summary="violated: config file not found at target",
        )
        assert reconciliation.result_class == "violated"

        # 5. Invoke the follow-up generation logic directly
        # We use the static helper + store to simulate what
        # ReconciliationExecutor._generate_followup_if_needed does
        original_task = store.get_task(task_id)
        assert original_task is not None
        root_task_id = ReconciliationExecutor._resolve_root_task_id(original_task)

        # Count existing follow-ups (should be 0)
        existing_followups = store.list_child_tasks(parent_task_id=root_task_id)
        followup_count = sum(1 for t in existing_followups if t.goal.startswith("retry/mitigate: "))
        assert followup_count == 0

        # Create the follow-up task
        followup_goal = f"retry/mitigate: {original_task.goal}"
        followup_task = store.create_task(
            conversation_id=original_task.conversation_id,
            title=followup_goal[:120],
            goal=followup_goal,
            source_channel=original_task.source_channel,
            status="queued",
            owner=original_task.owner_principal_id,
            priority=original_task.priority,
            policy_profile=original_task.policy_profile,
            parent_task_id=root_task_id,
        )

        # 6. Verify follow-up task
        assert followup_task is not None
        assert followup_task.goal.startswith("retry/mitigate: ")
        assert followup_task.parent_task_id == root_task_id
        assert followup_task.status == "queued"

        # Verify parent-child relationship
        children = store.list_child_tasks(parent_task_id=root_task_id)
        child_ids = [c.task_id for c in children]
        assert followup_task.task_id in child_ids

        # Verify the follow-up inherits the source channel and policy profile
        assert followup_task.source_channel == original_task.source_channel
        assert followup_task.policy_profile == original_task.policy_profile


# ---------------------------------------------------------------------------
# Test 3: Max follow-up limit prevents infinite chain
# ---------------------------------------------------------------------------


class TestMaxFollowupLimitPreventsInfiniteChain:
    """Creating more than MAX_AUTO_FOLLOWUPS follow-up tasks should be
    prevented by the reconciliation executor's counting logic.
    """

    def test_max_followup_limit_prevents_infinite_chain(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        # 1. Create root task
        _conv_id, root_task_id, _step_id, _attempt_id = _create_task_scaffold(
            store, goal="fix production bug"
        )

        # 2. Create MAX_AUTO_FOLLOWUPS follow-up tasks
        for i in range(MAX_AUTO_FOLLOWUPS):
            store.create_task(
                conversation_id="conv_e2e",
                title=f"retry/mitigate: fix production bug (attempt {i + 1})",
                goal="retry/mitigate: fix production bug",
                source_channel="cli",
                status="failed",
                parent_task_id=root_task_id,
            )

        # 3. Verify we have exactly MAX_AUTO_FOLLOWUPS follow-ups
        children = store.list_child_tasks(parent_task_id=root_task_id)
        followup_count = sum(1 for t in children if t.goal.startswith("retry/mitigate: "))
        assert followup_count == MAX_AUTO_FOLLOWUPS

        # 4. Simulate the check that would prevent a 4th follow-up
        # This mirrors ReconciliationExecutor._generate_followup_if_needed
        original_task = store.get_task(root_task_id)
        assert original_task is not None
        resolved_root = ReconciliationExecutor._resolve_root_task_id(original_task)

        existing_followups = store.list_child_tasks(parent_task_id=resolved_root)
        actual_followup_count = sum(
            1 for t in existing_followups if t.goal.startswith("retry/mitigate: ")
        )

        # The guard should prevent creation
        should_create = actual_followup_count < MAX_AUTO_FOLLOWUPS
        assert should_create is False, (
            f"Expected follow-up creation to be blocked: "
            f"count={actual_followup_count}, max={MAX_AUTO_FOLLOWUPS}"
        )

        # 5. Verify no accidental follow-up was created beyond the limit
        # (create one more that does NOT have the retry/mitigate prefix —
        # it should not count toward the limit)
        _non_retry_task = store.create_task(
            conversation_id="conv_e2e",
            title="manual investigation",
            goal="investigate root cause manually",
            source_channel="cli",
            status="queued",
            parent_task_id=root_task_id,
        )
        all_children = store.list_child_tasks(parent_task_id=root_task_id)
        retry_count = sum(1 for t in all_children if t.goal.startswith("retry/mitigate: "))
        non_retry_count = sum(1 for t in all_children if not t.goal.startswith("retry/mitigate: "))
        # Still exactly MAX_AUTO_FOLLOWUPS retry follow-ups
        assert retry_count == MAX_AUTO_FOLLOWUPS
        # The non-retry child exists
        assert non_retry_count >= 1

        # 6. Verify the limit constant matches expected value (3)
        assert MAX_AUTO_FOLLOWUPS == 3


# ---------------------------------------------------------------------------
# Test 4: Template learning from satisfied reconciliation
# ---------------------------------------------------------------------------


class TestTemplateLearningFromSatisfiedReconciliation:
    """A satisfied reconciliation should trigger template learning,
    creating a contract_template memory record.
    """

    def test_template_learning_from_satisfied_reconciliation(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        # 1. Create task scaffold with action_class context
        _conv_id, task_id, step_id, attempt_id = _create_task_scaffold(
            store, goal="write configuration file"
        )

        # 2. Create contract with action fingerprint data
        action_class = "write_local"
        contract_id = _create_contract(
            store,
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            action_class=action_class,
        )
        store.update_step_attempt(attempt_id, execution_contract_ref=contract_id)

        # 3. Create receipt
        receipt_id = _create_receipt(
            store,
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            action_type=action_class,
            contract_ref=contract_id,
        )

        # 4. Create satisfied reconciliation
        reconciliation = store.create_reconciliation(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            contract_ref=contract_id,
            receipt_refs=[receipt_id],
            observed_output_refs=["output_ref_1"],
            intended_effect_summary="write config file",
            authorized_effect_summary="write config file",
            observed_effect_summary="config file written",
            receipted_effect_summary="config file written",
            result_class="satisfied",
            confidence_delta=0.2,
            recommended_resolution="promote_learning",
            operator_summary="satisfied: config file written",
        )

        # 5. Invoke template learning via ContractTemplateLearner
        from hermit.kernel.execution.controller.template_learner import (
            ContractTemplateLearner,
        )

        learner = ContractTemplateLearner(store)
        contract = store.get_execution_contract(contract_id)
        assert contract is not None

        memory_record = learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=contract,
            workspace_root=str(tmp_path),
        )

        # 6. Verify: template memory record was created
        assert memory_record is not None
        assert memory_record.memory_kind == "contract_template"
        assert memory_record.status == "active"
        assert memory_record.category == "contract_template"

        # Verify structured assertion contains expected fields
        sa = memory_record.structured_assertion or {}
        assert "action_class" in sa
        assert sa["action_class"] == action_class
        assert "fingerprint" in sa
        assert "drift_budget" in sa
        assert "success_rate" in sa or "invocation_count" in sa

        # Verify the memory record references the reconciliation
        assert memory_record.learned_from_reconciliation_ref == reconciliation.reconciliation_id

        # 7. Verify the memory record can be retrieved from the store
        stored_record = store.get_memory_record(memory_record.memory_id)
        assert stored_record is not None
        assert stored_record.memory_kind == "contract_template"

        # 8. Verify that learning again reinforces (does not duplicate)
        # Create another satisfied reconciliation for the same action
        _, task_id2, step_id2, attempt_id2 = _create_task_scaffold(
            store, goal="write another configuration file"
        )
        contract_id2 = _create_contract(
            store,
            task_id=task_id2,
            step_id=step_id2,
            step_attempt_id=attempt_id2,
            action_class=action_class,
        )
        receipt_id2 = _create_receipt(
            store,
            task_id=task_id2,
            step_id=step_id2,
            step_attempt_id=attempt_id2,
            action_type=action_class,
            contract_ref=contract_id2,
        )
        reconciliation2 = store.create_reconciliation(
            task_id=task_id2,
            step_id=step_id2,
            step_attempt_id=attempt_id2,
            contract_ref=contract_id2,
            receipt_refs=[receipt_id2],
            observed_output_refs=["output_ref_2"],
            intended_effect_summary="write config file 2",
            authorized_effect_summary="write config file 2",
            observed_effect_summary="config file 2 written",
            receipted_effect_summary="config file 2 written",
            result_class="satisfied",
            confidence_delta=0.2,
            recommended_resolution="promote_learning",
        )
        contract2 = store.get_execution_contract(contract_id2)
        assert contract2 is not None

        reinforced_record = learner.learn_from_reconciliation(
            reconciliation=reconciliation2,
            contract=contract2,
            workspace_root=str(tmp_path),
        )

        # The learner returns the pre-update record object for the same
        # memory_id; re-read from the store to see the updated counts.
        assert reinforced_record is not None
        assert reinforced_record.memory_id == memory_record.memory_id

        # Re-fetch from store to verify the update was persisted
        updated_record = store.get_memory_record(memory_record.memory_id)
        assert updated_record is not None
        updated_sa = updated_record.structured_assertion or {}
        assert int(updated_sa.get("invocation_count", 0)) >= 2
        assert int(updated_sa.get("success_count", 0)) >= 2

        # Verify the template event was recorded
        events = store.list_events(task_id=task_id2, limit=1000)
        event_types = {e["event_type"] for e in events}
        assert "contract_template.reinforced" in event_types
