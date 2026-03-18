---
id: memory-decay-governance
title: "Automated memory decay with governed deprecation policies"
priority: normal
trust_zone: low
---

## Goal

Implement governed memory decay: memories transition through freshness states (fresh → aging → stale → expired) based on configurable policies, with deprecation reports. Prevents knowledge rot where outdated memories silently degrade decision quality.

## Steps

1. Create `src/hermit/kernel/context/memory/decay.py`:
   - `MemoryDecayService` class:
     - `evaluate_freshness(memory_id, store)` → FreshnessAssessment
     - `run_decay_sweep(store)` → DecaySweepReport
     - `quarantine(memory_id, reason)` → bool
     - `revive(memory_id, new_evidence_refs)` → bool

2. Create `src/hermit/kernel/context/memory/decay_models.py`:
   - `FreshnessAssessment`: memory_id, freshness_state, age_days, ttl_days, pct_remaining, last_accessed_days_ago
   - `DecaySweepReport`: sweep_id, swept_at, total_evaluated, transitions, quarantine_candidates

3. Integrate with ContextCompiler — exclude expired/quarantined, warn on stale
4. Scheduled sweep every 24h via scheduler hook

5. Write tests in `tests/unit/kernel/test_memory_decay.py` (>= 7 tests)

## Constraints

- Soft-delete only — never remove records from store
- TTL policies from MemoryGovernanceService
- Do NOT decay retention_class="audit" memories
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/context/memory/decay.py` exists
- [ ] `src/hermit/kernel/context/memory/decay_models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_memory_decay.py -q` passes with >= 7 tests

## Context

- MemoryGovernanceService: `src/hermit/kernel/context/memory/governance.py`
- ContextCompiler: `src/hermit/kernel/context/compiler/compiler.py`
