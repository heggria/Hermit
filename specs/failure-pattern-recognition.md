---
id: failure-pattern-recognition
title: "Recognize recurring failure patterns and adapt execution strategies"
priority: normal
trust_zone: low
---

## Goal

Build a failure pattern recognition system that analyzes the kernel journal to identify recurring failure modes. When a new task attempts an action that historically fails, inject a warning or suggest alternative strategy.

## Steps

1. Create `src/hermit/kernel/execution/controller/failure_patterns.py`:
   - `FailurePatternService` class:
     - `analyze_failures(store, window_days=30)` → list[FailurePattern]
     - `check_risk(action_request, store)` → FailureRisk
     - `record_recovery(pattern_id, recovery_strategy, success)` → None
     - `suggest_alternative(pattern)` → AlternativeStrategy

2. Create `src/hermit/kernel/execution/controller/failure_models.py`:
   - `FailurePattern`, `FailureRisk`, `AlternativeStrategy`, `RecoveryRecord`

3. Integrate into ExecutionContractService — check risk after template matching
4. Emit EvidenceSignal on new failure pattern (>= 3 occurrences)

5. Write tests in `tests/unit/kernel/test_failure_patterns.py` (>= 7 tests)

## Constraints

- Pattern analysis is READ-ONLY
- Fingerprinting is deterministic (no LLM)
- Suggestions are ADVISORY only
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/execution/controller/failure_patterns.py` exists
- [ ] `src/hermit/kernel/execution/controller/failure_models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_failure_patterns.py -q` passes with >= 7 tests

## Context

- ExecutionContractService: `src/hermit/kernel/execution/controller/execution_contracts.py`
- ContractTemplateLearner: `src/hermit/kernel/execution/controller/template_learner.py`
- EvidenceSignal: `src/hermit/kernel/signals/models.py`
