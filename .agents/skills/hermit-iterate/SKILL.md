---
name: hermit-iterate
description: Execute spec-driven iteration — manually from a spec file, or automated via the meta-loop. Covers both manual workflow (spec → branch → Hermit → proof → PR) and automated self-iteration (hermit_self_iterate → 10-phase autonomous pipeline).
---

# Hermit Iteration

Two modes: **manual** (spec file → PR) and **automated** (meta-loop self-iteration).

## When to use

- The user provides a spec file path and asks to iterate → **Manual mode**
- The user says "iterate on specs/xxx.md" → **Manual mode**
- The user wants autonomous, goal-driven iteration → **Automated mode**
- The user says "自迭代" or "self-iterate" → **Automated mode**
- Continuous improvement tasks (code quality, error handling, test coverage) → **Automated mode**

---

## Mode 1: Automated Self-Iteration (Meta-Loop)

Submit a goal and let Hermit autonomously research, plan, implement, review, benchmark, and learn.

### 1. Submit iteration goal

```
hermit_self_iterate(
  iterations=[
    {
      "goal": "Improve error handling in the store module",
      "priority": "high",
      "research_hints": ["focus on exception propagation patterns"]
    }
  ]
)
```

Multiple goals can be submitted at once — they execute independently.

### 2. Monitor progress

Use `hermit_spec_queue` to check status:

```
hermit_spec_queue(action="list", filters={"limit": 20})
```

### 3. 10-Phase lifecycle

Each iteration progresses automatically:

```
PENDING → RESEARCHING → GENERATING_SPEC → SPEC_APPROVAL → DECOMPOSING
→ IMPLEMENTING → REVIEWING → BENCHMARKING → LEARNING → COMPLETED
```

| Phase | What happens |
|-------|-------------|
| RESEARCHING | Analyzes codebase + git history, injects prior lessons as hints |
| GENERATING_SPEC | Generates deterministic spec (title, constraints, acceptance criteria, file plan) |
| SPEC_APPROVAL | v0.3: auto-approved |
| DECOMPOSING | Splits spec into DAG steps (code → review → check) |
| IMPLEMENTING | Creates git worktree, submits DAG task, waits for completion |
| REVIEWING | Code review on planned files |
| BENCHMARKING | Runs `make check`, collects test/coverage/lint metrics |
| LEARNING | Extracts lessons; auto-spawns follow-up specs for mistakes/regressions |

### 4. Three feedback loops

1. **Manual trigger**: Submit goal → 10-phase pipeline → COMPLETED
2. **Lessons feedback**: Learning phase finds mistakes → auto-creates follow-up spec → next iteration fixes it
3. **Signal-driven**: PatrolEngine/benchmark/review signals → auto-creates spec → autonomous fix

### 5. Queue management

```
# Add to queue
hermit_spec_queue(action="add", entries=[
  {"goal": "Add retry logic to HTTP client", "priority": "normal"}
])

# Reprioritize
hermit_spec_queue(action="reprioritize", spec_id="iter-xxx", priority="high")

# View all
hermit_spec_queue(action="list", filters={"status": "pending", "limit": 50})
```

### Decision rules (automated mode)

- **Iteration completes** → Report summary (findings, review, benchmark results, lessons)
- **Iteration fails** → Auto-retries up to `max_retries` (default 2), then marks FAILED
- **Lessons spawn follow-ups** → Inform user of new specs created
- **All code changes** are in isolated git worktrees — main branch stays clean until merge

---

## Mode 2: Manual Spec-Driven Iteration

## Workflow

### 1. Parse spec

Read `specs/<name>.md` and extract:
- `id` — used as branch suffix
- `title` — used in commit/PR title
- `trust_zone` — controls automation level
- `Acceptance Criteria` — commands to validate

### 2. Create work branch

```bash
git checkout -b iterate/<spec-id>
```

### 3. Execute Hermit

```bash
scripts/hermit-iterate.sh specs/<name>.md
```

Capture the task ID from the output.

### 4. Check task status

```bash
uv run python -m hermit.surfaces.cli.main task show <task-id>
```

Confirm the task completed successfully.

### 5. Export proof

```bash
mkdir -p .hermit-proof
uv run python -m hermit.surfaces.cli.main task proof-export <task-id> --output .hermit-proof/<spec-id>.json
```

### 6. Run acceptance tests

Execute each command from the spec's Acceptance Criteria section. Record pass/fail for each.

### 7. Commit and create PR

```bash
git add -A
git commit -m "iterate(<spec-id>): <title>"
git push -u origin iterate/<spec-id>
gh pr create --title "iterate(<spec-id>): <title>" --body "<body>"
```

PR body must include:
- Summary of changes
- Acceptance criteria results (pass/fail for each)
- Proof summary (task ID, status, receipt count)

### 8. Report results

Output the PR URL and a summary of acceptance results to the user.

## Decision rules

- **Task failed** → Do not create a PR. Report the failure reason to the user.
- **Acceptance criteria partially failed** → Create a draft PR (`gh pr create --draft`). Mark failed items in the PR body.
- **All acceptance criteria passed** → Create a regular PR.
- **trust_zone=high** → Ask for user confirmation before each major step (branch creation, Hermit execution, PR creation).
- **trust_zone=normal** → Ask for user confirmation before PR creation only.
- **trust_zone=low** → Fully automated, no confirmation needed.

## Minimum completion bar

Do not report success until all are true:

- Hermit task has completed (or failure has been reported)
- All acceptance criteria commands have been executed and results recorded
- PR has been created, or a clear explanation given for why not
- User can see the full acceptance results and proof summary
