---
paths:
  - "src/hermit/plugins/builtin/**"
  - "src/hermit/runtime/capability/**"
---

# Plugin Conventions

- Every plugin must have a `plugin.toml` manifest with `[plugin]` and `[entry]` sections
- Tool specs must declare `action_class`, `risk_hint`, and `requires_receipt`
- Readonly tools: `requires_receipt=False`; mutating tools: must declare `risk_hint` and `requires_receipt=True`
- Plugin discovery paths: `src/hermit/plugins/builtin/` (builtin) and `~/.hermit/plugins/` (user)
- Plugin system: `src/hermit/plugins/` (plugin code), `src/hermit/runtime/capability/` (registry/loader)
- Hook events: use `HookEvent` enum values (e.g., `DISPATCH_RESULT`, not `SCHEDULE_RESULT`)
- Adapter classes implement `AdapterProtocol` with `start()` and `stop()`
- MCP tool naming: `mcp__<server>__<tool>` (double underscore separator)
- Use `FileGuard.acquire(path, cross_process=True)` for atomic file operations
- Skills go in `skills/<skill-name>/SKILL.md` with `name` and `description` frontmatter
