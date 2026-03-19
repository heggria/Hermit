from __future__ import annotations

from typing import Any

from hermit.kernel.ledger.journal.store import KernelStore


class StepDataFlowService:
    """Resolve cross-step artifact bindings for DAG data flow."""

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    def resolve_inputs(
        self,
        task_id: str,
        step_id: str,
        key_to_step_id: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Resolve input_bindings for a step into actual artifact references.

        Each binding maps a local_name to "step_key.output_ref".
        Returns local_name → artifact_ref mapping.
        """
        step = self._store.get_step(step_id)
        if step is None or not step.input_bindings:
            return {}

        resolved: dict[str, str] = {}
        for local_name, binding in step.input_bindings.items():
            parts = binding.split(".", 1)
            if len(parts) != 2:
                continue
            source_key, output_field = parts

            source_step_id = source_key
            if key_to_step_id and source_key in key_to_step_id:
                source_step_id = key_to_step_id[source_key]

            source_step = self._store.get_step(source_step_id)
            if source_step is None:
                continue

            if output_field == "output_ref" and source_step.output_ref:
                resolved[local_name] = source_step.output_ref

        return resolved

    def inject_resolved_inputs(
        self,
        step_attempt_id: str,
        resolved: dict[str, str],
    ) -> None:
        """Inject resolved input bindings into the step attempt context."""
        if not resolved:
            return
        attempt = self._store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        context: dict[str, Any] = dict(attempt.context or {})
        context["resolved_inputs"] = resolved
        self._store.update_step_attempt(step_attempt_id, context=context)
