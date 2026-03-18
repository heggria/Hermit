# Phase 3 Rollback Verification Spec

## Objective

Create a small, reversible file change that produces rollback-capable receipts via `write_file`.

## Steps

1. **Create a new file** `src/hermit/ROLLBACK_TEST.md` using the `write_file` tool with this content:

```markdown
# Rollback Test

This file was created by the Phase 3 rollback verification spec.
If rollback works correctly, this file will be deleted by `hermit task rollback`.
Created: 2026-03-17
```

2. **Run the existing tests** to confirm nothing is broken:

```bash
uv run pytest tests/unit/runtime/test_tools.py -q
```

## Constraints

- Use `write_file` for ALL file creation — never use bash to write files.
- Do not modify any existing source code.
- Only create the single test file above.

## Success Criteria

- The write produces a receipt with `rollback_supported: true`
- The existing tests still pass
