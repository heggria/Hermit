"""DAGExecutionService: encapsulates DAG step activation and failure propagation.

Extracted from TaskController.finalize_result so the DAG progression logic
lives in one focused module and can be tested independently of the full
controller lifecycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
        """Activate waiting dependents and inject their input bindings."""
        activated_step_ids = self._store.activate_waiting_dependents(task_id, step_id)

        # Fix 2: auto-inject input_bindings for newly activated steps.
        # Fix 3: load key_to_step_id from DB via node_key so symbolic bindings
        #        resolve even when the in-memory mapping is not available
        #        (e.g. across process restarts or in a separate worker).
        if not activated_step_ids:
            return

        data_flow = StepDataFlowService(self._store)
        key_to_step_id = self._store.get_key_to_step_id(task_id)

        for activated_step_id in activated_step_ids:
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
