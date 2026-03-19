from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Graceful degradation when pytest-benchmark is not installed.
# The `benchmark` fixture is provided by pytest-benchmark; when that package
# is absent we skip the test instead of erroring with "fixture not found".
# ---------------------------------------------------------------------------
try:
    import pytest_benchmark  # noqa: F401

    _BENCHMARK_AVAILABLE = True
except ImportError:
    _BENCHMARK_AVAILABLE = False


if not _BENCHMARK_AVAILABLE:

    @pytest.fixture
    def benchmark(request: pytest.FixtureRequest):
        """Stub fixture: skip benchmark tests when pytest-benchmark is not installed."""
        pytest.skip(
            "pytest-benchmark not installed — run `uv sync --group dev` to enable benchmarks"
        )


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
