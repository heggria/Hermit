---
id: demo-governed-hello
title: "Add governed iteration marker file"
priority: normal
trust_zone: low
---

## Objective

Add a small marker file that proves the governed self-evolution pipeline works end-to-end.

## Steps

1. Use `write_file` to create `src/hermit/GOVERNED_ITERATION.md` with this content:

```markdown
# Governed Iteration

This file was created by Hermit's governed self-evolution pipeline.

Every file mutation in this iteration was:
- Authorized by the policy engine (autonomous profile)
- Granted a capability with scoped workspace lease
- Executed with a durable receipt
- Recorded in the append-only ledger
- Verifiable via hash-chained proof bundle

Pipeline: spec → parse → branch → execute → proof-export → PR
```

2. Run `uv run pytest tests/unit/runtime/test_tools.py -q` to confirm nothing is broken.

## Constraints

- Use `write_file` for ALL file writes.
- Do not modify any existing source code.

## Acceptance Criteria

- [ ] `src/hermit/GOVERNED_ITERATION.md` exists with the specified content
- [ ] Tests pass
