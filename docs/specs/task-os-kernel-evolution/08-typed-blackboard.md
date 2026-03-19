# Spec 08: Typed Blackboard Primitive

## Goal
Add a typed blackboard as a kernel primitive for structured inter-step communication within a task.

## Current Problem
- Steps communicate via step output artifacts — unstructured text/JSON
- No typed claims, evidence, patches, risks, conflicts, todos
- No ensemble→refinement pattern support
- Workers can't post structured findings for other steps to consume

## Deliverables
1. **BlackboardRecord** in records.py:
   - `entry_id`, `task_id`, `step_id`, `step_attempt_id`
   - `entry_type` StrEnum: `claim`, `evidence`, `patch`, `risk`, `conflict`, `todo`, `decision`
   - `content` dict — structured payload per type
   - `confidence` float (0.0-1.0) — for claims/evidence
   - `supersedes_entry_id` optional — for refinement
   - `status`: `active`, `superseded`, `resolved`
   - `created_at`
2. **BlackboardService**:
   - `post(task_id, step_id, entry_type, content, confidence)` → BlackboardEntry
   - `query(task_id, entry_type=None, status=None)` → list[BlackboardEntry]
   - `supersede(entry_id, new_entry)` — mark old as superseded, link new
   - `resolve(entry_id, resolution)` — mark as resolved
3. **Persist in KernelStore**: New `blackboard` table + events
4. **Context compiler integration**: BlackboardService entries injected into context for downstream steps
5. **Tests** — post/query/supersede/resolve, cross-step visibility

## Files to Modify
- `src/hermit/kernel/task/models/records.py` (BlackboardRecord)
- `src/hermit/kernel/artifacts/` (new blackboard service)
- `src/hermit/kernel/ledger/journal/store.py` (blackboard table)
- `src/hermit/kernel/context/compiler/compiler.py` (inject blackboard)
- `tests/` (new test files)

## Constraints
- Blackboard is task-scoped (not global)
- No inter-task blackboard sharing in v1
- Entry types are extensible via StrEnum
- Schema bump required
