from __future__ import annotations


class KernelError(RuntimeError):
    """Base for all kernel-layer errors.

    Subclasses should declare a class-level ``default_code`` so raise sites
    don't have to repeat the error-code string, reducing the risk of typos or
    inconsistent codes scattered across the codebase.
    """

    # Fallback used when no subclass overrides it and no explicit code is given.
    default_code: str = "KERNEL_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code if code is not None else self.default_code


class ContractError(KernelError):
    """Raised when an execution contract constraint is violated."""

    default_code = "CONTRACT_VIOLATION"


class SnapshotError(KernelError):
    """Raised for runtime snapshot validation failures."""

    default_code = "SNAPSHOT_INVALID"


class RollbackError(KernelError):
    """Raised when a rollback operation cannot proceed."""

    default_code = "ROLLBACK_FAILED"


class ReconciliationError(KernelError):
    """Raised when post-execution reconciliation fails."""

    default_code = "RECONCILIATION_FAILED"
