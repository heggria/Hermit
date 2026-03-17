from __future__ import annotations


class KernelError(RuntimeError):
    """Base for all kernel-layer errors."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ContractError(KernelError):
    """Raised when an execution contract constraint is violated."""


class SnapshotError(KernelError):
    """Raised for runtime snapshot validation failures."""


class RollbackError(KernelError):
    """Raised when a rollback operation cannot proceed."""


class ReconciliationError(KernelError):
    """Raised when post-execution reconciliation fails."""
