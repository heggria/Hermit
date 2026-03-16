---
paths:
  - "tests/**"
---

# Test Conventions

- Use `pytest` with `pytest-asyncio` (asyncio_mode = "auto") and `pytest-xdist`
- Test functions: `test_<feature>_<scenario>` descriptive naming
- Test classes: `TestXyzFeature` for grouping related tests
- Use `tmp_path` fixture for temporary files/databases, never hardcoded paths
- Mocking: use `monkeypatch` for functions, `MagicMock` for object interactions
- Use `SimpleNamespace` for lightweight mock objects (FakeRunner, FakePM etc.)
- Track events in lists during mocks for assertions
- Clear caches before tests: `get_settings.cache_clear()`
- CLI testing via `typer.testing.CliRunner`
- Run single test: `uv run pytest tests/test_file.py::test_name -q`
- Do not import from `hermit/` (old path) — use `src/hermit/` package
