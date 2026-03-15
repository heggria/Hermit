from __future__ import annotations

import sqlite3

from hermit.builtin.scheduler.models import ScheduledJob
from hermit.capabilities.models import CapabilityGrantRecord
from hermit.identity.models import PrincipalRecord
from hermit.kernel.models import (
    ApprovalRecord,
    ArtifactRecord,
    BeliefRecord,
    ConversationRecord,
    DecisionRecord,
    IngressRecord,
    MemoryRecord,
    ReceiptRecord,
    RollbackRecord,
    StepAttemptRecord,
    StepRecord,
    TaskRecord,
)
from hermit.kernel.store_support import _json_loads
from hermit.workspaces.models import WorkspaceLeaseRecord


class KernelStoreRecordMixin:
    def _conversation_from_row(self, row: sqlite3.Row) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            source_channel=str(row["source_channel"]),
            source_ref=row["source_ref"],
            last_task_id=row["last_task_id"],
            focus_task_id=row["focus_task_id"],
            focus_reason=row["focus_reason"],
            focus_updated_at=row["focus_updated_at"],
            status=str(row["status"]),
            metadata=_json_loads(row["metadata_json"]),
            total_input_tokens=int(row["total_input_tokens"]),
            total_output_tokens=int(row["total_output_tokens"]),
            total_cache_read_tokens=int(row["total_cache_read_tokens"]),
            total_cache_creation_tokens=int(row["total_cache_creation_tokens"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _task_from_row(self, row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=str(row["task_id"]),
            conversation_id=str(row["conversation_id"]),
            title=str(row["title"]),
            goal=str(row["goal"]),
            status=str(row["status"]),
            priority=str(row["priority"]),
            owner_principal_id=str(row["owner_principal_id"]),
            policy_profile=str(row["policy_profile"]),
            source_channel=str(row["source_channel"]),
            parent_task_id=row["parent_task_id"],
            task_contract_ref=row["task_contract_ref"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            requested_by_principal_id=row["requested_by_principal_id"],
        )

    def _step_from_row(self, row: sqlite3.Row) -> StepRecord:
        return StepRecord(
            step_id=str(row["step_id"]),
            task_id=str(row["task_id"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            attempt=int(row["attempt"]),
            input_ref=row["input_ref"],
            output_ref=row["output_ref"],
            title=row["title"],
            contract_ref=row["contract_ref"],
            depends_on=list(_json_loads(row["depends_on_json"])),
            max_attempts=int(row["max_attempts"] or 1),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _step_attempt_from_row(self, row: sqlite3.Row) -> StepAttemptRecord:
        return StepAttemptRecord(
            step_attempt_id=str(row["step_attempt_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            attempt=int(row["attempt"]),
            status=str(row["status"]),
            context=_json_loads(row["context_json"]),
            queue_priority=int(row["queue_priority"] or 0),
            waiting_reason=row["waiting_reason"],
            approval_id=row["approval_id"],
            decision_id=row["decision_id"],
            capability_grant_id=row["capability_grant_id"],
            workspace_lease_id=row["workspace_lease_id"],
            state_witness_ref=row["state_witness_ref"],
            context_pack_ref=row["context_pack_ref"],
            working_state_ref=row["working_state_ref"],
            environment_ref=row["environment_ref"],
            action_request_ref=row["action_request_ref"],
            policy_result_ref=row["policy_result_ref"],
            approval_packet_ref=row["approval_packet_ref"],
            pending_execution_ref=row["pending_execution_ref"],
            idempotency_key=row["idempotency_key"],
            executor_mode=row["executor_mode"],
            policy_version=row["policy_version"],
            resume_from_ref=row["resume_from_ref"],
            superseded_by_step_attempt_id=row["superseded_by_step_attempt_id"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def _ingress_from_row(self, row: sqlite3.Row) -> IngressRecord:
        return IngressRecord(
            ingress_id=str(row["ingress_id"]),
            conversation_id=str(row["conversation_id"]),
            source_channel=str(row["source_channel"]),
            actor_principal_id=row["actor_principal_id"],
            raw_text=str(row["raw_text"]),
            normalized_text=str(row["normalized_text"]),
            prompt_ref=row["prompt_ref"],
            reply_to_ref=row["reply_to_ref"],
            quoted_message_ref=row["quoted_message_ref"],
            explicit_task_ref=row["explicit_task_ref"],
            referenced_artifact_refs=list(_json_loads(row["referenced_artifact_refs_json"])),
            status=str(row["status"]),
            resolution=str(row["resolution"] or "none"),
            chosen_task_id=row["chosen_task_id"],
            parent_task_id=row["parent_task_id"],
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            margin=float(row["margin"]) if row["margin"] is not None else None,
            rationale=dict(_json_loads(row["rationale_json"] or "{}")),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _artifact_from_row(self, row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=str(row["artifact_id"]),
            task_id=row["task_id"],
            step_id=row["step_id"],
            kind=str(row["kind"]),
            uri=str(row["uri"]),
            content_hash=str(row["content_hash"]),
            producer=str(row["producer"]),
            retention_class=str(row["retention_class"]),
            trust_tier=str(row["trust_tier"]),
            artifact_class=row["artifact_class"],
            media_type=row["media_type"],
            byte_size=int(row["byte_size"]) if row["byte_size"] is not None else None,
            sensitivity_class=row["sensitivity_class"],
            lineage_ref=row["lineage_ref"],
            metadata=_json_loads(row["metadata_json"]),
            created_at=float(row["created_at"]),
        )

    def _approval_from_row(self, row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=str(row["approval_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            step_attempt_id=str(row["step_attempt_id"]),
            status=str(row["status"]),
            approval_type=str(row["approval_type"]),
            requested_action=_json_loads(row["requested_action_json"]),
            request_packet_ref=row["request_packet_ref"],
            requested_action_ref=row["requested_action_ref"],
            approval_packet_ref=row["approval_packet_ref"] or row["request_packet_ref"],
            policy_result_ref=row["policy_result_ref"],
            decision_ref=row["decision_ref"],
            state_witness_ref=row["state_witness_ref"],
            requested_at=float(row["requested_at"]),
            expires_at=row["expires_at"],
            resolved_at=row["resolved_at"],
            resolved_by_principal_id=row["resolved_by_principal_id"],
            resolution=_json_loads(row["resolution_json"]),
        )

    def _decision_from_row(self, row: sqlite3.Row) -> DecisionRecord:
        return DecisionRecord(
            decision_id=str(row["decision_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            step_attempt_id=str(row["step_attempt_id"]),
            decision_type=str(row["decision_type"]),
            verdict=str(row["verdict"]),
            reason=str(row["reason"]),
            summary=row["summary"] or row["reason"],
            rationale=row["rationale"] or row["reason"],
            evidence_refs=list(_json_loads(row["evidence_refs_json"])),
            policy_ref=row["policy_ref"],
            approval_ref=row["approval_ref"],
            action_type=row["action_type"],
            risk_level=row["risk_level"],
            reversible=bool(row["reversible"]) if row["reversible"] is not None else None,
            decided_by_principal_id=str(row["decided_by_principal_id"]),
            created_at=float(row["created_at"]),
        )

    def _principal_from_row(self, row: sqlite3.Row) -> PrincipalRecord:
        return PrincipalRecord(
            principal_id=str(row["principal_id"]),
            principal_type=str(row["principal_type"]),
            display_name=str(row["display_name"]),
            source_channel=row["source_channel"],
            external_ref=row["external_ref"],
            status=str(row["status"]),
            metadata=dict(_json_loads(row["metadata_json"])),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _capability_grant_from_row(self, row: sqlite3.Row) -> CapabilityGrantRecord:
        return CapabilityGrantRecord(
            grant_id=str(row["grant_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            step_attempt_id=str(row["step_attempt_id"]),
            decision_ref=str(row["decision_ref"]),
            approval_ref=row["approval_ref"],
            policy_ref=row["policy_ref"],
            issued_to_principal_id=str(row["issued_to_principal_id"]),
            issued_by_principal_id=str(row["issued_by_principal_id"]),
            workspace_lease_ref=row["workspace_lease_ref"],
            action_class=str(row["action_class"]),
            resource_scope=list(_json_loads(row["resource_scope_json"])),
            constraints=dict(_json_loads(row["constraints_json"])),
            idempotency_key=row["idempotency_key"],
            status=str(row["status"]),
            issued_at=float(row["issued_at"]),
            expires_at=row["expires_at"],
            consumed_at=row["consumed_at"],
            revoked_at=row["revoked_at"],
        )

    def _workspace_lease_from_row(self, row: sqlite3.Row) -> WorkspaceLeaseRecord:
        return WorkspaceLeaseRecord(
            lease_id=str(row["lease_id"]),
            task_id=str(row["task_id"]),
            step_attempt_id=str(row["step_attempt_id"]),
            workspace_id=str(row["workspace_id"]),
            root_path=str(row["root_path"]),
            holder_principal_id=str(row["holder_principal_id"]),
            mode=str(row["mode"]),
            resource_scope=list(_json_loads(row["resource_scope_json"])),
            environment_ref=row["environment_ref"],
            status=str(row["status"]),
            acquired_at=float(row["acquired_at"]),
            expires_at=row["expires_at"],
            released_at=row["released_at"],
            metadata=dict(_json_loads(row["metadata_json"] or "{}")),
        )

    def _receipt_from_row(self, row: sqlite3.Row) -> ReceiptRecord:
        return ReceiptRecord(
            receipt_id=str(row["receipt_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            step_attempt_id=str(row["step_attempt_id"]),
            action_type=str(row["action_type"]),
            receipt_class=row["receipt_class"] or row["action_type"],
            input_refs=list(_json_loads(row["input_refs_json"])),
            environment_ref=row["environment_ref"],
            policy_result=_json_loads(row["policy_result_json"]),
            approval_ref=row["approval_ref"],
            output_refs=list(_json_loads(row["output_refs_json"])),
            result_summary=str(row["result_summary"]),
            result_code=str(row["result_code"]),
            decision_ref=row["decision_ref"],
            capability_grant_ref=row["capability_grant_ref"],
            workspace_lease_ref=row["workspace_lease_ref"],
            policy_ref=row["policy_ref"],
            action_request_ref=row["action_request_ref"],
            policy_result_ref=row["policy_result_ref"] or row["policy_ref"],
            witness_ref=row["witness_ref"],
            idempotency_key=row["idempotency_key"],
            receipt_bundle_ref=row["receipt_bundle_ref"],
            proof_mode=str(row["proof_mode"] or "none"),
            verifiability=row["verifiability"],
            signature=row["signature"],
            signer_ref=row["signer_ref"],
            rollback_supported=bool(row["rollback_supported"]),
            rollback_strategy=row["rollback_strategy"],
            rollback_status=str(row["rollback_status"] or "not_requested"),
            rollback_ref=row["rollback_ref"],
            rollback_artifact_refs=list(_json_loads(row["rollback_artifact_refs_json"])),
            created_at=float(row["created_at"]),
        )

    def _belief_from_row(self, row: sqlite3.Row) -> BeliefRecord:
        return BeliefRecord(
            belief_id=str(row["belief_id"]),
            task_id=str(row["task_id"]),
            conversation_id=row["conversation_id"],
            scope_kind=str(row["scope_kind"]),
            scope_ref=str(row["scope_ref"]),
            category=str(row["category"]),
            claim_text=str(row["claim_text"] or row["content"]),
            structured_assertion=dict(_json_loads(row["structured_assertion_json"] or "{}")),
            promotion_candidate=bool(row["promotion_candidate"]),
            status=str(row["status"]),
            confidence=float(row["confidence"]),
            trust_tier=str(row["trust_tier"]),
            evidence_refs=list(_json_loads(row["evidence_refs_json"])),
            supersedes=list(_json_loads(row["supersedes_json"])),
            contradicts=list(_json_loads(row["contradicts_json"])),
            memory_ref=row["memory_ref"],
            invalidated_at=row["invalidated_at"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _memory_record_from_row(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=str(row["memory_id"]),
            task_id=str(row["task_id"]),
            conversation_id=row["conversation_id"],
            category=str(row["category"]),
            claim_text=str(row["claim_text"] or row["content"]),
            structured_assertion=dict(_json_loads(row["structured_assertion_json"] or "{}")),
            scope_kind=str(row["scope_kind"] or "conversation"),
            scope_ref=str(row["scope_ref"] or row["conversation_id"] or ""),
            promotion_reason=str(row["promotion_reason"] or "belief_promotion"),
            retention_class=str(row["retention_class"] or "volatile_fact"),
            status=str(row["status"]),
            confidence=float(row["confidence"]),
            trust_tier=str(row["trust_tier"]),
            evidence_refs=list(_json_loads(row["evidence_refs_json"])),
            supersedes=list(_json_loads(row["supersedes_json"])),
            supersedes_memory_ids=list(_json_loads(row["supersedes_memory_ids_json"] or "[]")),
            superseded_by_memory_id=row["superseded_by_memory_id"],
            source_belief_ref=row["source_belief_ref"],
            invalidation_reason=row["invalidation_reason"],
            invalidated_at=row["invalidated_at"],
            expires_at=row["expires_at"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _rollback_from_row(self, row: sqlite3.Row) -> RollbackRecord:
        return RollbackRecord(
            rollback_id=str(row["rollback_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            step_attempt_id=str(row["step_attempt_id"]),
            receipt_ref=str(row["receipt_ref"]),
            action_type=str(row["action_type"]),
            strategy=str(row["strategy"]),
            status=str(row["status"]),
            result_summary=row["result_summary"],
            artifact_refs=list(_json_loads(row["artifact_refs_json"])),
            created_at=float(row["created_at"]),
            executed_at=row["executed_at"],
        )

    def _schedule_from_row(self, row: sqlite3.Row) -> ScheduledJob:
        return ScheduledJob(
            id=str(row["id"]),
            name=str(row["name"]),
            prompt=str(row["prompt"]),
            schedule_type=str(row["schedule_type"]),
            cron_expr=row["cron_expr"],
            once_at=row["once_at"],
            interval_seconds=row["interval_seconds"],
            enabled=bool(row["enabled"]),
            created_at=float(row["created_at"]),
            last_run_at=row["last_run_at"],
            next_run_at=row["next_run_at"],
            max_retries=int(row["max_retries"]),
            feishu_chat_id=row["feishu_chat_id"],
        )
