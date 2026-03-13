from __future__ import annotations

import sqlite3

from hermit.builtin.scheduler.models import ScheduledJob
from hermit.kernel.models import (
    ApprovalRecord,
    ArtifactRecord,
    BeliefRecord,
    ConversationRecord,
    DecisionRecord,
    ExecutionPermitRecord,
    MemoryRecord,
    PathGrantRecord,
    ReceiptRecord,
    RollbackRecord,
    StepAttemptRecord,
    StepRecord,
    TaskRecord,
)
from hermit.kernel.store_support import _json_loads


class KernelStoreRecordMixin:
    def _conversation_from_row(self, row: sqlite3.Row) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            source_channel=str(row["source_channel"]),
            source_ref=row["source_ref"],
            last_task_id=row["last_task_id"],
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
            owner=str(row["owner"]),
            policy_profile=str(row["policy_profile"]),
            source_channel=str(row["source_channel"]),
            parent_task_id=row["parent_task_id"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            requested_by=row["requested_by"],
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
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def _step_attempt_from_row(self, row: sqlite3.Row) -> StepAttemptRecord:
        return StepAttemptRecord(
            step_attempt_id=str(row["step_attempt_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            attempt=int(row["attempt"]),
            status=str(row["status"]),
            context=_json_loads(row["context_json"]),
            waiting_reason=row["waiting_reason"],
            approval_id=row["approval_id"],
            decision_id=row["decision_id"],
            permit_id=row["permit_id"],
            state_witness_ref=row["state_witness_ref"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
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
            decision_ref=row["decision_ref"],
            state_witness_ref=row["state_witness_ref"],
            requested_at=float(row["requested_at"]),
            resolved_at=row["resolved_at"],
            resolved_by=row["resolved_by"],
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
            evidence_refs=list(_json_loads(row["evidence_refs_json"])),
            policy_ref=row["policy_ref"],
            approval_ref=row["approval_ref"],
            action_type=row["action_type"],
            decided_by=str(row["decided_by"]),
            created_at=float(row["created_at"]),
        )

    def _execution_permit_from_row(self, row: sqlite3.Row) -> ExecutionPermitRecord:
        return ExecutionPermitRecord(
            permit_id=str(row["permit_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            step_attempt_id=str(row["step_attempt_id"]),
            decision_ref=str(row["decision_ref"]),
            approval_ref=row["approval_ref"],
            policy_ref=row["policy_ref"],
            action_class=str(row["action_class"]),
            resource_scope=list(_json_loads(row["resource_scope_json"])),
            constraints=dict(_json_loads(row["constraints_json"])),
            idempotency_key=row["idempotency_key"],
            status=str(row["status"]),
            issued_at=float(row["issued_at"]),
            expires_at=row["expires_at"],
            consumed_at=row["consumed_at"],
        )

    def _receipt_from_row(self, row: sqlite3.Row) -> ReceiptRecord:
        return ReceiptRecord(
            receipt_id=str(row["receipt_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            step_attempt_id=str(row["step_attempt_id"]),
            action_type=str(row["action_type"]),
            input_refs=list(_json_loads(row["input_refs_json"])),
            environment_ref=row["environment_ref"],
            policy_result=_json_loads(row["policy_result_json"]),
            approval_ref=row["approval_ref"],
            output_refs=list(_json_loads(row["output_refs_json"])),
            result_summary=str(row["result_summary"]),
            result_code=str(row["result_code"]),
            decision_ref=row["decision_ref"],
            permit_ref=row["permit_ref"],
            grant_ref=row["grant_ref"],
            policy_ref=row["policy_ref"],
            witness_ref=row["witness_ref"],
            idempotency_key=row["idempotency_key"],
            receipt_bundle_ref=row["receipt_bundle_ref"],
            proof_mode=str(row["proof_mode"] or "none"),
            signature=row["signature"],
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

    def _path_grant_from_row(self, row: sqlite3.Row) -> PathGrantRecord:
        return PathGrantRecord(
            grant_id=str(row["grant_id"]),
            subject_kind=str(row["subject_kind"]),
            subject_ref=str(row["subject_ref"]),
            action_class=str(row["action_class"]),
            path_prefix=str(row["path_prefix"]),
            path_display=str(row["path_display"]),
            created_by=str(row["created_by"]),
            approval_ref=row["approval_ref"],
            decision_ref=row["decision_ref"],
            policy_ref=row["policy_ref"],
            status=str(row["status"]),
            created_at=float(row["created_at"]),
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
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
