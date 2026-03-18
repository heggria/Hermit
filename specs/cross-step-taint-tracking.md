---
id: cross-step-taint-tracking
title: "Cross-step data taint tracking for sensitive information flow control"
priority: high
trust_zone: low
---

## Goal

Implement cross-step taint tracking in the kernel: mark data originating from sensitive sources (credentials, private files, API responses containing PII), propagate taint labels through the execution graph as data flows between tool calls, and block unauthorized outflows where tainted data reaches external endpoints. No agent framework has this — it positions Hermit as the first governed agent with information flow control.

## Steps

1. Create `src/hermit/kernel/policy/taint/tracker.py`:
   - `TaintTracker` class:
     - `mark_source(artifact_id, taint_labels, source_reason)` → TaintRecord
     - `propagate(input_artifacts, output_artifacts, step_attempt_id)` → list[TaintRecord]
     - `check_outflow(artifact_id, target_scope)` → TaintVerdict
     - `get_taint_chain(artifact_id)` → list[TaintHop]

2. Create `src/hermit/kernel/policy/taint/models.py`:
   - `TaintRecord`: taint_id, artifact_id, labels, source_artifact_id, source_reason, propagated_from, step_attempt_id, created_at
   - `TaintVerdict`: verdict (allow/warn/block), taint_labels, propagation_depth, reason
   - `TaintHop`: artifact_id, step_attempt_id, tool_name, hop_depth

3. Create `src/hermit/kernel/policy/taint/rules.py`:
   - Default rules: "credential" → block network_write/publication; "pii" → block network_write; "internal_only" → block publication
   - `evaluate(taint_labels, action_class)` → TaintVerdict

4. Integrate into ToolExecutor pipeline:
   - After tool invocation, call `propagate()` with input/output artifacts
   - Before grant issuance, call `check_outflow()` for network/publication actions
   - Auto-mark sources: files matching `*.env`, `*credential*`, `*secret*` patterns

5. Write tests in `tests/unit/kernel/test_taint_tracking.py`:
   - Test mark_source creates taint record
   - Test propagation from tainted input to output
   - Test transitive propagation (A→B→C all tainted)
   - Test check_outflow blocks tainted data to network_write
   - Test check_outflow allows untainted data
   - Test taint chain traces full propagation path
   - Test auto-marking for sensitive file patterns
   - Test credential taint blocks publication

## Constraints

- Taint tracking MUST NOT add latency to read-only operations
- Use existing memory_records table with memory_kind="taint" — no new tables
- Taint propagation must be deterministic (no LLM calls)
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/policy/taint/tracker.py` exists
- [ ] `src/hermit/kernel/policy/taint/models.py` exists
- [ ] `src/hermit/kernel/policy/taint/rules.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_taint_tracking.py -q` passes with >= 8 tests

## Context

- ToolExecutor pipeline: `src/hermit/kernel/execution/executor/executor.py`
- Policy guards: `src/hermit/kernel/policy/guards/rules.py`
- Memory records: `memory_records` table in KernelStore
