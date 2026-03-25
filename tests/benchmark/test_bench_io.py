"""I/O and startup performance benchmarks."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.benchmark


class TestCLIStartupBenchmarks:
    """Benchmark CLI startup time."""

    def test_hermit_help_startup(self, benchmark):
        """Benchmark `hermit --help` startup time.

        Uses 3 rounds (reduced from 5) to keep wall-clock time reasonable
        since each round spawns a real subprocess.
        """

        def run_help():
            result = subprocess.run(
                [sys.executable, "-m", "hermit.surfaces.cli.main", "--help"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0

        benchmark.pedantic(run_help, iterations=1, rounds=3, warmup_rounds=1)
