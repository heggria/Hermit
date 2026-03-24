from __future__ import annotations


class KernelError(RuntimeError):
    """Base for all kernel-layer errors.

    Attributes:
        code:    A short machine-readable error code (e.g. ``"SNAPSHOT_INVALID"``).
        message: Human-readable explanation forwarded to :class:`RuntimeError`.
    """

    # Subclasses may override this to supply a sensible default code so that
    # call sites do not have to hard-code the same magic string repeatedly.
    default_code: str = "KERNEL_ERROR"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code: str = code if code is not None else self.default_code


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
    """Raised for post-execution reconciliation failures."""

    default_code = "RECONCILIATION_FAILED"
