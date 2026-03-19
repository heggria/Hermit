# Zone 1: CLI Surface Layer — Unit Test Report

## Summary

Brought `src/hermit/surfaces/cli/` from 10-59% coverage to 94-100% across all 9 modules.
Total of **232 tests** across **9 test files**, all passing.

## Coverage Results

| Module | Stmts | Coverage | Missing |
|--------|-------|----------|---------|
| `_helpers.py` | 121 | **97%** | Lines 25-26 (import guard), 185-186 (edge path) |
| `_preflight.py` | 154 | **99%** | Branch-only partial misses |
| `_commands_core.py` | 163 | **94%** | Lines 224, 256-278 (build_runner internals) |
| `_commands_memory.py` | 142 | **99%** | Line 361 (single edge case) |
| `_commands_plugin.py` | 73 | **100%** | None |
| `_commands_schedule.py` | 98 | **98%** | Line 14 (import) |
| `_commands_task.py` | 204 | **96%** | Lines 440, 455, 466 (edge branches) |
| `_serve.py` | 209 | **96%** | Lines 124-125, 172, 179-182 (signal handlers, main-thread only) |
| `main.py` | 67 | **97%** | Line 110 (`__main__` guard) |

## Test Files Created

| File | Tests | Target Module |
|------|-------|---------------|
| `tests/unit/surfaces/test_helpers.py` | 40 | `_helpers.py` |
| `tests/unit/surfaces/test_preflight.py` | 42 | `_preflight.py` |
| `tests/unit/surfaces/test_commands_core.py` | 20 | `_commands_core.py` |
| `tests/unit/surfaces/test_commands_memory.py` | 22 | `_commands_memory.py` (new) |
| `tests/unit/surfaces/test_commands_plugin.py` | 11 | `_commands_plugin.py` (new) |
| `tests/unit/surfaces/test_commands_schedule.py` | 25 | `_commands_schedule.py` (new) |
| `tests/unit/surfaces/test_commands_task.py` | 26 | `_commands_task.py` (new) |
| `tests/unit/surfaces/test_serve_commands.py` | 31 | `_serve.py` helpers + commands |
| `tests/unit/surfaces/test_serve_loop.py` | 5 | `_serve.py` loop paths |
| `tests/unit/surfaces/test_main.py` | 10 | `main.py` (new) |

## Issues Encountered

1. **Circular imports**: Importing `_preflight` directly triggered circular import via `main.py` -> `_serve.py` -> `_preflight.py`. Fixed by importing `hermit.surfaces.cli.main` first.

2. **Lazy imports inside functions**: Several modules use lazy imports (`croniter`, `parse_manifest`, `SteeringProtocol`, `build_runner`). Patching `module._name` fails because the name isn't bound at module scope. Solutions:
   - `patch.dict("sys.modules", {"croniter": mock})` for third-party lazy imports
   - `patch("hermit.runtime.capability.loader.loader.parse_manifest")` at the source module
   - `patch("hermit.kernel.signals.steering.SteeringProtocol")` at the defining module

3. **typer.Exit vs SystemExit**: Typer raises `click.exceptions.Exit` (aliased as `typer.Exit`), not `SystemExit`. Tests must use `pytest.raises(typer.Exit)`.

4. **Signal handlers require main thread**: `asyncio.get_running_loop().add_signal_handler()` only works on the main thread. Tests for SIGHUP/SIGTERM paths use `sys.platform = "win32"` to skip handler registration, then directly manipulate the `asyncio.Event` objects to simulate signals. Lines 124-125 and 179-182 remain uncovered (6 lines, main-thread-only signal registration/cleanup).

5. **get_settings caching**: The `@lru_cache` on `get_settings` means patches must target the call site (`hermit.surfaces.cli._commands_core.get_settings`) rather than the source module, or tests will see stale cached values.

6. **plugin_info directory check**: The `plugin info` command checks `candidate.is_dir()` on the filesystem. Tests must create actual directories in `tmp_path` for the check to pass.

## Decisions: Mock vs Test Directly

**Mocked (patch at call site):**
- `get_settings` — always mocked to avoid reading real `~/.hermit` config
- `ensure_workspace` — filesystem side effect, not relevant to command logic
- `PluginManager` — heavyweight initialization with real plugin discovery
- `SessionManager` — depends on real session directory structure
- `configure_logging` — global state mutation
- `build_runner` — assembles full runtime (too heavyweight for unit tests)
- `asyncio.run` — controls the serve loop lifecycle
- `caffeinate` — spawns real subprocess

**Tested directly (real code executes):**
- All CLI command functions via `typer.testing.CliRunner`
- PID file helpers (`_write_pid`, `_read_pid`, `_remove_pid`) with real `tmp_path` files
- `_ensure_single_serve_instance` with real PID files and mocked `os.kill`
- `_configure_unbuffered_stdio` with mock streams
- `_serve_with_signals` async function with real asyncio event loop
- `_notify_reload` with mocked PluginManager
- `hermit_env_path` and `_load_hermit_env` with real `tmp_path` .env files
- `_current_locale` and translation helpers
- All data formatting/rendering functions (memory payloads, preflight items, etc.)

## Lines Intentionally Not Covered

| Lines | Reason |
|-------|--------|
| `_serve.py:124-125, 179-182` | Signal handler add/remove requires main thread; cannot be tested in pytest |
| `_serve.py:172` | Unreachable fallback (all three wait conditions are already checked) |
| `main.py:110` | `if __name__ == "__main__"` boilerplate |
