---
id: execution-analytics
title: "Governance execution analytics with metrics and decision patterns"
priority: normal
trust_zone: low
---

## Goal

Build a governance analytics engine that aggregates execution data into actionable metrics: approval latency, policy override rates, rollback frequency, evidence sufficiency trends, and tool usage heatmaps. Expose via webhook API and overnight dashboard integration.

## Steps

1. Create `src/hermit/kernel/analytics/engine.py`:
   - `AnalyticsEngine` class:
     - `compute_metrics(store, window_hours=24)` → `GovernanceMetrics`
       - Queries store for tasks, receipts, approvals, decisions, reconciliations within window
       - Computes:
         - `task_throughput`: completed tasks / hour
         - `approval_rate`: approved / total approval requests
         - `avg_approval_latency_seconds`: mean time from request to resolution
         - `rollback_rate`: rollbacks / total receipts
         - `evidence_sufficiency_avg`: mean evidence case sufficiency score
         - `tool_usage_counts`: dict of tool_name → execution count
         - `action_class_distribution`: dict of action_class → count
         - `policy_override_count`: decisions where verdict was changed from initial evaluation
         - `reconciliation_success_rate`: reconciled_applied / total reconciliations
     - `compute_trends(store, periods=7, period_hours=24)` → list of GovernanceMetrics
       - Computes metrics for each period to show trends
     - `top_risk_actions(store, limit=10)` → list of high-risk actions ranked by frequency

2. Create `src/hermit/kernel/analytics/models.py`:
   - `GovernanceMetrics`: all fields from above, plus `window_start`, `window_end`, `computed_at`
   - `ActionRiskEntry`: tool_name, action_class, risk_level, execution_count, rollback_count

3. Register analytics webhook endpoints:
   - `GET /analytics/metrics?hours=24` — current period metrics
   - `GET /analytics/trends?periods=7` — multi-period trend data
   - `GET /analytics/risk-actions?limit=10` — top risk actions
   - All require control_secret verification

4. Integrate into overnight dashboard:
   - Add analytics summary section to the overnight report
   - Include: task throughput trend, rollback rate alert (if > 10%), top 5 tools by usage

5. Write tests in `tests/unit/kernel/test_execution_analytics.py`:
   - Test metrics computation with known data (insert test receipts/approvals)
   - Test approval latency calculation
   - Test rollback rate with mixed success/rollback receipts
   - Test tool usage counts aggregation
   - Test trend computation across multiple periods
   - Test top_risk_actions ranking

## Constraints

- Analytics are READ-ONLY — never modify store data
- Use existing store query methods (list_receipts, list_approvals, list_decisions, etc.)
- Do NOT add new tables to KernelStore — compute everything from existing data
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/analytics/engine.py` exists
- [ ] `src/hermit/kernel/analytics/models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_execution_analytics.py -q` passes with >= 6 tests
- [ ] Metrics include task_throughput, rollback_rate, and evidence_sufficiency_avg

## Context

- KernelStore queries: `src/hermit/kernel/ledger/journal/store.py`
- Receipt model: `src/hermit/kernel/verification/receipts/receipts.py`
- Reconciliation model: `src/hermit/kernel/execution/recovery/reconciliations.py`
- Overnight dashboard: `src/hermit/plugins/builtin/hooks/overnight/dashboard.py`
- Webhook server: `src/hermit/plugins/builtin/hooks/webhook/server.py`
