from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Graceful degradation when the pytest-benchmark plugin is not active.
# The `benchmark` fixture is provided by the pytest-benchmark plugin.
# The plugin may be absent (not installed) or explicitly disabled via
# `-p no:benchmark` in addopts.  In either case we provide a stub fixture
# that skips the test instead of erroring with "fixture 'benchmark' not found".
#
# We use a pytest_configure hook so the stub is only registered when the
# real plugin is NOT active — this avoids shadowing the plugin's fixture.
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """Register a stub ``benchmark`` fixture when the plugin is not active."""
    if not config.pluginmanager.has_plugin("benchmark"):

        @pytest.fixture
        def benchmark(request: pytest.FixtureRequest):
            """Stub: skip when pytest-benchmark plugin is not active."""
            pytest.skip(
                "pytest-benchmark plugin not active — it may be disabled via "
                "'-p no:benchmark' in addopts, or not installed. "
                "Run benchmarks with: uv run pytest tests/benchmark/ -p benchmark --override-ini='addopts='"
            )

        # Inject the stub fixture into this module so pytest discovers it.
        globals()["benchmark"] = benchmark


@pytest.fixture
def tmp_hermit_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for Hermit state."""
    hermit_dir = tmp_path / ".hermit"
    hermit_dir.mkdir()
    return hermit_dir


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Provide a temporary SQLite database path."""
    return tmp_path / "hermit.db"


@pytest.fixture
def tmp_json_path(tmp_path: Path) -> Path:
    """Provide a temporary JSON file path."""
    return tmp_path / "test.json"
