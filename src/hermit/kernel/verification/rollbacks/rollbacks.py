from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.authority.grants import CapabilityGrantService
from hermit.kernel.authority.workspaces import WorkspaceLeaseService
from hermit.kernel.execution.suspension.git_worktree import GitWorktreeInspector
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.approvals.decisions import DecisionService
from hermit.kernel.task.models.records import ReceiptRecord
from hermit.kernel.verification.receipts.receipts import ReceiptService


class RollbackService:
    def __init__(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore | None = None,
        git_worktree: GitWorktreeInspector | None = None,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store or ArtifactStore(store.db_path.parent / "artifacts")
        self.git_worktree = git_worktree or GitWorktreeInspector()
        self.decisions = DecisionService(store)
        self.capabilities = CapabilityGrantService(store)
        self.receipts = ReceiptService(store, self.artifact_store)
        self.workspace_leases = WorkspaceLeaseService(store, self.artifact_store)

    def execute(self, receipt_id: str) -> dict[str, Any]:
        receipt = self.store.get_receipt(receipt_id)
        if receipt is None:
            raise KeyError(f"Receipt not found: {receipt_id}")
        strategy = str(receipt.rollback_strategy or "").strip()
        if not receipt.rollback_supported or not strategy:
            return self._mark_unsupported(receipt, self._t("kernel.rollback.unsupported"))

        step = self.store.create_step(task_id=receipt.task_id, kind="rollback", status="running")
        attempt = self.store.create_step_attempt(
            task_id=receipt.task_id,
            step_id=step.step_id,
            status="running",
            context={"receipt_ref": receipt_id, "rollback_strategy": strategy},
        )
        rollback = self.store.create_rollback(
            task_id=receipt.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            receipt_ref=receipt_id,
            action_type=receipt.action_type,
            strategy=strategy,
            status="executing",
            artifact_refs=list(receipt.rollback_artifact_refs),
        )
        self.store.update_receipt_rollback_fields(
            receipt_id,
            rollback_status="executing",
            rollback_ref=rollback.rollback_id,
        )
        decision_id = self.decisions.record(
            task_id=receipt.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            decision_type="rollback_execution",
            verdict="allow",
            reason=f"Operator requested rollback of {receipt.receipt_id}.",
            evidence_refs=[
                ref for ref in [receipt.receipt_bundle_ref, *receipt.rollback_artifact_refs] if ref
            ],
            action_type="rollback",
            decided_by="operator",
        )
        holder_principal_id = "rollback_service"
        workspace_lease_id = self._acquire_workspace_lease(
            receipt,
            attempt.step_attempt_id,
            holder_principal_id=holder_principal_id,
        )
        capability_grant_id = self.capabilities.issue(
            task_id=receipt.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            decision_ref=decision_id,
            approval_ref=None,
            policy_ref=None,
            issued_to_principal_id=holder_principal_id,
            issued_by_principal_id="kernel",
            workspace_lease_ref=workspace_lease_id,
            action_class="rollback",
            resource_scope=list(receipt.rollback_artifact_refs),
            idempotency_key=f"rollback:{receipt.receipt_id}",
            constraints={"receipt_ref": receipt.receipt_id, "strategy": strategy},
        )
        self.capabilities.consume(capability_grant_id)
        try:
            output_payload = self._apply_rollback(receipt, strategy)
            output_uri, output_hash = self.artifact_store.store_json(output_payload)
            artifact = self.store.create_artifact(
                task_id=receipt.task_id,
                step_id=step.step_id,
                kind="rollback.result",
                uri=output_uri,
                content_hash=output_hash,
                producer="rollback_service",
                retention_class="audit",
                trust_tier="observed",
                metadata={"receipt_ref": receipt.receipt_id, "strategy": strategy},
            )
            rollback_receipt_id = self.receipts.issue(
                task_id=receipt.task_id,
                step_id=step.step_id,
                step_attempt_id=attempt.step_attempt_id,
                action_type="rollback",
                input_refs=[
                    ref
                    for ref in [receipt.receipt_bundle_ref, *receipt.rollback_artifact_refs]
                    if ref
                ],
                environment_ref=None,
                policy_result={"verdict": "allow", "reason": "Operator-triggered rollback."},
                approval_ref=None,
                output_refs=[artifact.artifact_id],
                result_summary=output_payload["result_summary"],
                result_code="succeeded",
                decision_ref=decision_id,
                capability_grant_ref=capability_grant_id,
                workspace_lease_ref=workspace_lease_id,
                rollback_supported=False,
            )
            self.store.update_rollback(
                rollback.rollback_id,
                status="succeeded",
                result_summary=output_payload["result_summary"],
            )
            self.store.update_receipt_rollback_fields(
                receipt.receipt_id,
                rollback_status="succeeded",
                rollback_ref=rollback.rollback_id,
            )
            self.store.update_step(step.step_id, status="succeeded", output_ref=rollback_receipt_id)
            self.store.update_step_attempt(attempt.step_attempt_id, status="succeeded")
            self.store.update_task_status(receipt.task_id, "completed")
            return {
                "rollback_id": rollback.rollback_id,
                "receipt_id": rollback_receipt_id,
                "status": "succeeded",
                "result_summary": output_payload["result_summary"],
            }
        except Exception as exc:
            summary = str(exc)
            self.store.update_rollback(
                rollback.rollback_id, status="failed", result_summary=summary
            )
            self.store.update_receipt_rollback_fields(
                receipt.receipt_id,
                rollback_status="failed",
                rollback_ref=rollback.rollback_id,
            )
            self.store.update_step(step.step_id, status="failed")
            self.store.update_step_attempt(
                attempt.step_attempt_id, status="failed", waiting_reason=summary
            )
            return {"rollback_id": rollback.rollback_id, "status": "failed", "error": summary}

    def _acquire_workspace_lease(
        self,
        receipt: ReceiptRecord,
        step_attempt_id: str,
        *,
        holder_principal_id: str,
    ) -> str | None:
        root_path = self._rollback_root_path(receipt)
        if not root_path:
            return None
        lease = self.workspace_leases.acquire(
            task_id=receipt.task_id,
            step_attempt_id=step_attempt_id,
            workspace_id="rollback",
            root_path=root_path,
            holder_principal_id=holder_principal_id,
            mode="mutable",
            resource_scope=[root_path],
            ttl_seconds=300,
        )
        return lease.lease_id

    def _rollback_root_path(self, receipt: ReceiptRecord) -> str | None:
        if receipt.workspace_lease_ref:
            original_lease = self.store.get_workspace_lease(receipt.workspace_lease_ref)
            if original_lease is not None and original_lease.root_path:
                return original_lease.root_path
        if receipt.action_type in {"write_local", "patch_file"} and receipt.rollback_artifact_refs:
            prestate = self._prestate_payload(receipt)
            return str(Path(str(prestate["path"])).expanduser().resolve().parent)
        if receipt.action_type == "vcs_mutation" and receipt.rollback_artifact_refs:
            prestate = self._prestate_payload(receipt)
            return str(Path(str(prestate["repo_path"])).expanduser().resolve())
        return None

    def _apply_rollback(self, receipt: ReceiptRecord, strategy: str) -> dict[str, Any]:
        if receipt.action_type in {"write_local", "patch_file"} and strategy == "file_restore":
            prestate = self._prestate_payload(receipt)
            target_path = Path(str(prestate["path"]))
            if bool(prestate.get("existed")):
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(str(prestate.get("content", "")), encoding="utf-8")
            elif target_path.exists():
                target_path.unlink()
            return {
                "result_summary": self._t(
                    "kernel.rollback.result.file_restore", target_path=target_path
                )
            }
        if receipt.action_type == "vcs_mutation" and strategy == "git_revert_or_reset":
            prestate = self._prestate_payload(receipt)
            repo_path = Path(str(prestate["repo_path"]))
            head = str(prestate["head"])
            if bool(prestate.get("dirty")):
                raise RuntimeError(self._t("kernel.rollback.error.dirty_repo"))
            self._git_worktree().hard_reset(repo_path, head)
            return {"result_summary": self._t("kernel.rollback.result.git_reset", head=head)}
        if receipt.action_type == "memory_write" and strategy == "supersede_or_invalidate":
            targets = self._prestate_payload(receipt)
            for memory_id in targets.get("memory_ids", []):
                self.store.update_memory_record(memory_id, status="invalidated")
            for belief_id in targets.get("belief_ids", []):
                self.store.update_belief(belief_id, status="invalidated")
            return {
                "result_summary": self._t(
                    "kernel.rollback.result.memory_invalidate",
                    count=len(targets.get("memory_ids", [])),
                )
            }
        raise RuntimeError(
            self._t("kernel.rollback.error.strategy_not_executable", strategy=strategy)
        )

    def _mark_unsupported(self, receipt: ReceiptRecord, summary: str) -> dict[str, Any]:
        if receipt.rollback_status != "unsupported":
            self.store.update_receipt_rollback_fields(
                receipt.receipt_id,
                rollback_status="unsupported",
            )
        return {"status": "unsupported", "result_summary": summary}

    def _prestate_payload(self, receipt: ReceiptRecord) -> dict[str, Any]:
        if not receipt.rollback_artifact_refs:
            raise RuntimeError(self._t("kernel.rollback.error.prestate_missing"))
        artifact = self.store.get_artifact(receipt.rollback_artifact_refs[0])
        if artifact is None:
            raise RuntimeError(self._t("kernel.rollback.error.artifact_missing"))
        return json.loads(self.artifact_store.read_text(artifact.uri))

    def _t(self, message_key: str, *, default: str | None = None, **kwargs: object) -> str:
        return tr(message_key, locale=resolve_locale(), default=default, **kwargs)

    def _git_worktree(self) -> GitWorktreeInspector:
        return getattr(self, "git_worktree", GitWorktreeInspector())
