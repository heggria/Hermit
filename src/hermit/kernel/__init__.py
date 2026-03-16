from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermit.kernel.approval_copy import ApprovalCopyService
    from hermit.kernel.approvals import ApprovalService
    from hermit.kernel.artifacts import ArtifactStore
    from hermit.kernel.context import CompiledProviderInput, TaskExecutionContext
    from hermit.kernel.controller import TaskController
    from hermit.kernel.conversation_projection import ConversationProjectionService
    from hermit.kernel.executor import ToolExecutionResult, ToolExecutor
    from hermit.kernel.knowledge import BeliefService, MemoryRecordService
    from hermit.kernel.planning import PlanningService
    from hermit.kernel.policy import PolicyDecision, PolicyEngine
    from hermit.kernel.projections import ProjectionService
    from hermit.kernel.proofs import ProofService
    from hermit.kernel.receipts import ReceiptService
    from hermit.kernel.rollbacks import RollbackService
    from hermit.kernel.store import KernelStore
    from hermit.kernel.supervision import SupervisionService

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
    "PlanningService",
    "ConversationProjectionService",
    "SupervisionService",
    "TaskController",
    "CompiledProviderInput",
    "TaskExecutionContext",
    "ToolExecutionResult",
    "ToolExecutor",
]

_EXPORTS = {
    "ApprovalCopyService": ("hermit.kernel.approval_copy", "ApprovalCopyService"),
    "ApprovalService": ("hermit.kernel.approvals", "ApprovalService"),
    "ArtifactStore": ("hermit.kernel.artifacts", "ArtifactStore"),
    "CompiledProviderInput": ("hermit.kernel.context", "CompiledProviderInput"),
    "TaskExecutionContext": ("hermit.kernel.context", "TaskExecutionContext"),
    "ConversationProjectionService": (
        "hermit.kernel.conversation_projection",
        "ConversationProjectionService",
    ),
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
    "PlanningService": ("hermit.kernel.planning", "PlanningService"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
