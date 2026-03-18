"""Unit tests for the execution analytics engine and models."""

from __future__ import annotations

import time
from pathlib import Path

from hermit.kernel.analytics.engine import AnalyticsEngine
from hermit.kernel.analytics.models import ActionRiskEntry, GovernanceMetrics
from hermit.kernel.ledger.journal.store import KernelStore


def _seed_store(store: KernelStore, *, now: float | None = None) -> dict[str, str]:
    """Create a minimal task/step/attempt chain and return ref IDs."""
    now = now or time.time()
    store.ensure_conversation("conv-analytics", source_channel="test")
    task = store.create_task(
        conversation_id="conv-analytics",
        title="Analytics Test Task",
        goal="test analytics",
        source_channel="test",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    return {
        "task_id": task.task_id,
        "step_id": step.step_id,
        "step_attempt_id": attempt.step_attempt_id,
    }


def test_models_default_values() -> None:
    """GovernanceMetrics and ActionRiskEntry have sensible defaults."""
    metrics = GovernanceMetrics()
    assert metrics.task_throughput == 0
    assert metrics.approval_rate == 0.0
    assert metrics.avg_approval_latency == 0.0
    assert metrics.rollback_rate == 0.0
    assert metrics.evidence_sufficiency_avg == 0.0
    assert metrics.tool_usage_counts == {}
    assert metrics.action_class_distribution == {}
    assert metrics.risk_entries == []

    entry = ActionRiskEntry(action_type="write_local")
    assert entry.risk_level is None
    assert entry.result_code == "succeeded"
    assert entry.rollback_supported is False


def test_compute_metrics_empty_store(tmp_path: Path) -> None:
    """An empty store produces zero-value metrics."""
    store = KernelStore(tmp_path / "state.db")
    engine = AnalyticsEngine(store)
    metrics = engine.compute_metrics()

    assert metrics.task_throughput == 0
    assert metrics.approval_rate == 0.0
    assert metrics.rollback_rate == 0.0
    assert metrics.tool_usage_counts == {}
    assert metrics.risk_entries == []
    assert metrics.window_end >= metrics.window_start


def test_compute_metrics_task_throughput(tmp_path: Path) -> None:
    """Task throughput counts tasks created within the window."""
    store = KernelStore(tmp_path / "state.db")
    now = time.time()

    store.ensure_conversation("conv-tp", source_channel="test")
    store.create_task(
        conversation_id="conv-tp",
        title="Task A",
        goal="goal",
        source_channel="test",
    )
    store.create_task(
        conversation_id="conv-tp",
        title="Task B",
        goal="goal",
        source_channel="test",
    )

    engine = AnalyticsEngine(store)
    metrics = engine.compute_metrics(window_start=now - 10, window_end=now + 10)
    assert metrics.task_throughput == 2


def test_compute_metrics_approval_rate_and_latency(tmp_path: Path) -> None:
    """Approval rate and latency are computed from approval records."""
    store = KernelStore(tmp_path / "state.db")
    refs = _seed_store(store)

    # Create two approvals: one granted, one pending
    granted = store.create_approval(
        task_id=refs["task_id"],
        step_id=refs["step_id"],
        step_attempt_id=refs["step_attempt_id"],
        approval_type="write_local",
        requested_action={"summary": "write"},
        request_packet_ref="packet_1",
    )
    store.resolve_approval(
        granted.approval_id,
        status="granted",
        resolved_by="operator",
        resolution={"action": "grant"},
    )

    store.create_approval(
        task_id=refs["task_id"],
        step_id=refs["step_id"],
        step_attempt_id=refs["step_attempt_id"],
        approval_type="execute_command",
        requested_action={"summary": "exec"},
        request_packet_ref="packet_2",
    )

    engine = AnalyticsEngine(store)
    now = time.time()
    metrics = engine.compute_metrics(window_start=now - 60, window_end=now + 60)

    assert metrics.approval_rate == 0.5  # 1 granted out of 2


def test_compute_metrics_tool_usage_and_risk_entries(tmp_path: Path) -> None:
    """Tool usage counts and risk entries are derived from receipts and decisions."""
    store = KernelStore(tmp_path / "state.db")
    refs = _seed_store(store)

    decision = store.create_decision(
        task_id=refs["task_id"],
        step_id=refs["step_id"],
        step_attempt_id=refs["step_attempt_id"],
        decision_type="execution_authorization",
        verdict="allow",
        reason="Allowed by policy.",
        risk_level="high",
        action_type="write_local",
    )

    store.create_receipt(
        task_id=refs["task_id"],
        step_id=refs["step_id"],
        step_attempt_id=refs["step_attempt_id"],
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="wrote file",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        rollback_supported=True,
    )

    store.create_receipt(
        task_id=refs["task_id"],
        step_id=refs["step_id"],
        step_attempt_id=refs["step_attempt_id"],
        action_type="read_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="read file",
        result_code="succeeded",
    )

    engine = AnalyticsEngine(store)
    now = time.time()
    metrics = engine.compute_metrics(window_start=now - 60, window_end=now + 60)

    assert metrics.tool_usage_counts == {"write_local": 1, "read_local": 1}
    assert metrics.action_class_distribution == {"write_local": 1}
    assert len(metrics.risk_entries) == 2

    high_risk = [e for e in metrics.risk_entries if e.risk_level == "high"]
    assert len(high_risk) == 1
    assert high_risk[0].action_type == "write_local"
    assert high_risk[0].rollback_supported is True


def test_compute_metrics_rollback_rate(tmp_path: Path) -> None:
    """Rollback rate reflects receipts with non-default rollback status."""
    store = KernelStore(tmp_path / "state.db")
    refs = _seed_store(store)

    store.create_receipt(
        task_id=refs["task_id"],
        step_id=refs["step_id"],
        step_attempt_id=refs["step_attempt_id"],
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="wrote",
        result_code="succeeded",
        rollback_supported=True,
        rollback_status="rolled_back",
    )

    store.create_receipt(
        task_id=refs["task_id"],
        step_id=refs["step_id"],
        step_attempt_id=refs["step_attempt_id"],
        action_type="read_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="read",
        result_code="succeeded",
        rollback_status="not_requested",
    )

    engine = AnalyticsEngine(store)
    now = time.time()
    metrics = engine.compute_metrics(window_start=now - 60, window_end=now + 60)

    assert metrics.rollback_rate == 0.5  # 1 rolled back out of 2


def test_compute_metrics_evidence_sufficiency(tmp_path: Path) -> None:
    """Evidence sufficiency average is computed from evidence case records."""
    store = KernelStore(tmp_path / "state.db")
    refs = _seed_store(store)

    store.create_evidence_case(
        task_id=refs["task_id"],
        subject_kind="step_attempt",
        subject_ref=refs["step_attempt_id"],
        sufficiency_score=0.8,
    )
    store.create_evidence_case(
        task_id=refs["task_id"],
        subject_kind="step_attempt",
        subject_ref=refs["step_attempt_id"],
        sufficiency_score=0.4,
    )

    engine = AnalyticsEngine(store)
    now = time.time()
    metrics = engine.compute_metrics(window_start=now - 60, window_end=now + 60)

    assert abs(metrics.evidence_sufficiency_avg - 0.6) < 1e-9


def test_compute_metrics_window_filtering(tmp_path: Path) -> None:
    """Records outside the time window are excluded from metrics."""
    store = KernelStore(tmp_path / "state.db")
    refs = _seed_store(store)

    store.create_receipt(
        task_id=refs["task_id"],
        step_id=refs["step_id"],
        step_attempt_id=refs["step_attempt_id"],
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={},
        approval_ref=None,
        output_refs=[],
        result_summary="wrote",
        result_code="succeeded",
    )

    engine = AnalyticsEngine(store)
    # Use a window far in the future so the receipt falls outside
    future = time.time() + 100000
    metrics = engine.compute_metrics(window_start=future, window_end=future + 10)

    assert metrics.task_throughput == 0
    assert metrics.tool_usage_counts == {}
    assert metrics.risk_entries == []


def test_compute_metrics_scoped_to_task(tmp_path: Path) -> None:
    """When task_id is specified, only that task's data is counted."""
    store = KernelStore(tmp_path / "state.db")
    refs1 = _seed_store(store)

    # Create a second task
    store.ensure_conversation("conv-analytics-2", source_channel="test")
    task2 = store.create_task(
        conversation_id="conv-analytics-2",
        title="Other Task",
        goal="other",
        source_channel="test",
    )
    step2 = store.create_step(task_id=task2.task_id, kind="respond")
    attempt2 = store.create_step_attempt(task_id=task2.task_id, step_id=step2.step_id)

    store.create_receipt(
        task_id=refs1["task_id"],
        step_id=refs1["step_id"],
        step_attempt_id=refs1["step_attempt_id"],
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={},
        approval_ref=None,
        output_refs=[],
        result_summary="task1 write",
        result_code="succeeded",
    )

    store.create_receipt(
        task_id=task2.task_id,
        step_id=step2.step_id,
        step_attempt_id=attempt2.step_attempt_id,
        action_type="read_local",
        input_refs=[],
        environment_ref=None,
        policy_result={},
        approval_ref=None,
        output_refs=[],
        result_summary="task2 read",
        result_code="succeeded",
    )

    engine = AnalyticsEngine(store)
    now = time.time()
    metrics = engine.compute_metrics(
        window_start=now - 60, window_end=now + 60, task_id=refs1["task_id"]
    )

    assert metrics.tool_usage_counts == {"write_local": 1}
    assert len(metrics.risk_entries) == 1
    assert metrics.task_throughput == 1
