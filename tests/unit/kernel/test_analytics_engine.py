"""Tests for AnalyticsEngine governance metrics computation."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.kernel.analytics.engine import AnalyticsEngine
from hermit.kernel.analytics.models import GovernanceMetrics

# ---------------------------------------------------------------------------
# Fake store
# ---------------------------------------------------------------------------


def _receipt(
    receipt_id: str,
    action_type: str = "bash",
    result_code: str = "succeeded",
    rollback_status: str = "not_requested",
    rollback_supported: bool = False,
    decision_ref: str | None = None,
    created_at: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        receipt_id=receipt_id,
        action_type=action_type,
        result_code=result_code,
        rollback_status=rollback_status,
        rollback_supported=rollback_supported,
        decision_ref=decision_ref,
        created_at=created_at,
    )


def _approval(
    approval_id: str,
    status: str = "granted",
    requested_at: float | None = None,
    resolved_at: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        approval_id=approval_id,
        status=status,
        requested_at=requested_at,
        resolved_at=resolved_at,
    )


def _decision(
    decision_id: str,
    action_type: str | None = None,
    risk_level: str | None = None,
    created_at: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        decision_id=decision_id,
        action_type=action_type,
        risk_level=risk_level,
        created_at=created_at,
    )


def _task(
    task_id: str,
    status: str = "completed",
    created_at: float = 0.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id,
        status=status,
        created_at=created_at,
    )


def _evidence_case(
    evidence_case_id: str,
    sufficiency_score: float = 0.8,
    created_at: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        evidence_case_id=evidence_case_id,
        sufficiency_score=sufficiency_score,
        created_at=created_at,
    )


def _reconciliation(
    reconciliation_id: str,
    created_at: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        reconciliation_id=reconciliation_id,
        created_at=created_at,
    )


class FakeAnalyticsStore:
    """Minimal store returning pre-seeded records for analytics queries."""

    def __init__(
        self,
        *,
        receipts: list[Any] | None = None,
        approvals: list[Any] | None = None,
        decisions: list[Any] | None = None,
        tasks: list[Any] | None = None,
        evidence_cases: list[Any] | None = None,
        reconciliations: list[Any] | None = None,
    ) -> None:
        self._receipts = receipts or []
        self._approvals = approvals or []
        self._decisions = decisions or []
        self._tasks = tasks or []
        self._evidence_cases = evidence_cases or []
        self._reconciliations = reconciliations or []

    def list_receipts(
        self, *, task_id: str | None = None, action_type: str | None = None, limit: int = 50
    ) -> list[Any]:
        if task_id:
            return [r for r in self._receipts if getattr(r, "task_id", None) == task_id][:limit]
        return self._receipts[:limit]

    def list_approvals(
        self, *, task_id: str | None = None, limit: int = 100, **_kw: Any
    ) -> list[Any]:
        if task_id:
            return [a for a in self._approvals if getattr(a, "task_id", None) == task_id][:limit]
        return self._approvals[:limit]

    def list_decisions(self, *, task_id: str | None = None, limit: int = 50) -> list[Any]:
        if task_id:
            return [d for d in self._decisions if getattr(d, "task_id", None) == task_id][:limit]
        return self._decisions[:limit]

    def list_tasks(self, *, limit: int = 500, **_kw: Any) -> list[Any]:
        return self._tasks[:limit]

    def list_evidence_cases(self, *, task_id: str | None = None, limit: int = 50) -> list[Any]:
        return self._evidence_cases[:limit]

    def list_reconciliations(self, *, task_id: str | None = None, limit: int = 50) -> list[Any]:
        return self._reconciliations[:limit]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAnalyticsEngineInit:
    def test_construction(self) -> None:
        store = FakeAnalyticsStore()
        engine = AnalyticsEngine(store)
        assert engine._store is store

    def test_compute_metrics_returns_governance_metrics(self) -> None:
        store = FakeAnalyticsStore()
        engine = AnalyticsEngine(store)
        result = engine.compute_metrics()
        assert isinstance(result, GovernanceMetrics)


class TestComputeMetricsEmpty:
    """Metrics from an empty store yield zero/default values."""

    def test_empty_store(self) -> None:
        store = FakeAnalyticsStore()
        engine = AnalyticsEngine(store)
        m = engine.compute_metrics()
        assert m.task_throughput == 0
        assert m.approval_rate == 0.0
        assert m.avg_approval_latency == 0.0
        assert m.rollback_rate == 0.0
        assert m.evidence_sufficiency_avg == 0.0
        assert m.tool_usage_counts == {}
        assert m.action_class_distribution == {}
        assert m.risk_entries == []


class TestComputeMetricsApprovals:
    """Approval rate and latency calculations."""

    def test_approval_rate_all_granted(self) -> None:
        now = time.time()
        store = FakeAnalyticsStore(
            approvals=[
                _approval("a1", status="granted", requested_at=now - 100, resolved_at=now - 50),
                _approval("a2", status="granted", requested_at=now - 200, resolved_at=now - 150),
            ]
        )
        engine = AnalyticsEngine(store)
        m = engine.compute_metrics(window_start=now - 300, window_end=now)
        assert m.approval_rate == 1.0

    def test_approval_rate_mixed(self) -> None:
        now = time.time()
        store = FakeAnalyticsStore(
            approvals=[
                _approval("a1", status="granted", requested_at=now - 100, resolved_at=now - 50),
                _approval("a2", status="denied", requested_at=now - 200, resolved_at=now - 150),
            ]
        )
        engine = AnalyticsEngine(store)
        m = engine.compute_metrics(window_start=now - 300, window_end=now)
        assert m.approval_rate == 0.5

    def test_approval_latency(self) -> None:
        now = time.time()
        store = FakeAnalyticsStore(
            approvals=[
                _approval("a1", status="granted", requested_at=now - 100, resolved_at=now - 80),
                _approval("a2", status="granted", requested_at=now - 200, resolved_at=now - 190),
            ]
        )
        engine = AnalyticsEngine(store)
        m = engine.compute_metrics(window_start=now - 300, window_end=now)
        # latencies: 20, 10 → avg = 15
        assert m.avg_approval_latency == pytest.approx(15.0)


class TestComputeMetricsReceipts:
    """Tool usage counts and rollback rate from receipts."""

    def test_tool_usage_counts(self) -> None:
        now = time.time()
        store = FakeAnalyticsStore(
            receipts=[
                _receipt("r1", action_type="bash", created_at=now - 10),
                _receipt("r2", action_type="bash", created_at=now - 20),
                _receipt("r3", action_type="write_file", created_at=now - 30),
            ]
        )
        engine = AnalyticsEngine(store)
        m = engine.compute_metrics(window_start=now - 300, window_end=now)
        assert m.tool_usage_counts == {"bash": 2, "write_file": 1}

    def test_rollback_rate(self) -> None:
        now = time.time()
        store = FakeAnalyticsStore(
            receipts=[
                _receipt("r1", rollback_status="not_requested", created_at=now - 10),
                _receipt("r2", rollback_status="completed", created_at=now - 20),
            ]
        )
        engine = AnalyticsEngine(store)
        m = engine.compute_metrics(window_start=now - 300, window_end=now)
        assert m.rollback_rate == 0.5

    def test_rollback_rate_na_excluded(self) -> None:
        now = time.time()
        store = FakeAnalyticsStore(
            receipts=[
                _receipt("r1", rollback_status="n/a", created_at=now - 10),
                _receipt("r2", rollback_status="n/a", created_at=now - 20),
            ]
        )
        engine = AnalyticsEngine(store)
        m = engine.compute_metrics(window_start=now - 300, window_end=now)
        assert m.rollback_rate == 0.0


class TestComputeMetricsEvidence:
    """Evidence sufficiency average."""

    def test_evidence_sufficiency_average(self) -> None:
        now = time.time()
        store = FakeAnalyticsStore(
            evidence_cases=[
                _evidence_case("e1", sufficiency_score=0.6, created_at=now - 10),
                _evidence_case("e2", sufficiency_score=1.0, created_at=now - 20),
            ]
        )
        engine = AnalyticsEngine(store)
        m = engine.compute_metrics(window_start=now - 300, window_end=now)
        assert m.evidence_sufficiency_avg == pytest.approx(0.8)


class TestComputeMetricsDecisions:
    """Action class distribution and risk entries from decisions."""

    def test_action_class_distribution(self) -> None:
        now = time.time()
        store = FakeAnalyticsStore(
            decisions=[
                _decision("d1", action_type="shell", created_at=now - 10),
                _decision("d2", action_type="shell", created_at=now - 20),
                _decision("d3", action_type="file_write", created_at=now - 30),
            ]
        )
        engine = AnalyticsEngine(store)
        m = engine.compute_metrics(window_start=now - 300, window_end=now)
        assert m.action_class_distribution == {"shell": 2, "file_write": 1}

    def test_risk_entries_linked_to_decisions(self) -> None:
        now = time.time()
        store = FakeAnalyticsStore(
            receipts=[
                _receipt("r1", action_type="bash", decision_ref="d1", created_at=now - 10),
            ],
            decisions=[
                _decision("d1", risk_level="high", created_at=now - 10),
            ],
        )
        engine = AnalyticsEngine(store)
        m = engine.compute_metrics(window_start=now - 300, window_end=now)
        assert len(m.risk_entries) == 1
        assert m.risk_entries[0].risk_level == "high"
        assert m.risk_entries[0].action_type == "bash"


class TestComputeMetricsTaskThroughput:
    """Task throughput: count of tasks created in the window."""

    def test_task_throughput(self) -> None:
        now = time.time()
        store = FakeAnalyticsStore(
            tasks=[
                _task("t1", created_at=now - 10),
                _task("t2", created_at=now - 20),
                _task("t3", created_at=now - 5000),  # outside window
            ]
        )
        engine = AnalyticsEngine(store)
        m = engine.compute_metrics(window_start=now - 300, window_end=now)
        assert m.task_throughput == 2


class TestComputeMetricsWindow:
    """Time window filtering."""

    def test_default_window_is_24h(self) -> None:
        store = FakeAnalyticsStore()
        engine = AnalyticsEngine(store)
        now = time.time()
        m = engine.compute_metrics(window_end=now)
        assert m.window_end == now
        assert m.window_start == pytest.approx(now - 86400.0, abs=1)

    def test_records_outside_window_excluded(self) -> None:
        now = time.time()
        store = FakeAnalyticsStore(
            receipts=[
                _receipt("r1", created_at=now - 500),  # outside
                _receipt("r2", created_at=now - 50),  # inside
            ]
        )
        engine = AnalyticsEngine(store)
        m = engine.compute_metrics(window_start=now - 100, window_end=now)
        assert len(m.risk_entries) == 1
