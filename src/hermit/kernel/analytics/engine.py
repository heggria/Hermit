from __future__ import annotations

import time

import structlog

from hermit.kernel.analytics.models import ActionRiskEntry, GovernanceMetrics
from hermit.kernel.ledger.journal.store import KernelStore

_log = structlog.get_logger()


class AnalyticsEngine:
    """Read-only analytics engine that computes governance metrics from the kernel store.

    This engine never modifies store data. It queries receipts, approvals,
    decisions, reconciliations, and evidence cases within a time window to
    produce aggregated governance metrics.
    """

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    def compute_metrics(
        self,
        *,
        window_start: float | None = None,
        window_end: float | None = None,
        task_id: str | None = None,
        limit: int = 2000,
    ) -> GovernanceMetrics:
        """Compute governance metrics over the given time window.

        Args:
            window_start: Unix timestamp for the start of the window.
                Defaults to 24 hours ago.
            window_end: Unix timestamp for the end of the window.
                Defaults to now.
            task_id: Optional task ID to scope metrics to a single task.
            limit: Maximum number of records to query per entity type.
                Set higher than the expected record count in the window
                to avoid silent truncation before Python-side time
                filtering.  The store layer should eventually support
                native time-window parameters to eliminate this issue.

        Returns:
            GovernanceMetrics with aggregated statistics.
        """
        now = time.time()
        if window_end is None:
            window_end = now
        if window_start is None:
            window_start = window_end - 86400.0

        # TODO(perf): Push time-window filtering into the store layer (SQL WHERE
        # clauses) so the limit applies *after* the time predicate.  Until then,
        # `limit` must be set high enough to avoid silently dropping records that
        # fall inside the window but beyond the LIMIT cutoff.

        raw_receipts = self._store.list_receipts(task_id=task_id, limit=limit)
        receipts = [
            r
            for r in raw_receipts
            if r.created_at is not None and window_start <= r.created_at <= window_end
        ]
        if len(raw_receipts) >= limit:
            _log.warning(
                "analytics_possible_truncation",
                entity="receipts",
                limit=limit,
                window_start=window_start,
                window_end=window_end,
                hint="increase limit or push time filtering to SQL",
            )

        raw_approvals = self._store.list_approvals(task_id=task_id, limit=limit)
        approvals = [
            a
            for a in raw_approvals
            if a.requested_at is not None and window_start <= a.requested_at <= window_end
        ]
        if len(raw_approvals) >= limit:
            _log.warning(
                "analytics_possible_truncation",
                entity="approvals",
                limit=limit,
                window_start=window_start,
                window_end=window_end,
                hint="increase limit or push time filtering to SQL",
            )

        raw_decisions = self._store.list_decisions(task_id=task_id, limit=limit)
        decisions = [
            d
            for d in raw_decisions
            if d.created_at is not None and window_start <= d.created_at <= window_end
        ]
        if len(raw_decisions) >= limit:
            _log.warning(
                "analytics_possible_truncation",
                entity="decisions",
                limit=limit,
                window_start=window_start,
                window_end=window_end,
                hint="increase limit or push time filtering to SQL",
            )

        reconciliations = self._store.list_reconciliations(task_id=task_id, limit=limit)
        reconciliations = [
            r
            for r in reconciliations
            if r.created_at is not None and window_start <= r.created_at <= window_end
        ]

        evidence_cases = self._store.list_evidence_cases(task_id=task_id, limit=limit)
        evidence_cases = [
            e
            for e in evidence_cases
            if e.created_at is not None and window_start <= e.created_at <= window_end
        ]

        tasks = self._store.list_tasks(limit=limit)
        tasks = [
            t
            for t in tasks
            if t.created_at is not None
            and window_start <= t.created_at <= window_end
            and (task_id is None or t.task_id == task_id)
        ]
        task_throughput = len(tasks)

        # Approval rate: fraction of approvals that were granted/approved
        total_approvals = len(approvals)
        granted_approvals = sum(1 for a in approvals if a.status in {"granted", "approved"})
        approval_rate = granted_approvals / total_approvals if total_approvals > 0 else 0.0

        # Average approval latency: time from requested_at to resolved_at
        latencies: list[float] = []
        for a in approvals:
            if a.resolved_at is not None and a.requested_at is not None:
                latency = a.resolved_at - a.requested_at
                if latency >= 0:
                    latencies.append(latency)
        avg_approval_latency = sum(latencies) / len(latencies) if latencies else 0.0

        # Rollback rate: fraction of receipts that have rollback_status != "not_requested"
        total_receipts = len(receipts)
        rolled_back = sum(1 for r in receipts if r.rollback_status not in {"not_requested", "n/a"})
        rollback_rate = rolled_back / total_receipts if total_receipts > 0 else 0.0

        # Evidence sufficiency average from evidence cases
        if evidence_cases:
            avg_evidence_sufficiency = sum(e.sufficiency_score for e in evidence_cases) / len(
                evidence_cases
            )
        else:
            avg_evidence_sufficiency = 0.0

        # Tool usage counts from receipt action_type
        tool_usage_counts: dict[str, int] = {}
        for r in receipts:
            action = r.action_type or "unknown"
            tool_usage_counts[action] = tool_usage_counts.get(action, 0) + 1

        # Action class distribution from decision action_type
        action_class_distribution: dict[str, int] = {}
        for d in decisions:
            action = d.action_type or "unknown"
            action_class_distribution[action] = action_class_distribution.get(action, 0) + 1

        # Build risk entries from receipts + decisions
        decision_risk_map: dict[str, str | None] = {}
        for d in decisions:
            if d.risk_level is not None:
                decision_risk_map[d.decision_id] = d.risk_level

        risk_entries: list[ActionRiskEntry] = []
        for r in receipts:
            risk_level = None
            if r.decision_ref and r.decision_ref in decision_risk_map:
                risk_level = decision_risk_map[r.decision_ref]
            risk_entries.append(
                ActionRiskEntry(
                    action_type=r.action_type,
                    risk_level=risk_level,
                    result_code=r.result_code,
                    receipt_id=r.receipt_id,
                    rollback_supported=r.rollback_supported,
                )
            )

        return GovernanceMetrics(
            task_throughput=task_throughput,
            approval_rate=approval_rate,
            avg_approval_latency=avg_approval_latency,
            rollback_rate=rollback_rate,
            avg_evidence_sufficiency=avg_evidence_sufficiency,
            tool_usage_counts=tool_usage_counts,
            action_class_distribution=action_class_distribution,
            risk_entries=risk_entries,
            window_start=window_start,
            window_end=window_end,
        )
