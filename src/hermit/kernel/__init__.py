from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.context.memory.knowledge import BeliefService, MemoryRecordService
    from hermit.kernel.context.models.context import CompiledProviderInput, TaskExecutionContext
    from hermit.kernel.execution.controller.supervision import SupervisionService
    from hermit.kernel.execution.executor.executor import ToolExecutionResult, ToolExecutor
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.policy import PolicyDecision, PolicyEngine
    from hermit.kernel.policy.approvals.approval_copy import ApprovalCopyService
    from hermit.kernel.policy.approvals.approvals import ApprovalService
    from hermit.kernel.task.projections.conversation import ConversationProjectionService
    from hermit.kernel.task.projections.projections import ProjectionService
    from hermit.kernel.task.services.controller import TaskController
    from hermit.kernel.task.services.planning import PlanningService
    from hermit.kernel.verification.proofs.proofs import ProofService
    from hermit.kernel.verification.receipts.receipts import ReceiptService
    from hermit.kernel.verification.rollbacks.rollbacks import RollbackService

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
    "ApprovalCopyService": ("hermit.kernel.policy.approvals.approval_copy", "ApprovalCopyService"),
    "ApprovalService": ("hermit.kernel.policy.approvals.approvals", "ApprovalService"),
    "ArtifactStore": ("hermit.kernel.artifacts.models.artifacts", "ArtifactStore"),
    "CompiledProviderInput": ("hermit.kernel.context.models.context", "CompiledProviderInput"),
    "TaskExecutionContext": ("hermit.kernel.context.models.context", "TaskExecutionContext"),
    "ConversationProjectionService": (
        "hermit.kernel.task.projections.conversation",
        "ConversationProjectionService",
    ),
    "TaskController": ("hermit.kernel.task.services.controller", "TaskController"),
    "ToolExecutionResult": ("hermit.kernel.execution.executor.executor", "ToolExecutionResult"),
    "ToolExecutor": ("hermit.kernel.execution.executor.executor", "ToolExecutor"),
    "PolicyDecision": ("hermit.kernel.policy", "PolicyDecision"),
    "PolicyEngine": ("hermit.kernel.policy", "PolicyEngine"),
    "ProofService": ("hermit.kernel.verification.proofs.proofs", "ProofService"),
    "ProjectionService": ("hermit.kernel.task.projections.projections", "ProjectionService"),
    "RollbackService": ("hermit.kernel.verification.rollbacks.rollbacks", "RollbackService"),
    "ReceiptService": ("hermit.kernel.verification.receipts.receipts", "ReceiptService"),
    "KernelStore": ("hermit.kernel.ledger.journal.store", "KernelStore"),
    "SupervisionService": (
        "hermit.kernel.execution.controller.supervision",
        "SupervisionService",
    ),
    "BeliefService": ("hermit.kernel.context.memory.knowledge", "BeliefService"),
    "MemoryRecordService": ("hermit.kernel.context.memory.knowledge", "MemoryRecordService"),
    "PlanningService": ("hermit.kernel.task.services.planning", "PlanningService"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
