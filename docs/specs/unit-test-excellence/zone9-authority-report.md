# Zone 9 Unit Test Report: kernel/authority/ + apps/companion/

## Summary

Zone 9 covers `src/hermit/kernel/authority/` (identity, workspaces, grants) and
`src/hermit/apps/companion/` (appbundle, control, menubar). This report documents
the test coverage improvements achieved and remaining gaps.

**Total new tests written:** 151 tests across 4 new test files
**All tests passing:** 191 (including 40 pre-existing tests)

## Coverage Results

### kernel/authority/

| Module | Before | After | Notes |
|--------|--------|-------|-------|
| `identity/service.py` | 0% (14 stmts) | **100%** | All branches covered |
| `identity/__init__.py` | 45% | **100%** | Lazy `__getattr__` covered |
| `workspaces/service.py` | 56% (14 missed) | **100%** | All branches covered |
| `workspaces/__init__.py` | 82% | **100%** | Lazy `__getattr__` covered |
| `grants/service.py` | 93% | 93%+ | Already high; tested in other suites |
| `grants/__init__.py` | 82% | **100%** | Lazy `__getattr__` covered |

### apps/companion/

| Module | Before | After | Notes |
|--------|--------|-------|-------|
| `appbundle.py` | 15% (143 missed) | **83%** | Icon subprocess pipeline excluded |
| `control.py` | 20% (294 missed) | **93%** | Near-complete |
| `menubar.py` | 58% | **74%** | `main()` with rumps excluded |

## New Test Files

### `tests/unit/kernel/authority/test_identity_service.py`
- 19 tests covering `PrincipalService.resolve()` and `resolve_name()`
- Exercises all principal type resolution logic: system, user, channel-based overrides
- Tests edge cases: None/empty/whitespace actor, kernel/user exclusions
- Tests `identity/__init__.py` lazy import

### `tests/unit/kernel/authority/test_workspace_service.py`
- 21 tests covering `WorkspaceLeaseService` and `capture_execution_environment`
- Covers acquire (readonly/mutable modes), conflict detection, auto-expiry
- Covers TTL handling (default vs provided), environment artifact creation
- Tests release, validate_active (not found, wrong status, expired, valid, no-expiry)
- Tests `workspaces/__init__.py` lazy import

### `tests/unit/apps/test_companion_control.py`
- 77 tests covering nearly all functions in `control.py`
- Covers: hermit_base_dir, log helpers, format_exception_message, config management
- Covers: PID helpers, process table parsing, env assignment matching
- Covers: command_prefix (4 branches), path/URL helpers
- Covers: TOML profile management (set_default, update_profile_setting, format_toml_value)
- Covers: service lifecycle (stop, reload, start with success/failure paths)
- Covers: switch_profile and update_profile_bool_and_restart (all 3 branches each)
- Covers: open_path/open_in_textedit/open_url (darwin + non-darwin)
- Covers: load_runtime_settings, load_profile_runtime_settings
- Also covers `grants/__init__.py` lazy import and `menubar._parse_args`/`main`

### `tests/unit/apps/test_companion_appbundle.py`
- 34 tests covering appbundle.py
- Covers: _base_dir_slug, app_name, bundle_id, app_path
- Covers: _launcher_command, _bundle_python_target, _icon_source
- Covers: _install_bundle_icon (null paths only)
- Covers: install_app_bundle (structure, plist, profile, project root, icon)
- Covers: open_app_bundle, _run_osascript
- Covers: login_item_enabled/enable/disable
- Covers: _parse_args, main (non-darwin, darwin with --open, --enable-login-item)

## Intentionally Uncovered

### `appbundle.py` lines 90-197 (`_install_bundle_icon` internals)
The icon installation function calls `sips` and `iconutil` via subprocess in a
multi-step pipeline (rasterize SVG, pad, resize to 10 icon sizes, convert to icns).
This is tightly coupled to macOS system utilities and temp directory I/O. The
function's entry/exit points and error paths are tested; the subprocess pipeline
internals are excluded as they would require elaborate subprocess mocking with
minimal value. Coverage: 3 of 4 early-return paths tested.

### `control.py` lines 298-309 (`_iter_process_table` live path)
The live `ps` subprocess call path is excluded. The parsing logic is fully tested
via the `process_table` parameter override.

### `menubar.py` lines 516-525 (rumps app creation in `main()`)
The `HermitMenuApp` class is marked `pragma: no cover` as it requires the `rumps`
macOS dependency and a running event loop. The non-rumps and non-darwin paths
in `main()` are tested.

## Verification

```bash
uv run pytest tests/unit/kernel/authority/ tests/unit/apps/ -q
# 191 passed

uv run pytest tests/unit/kernel/authority/ tests/unit/apps/ \
  --cov=src/hermit/kernel/authority/identity \
  --cov=src/hermit/kernel/authority/workspaces \
  --cov=src/hermit/kernel/authority/grants \
  --cov=src/hermit/apps/companion \
  --cov-report=term-missing -q
# 191 passed, 82.78% total coverage (exceeds 80% threshold)
```
