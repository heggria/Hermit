from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "ApprovalService",
    "ApprovalCopyService",
    "ArtifactStore",
    "KernelStore",
    "PolicyDecision",
    "PolicyEngine",
    "ProofService",
    "ProjectionService",
    "RollbackService",
    "ReceiptService",
    "BeliefService",
    "MemoryRecordService",
    "SupervisionService",
    "TaskController",
    "TaskExecutionContext",
    "ToolExecutionResult",
    "ToolExecutor",
]

_EXPORTS = {
    "ApprovalCopyService": ("hermit.kernel.approval_copy", "ApprovalCopyService"),
    "ApprovalService": ("hermit.kernel.approvals", "ApprovalService"),
    "ArtifactStore": ("hermit.kernel.artifacts", "ArtifactStore"),
    "TaskExecutionContext": ("hermit.kernel.context", "TaskExecutionContext"),
    "TaskController": ("hermit.kernel.controller", "TaskController"),
    "ToolExecutionResult": ("hermit.kernel.executor", "ToolExecutionResult"),
    "ToolExecutor": ("hermit.kernel.executor", "ToolExecutor"),
    "PolicyDecision": ("hermit.kernel.policy", "PolicyDecision"),
    "PolicyEngine": ("hermit.kernel.policy", "PolicyEngine"),
    "ProofService": ("hermit.kernel.proofs", "ProofService"),
    "ProjectionService": ("hermit.kernel.projections", "ProjectionService"),
    "RollbackService": ("hermit.kernel.rollbacks", "RollbackService"),
    "ReceiptService": ("hermit.kernel.receipts", "ReceiptService"),
    "KernelStore": ("hermit.kernel.store", "KernelStore"),
    "SupervisionService": ("hermit.kernel.supervision", "SupervisionService"),
    "BeliefService": ("hermit.kernel.knowledge", "BeliefService"),
    "MemoryRecordService": ("hermit.kernel.knowledge", "MemoryRecordService"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
