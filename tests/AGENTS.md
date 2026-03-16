# Test Guidance

## Conventions

- `pytest` + `pytest-asyncio` (asyncio_mode = "auto") + `pytest-xdist` for parallel execution
- Test functions: `test_<feature>_<scenario>`; Test classes: `TestXyzFeature`
- Use `tmp_path` fixture for temp files/databases; never hardcoded paths
- Mocking: `monkeypatch` for functions, `MagicMock` for objects, `SimpleNamespace` for lightweight fakes
- Track events in lists during mocks for assertion
- CLI testing via `typer.testing.CliRunner`
- Run single test: `uv run pytest tests/test_file.py::test_name -q`
- Import from `hermit` package (resolved via `src/`), not from old `hermit/` top-level path
