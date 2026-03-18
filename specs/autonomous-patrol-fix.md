---
id: autonomous-patrol-fix
title: "Autonomous patrol-to-fix loop with governed remediation"
priority: high
trust_zone: low
---

## Goal

Extend the patrol engine to not just detect issues but autonomously create governed fix tasks. When patrol detects a lint error, test failure, or TODO, it creates a task with the fix goal, runs it under governed execution with full receipts and proofs, and reports the result. This closes the observe → fix → verify loop.

## Steps

1. Create `src/hermit/plugins/builtin/hooks/patrol/remediation.py`:
   - `RemediationEngine` class:
     - `plan_remediation(signal: EvidenceSignal)` → `RemediationPlan`
       - Maps signal source_kind to fix strategy:
         - `patrol:lint` → "Run ruff fix on affected files"
         - `patrol:test` → "Analyze test failure and fix the root cause"
         - `patrol:todo_scan` → "Implement the TODO item"
       - Generates goal prompt with context (file paths, error messages from signal metadata)
     - `execute_remediation(plan, runner)` → task_id
       - Creates a governed task via runner.enqueue_ingress()
       - Tags task with source_ref="patrol/remediation"
       - Sets policy_profile to "autonomous" for lint fixes, "default" for code changes
     - `should_remediate(signal)` → bool
       - Checks remediation policy: only auto-fix signals with risk_level <= "medium"
       - Respects cooldown (don't re-fix the same file within 1 hour)
       - Max 3 active remediation tasks at once

2. Create `src/hermit/plugins/builtin/hooks/patrol/remediation_models.py`:
   - `RemediationPlan`: signal_ref, strategy, goal_prompt, policy_profile, priority, affected_paths
   - `RemediationPolicy`: auto_fix_risk_threshold, cooldown_seconds, max_concurrent

3. Integrate into patrol engine:
   - After `_emit_signals()` in the patrol run, pass each new signal to `RemediationEngine.should_remediate()`
   - If yes, call `execute_remediation()` in a background thread
   - Add `remediation_enabled` config flag (default: false, opt-in)

4. Add remediation status tool:
   - `patrol_remediation_status` tool: lists active/completed remediation tasks with success/failure counts

5. Write tests in `tests/unit/plugins/hooks/test_patrol_remediation.py`:
   - Test plan_remediation generates correct strategy for each signal type
   - Test should_remediate respects risk threshold
   - Test should_remediate respects cooldown
   - Test should_remediate respects max_concurrent limit
   - Test execute_remediation creates task with correct tags
   - Test lint fix gets autonomous policy, code fix gets default policy

## Constraints

- Remediation MUST be opt-in (disabled by default)
- Remediation tasks MUST go through governed execution (receipts, proofs)
- Do NOT auto-fix signals with risk_level > "medium"
- Use `write_file` for ALL file writes
- Do NOT modify existing patrol check implementations

## Acceptance Criteria

- [ ] `src/hermit/plugins/builtin/hooks/patrol/remediation.py` exists
- [ ] `src/hermit/plugins/builtin/hooks/patrol/remediation_models.py` exists
- [ ] `uv run pytest tests/unit/plugins/hooks/test_patrol_remediation.py -q` passes with >= 6 tests
- [ ] Remediation plans map signal types to correct fix strategies

## Context

- Patrol engine: `src/hermit/plugins/builtin/hooks/patrol/engine.py`
- Patrol checks: `src/hermit/plugins/builtin/hooks/patrol/checks.py`
- EvidenceSignal model: `src/hermit/kernel/signals/models.py`
- Trigger engine (similar pattern): `src/hermit/plugins/builtin/hooks/trigger/engine.py`
