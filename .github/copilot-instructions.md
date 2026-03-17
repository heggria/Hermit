# Copilot Instructions

This repository also maintains `AGENTS.md` (cross-tool standard) and `CLAUDE.md` at the project root. Refer to `AGENTS.md` for comprehensive project guidance including architecture, code quality standards, development environment, and contributing direction.

## Quick Reference

- **Language:** Python 3.13+, managed by `uv`
- **Linter/Formatter:** Ruff (line-length 100)
- **Tests:** `make test` (pytest + pytest-xdist)
- **Quick check:** `make check` (lint + typecheck + test)
- **Full verification:** `make verify`

## Key Conventions

- Follow the governed execution path: Task → Step → Approval → Execution → Receipt
- No direct model-to-tool execution — the kernel authorizes all actions
- Plugin system: `src/hermit/plugins/` (plugin code), `src/hermit/runtime/capability/` (registry/loader)
- Use `plugin.toml` manifests for plugin configuration
- Storage uses SQLite with atomic writes — use `JsonStore` and `atomic_write()` APIs
