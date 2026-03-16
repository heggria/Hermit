# Builtin Plugin Guidance

## Conventions

- Every plugin must have a `plugin.toml` manifest with `[plugin]` and `[entry]` sections
- Tool specs must declare `action_class`, `risk_hint`, and `requires_receipt`
- Readonly tools: `requires_receipt=False`; mutating tools: `risk_hint` + `requires_receipt=True`
- Hook events: use `HookEvent` enum (e.g., `DISPATCH_RESULT`, not old `SCHEDULE_RESULT`)
- Adapter classes implement `AdapterProtocol` with `start()` and `stop()`
- MCP tool naming: `mcp__<server>__<tool>` (double underscore separator)
- Use `FileGuard.acquire(path, cross_process=True)` for atomic file operations
- Skills in `skills/<skill-name>/SKILL.md` with `name` and `description` frontmatter
- Use `src/hermit/plugin/` (current), not `src/hermit/plugins/` (legacy)
