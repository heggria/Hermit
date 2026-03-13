from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskRecord:
    task_id: str
    conversation_id: str
    title: str
    goal: str
    status: str
    priority: str
    owner: str
    policy_profile: str
    source_channel: str
    parent_task_id: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    requested_by: str | None = None


@dataclass
class StepRecord:
    step_id: str
    task_id: str
    kind: str
    status: str
    attempt: int
    input_ref: str | None = None
    output_ref: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


@dataclass
class StepAttemptRecord:
    step_attempt_id: str
    task_id: str
    step_id: str
    attempt: int
    status: str
    context: dict[str, Any] = field(default_factory=dict)
    waiting_reason: str | None = None
    approval_id: str | None = None
    decision_id: str | None = None
    permit_id: str | None = None
    state_witness_ref: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


@dataclass
class ApprovalRecord:
    approval_id: str
    task_id: str
    step_id: str
    step_attempt_id: str
    status: str
    approval_type: str
    requested_action: dict[str, Any]
    request_packet_ref: str | None = None
    decision_ref: str | None = None
    state_witness_ref: str | None = None
    requested_at: float | None = None
    resolved_at: float | None = None
    resolved_by: str | None = None
    resolution: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionRecord:
    decision_id: str
    task_id: str
    step_id: str
    step_attempt_id: str
    decision_type: str
    verdict: str
    reason: str
    evidence_refs: list[str] = field(default_factory=list)
    policy_ref: str | None = None
    approval_ref: str | None = None
    action_type: str | None = None
    decided_by: str = "kernel"
    created_at: float | None = None


@dataclass
class ExecutionPermitRecord:
    permit_id: str
    task_id: str
    step_id: str
    step_attempt_id: str
    decision_ref: str
    approval_ref: str | None
    policy_ref: str | None
    action_class: str
    resource_scope: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    status: str = "issued"
    issued_at: float | None = None
    expires_at: float | None = None
    consumed_at: float | None = None


@dataclass
class PathGrantRecord:
    grant_id: str
    subject_kind: str
    subject_ref: str
    action_class: str
    path_prefix: str
    path_display: str
    created_by: str
    approval_ref: str | None = None
    decision_ref: str | None = None
    policy_ref: str | None = None
    status: str = "active"
    created_at: float | None = None
    expires_at: float | None = None
    last_used_at: float | None = None


@dataclass
class ArtifactRecord:
    artifact_id: str
    task_id: str | None
    step_id: str | None
    kind: str
    uri: str
    content_hash: str
    producer: str
    retention_class: str
    trust_tier: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float | None = None


@dataclass
class ReceiptRecord:
    receipt_id: str
    task_id: str
    step_id: str
    step_attempt_id: str
    action_type: str
    input_refs: list[str]
    environment_ref: str | None
    policy_result: dict[str, Any]
    approval_ref: str | None
    output_refs: list[str]
    result_summary: str
    result_code: str = "succeeded"
    decision_ref: str | None = None
    permit_ref: str | None = None
    grant_ref: str | None = None
    policy_ref: str | None = None
    witness_ref: str | None = None
    idempotency_key: str | None = None
    receipt_bundle_ref: str | None = None
    proof_mode: str = "none"
    signature: str | None = None
    rollback_supported: bool = False
    rollback_strategy: str | None = None
    rollback_status: str = "not_requested"
    rollback_ref: str | None = None
    rollback_artifact_refs: list[str] = field(default_factory=list)
    created_at: float | None = None


@dataclass
class BeliefRecord:
    belief_id: str
    task_id: str
    conversation_id: str | None
    scope_kind: str
    scope_ref: str
    category: str
    claim_text: str
    structured_assertion: dict[str, Any] = field(default_factory=dict)
    promotion_candidate: bool = True
    status: str = "active"
    confidence: float = 0.5
    trust_tier: str = "observed"
    evidence_refs: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    contradicts: list[str] = field(default_factory=list)
    memory_ref: str | None = None
    invalidated_at: float | None = None
    created_at: float | None = None
    updated_at: float | None = None

    @property
    def content(self) -> str:
        return self.claim_text


@dataclass
class MemoryRecord:
    memory_id: str
    task_id: str
    conversation_id: str | None
    category: str
    claim_text: str
    structured_assertion: dict[str, Any] = field(default_factory=dict)
    scope_kind: str = "conversation"
    scope_ref: str = ""
    promotion_reason: str = "belief_promotion"
    retention_class: str = "volatile_fact"
    status: str = "active"
    confidence: float = 0.5
    trust_tier: str = "durable"
    evidence_refs: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    supersedes_memory_ids: list[str] = field(default_factory=list)
    superseded_by_memory_id: str | None = None
    source_belief_ref: str | None = None
    invalidation_reason: str | None = None
    invalidated_at: float | None = None
    expires_at: float | None = None
    created_at: float | None = None
    updated_at: float | None = None

    @property
    def content(self) -> str:
        return self.claim_text


@dataclass
class RollbackRecord:
    rollback_id: str
    task_id: str
    step_id: str
    step_attempt_id: str
    receipt_ref: str
    action_type: str
    strategy: str
    status: str = "not_requested"
    result_summary: str | None = None
    artifact_refs: list[str] = field(default_factory=list)
    created_at: float | None = None
    executed_at: float | None = None


@dataclass
class ConversationRecord:
    conversation_id: str
    source_channel: str
    source_ref: str | None = None
    last_task_id: str | None = None
    status: str = "open"
    metadata: dict[str, Any] = field(default_factory=dict)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0


CapabilityGrantRecord = ExecutionPermitRecord
