---
id: dynamic-trust-scoring
title: "Dynamic trust scoring system for tools, actions, and patterns"
priority: normal
trust_zone: low
---

## Goal

Build a dynamic trust scoring system that learns from execution history. Each tool, action class, and resource scope gets a trust score based on historical success rates, rollback frequency, and reconciliation outcomes. Policy evaluation uses trust scores to auto-adjust risk levels and approval thresholds.

## Steps

1. Create `src/hermit/kernel/policy/trust/scoring.py`:
   - `TrustScorer` class:
     - `compute_tool_score(tool_name, store)` → TrustScore
       - Query receipts for the tool, compute: success_rate, rollback_rate, avg_reconciliation_confidence
       - Score = weighted combination: 0.5 * success_rate + 0.3 * (1 - rollback_rate) + 0.2 * avg_confidence
       - Minimum 5 executions before scoring (returns None otherwise)
     - `compute_action_class_score(action_class, store)` → TrustScore
       - Same formula, aggregated by action_class
     - `compute_resource_score(resource_pattern, store)` → TrustScore
       - Score based on path prefix match against historical receipts
     - `suggest_risk_adjustment(tool_name, current_risk, store)` → RiskAdjustment
       - If trust score > 0.9 and executions > 20: suggest downgrade risk by one level
       - If trust score < 0.5 or rollback_rate > 0.3: suggest upgrade risk by one level
       - Returns adjustment with rationale

2. Create `src/hermit/kernel/policy/trust/models.py`:
   - `TrustScore`: score (0.0-1.0), success_count, failure_count, rollback_count, sample_size, computed_at
   - `RiskAdjustment`: tool_name, current_risk, suggested_risk, direction (upgrade/downgrade/maintain), rationale, confidence

3. Integrate into PolicyEngine:
   - After `evaluate_rules()`, check `suggest_risk_adjustment()` for the tool
   - If an adjustment is suggested, log it as a decision event but do NOT auto-apply
   - Expose adjustments in proof summary for operator review

4. Add trust inspection tool:
   - `trust_scores` tool: displays trust scores for top-N tools and action classes
   - Registered via patrol or webhook tool registration

5. Write tests in `tests/unit/kernel/test_trust_scoring.py`:
   - Test score computation with all-success history → high score
   - Test score computation with high rollback rate → low score
   - Test minimum sample threshold (< 5 returns None)
   - Test risk adjustment suggestion for high-trust tool
   - Test risk adjustment suggestion for low-trust tool
   - Test resource score with path prefix matching

## Constraints

- Trust scores are ADVISORY only — they do not auto-change policy
- Minimum sample size of 5 executions before scoring
- Do NOT modify PolicyEngine's core evaluation logic — adjustments are logged, not applied
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/policy/trust/scoring.py` exists
- [ ] `src/hermit/kernel/policy/trust/models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_trust_scoring.py -q` passes with >= 6 tests
- [ ] Trust scores are computed from receipt history with correct formula

## Context

- PolicyEngine: `src/hermit/kernel/policy/evaluators/engine.py`
- Receipt queries: `src/hermit/kernel/ledger/journal/store.py` (list_receipts)
- Reconciliation records: `src/hermit/kernel/execution/recovery/reconciliations.py`
- Risk levels: defined in action contracts `src/hermit/kernel/execution/controller/contracts.py`
