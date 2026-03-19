from __future__ import annotations

from pathlib import Path
from typing import Any

from hermit.infra.system.i18n import _t
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.authority.grants import CapabilityGrantService
from hermit.kernel.authority.workspaces import WorkspaceLeaseService
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.controller.contracts import contract_for
from hermit.kernel.execution.suspension.git_worktree import GitWorktreeInspector
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import PolicyDecision
from hermit.kernel.policy.models.models import ActionRequest
from hermit.kernel.policy.permits.authorization_plans import AuthorizationPlanService
from hermit.runtime.capability.registry.tools import ToolRegistry


class AuthorizationHandler:
    """Authorization helpers extracted from ToolExecutor.

    Handles authorization reasoning, result summaries, rollback planning,
    artifact storage, workspace leases, and capability constraints.
    """

    def __init__(
        self,
        *,
        store: KernelStore,
        artifact_store: ArtifactStore,
        capability_service: CapabilityGrantService,
        workspace_lease_service: WorkspaceLeaseService,
        authorization_plans: AuthorizationPlanService,
        registry: ToolRegistry,
        policy_engine: object,
        git_worktree: GitWorktreeInspector | None = None,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.capability_service = capability_service
        self.workspace_lease_service = workspace_lease_service
        self.authorization_plans = authorization_plans
        self.registry = registry
        self.policy_engine = policy_engine
        self.git_worktree = git_worktree or GitWorktreeInspector()

    def authorization_reason(
        self,
        *,
        policy: PolicyDecision,
        approval_mode: str,
    ) -> str:
        if approval_mode == "mutable_workspace":
            return _t(
                "kernel.executor.authorization.policy_allowed",
                default="Allowed after mutable workspace approval.",
            )
        if approval_mode == "once":
            return _t("kernel.executor.authorization.once")
        return policy.reason or _t("kernel.executor.authorization.policy_allowed")

    def successful_result_summary(
        self,
        *,
        tool_name: str,
        approval_mode: str,
    ) -> str:
        if approval_mode == "mutable_workspace":
            return _t(
                "kernel.executor.result.success",
                default="{tool_name} completed under mutable workspace lease.",
                tool_name=tool_name,
            )
        if approval_mode == "once":
            return _t("kernel.executor.result.once", tool_name=tool_name)
        return _t("kernel.executor.result.success", tool_name=tool_name)

    def prepare_rollback_plan(
        self,
        *,
        action_type: str,
        tool_name: str,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
    ) -> dict[str, Any]:
        contract = contract_for(action_type)
        strategy = contract.rollback_strategy
        artifact_refs: list[str] = []
        supported = False
        if action_type in {"write_local", "patch_file"}:
            raw_path = str(tool_input.get("path", "") or "").strip()
            if raw_path:
                target = Path(raw_path)
                if not target.is_absolute():
                    target = Path(attempt_ctx.workspace_root or ".") / raw_path
                payload = {
                    "tool_name": tool_name,
                    "path": str(target),
                    "existed": target.exists(),
                    "content": target.read_text(encoding="utf-8") if target.exists() else "",
                }
                artifact_refs.append(
                    self.store_inline_json_artifact(
                        task_id=attempt_ctx.task_id,
                        step_id=attempt_ctx.step_id,
                        kind="rollback.prestate",
                        payload=payload,
                        metadata={"action_type": action_type, "strategy": strategy},
                    )
                )
                supported = True
        elif action_type == "vcs_mutation":
            repo = Path(attempt_ctx.workspace_root or ".")
            prestate = self.git_worktree.snapshot(repo).to_prestate()
            if prestate is not None:
                artifact_refs.append(
                    self.store_inline_json_artifact(
                        task_id=attempt_ctx.task_id,
                        step_id=attempt_ctx.step_id,
                        kind="rollback.prestate",
                        payload=prestate,
                        metadata={"action_type": action_type, "strategy": strategy},
                    )
                )
                supported = not bool(prestate.get("dirty"))
            else:
                supported = False
        elif action_type == "memory_write":
            strategy = "supersede_or_invalidate"
            supported = True
        return {"supported": supported, "strategy": strategy, "artifact_refs": artifact_refs}

    def store_inline_json_artifact(
        self,
        *,
        task_id: str,
        step_id: str,
        kind: str,
        payload: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=task_id,
            step_id=step_id,
            kind=kind,
            uri=uri,
            content_hash=content_hash,
            producer="tool_executor",
            retention_class="audit",
            trust_tier="observed",
            metadata=metadata,
        )
        return artifact.artifact_id

    def lease_root_path(
        self,
        action_request: ActionRequest,
        *,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        target_paths = [
            str(path) for path in action_request.derived.get("target_paths", []) if path
        ]
        if target_paths:
            try:
                return str(Path(target_paths[0]).expanduser().resolve().parent)
            except OSError:
                return str(Path(target_paths[0]).expanduser())
        workspace_root = str(attempt_ctx.workspace_root or "").strip()
        return workspace_root

    def ensure_workspace_lease(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        action_request: ActionRequest,
        approval_mode: str,
    ) -> str | None:
        lease_root = self.lease_root_path(action_request, attempt_ctx=attempt_ctx)
        if not lease_root:
            return None
        existing = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        if existing is not None and existing.workspace_lease_id:
            lease = self.workspace_lease_service.validate_active(existing.workspace_lease_id)
            return lease.lease_id
        lease_mode = "mutable" if approval_mode == "mutable_workspace" else "scoped"
        lease = self.workspace_lease_service.acquire(
            task_id=attempt_ctx.task_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            workspace_id=f"{attempt_ctx.task_id}:{attempt_ctx.step_id}",
            root_path=lease_root,
            holder_principal_id=attempt_ctx.actor_principal_id,
            mode=lease_mode,
            resource_scope=list(action_request.resource_scopes),
        )
        return lease.lease_id

    def capability_constraints(
        self,
        action_request: ActionRequest,
        *,
        workspace_lease_id: str | None,
    ) -> dict[str, Any]:
        constraints = dict(action_request.derived.get("constraints", {}))
        constraints.update(
            {
                "target_paths": list(action_request.derived.get("target_paths", [])),
                "network_hosts": list(action_request.derived.get("network_hosts", [])),
                "command_preview": action_request.derived.get("command_preview"),
            }
        )
        if workspace_lease_id:
            lease = self.store.get_workspace_lease(workspace_lease_id)
            if lease is not None:
                constraints["lease_root_path"] = lease.root_path
        return {key: value for key, value in constraints.items() if value not in (None, [], {}, "")}
