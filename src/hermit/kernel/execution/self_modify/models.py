"""Data models for self-modification worktree infrastructure."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class VerificationGate(StrEnum):
    """Three-level verification gates for self-modification."""

    QUICK = "test-quick"  # ~10s — core smoke tests
    CHANGED = "test-changed"  # ~1-3min — affected tests only
    FULL = "check"  # ~5-10min — lint + typecheck + full test suite


@dataclass(frozen=True)
class VerificationResult:
    """Result of running a verification gate."""

    gate: VerificationGate
    passed: bool
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    error: str | None = None

    @property
    def failed(self) -> bool:
        return not self.passed


@dataclass(frozen=True)
class StagedVerificationResult:
    """Aggregate result of running all verification gates."""

    results: tuple[VerificationResult, ...] = ()
    passed: bool = False

    @property
    def failed_gate(self) -> VerificationGate | None:
        for r in self.results:
            if r.failed:
                return r.gate
        return None

    @property
    def duration_seconds(self) -> float:
        return sum(r.duration_seconds for r in self.results)


class SelfModPhase(StrEnum):
    """Lifecycle phases of a self-modification session."""

    CREATED = "created"
    MODIFYING = "modifying"
    VERIFYING = "verifying"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class SelfModSession:
    """Tracks a self-modification session lifecycle."""

    iteration_id: str
    worktree_path: str = ""
    branch_name: str = ""
    phase: SelfModPhase = SelfModPhase.CREATED
    verification: StagedVerificationResult | None = None
    commit_sha: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_phase(self, phase: SelfModPhase, **updates: Any) -> SelfModSession:
        """Return a new session with updated phase and optional field overrides."""
        fields = {
            "iteration_id": self.iteration_id,
            "worktree_path": self.worktree_path,
            "branch_name": self.branch_name,
            "phase": phase,
            "verification": self.verification,
            "commit_sha": self.commit_sha,
            "error": self.error,
            "metadata": self.metadata,
        }
        fields.update(updates)
        return SelfModSession(**fields)


class MergeConflictError(RuntimeError):
    """Raised when merging a self-modify branch encounters conflicts."""
