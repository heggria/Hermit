---
id: episodic-memory-index
title: "Episode-scoped memory index tied to task execution receipts"
priority: normal
trust_zone: low
---

## Goal

Build an episodic memory system that indexes memories by the task execution episodes that produced them. Each memory links back to the task, step, and receipt, enabling receipt-backed memory retrieval.

## Steps

1. Create `src/hermit/kernel/context/memory/episodic.py`:
   - `EpisodicMemoryService` class:
     - `index_episode(task_id, store)` → EpisodeIndex
     - `query_by_episode(task_id)` → list[MemoryRecord]
     - `query_by_artifact(artifact_pattern)` → list[EpisodicResult]
     - `query_by_tool(tool_name)` → list[EpisodicResult]
     - `decay_stale_episodes(max_age_days=30)` → int

2. Create `src/hermit/kernel/context/memory/episodic_models.py`:
   - `EpisodeIndex`: task_id, episode_id, memory_ids, artifact_refs, tool_names, indexed_at
   - `EpisodicResult`: memory_record, episode_ref, task_summary, relevance_score, receipt_count
   - `EpisodeKnowledge`: claim_text, source_type, source_ref, confidence

3. Integrate with ContextCompiler — add episodic_context section
4. Auto-index on DISPATCH_RESULT hook for completed tasks

5. Write tests in `tests/unit/kernel/test_episodic_memory.py` (>= 6 tests)

## Constraints

- Use existing memory_records table with episode_ref in metadata
- Only index successful task completions
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/context/memory/episodic.py` exists
- [ ] `src/hermit/kernel/context/memory/episodic_models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_episodic_memory.py -q` passes with >= 6 tests

## Context

- MemoryGovernanceService: `src/hermit/kernel/context/memory/governance.py`
- ContextCompiler: `src/hermit/kernel/context/compiler/compiler.py`
- Memory records: `src/hermit/kernel/ledger/journal/store.py`
