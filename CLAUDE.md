# CLAUDE.md

@AGENTS.md

## Claude Code Specific

- Use `uv` as the package manager (not pip or poetry)
- Run tests with `uv run pytest`
- Prefer `make check` for quick validation (lint + typecheck + test)
- When modifying existing files, read them first before suggesting changes
- Follow Ruff formatting — do not manually adjust style beyond what Ruff enforces
