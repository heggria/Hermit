"""I/O and startup performance benchmarks."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.benchmark


class TestCLIStartupBenchmarks:
    """Benchmark CLI startup time."""

    def test_hermit_help_startup(self, benchmark):
        """Benchmark `hermit --help` startup time."""

        def run_help():
            result = subprocess.run(
                [sys.executable, "-m", "hermit.surfaces.cli.main", "--help"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0

        benchmark.pedantic(run_help, iterations=1, rounds=5, warmup_rounds=1)
