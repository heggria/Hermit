from __future__ import annotations

from pathlib import Path
from typing import Any

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.execution_helpers import (
    load_witness_payload as _load_witness_payload,
)
from hermit.kernel.execution.executor.witness import WitnessCapture
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.models.models import ActionRequest


class WitnessHandler:
    """Thin adapter that delegates witness operations to WitnessCapture."""

    def __init__(
        self,
        *,
        store: KernelStore,
        artifact_store: ArtifactStore,
        witness: WitnessCapture,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self._witness = witness

    def capture_state_witness(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
        *,
        store_artifact: Any,
    ) -> str:
        return self._witness.capture(action_request, attempt_ctx, store_artifact=store_artifact)

    def state_witness_payload(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> dict[str, Any]:
        return self._witness.payload(action_request, attempt_ctx)

    def path_witness(self, path: str, *, workspace_root: Path) -> dict[str, Any]:
        return self._witness.path_witness(path, workspace_root=workspace_root)

    def git_witness(self, workspace_root: Path) -> dict[str, Any]:
        return self._witness.git_witness(workspace_root)

    def validate_state_witness(
        self,
        witness_ref: str,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> bool:
        return self._witness.validate(witness_ref, action_request, attempt_ctx)

    def load_witness_payload(self, witness_ref: str | None) -> dict[str, Any]:
        return _load_witness_payload(self.store, self.artifact_store, witness_ref)
