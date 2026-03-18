---
id: memory-lineage-graph
title: "Track which memories influenced which decisions through artifact lineage"
priority: normal
trust_zone: low
---

## Goal

Build a memory lineage graph that tracks causal relationships between memories and kernel decisions. When a memory is injected into context and the execution produces decisions/receipts, record the link. Enables "why did Hermit do X?" auditing.

## Steps

1. Create `src/hermit/kernel/context/memory/lineage.py`:
   - `MemoryLineageService` class:
     - `record_influence(context_pack_id, decision_ids, memory_ids)` → list[InfluenceLink]
     - `trace_decision(decision_id)` → DecisionLineage
     - `trace_memory(memory_id)` → MemoryImpact
     - `find_stale_influencers(min_decisions=5, failure_rate_threshold=0.5)` → list[StaleMemory]

2. Create `src/hermit/kernel/context/memory/lineage_models.py`:
   - `InfluenceLink`: link_id, context_pack_id, memory_id, decision_id, step_attempt_id, recorded_at
   - `DecisionLineage`: decision_id, influencing_memories, context_pack_hash, task_id
   - `MemoryImpact`: memory_id, influenced_decisions, success_rate, failure_rate, total_influences
   - `StaleMemory`: memory_id, memory_text, failure_rate, decision_count, suggested_action

3. Integrate into execution pipeline — record influence after each step
4. Add lineage inspection endpoints

5. Write tests in `tests/unit/kernel/test_memory_lineage.py` (>= 7 tests)

## Constraints

- Use memory_records with memory_kind="influence_link"
- Influence links are append-only
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/context/memory/lineage.py` exists
- [ ] `src/hermit/kernel/context/memory/lineage_models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_memory_lineage.py -q` passes with >= 7 tests

## Context

- ContextCompiler: `src/hermit/kernel/context/compiler/compiler.py`
- Decision model: `src/hermit/kernel/policy/approvals/decisions.py`
