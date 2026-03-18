---
id: cross-step-policy-patterns
title: "Invariant-style cross-step policy pattern matching"
priority: high
trust_zone: low
---

## Goal

Add cross-step policy pattern detection: rules that evaluate across the full execution trace, not just individual steps. For example, "if step N reads credentials and step N+k writes to an external API, require re-approval." Inspired by Invariant Guardrails' approach to detecting multi-step attack chains.

## Steps

1. Create `src/hermit/kernel/policy/patterns/detector.py`:
   - `PatternDetector` class:
     - `check_trace(task_id, current_step, store)` → list[PatternMatch]
     - `register_pattern(pattern)` → pattern_id
     - `load_default_patterns()` → loads built-in patterns

2. Create `src/hermit/kernel/policy/patterns/models.py`:
   - `TracePattern`: pattern_id, name, description, trigger_sequence, action, severity, cooldown_steps
   - `StepMatcher`: action_class, resource_scope_pattern, artifact_label_match, within_n_steps
   - `PatternMatch`: pattern_id, matched_steps, severity, recommended_action, explanation

3. Create `src/hermit/kernel/policy/patterns/builtin_patterns.py`:
   - `credential_exfiltration`: read_local on `*.env|*credential*` → network_write within 10 steps → BLOCK
   - `sensitive_data_publication`: read_local on `*private*` → publication → require_approval
   - `mass_file_mutation`: write_local > 5 times in 3 steps → require_approval
   - `vcs_after_untested_write`: write_local → vcs_mutation without execute_command(test) → WARN
   - `escalation_spiral`: 3+ scope_escalation in 5 steps → BLOCK

4. Integrate into ToolExecutor — before grant issuance, check trace patterns
5. Write tests in `tests/unit/kernel/test_cross_step_patterns.py` (>= 7 tests)

## Constraints

- Pattern evaluation MUST be deterministic (no LLM calls)
- O(steps * patterns), not exponential
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/policy/patterns/detector.py` exists
- [ ] `src/hermit/kernel/policy/patterns/models.py` exists
- [ ] `src/hermit/kernel/policy/patterns/builtin_patterns.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_cross_step_patterns.py -q` passes with >= 7 tests

## Context

- ToolExecutor: `src/hermit/kernel/execution/executor/executor.py`
- StepAttemptRecord: has action_class, input_refs, output_refs
- Policy guards: `src/hermit/kernel/policy/guards/rules.py`
