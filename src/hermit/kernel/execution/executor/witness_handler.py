from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
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
        if not witness_ref:
            return {}
        artifact = self.store.get_artifact(witness_ref)
        if artifact is None:
            return {}
        try:
            payload: Any = json.loads(self.artifact_store.read_text(artifact.uri))
        except (OSError, json.JSONDecodeError):
            return {}
        return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
