"""Dependency tracker for recursive rollback planning.

Traces receipt chains via output_refs / input_refs overlap to build a
dependency graph, then produces a leaf-first rollback plan.
"""

from __future__ import annotations

from collections import deque

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import ReceiptRecord
from hermit.kernel.verification.rollbacks.rollback_models import (
    DependentReceipt,
    RollbackPlan,
    RollbackPlanExecution,
)
from hermit.kernel.verification.rollbacks.rollbacks import RollbackService


class RollbackDependencyTracker:
    """Builds a dependency graph of receipts and produces a leaf-first rollback plan.

    Receipt A's ``output_refs`` overlapping with receipt B's ``input_refs``
    means B depends on A.  When rolling back A we must first roll back B
    (and anything that depends on B, recursively).
    """

    def __init__(self, store: KernelStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_plan(self, root_receipt_id: str) -> RollbackPlan:
        """Build a recursive rollback plan rooted at *root_receipt_id*.

        Uses BFS to discover all downstream dependents, detects cycles via
        a visited set, and returns an execution order that is leaf-first
        (reverse topological order).
        """
        root = self.store.get_receipt(root_receipt_id)
        if root is None:
            raise KeyError(f"Receipt not found: {root_receipt_id}")

        plan = RollbackPlan(root_receipt_id=root_receipt_id)

        # Index all receipts for the same task by their input_refs for fast lookup.
        task_receipts = self.store.list_receipts(task_id=root.task_id, limit=1000)
        input_index: dict[str, list[ReceiptRecord]] = {}
        for r in task_receipts:
            for ref in r.input_refs:
                input_index.setdefault(ref, []).append(r)

        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque()
        queue.append((root_receipt_id, 0))

        while queue:
            rid, depth = queue.popleft()
            if rid in visited:
                plan.cycle_detected = True
                continue
            visited.add(rid)

            receipt = self._get_receipt(rid, task_receipts)
            if receipt is None:
                continue

            manual = not receipt.rollback_supported
            node = DependentReceipt(
                receipt_id=rid,
                depth=depth,
                rollback_supported=receipt.rollback_supported,
                rollback_strategy=receipt.rollback_strategy,
                manual_review_required=manual,
            )

            # Find dependents: receipts whose input_refs overlap this receipt's output_refs.
            for out_ref in receipt.output_refs:
                for dep in input_index.get(out_ref, []):
                    if dep.receipt_id not in visited:
                        node.dependent_ids.append(dep.receipt_id)
                        queue.append((dep.receipt_id, depth + 1))

            plan.nodes[rid] = node
            if manual:
                plan.manual_review_ids.append(rid)

        # Build execution order: leaf-first (highest depth first, stable within depth).
        plan.execution_order = sorted(
            plan.nodes,
            key=lambda rid: (-plan.nodes[rid].depth, rid),
        )

        return plan

    def execute_plan(
        self,
        plan: RollbackPlan,
        rollback_service: RollbackService,
    ) -> RollbackPlanExecution:
        """Execute a rollback plan leaf-first.

        Receipts marked ``manual_review_required`` are skipped.  If a rollback
        fails, the receipt is recorded as failed and execution continues with
        the remaining receipts.
        """
        execution = RollbackPlanExecution(plan=plan)

        for rid in plan.execution_order:
            node = plan.nodes[rid]

            if node.manual_review_required:
                execution.skipped_ids.append(rid)
                execution.results[rid] = {
                    "status": "skipped",
                    "reason": "manual_review_required",
                }
                continue

            try:
                result = rollback_service.execute(rid)
                status = str(result.get("status", "unknown"))
                execution.results[rid] = result
                if status == "succeeded":
                    execution.succeeded_ids.append(rid)
                else:
                    execution.failed_ids.append(rid)
            except Exception as exc:
                execution.failed_ids.append(rid)
                execution.results[rid] = {"status": "failed", "error": str(exc)}

        if execution.failed_ids:
            execution.status = "partial"
        elif execution.skipped_ids and not execution.succeeded_ids:
            execution.status = "skipped"
        else:
            execution.status = "succeeded"

        return execution

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_receipt(
        receipt_id: str,
        receipts: list[ReceiptRecord],
    ) -> ReceiptRecord | None:
        for r in receipts:
            if r.receipt_id == receipt_id:
                return r
        return None
