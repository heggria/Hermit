"""Tests for PolicyEvidenceEnricher — pre-policy evidence injection.

Verifies that:
1. No-op when no matching template or pattern exists
2. Template match writes policy_suggestion to context for safe action classes
3. Template suggestion is filtered for unsafe action classes (write_local, patch_file)
4. Uses risk_hint (not risk_level) for suggestion computation
5. Task pattern is written to context when task_goal present
6. Pattern enrichment skipped when no task_goal
7. Critical risk_hint produces skip_approval_eligible=False
8. Trust score enrichment is added when sufficient receipt data exists
"""

from __future__ import annotations

import time
from pathlib import Path

from hermit.kernel.execution.controller.pattern_learner import TaskPatternLearner
from hermit.kernel.execution.controller.template_learner import ContractTemplateLearner
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.evaluators.enrichment import PolicyEvidenceEnricher
from hermit.kernel.policy.models.models import ActionRequest
from hermit.kernel.task.services.controller import TaskController

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


def _make_action_request(
    *,
    action_class: str = "write_local",
    tool_name: str = "write_file",
    risk_hint: str = "high",
    task_goal: str = "",
    derived: dict | None = None,
) -> ActionRequest:
    ctx: dict = {}
    if task_goal:
        ctx["task_goal"] = task_goal
    return ActionRequest(
        request_id="req-test",
        tool_name=tool_name,
        action_class=action_class,
        risk_hint=risk_hint,
        context=ctx,
        derived=derived or {},
    )


def _seed_template(
    store: KernelStore,
    *,
    action_class: str = "write_local",
    tool_name: str = "write_file",
    risk_level: str = "high",
    expected_effects: list[str] | None = None,
    invocation_count: int = 10,
    success_count: int = 10,
    success_rate: float = 1.0,
) -> str:
    """Create a contract + reconciliation to learn a template, then boost stats."""
    contract = store.create_execution_contract(
        task_id="task-seed",
        step_id="step-seed",
        step_attempt_id="attempt-seed",
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
    reconciliation = store.create_reconciliation(
        task_id="task-seed",
        step_id="step-seed",
        step_attempt_id="attempt-seed",
        contract_ref=contract.contract_id,
        receipt_refs=["receipt-seed"],
        observed_output_refs=[],
        intended_effect_summary="Expected.",
        authorized_effect_summary="Expected.",
        observed_effect_summary="Reconciled.",
        receipted_effect_summary="Reconciled.",
        result_class="satisfied",
        recommended_resolution="promote_learning",
    )
    learner = ContractTemplateLearner(store)
    memory = learner.learn_from_reconciliation(
        reconciliation=reconciliation,
        contract=contract,
    )
    assert memory is not None

    # Boost invocation stats so compute_policy_suggestion returns a result
    sa = dict(memory.structured_assertion or {})
    sa["invocation_count"] = invocation_count
    sa["success_count"] = success_count
    sa["success_rate"] = success_rate
    store.update_memory_record(memory.memory_id, structured_assertion=sa)
    return memory.memory_id


def _seed_task_pattern(store: KernelStore, *, goal: str = "write and test a file") -> str:
    """Create a task pattern memory record directly."""
    import hashlib

    fp = hashlib.sha256(b"write_local:write_file|execute_command:bash").hexdigest()[:16]
    memory = store.create_memory_record(
        task_id="task-pattern-seed",
        conversation_id=None,
        category="task_pattern",
        claim_text="Task pattern: write_local → execute_command",
        structured_assertion={
            "pattern_fingerprint": fp,
            "step_fingerprints": ["aaa", "bbb"],
            "step_descriptions": [
                {"action_class": "write_local", "tool_name": "write_file"},
                {"action_class": "execute_command", "tool_name": "bash"},
            ],
            "goal_keywords": sorted({"write", "test", "file"}),
            "invocation_count": 3,
            "success_count": 3,
            "success_rate": 1.0,
            "source_task_refs": ["task-pattern-seed"],
        },
        scope_kind="global",
        scope_ref="",
        promotion_reason="task_completed",
        retention_class="durable_template",
        status="active",
        confidence=0.6,
        trust_tier="durable",
        evidence_refs=["task-pattern-seed"],
        memory_kind="task_pattern",
        validation_basis="task_completed:task-pattern-seed",
        last_validated_at=time.time(),
    )
    return memory.memory_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPolicyEvidenceEnricher:
    def test_no_template_no_modification(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        enricher = PolicyEvidenceEnricher(store)
        req = _make_action_request()

        result = enricher.enrich(req)

        assert "policy_suggestion" not in result.context
        assert "matched_template_ref" not in result.context
        assert "task_pattern" not in result.context

    def test_template_match_injects_policy_suggestion(self, tmp_path: Path) -> None:
        """Template suggestion is injected for safe action classes."""
        store = _make_store(tmp_path)
        _seed_template(store, action_class="execute_command", tool_name="bash")
        enricher = PolicyEvidenceEnricher(store)
        req = _make_action_request(action_class="execute_command", tool_name="bash")

        result = enricher.enrich(req)

        assert "policy_suggestion" in result.context
        suggestion = result.context["policy_suggestion"]
        assert suggestion["skip_approval_eligible"] is True
        assert suggestion["template_ref"] != ""
        assert "matched_template_ref" in result.context

    def test_template_suggestion_filtered_for_unsafe_action_class(self, tmp_path: Path) -> None:
        """Template suggestion is NOT injected for dangerous action classes like write_local."""
        store = _make_store(tmp_path)
        _seed_template(store)  # default: write_local
        enricher = PolicyEvidenceEnricher(store)
        req = _make_action_request()  # default: write_local

        result = enricher.enrich(req)

        # Template is matched, but suggestion is filtered out
        assert "matched_template_ref" in result.context
        assert "policy_suggestion" not in result.context

    def test_uses_risk_hint_not_risk_level(self, tmp_path: Path) -> None:
        """The enricher uses action_request.risk_hint for suggestion computation."""
        store = _make_store(tmp_path)
        _seed_template(store, action_class="execute_command", tool_name="bash", risk_level="high")
        enricher = PolicyEvidenceEnricher(store)

        # With risk_hint="medium", suggestion should allow skip
        req = _make_action_request(
            action_class="execute_command", tool_name="bash", risk_hint="medium"
        )
        result = enricher.enrich(req)
        assert result.context["policy_suggestion"]["skip_approval_eligible"] is True

    def test_task_pattern_injected_with_goal(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        _seed_task_pattern(store)
        enricher = PolicyEvidenceEnricher(store)
        req = _make_action_request(task_goal="write and test a file")

        result = enricher.enrich(req)

        assert "task_pattern" in result.context
        pattern = result.context["task_pattern"]
        assert pattern["invocation_count"] == 3
        assert pattern["success_rate"] == 1.0
        assert len(pattern["step_descriptions"]) == 2

    def test_no_goal_skips_pattern_enrichment(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        _seed_task_pattern(store)
        enricher = PolicyEvidenceEnricher(store)
        req = _make_action_request(task_goal="")

        result = enricher.enrich(req)

        assert "task_pattern" not in result.context

    def test_critical_risk_hint_no_skip_approval(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        _seed_template(store, action_class="execute_command", tool_name="bash")
        enricher = PolicyEvidenceEnricher(store)
        req = _make_action_request(
            action_class="execute_command", tool_name="bash", risk_hint="critical"
        )

        result = enricher.enrich(req)

        assert "policy_suggestion" in result.context
        assert result.context["policy_suggestion"]["skip_approval_eligible"] is False

    def test_trust_enrichment_with_sufficient_data(self, tmp_path: Path) -> None:
        """Trust score adjustment is added to context when enough receipts exist."""
        store = _make_store(tmp_path)
        # Create a task/step/attempt for proper receipt seeding
        conv = store.ensure_conversation("conv-trust-test", source_channel="test")
        task = store.create_task(
            conversation_id=conv.conversation_id,
            title="t",
            goal="g",
            status="active",
            priority="normal",
            owner="operator",
            policy_profile="default",
            source_channel="test",
        )
        step = store.create_step(task_id=task.task_id, kind="tool_call", status="active")
        attempt = store.create_step_attempt(
            task_id=task.task_id, step_id=step.step_id, attempt=1, status="active"
        )
        # Create enough receipts to exceed _MIN_EXECUTIONS threshold (5)
        for _i in range(6):
            store.create_receipt(
                task_id=task.task_id,
                step_id=step.step_id,
                step_attempt_id=attempt.step_attempt_id,
                action_type="execute_command",
                input_refs=[],
                environment_ref=None,
                policy_result={},
                approval_ref=None,
                output_refs=[],
                result_summary="ok",
                result_code="succeeded",
            )
        enricher = PolicyEvidenceEnricher(store)
        req = _make_action_request(
            action_class="execute_command", tool_name="bash", risk_hint="high"
        )

        result = enricher.enrich(req)

        # With 6/6 succeeded, composite score should be high (>= 0.85)
        # suggesting a downgrade from "high" to "low"
        assert "trust_risk_adjustment" in result.context
        adj = result.context["trust_risk_adjustment"]
        assert adj["current_risk_band"] == "high"
        assert adj["suggested_risk_band"] in ("low", "medium")
        assert adj["trust_score_ref"] > 0

    def test_trust_enrichment_skipped_insufficient_data(self, tmp_path: Path) -> None:
        """Trust enrichment is skipped when not enough receipts exist."""
        store = _make_store(tmp_path)
        enricher = PolicyEvidenceEnricher(store)
        req = _make_action_request()

        result = enricher.enrich(req)

        assert "trust_risk_adjustment" not in result.context


# ---------------------------------------------------------------------------
# Pattern learning trigger
# ---------------------------------------------------------------------------


def _create_completed_task_with_steps(
    store: KernelStore,
    controller: TaskController,
    *,
    goal: str = "write and test a file",
) -> str:
    """Create a task with 2+ satisfied step attempts and execution contracts."""
    ctx = controller.start_task(
        conversation_id="chat-learn",
        goal=goal,
        source_channel="chat",
        kind="respond",
        workspace_root="/tmp",
    )
    task_id = ctx.task_id

    for i, (ac, tn) in enumerate(
        [
            ("write_local", "write_file"),
            ("execute_command", "bash"),
        ]
    ):
        step = store.create_step(task_id=task_id, kind="action")
        attempt = store.create_step_attempt(task_id=task_id, step_id=step.step_id, attempt=i + 1)
        contract = store.create_execution_contract(
            task_id=task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            objective=f"{tn}: {ac}",
            expected_effects=[f"action:{ac}"],
            success_criteria={"tool_name": tn, "action_class": ac},
            reversibility_class="reversible",
            required_receipt_classes=[],
            drift_budget={},
            risk_budget={"risk_level": "high"},
            status="satisfied",
            action_contract_refs=[ac],
        )
        store.update_step_attempt(
            attempt.step_attempt_id,
            status="succeeded",
            execution_contract_ref=contract.contract_id,
        )
        store.update_step(step.step_id, status="succeeded")

    return task_id


class TestPatternLearningTrigger:
    def test_learn_from_completed_task_creates_pattern(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        controller = TaskController(store)
        task_id = _create_completed_task_with_steps(store, controller)

        learner = TaskPatternLearner(store)
        memory = learner.learn_from_completed_task(task_id)

        assert memory is not None
        assert memory.memory_kind == "task_pattern"
        assert memory.status == "active"

    def test_learned_pattern_found_by_enricher(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        controller = TaskController(store)
        task_id = _create_completed_task_with_steps(store, controller, goal="write and test a file")

        learner = TaskPatternLearner(store)
        learner.learn_from_completed_task(task_id)

        enricher = PolicyEvidenceEnricher(store)
        req = _make_action_request(task_goal="write and test a file")
        result = enricher.enrich(req)

        assert "task_pattern" in result.context
        assert result.context["task_pattern"]["invocation_count"] >= 1
