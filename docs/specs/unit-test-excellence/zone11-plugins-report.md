# Zone 11: Plugins Unit Test Report

## Summary

Added 100 new unit tests across 10 test files covering all targeted 0%-coverage plugin files and low-coverage plugin files. All tests pass.

## Files Covered

| Source File | Previous Coverage | Test File | Tests Added |
|---|---|---|---|
| `plugins/builtin/hooks/webhook/tools.py` (121 stmts) | 0% | `test_webhook_tools.py` | 28 |
| `plugins/builtin/mcp/mcp_loader/mcp.py` (67 stmts) | 0% | `test_mcp_loader.py` | 14 |
| `plugins/builtin/hooks/memory/services.py` (65 stmts) | 0% | `test_memory_services.py` | 7 |
| `plugins/builtin/mcp/github/mcp.py` (29 stmts) | 0% | `test_github_mcp.py` | 12 |
| `plugins/builtin/subagents/orchestrator/dag_orchestrator.py` (27 stmts) | 0% | `test_dag_orchestrator.py` | 7 |
| `plugins/builtin/hooks/webhook/models.py` (19 missed) | ~60% | `test_webhook_models.py` | 12 |
| `plugins/builtin/tools/computer_use/tools.py` (9 stmts) | 0% | `test_computer_use_register.py` | 5 |
| `plugins/builtin/tools/web_tools/tools.py` (8 stmts) | 0% | `test_web_tools_register.py` | 5 |
| `plugins/builtin/tools/grok/tools.py` (6 stmts) | 0% | `test_grok_register.py` | 4 |
| `plugins/builtin/subagents/orchestrator/subagents.py` (5 stmts) | 0% | `test_orchestrator_subagents.py` | 4 |

**Total: 98 tests across 10 files (100 passed including 2 pre-existing parametrizations)**

## Test Approach

### Webhook Tools (`test_webhook_tools.py` - 28 tests)
- Full CRUD cycle: `_handle_list`, `_handle_add`, `_handle_delete`, `_handle_update`
- File I/O via `tmp_path` fixture for isolation
- Edge cases: missing names, duplicate routes, overwrite flag, secret add/remove, feishu add/remove
- `register()` verification: confirms 4 tools registered with correct names

### MCP Loader (`test_mcp_loader.py` - 14 tests)
- `_load_mcp_json`: missing file, valid JSON, invalid JSON
- `_parse_server_entry`: stdio (with/without args, non-list args), http, missing transport, default descriptions, tool governance
- `_parse_tool_governance`: empty/null input, valid camelCase and snake_case keys, non-dict entry filtering
- `register()`: base_dir loading, cwd loading, invalid entry skipping, non-dict mcpServers

### Memory Services (`test_memory_services.py` - 7 tests)
- `reset_services()` clears singleton cache
- `_ensure_schemas()` idempotency (runs once), exception handling
- `get_services()` returns `MemoryServices` NamedTuple, caching behavior
- All memory module imports mocked to avoid heavy dependencies

### GitHub MCP (`test_github_mcp.py` - 12 tests)
- Tool governance constants: read tools are readonly/low-risk, mutation tools are high-risk
- `_build_github_spec()`: no context (env var fallbacks: GITHUB_PERSONAL_ACCESS_TOKEN, GITHUB_PAT, GITHUB_TOKEN), custom URL, context-based config, empty header filtering
- `register()` adds exactly one MCP server

### DAG Orchestrator (`test_dag_orchestrator.py` - 7 tests)
- `DAGPlan` frozen dataclass, default rationale
- `plan_from_nodes`: minimal node (defaults), full options (kind, join_strategy, input_bindings, max_attempts, metadata, depends_on)
- `materialize_and_dispatch`: calls builder with correct args
- `get_step_statuses`: returns found steps, skips missing

### Webhook Models (`test_webhook_models.py` - 12 tests)
- `WebhookRoute` and `WebhookConfig` defaults
- `_resolve_config_path` with/without base_dir, None settings
- `load_config`: missing file, corrupt JSON, valid config, settings overrides (host/port/control_secret), empty control_secret normalization, route defaults

### Registration Tests (computer_use, web_tools, grok, subagents - 18 tests total)
- Each `register()` function verified for correct tool/subagent count and names
- Tool metadata verified: readonly, risk_hint, action_class, requires_receipt
- Input schema validation: required fields, property presence
- Handler callability checks

## Mocking Strategy

- `tmp_path` for all file I/O (webhook configs, MCP configs)
- `monkeypatch` for `Path.cwd()` overrides
- `unittest.mock.patch` for memory service module imports
- `MagicMock` for `KernelStore` and `StepDAGBuilder`
- `SimpleNamespace` for lightweight settings objects
- `os.environ` patching via `patch.dict` for GitHub token tests

## Test Execution

```
100 passed in 2.46s
```
