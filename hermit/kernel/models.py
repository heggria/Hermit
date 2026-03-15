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
    owner_principal_id: str
    policy_profile: str
    source_channel: str
    parent_task_id: str | None = None
    task_contract_ref: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    requested_by_principal_id: str | None = None

    @property
    def owner(self) -> str:
        return self.owner_principal_id

    @property
    def requested_by(self) -> str | None:
        return self.requested_by_principal_id


@dataclass
class StepRecord:
    step_id: str
    task_id: str
    kind: str
    status: str
    attempt: int
    input_ref: str | None = None
    output_ref: str | None = None
    title: str | None = None
    contract_ref: str | None = None
    depends_on: list[str] = field(default_factory=list)
    max_attempts: int = 1
    started_at: float | None = None
    finished_at: float | None = None
    created_at: float | None = None
    updated_at: float | None = None


@dataclass
class StepAttemptRecord:
    step_attempt_id: str
    task_id: str
    step_id: str
    attempt: int
    status: str
    context: dict[str, Any] = field(default_factory=dict)
    queue_priority: int = 0
    waiting_reason: str | None = None
    approval_id: str | None = None
    decision_id: str | None = None
    capability_grant_id: str | None = None
    workspace_lease_id: str | None = None
    state_witness_ref: str | None = None
    context_pack_ref: str | None = None
    working_state_ref: str | None = None
    environment_ref: str | None = None
    action_request_ref: str | None = None
    policy_result_ref: str | None = None
    approval_packet_ref: str | None = None
    pending_execution_ref: str | None = None
    idempotency_key: str | None = None
    executor_mode: str | None = None
    policy_version: str | None = None
    resume_from_ref: str | None = None
    superseded_by_step_attempt_id: str | None = None
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def attempt_id(self) -> str:
        return self.step_attempt_id

    @property
    def attempt_no(self) -> int:
        return self.attempt


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
    requested_action_ref: str | None = None
    approval_packet_ref: str | None = None
    policy_result_ref: str | None = None
    decision_ref: str | None = None
    state_witness_ref: str | None = None
    requested_at: float | None = None
    expires_at: float | None = None
    resolved_at: float | None = None
    resolved_by_principal_id: str | None = None
    resolution: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.approval_packet_ref is None:
            self.approval_packet_ref = self.request_packet_ref
        if self.request_packet_ref is None:
            self.request_packet_ref = self.approval_packet_ref

    @property
    def resolved_by(self) -> str | None:
        return self.resolved_by_principal_id


@dataclass
class DecisionRecord:
    decision_id: str
    task_id: str
    step_id: str
    step_attempt_id: str
    decision_type: str
    verdict: str
    reason: str
    summary: str | None = None
    rationale: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    policy_ref: str | None = None
    approval_ref: str | None = None
    action_type: str | None = None
    risk_level: str | None = None
    reversible: bool | None = None
    decided_by_principal_id: str = "principal_kernel"
    created_at: float | None = None

    def __post_init__(self) -> None:
        canonical_reason = str(self.rationale or self.reason or "").strip()
        if canonical_reason and not self.reason:
            self.reason = canonical_reason
        if not self.rationale:
            self.rationale = canonical_reason or None
        if not self.summary:
            self.summary = canonical_reason or None


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
    artifact_class: str | None = None
    media_type: str | None = None
    byte_size: int | None = None
    sensitivity_class: str | None = None
    lineage_ref: str | None = None
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
    receipt_class: str | None = None
    result_code: str = "succeeded"
    decision_ref: str | None = None
    capability_grant_ref: str | None = None
    workspace_lease_ref: str | None = None
    policy_ref: str | None = None
    action_request_ref: str | None = None
    policy_result_ref: str | None = None
    witness_ref: str | None = None
    idempotency_key: str | None = None
    receipt_bundle_ref: str | None = None
    proof_mode: str = "hash_only"
    verifiability: str | None = None
    signature: str | None = None
    signer_ref: str | None = None
    rollback_supported: bool = False
    rollback_strategy: str | None = None
    rollback_status: str = "not_requested"
    rollback_ref: str | None = None
    rollback_artifact_refs: list[str] = field(default_factory=list)
    created_at: float | None = None

    def __post_init__(self) -> None:
        if not self.receipt_class:
            self.receipt_class = self.action_type
        if not self.action_type:
            self.action_type = str(self.receipt_class or "")
        if self.policy_result_ref is None and self.policy_ref is not None:
            self.policy_result_ref = self.policy_ref

    @property
    def attempt_id(self) -> str:
        return self.step_attempt_id


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
    focus_task_id: str | None = None
    focus_reason: str | None = None
    focus_updated_at: float | None = None
    status: str = "open"
    metadata: dict[str, Any] = field(default_factory=dict)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class IngressRecord:
    ingress_id: str
    conversation_id: str
    source_channel: str
    actor_principal_id: str | None = None
    raw_text: str = ""
    normalized_text: str = ""
    prompt_ref: str | None = None
    reply_to_ref: str | None = None
    quoted_message_ref: str | None = None
    explicit_task_ref: str | None = None
    referenced_artifact_refs: list[str] = field(default_factory=list)
    status: str = "received"
    resolution: str = "none"
    chosen_task_id: str | None = None
    parent_task_id: str | None = None
    confidence: float | None = None
    margin: float | None = None
    rationale: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
