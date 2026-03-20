"""Tests for StagedVerifier."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hermit.kernel.execution.self_modify.models import (
    VerificationGate,
    VerificationResult,
)
from hermit.kernel.execution.self_modify.verifier import StagedVerifier


@pytest.fixture()
def verifier(tmp_path: Path) -> StagedVerifier:
    return StagedVerifier(
        repo_root=tmp_path,
        timeouts={
            VerificationGate.QUICK: 5,
            VerificationGate.CHANGED: 10,
            VerificationGate.FULL: 15,
        },
    )


@pytest.fixture()
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "worktree"
    wt.mkdir()
    return wt


class TestGateCommand:
    def test_quick_gate_uses_make_test_quick(
        self, verifier: StagedVerifier, worktree: Path
    ) -> None:
        cmd = verifier._gate_command(VerificationGate.QUICK, worktree)
        assert cmd == ["make", "test-quick"]

    def test_full_gate_uses_make_check(self, verifier: StagedVerifier, worktree: Path) -> None:
        cmd = verifier._gate_command(VerificationGate.FULL, worktree)
        assert cmd == ["make", "check"]

    def test_changed_gate_falls_back_to_quick(
        self, verifier: StagedVerifier, worktree: Path
    ) -> None:
        # No git repo in worktree, so diff will fail → fallback
        cmd = verifier._changed_tests_command(worktree)
        assert cmd == ["make", "test-quick"]


class TestVerify:
    @pytest.mark.asyncio
    async def test_all_gates_pass(self, verifier: StagedVerifier, worktree: Path) -> None:
        success = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        with patch.object(verifier, "_run_subprocess", return_value=success):
            result = await verifier.verify(worktree)
        assert result.passed
        assert len(result.results) == 3
        assert all(r.passed for r in result.results)

    @pytest.mark.asyncio
    async def test_fails_fast_on_gate1(self, verifier: StagedVerifier, worktree: Path) -> None:
        failure = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="FAILED")
        with patch.object(verifier, "_run_subprocess", return_value=failure):
            result = await verifier.verify(worktree)
        assert not result.passed
        assert len(result.results) == 1  # Only gate 1 ran
        assert result.failed_gate == VerificationGate.QUICK

    @pytest.mark.asyncio
    async def test_fails_fast_on_gate2(self, verifier: StagedVerifier, worktree: Path) -> None:
        success = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        failure = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="test fail")

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return success if call_count == 1 else failure

        with patch.object(verifier, "_run_subprocess", side_effect=side_effect):
            result = await verifier.verify(worktree)
        assert not result.passed
        assert len(result.results) == 2
        assert result.failed_gate == VerificationGate.CHANGED

    @pytest.mark.asyncio
    async def test_timeout_returns_failure(self, verifier: StagedVerifier, worktree: Path) -> None:
        def timeout_side_effect(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="make", timeout=5)

        with patch.object(verifier, "_run_subprocess", side_effect=timeout_side_effect):
            result = await verifier.verify(worktree)
        assert not result.passed
        assert result.results[0].error is not None
        assert "Timeout" in (result.results[0].error or "")

    @pytest.mark.asyncio
    async def test_exception_returns_failure(
        self, verifier: StagedVerifier, worktree: Path
    ) -> None:
        with patch.object(verifier, "_run_subprocess", side_effect=OSError("boom")):
            result = await verifier.verify(worktree)
        assert not result.passed
        assert "boom" in (result.results[0].error or "")


class TestStagedVerificationResult:
    def test_duration_sums_all_gates(self) -> None:
        from hermit.kernel.execution.self_modify.models import StagedVerificationResult

        results = (
            VerificationResult(gate=VerificationGate.QUICK, passed=True, duration_seconds=2.0),
            VerificationResult(gate=VerificationGate.CHANGED, passed=True, duration_seconds=30.0),
            VerificationResult(gate=VerificationGate.FULL, passed=True, duration_seconds=120.0),
        )
        svr = StagedVerificationResult(results=results, passed=True)
        assert svr.duration_seconds == 152.0

    def test_failed_gate_returns_first_failure(self) -> None:
        from hermit.kernel.execution.self_modify.models import StagedVerificationResult

        results = (
            VerificationResult(gate=VerificationGate.QUICK, passed=True, duration_seconds=1.0),
            VerificationResult(gate=VerificationGate.CHANGED, passed=False, duration_seconds=5.0),
        )
        svr = StagedVerificationResult(results=results, passed=False)
        assert svr.failed_gate == VerificationGate.CHANGED

    def test_no_failed_gate_when_all_pass(self) -> None:
        from hermit.kernel.execution.self_modify.models import StagedVerificationResult

        results = (
            VerificationResult(gate=VerificationGate.QUICK, passed=True, duration_seconds=1.0),
        )
        svr = StagedVerificationResult(results=results, passed=True)
        assert svr.failed_gate is None
