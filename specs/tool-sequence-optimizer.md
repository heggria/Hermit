---
id: tool-sequence-optimizer
title: "Learn optimal tool call sequences from successful task executions"
priority: normal
trust_zone: low
---

## Goal

Build a tool sequence optimizer that learns from the kernel journal which tool call sequences lead to successful task completion. Distinct from per-action contract template learning — this operates at the task/plan level, learning multi-step strategies.

## Steps

1. Create `src/hermit/kernel/execution/controller/sequence_optimizer.py`:
   - `SequenceOptimizer` class:
     - `extract_sequence(task_id, store)` → ToolSequence
     - `store_successful_sequence(task_id, goal_tokens, sequence, store)` → sequence_id
     - `suggest_sequence(goal_text, store)` → SequenceSuggestion
     - `reinforce(sequence_id, success)` → None

2. Create `src/hermit/kernel/execution/controller/sequence_models.py`:
   - `ToolSequence`, `SequenceSuggestion`, `SequenceStep`

3. Integrate with ContextCompiler as planning guidance
4. Store sequences as memory_records with memory_kind="tool_sequence"

5. Write tests in `tests/unit/kernel/test_sequence_optimizer.py` (>= 7 tests)

## Constraints

- Suggestions are HINTS — never auto-execute
- Jaccard similarity on tokens (no LLM)
- Minimum 2 successful uses before suggesting
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/execution/controller/sequence_optimizer.py` exists
- [ ] `src/hermit/kernel/execution/controller/sequence_models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_sequence_optimizer.py -q` passes with >= 7 tests

## Context

- ContractTemplateLearner: `src/hermit/kernel/execution/controller/template_learner.py`
- ContextCompiler: `src/hermit/kernel/context/compiler/compiler.py`
