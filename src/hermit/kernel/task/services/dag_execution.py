"""DAGExecutionService: encapsulates DAG step activation and failure propagation.

Extracted from TaskController.finalize_result so the DAG progression logic
lives in one focused module and can be tested independently of the full
controller lifecycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hermit.kernel.execution.coordination.data_flow import StepDataFlowService
from hermit.kernel.ledger.journal.store import KernelStore

if TYPE_CHECKING:
    pass

# Statuses that represent a successful step completion and should unlock
# downstream DAG nodes.
_SUCCESS_STATUSES: frozenset[str] = frozenset({"succeeded", "completed", "skipped"})


class DAGExecutionService:
    """Handles DAG step activation and failure propagation after a step finishes.

    Responsibilities
    ----------------
    - After a step succeeds/is skipped: activate waiting downstream steps and
      auto-inject their input bindings (data-flow wiring).
    - After a step fails: retry if max_attempts allows, otherwise cascade the
      failure to all downstream dependents via ``propagate_step_failure``.
    - Determine the new aggregate task status (running / completed / failed).

    This service is intentionally *stateless* beyond the injected ``KernelStore``.
    The caller (``TaskController.finalize_result``) remains responsible for the
    CAS guard, steering application, and focus refresh — concerns orthogonal to
    DAG graph progression.
    """

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def advance(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        status: str,
    ) -> None:
        """Progress the DAG after ``step_id`` reaches ``status``.

        Parameters
        ----------
        task_id:          Parent task identifier.
        step_id:          The step that just finished.
        step_attempt_id:  The attempt that was finalised (used only for retry
                          logic; the store resolves the current attempt count).
        status:           Terminal status of the step attempt
                          (``"succeeded"``, ``"completed"``, ``"skipped"``,
                          ``"failed"``).
        """
        if status in _SUCCESS_STATUSES:
            self._handle_success(task_id=task_id, step_id=step_id)
        elif status == "failed":
            # If this step has `verifies` edges and it failed, it means
            # verification failed — reopen the verified steps.
            step = self._store.get_step(step_id)
            if step is not None and step.verifies:
                for verified_step_id in step.verifies:
                    self.reopen_verified_step(
                        task_id=task_id,
                        verifier_step_id=step_id,
                        verified_step_id=verified_step_id,
                    )
            else:
                self._handle_failure(task_id=task_id, step_id=step_id)
        # Other terminal statuses (e.g. "cancelled") intentionally do nothing
        # to the DAG graph — upstream callers set task status directly.

    def compute_task_status(
        self,
        *,
        task_id: str,
        step_status: str,
    ) -> str:
        """Return the aggregate task status after the current step finishes.

        Parameters
        ----------
        task_id:      Parent task identifier.
        step_status:  Terminal status of the step that just finished.

        Returns
        -------
        The new task-level status string to persist.
        """
        if self._store.has_non_terminal_steps(task_id):
            return "running"
        # All steps are terminal — derive task status from the last step result.
        return "completed" if step_status in _SUCCESS_STATUSES else step_status

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_success(self, *, task_id: str, step_id: str) -> None:
        """Activate waiting dependents, inject bindings, and emit super-step checkpoints."""
        self._maybe_emit_super_step_checkpoint(task_id=task_id, completed_step_id=step_id)
        activated_step_ids = self._store.activate_waiting_dependents(task_id, step_id)

        # Fix 2: auto-inject input_bindings for newly activated steps.
        # Fix 3: load key_to_step_id from DB via node_key so symbolic bindings
        #        resolve even when the in-memory mapping is not available
        #        (e.g. across process restarts or in a separate worker).
        if not activated_step_ids:
            return

        # Evaluate conditional predicates on newly activated steps.
        # Steps whose predicates evaluate to False are skipped automatically.
        activated_step_ids = self._evaluate_conditional_steps(task_id, activated_step_ids)
        if not activated_step_ids:
            return

        # Verification gate: check activated steps with verification_required.
        # If upstream receipts have reconciliation issues, block instead of activating.
        gate_blocked_ids: list[str] = []
        for activated_step_id in activated_step_ids:
            activated_step = self._store.get_step(activated_step_id)
            if (
                activated_step is not None
                and activated_step.verification_required
                and self._check_verification_gate_blocked(task_id, activated_step)
            ):
                gate_blocked_ids.append(activated_step_id)

        data_flow = StepDataFlowService(self._store)
        key_to_step_id = self._store.get_key_to_step_id(task_id)

        for activated_step_id in activated_step_ids:
            if activated_step_id in gate_blocked_ids:
                continue
            activated_attempts = self._store.list_step_attempts(
                step_id=activated_step_id, status="ready", limit=1
            )
            if not activated_attempts:
                continue
            resolved = data_flow.resolve_inputs(
                task_id, activated_step_id, key_to_step_id=key_to_step_id
            )
            if resolved:
                data_flow.inject_resolved_inputs(activated_attempts[0].step_attempt_id, resolved)

    def _evaluate_conditional_steps(self, task_id: str, activated_step_ids: list[str]) -> list[str]:
        """Evaluate predicates on newly activated steps.

        Steps whose predicate evaluates to False are automatically skipped.
        Returns the list of step_ids that were kept (predicate True or absent).
        """
        from hermit.kernel.task.services.dag_builder import StepDAGBuilder

        kept: list[str] = []
        for step_id in activated_step_ids:
            attempts = self._store.list_step_attempts(step_id=step_id, status="ready", limit=1)
            if not attempts:
                kept.append(step_id)
                continue
            ctx = attempts[0].context or {}
            meta = ctx.get("ingress_metadata", {})
            node_meta = meta.get("dag_node_metadata", {})
            predicate = node_meta.get("predicate")
            if not predicate:
                kept.append(step_id)
                continue

            # Gather upstream outputs
            step = self._store.get_step(step_id)
            if step is None:
                kept.append(step_id)
                continue
            upstream_outputs: dict[str, Any] = {}
            key_to_step_id = self._store.get_key_to_step_id(task_id)
            step_id_to_key = {v: k for k, v in key_to_step_id.items()}
            for dep_id in step.depends_on:
                dep_step = self._store.get_step(dep_id)
                dep_key = step_id_to_key.get(dep_id, dep_id)
                if dep_step and dep_step.output_ref:
                    upstream_outputs[dep_key] = dep_step.output_ref
                else:
                    upstream_outputs[dep_key] = dep_step.status if dep_step else None

            result = StepDAGBuilder.evaluate_predicate(predicate, upstream_outputs)
            if not result:
                self._store.skip_step(task_id, step_id, reason=f"predicate_false: {predicate}")
            else:
                kept.append(step_id)
        return kept

    def _check_verification_gate_blocked(
        self,
        task_id: str,
        step: Any,
    ) -> bool:
        """Check if a verification-required step should be blocked.

        Examines receipts of all upstream dependencies. If any receipt has
        reconciliation_required=True, the step is blocked and a
        verification.gate_blocked event is emitted.

        Returns True if the step was blocked.
        """
        for dep_step_id in step.depends_on:
            receipts = self._store.list_receipts_for_step(step_id=dep_step_id, limit=100)
            for receipt in receipts:
                if receipt.reconciliation_required:
                    # Block the step: revert to waiting with a specific status
                    self._store.update_step(
                        step.step_id,
                        status="verification_blocked",
                        finished_at=None,
                    )
                    # Also block the step attempts
                    ready_attempts = self._store.list_step_attempts(
                        step_id=step.step_id, status="ready", limit=10
                    )
                    for attempt in ready_attempts:
                        self._store.update_step_attempt(
                            attempt.step_attempt_id,
                            status="verification_blocked",
                            waiting_reason="upstream_reconciliation_required",
                        )
                    self._store.append_event(
                        event_type="verification.gate_blocked",
                        entity_type="step",
                        entity_id=step.step_id,
                        task_id=task_id,
                        step_id=step.step_id,
                        actor="kernel",
                        payload={
                            "blocked_step_id": step.step_id,
                            "blocking_receipt_id": receipt.receipt_id,
                            "blocking_step_id": dep_step_id,
                            "reason": "upstream_reconciliation_required",
                        },
                    )
                    return True
        return False

    def reopen_verified_step(
        self,
        *,
        task_id: str,
        verifier_step_id: str,
        verified_step_id: str,
    ) -> str | None:
        """Reopen a previously completed step after its verifier invalidates it.

        Creates a new StepAttempt for the verified step and emits a
        verification.step_invalidated event. Does not modify the existing
        completed attempt.

        Returns the new step_attempt_id, or None if the step was not found.
        """
        step = self._store.get_step(verified_step_id)
        if step is None:
            return None

        new_attempt = self._store.retry_step(task_id, verified_step_id, queue_priority=0)

        self._store.append_event(
            event_type="verification.step_invalidated",
            entity_type="step",
            entity_id=verified_step_id,
            task_id=task_id,
            step_id=verified_step_id,
            actor="kernel",
            payload={
                "invalidated_step_id": verified_step_id,
                "verifier_step_id": verifier_step_id,
                "new_step_attempt_id": new_attempt.step_attempt_id,
            },
        )
        return new_attempt.step_attempt_id

    def _handle_failure(self, *, task_id: str, step_id: str) -> None:
        """Retry the step or cascade failure to downstream dependents.

        Fix 1: max_attempts retry — use retry_step() for atomic attempt
        increment instead of raw _get_conn() calls.
        """
        step = self._store.get_step(step_id)
        if step is not None and step.attempt < step.max_attempts:
            self._store.retry_step(task_id, step_id)
        else:
            self._store.propagate_step_failure(task_id, step_id)

    def _maybe_emit_super_step_checkpoint(self, *, task_id: str, completed_step_id: str) -> None:
        """Emit a ``checkpoint.super_step`` event when all peers at the same
        topological level have finished successfully.

        Super-steps are derived from the DAG topology: steps at the same
        depth (number of edges from a root) form a group.  When all steps
        in a group reach a terminal success status, the checkpoint event is
        emitted so that on recovery the entire group can be skipped.
        """
        completed_step = self._store.get_step(completed_step_id)
        if completed_step is None:
            return

        # Build super-step groups from the stored DAG structure.
        all_steps = self._store.list_steps(task_id=task_id)
        if not all_steps:
            return

        # Compute depth of each step by traversing depends_on.
        step_by_id = {s.step_id: s for s in all_steps}
        depth: dict[str, int] = {}
        for step in all_steps:
            self._compute_depth(step.step_id, step_by_id, depth)

        completed_depth = depth.get(completed_step_id, 0)

        # Find all peers at the same depth.
        peers = [sid for sid, d in depth.items() if d == completed_depth]

        # Check if all peers are in a terminal success status.
        for peer_id in peers:
            peer = step_by_id.get(peer_id)
            if peer is None or peer.status not in _SUCCESS_STATUSES:
                return

        # All peers done — emit checkpoint.
        self._store.append_event(
            event_type="checkpoint.super_step",
            entity_type="task",
            entity_id=task_id,
            task_id=task_id,
            actor="kernel",
            payload={
                "super_step_depth": completed_depth,
                "step_ids": peers,
                "completed_by": completed_step_id,
            },
        )

    @staticmethod
    def _compute_depth(
        step_id: str,
        step_by_id: dict[str, Any],
        depth: dict[str, int],
    ) -> int:
        """Recursively compute the topological depth of a step."""
        if step_id in depth:
            return depth[step_id]
        step = step_by_id.get(step_id)
        if step is None or not step.depends_on:
            depth[step_id] = 0
            return 0
        d = (
            max(
                DAGExecutionService._compute_depth(dep, step_by_id, depth)
                for dep in step.depends_on
            )
            + 1
        )
        depth[step_id] = d
        return d
