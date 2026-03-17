---
name: hermit-iterate
description: Execute a spec-driven self-iteration workflow. Reads a spec file, runs Hermit to implement it, validates acceptance criteria, exports proof, and creates a PR. Use when the user provides a spec file and asks to iterate, or says "iterate on specs/xxx.md".
---

# Hermit Spec-Driven Iteration

Use this skill to execute a full iteration cycle from a spec file to a pull request.

## When to use

- The user provides a spec file path and asks to iterate
- The user says "iterate on specs/xxx.md"
- The user wants to run a spec-driven self-iteration workflow

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
