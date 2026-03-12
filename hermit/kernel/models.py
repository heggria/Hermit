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
    requested_at: float | None = None
    resolved_at: float | None = None
    resolved_by: str | None = None
    resolution: dict[str, Any] = field(default_factory=dict)


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
    created_at: float | None = None


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
