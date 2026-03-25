---
name: hermit-create-team
description: Quickly create teams with full role assemblies via Hermit MCP tools. Supports templates and custom compositions.
---

# Create Team

Create teams with complete role assemblies in one call via Hermit MCP tools.

## When to use

- User wants to create a new team with multiple roles
- User describes a team composition (e.g. "3 executors + 1 planner")
- User asks to set up a development/research/quality team
- User says "create team", "build a team", "set up a team"

## When NOT to use

- Managing existing teams (status changes, archiving)
- Role definition CRUD (creating new role types)
- Task submission — use `hermit-delegate` instead

## Available builtin roles

| Role | Description |
|------|-------------|
| `planner` | Plans and decomposes tasks into steps |
| `executor` | Executes task steps |
| `verifier` | Verifies execution results |
| `benchmarker` | Runs benchmarks and quality gates |
| `researcher` | Researches context and prior art |
| `reconciler` | Reconciles authorized vs observed effects |
| `tester` | Writes and runs tests |
| `spec` | Generates specifications |
| `reviewer` | Reviews code and artifacts |

Use `hermit_list_roles` to check for custom roles beyond builtins.

## Team templates

### Development team (开发团队)
```
hermit_create_team(
  title="开发团队",
  roles={"planner": 1, "executor": 3, "reviewer": 1, "tester": 1}
)
```

### Research team (研究团队)
```
hermit_create_team(
  title="研究团队",
  roles={"researcher": 2, "planner": 1, "spec": 1}
)
```

### Quality team (质量团队)
```
hermit_create_team(
  title="质量团队",
  roles={"benchmarker": 2, "verifier": 1, "reviewer": 1}
)
```

### Full-stack team (全栈团队)
```
hermit_create_team(
  title="全栈团队",
  roles={"planner": 1, "executor": 4, "tester": 2, "reviewer": 1, "benchmarker": 1}
)
```

### Iteration team (迭代团队)
```
hermit_create_team(
  title="迭代团队",
  roles={"researcher": 1, "spec": 1, "executor": 2, "tester": 1, "benchmarker": 1, "reviewer": 1}
)
```

## Workflow

1. **List roles** (optional): `hermit_list_roles()` to see available roles
2. **Create team**: Choose a template or compose custom roles
3. **Verify**: Check the response for `team_id` and role summary

## MCP tools reference

### hermit_list_roles

List available role definitions.

```
hermit_list_roles(include_builtin=True)
```

Returns: `{roles: [{role_id, name, description, mcp_servers, skills, is_builtin}], count}`

### hermit_create_team

Create a team with role assembly.

```
hermit_create_team(
  title="Team name",           # required
  roles={"role": count, ...},  # optional, defaults to {"executor": 1}
  program_id=""                # optional, auto-resolves if empty
)
```

Returns: `{team_id, title, status, program_id, roles, total_workers, role_assembly}`

### hermit_list_teams

List existing teams.

```
hermit_list_teams(status="active", limit=50)
```

Returns: `{teams: [{team_id, title, status, roles, total_workers, created_at}], count}`

## Natural language mapping

| User says | Action |
|-----------|--------|
| "创建一个开发团队" | Use development team template |
| "搭建 5 人执行团队" | `roles={"executor": 5}` |
| "需要 3 个 executor + 2 个 tester" | `roles={"executor": 3, "tester": 2}` |
| "建一个小团队做调研" | Use research team template |
| "全栈团队，planner 带 executor 和 reviewer" | Use full-stack team template |
