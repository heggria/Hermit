from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hermit.kernel.context.memory.taxonomy import MemoryType, classify_memory_type


@dataclass
class _FakeMemoryRecord:
    """Minimal stand-in for MemoryRecord used by classify_memory_type."""

    memory_id: str = "mem-fake"
    task_id: str = "task-1"
    conversation_id: str | None = "conv-1"
    category: str = "project_convention"
    claim_text: str = ""
    structured_assertion: dict[str, Any] = field(default_factory=dict)
    scope_kind: str = "workspace"
    scope_ref: str = "workspace:default"
    promotion_reason: str = "belief_promotion"
    retention_class: str = "project_convention"
    status: str = "active"
    confidence: float = 0.8
    trust_tier: str = "durable"
    evidence_refs: list[str] = field(default_factory=list)
    memory_kind: str = "durable_fact"
    validation_basis: str | None = None
    last_validated_at: float | None = None
    supersession_reason: str | None = None
    learned_from_reconciliation_ref: str | None = None
    supersedes: list[str] = field(default_factory=list)
    supersedes_memory_ids: list[str] = field(default_factory=list)
    superseded_by_memory_id: str | None = None
    source_belief_ref: str | None = None
    invalidation_reason: str | None = None
    invalidated_at: float | None = None
    expires_at: float | None = None
    created_at: float | None = None
    updated_at: float | None = None


def test_classify_episode_index() -> None:
    """memory_kind='episode_index' classifies as EPISODIC."""
    record = _FakeMemoryRecord(memory_kind="episode_index")
    assert classify_memory_type(record) == MemoryType.EPISODIC


def test_classify_procedural() -> None:
    """memory_kind='procedural' classifies as PROCEDURAL."""
    record = _FakeMemoryRecord(memory_kind="procedural")
    assert classify_memory_type(record) == MemoryType.PROCEDURAL


def test_classify_procedural_from_text() -> None:
    """Text containing 'To do this, first X then Y' classifies as PROCEDURAL."""
    record = _FakeMemoryRecord(
        memory_kind="durable_fact",
        claim_text="To do this, first install the package, then run the tests.",
    )
    assert classify_memory_type(record) == MemoryType.PROCEDURAL


def test_classify_volatile_conversation() -> None:
    """volatile_fact + conversation scope classifies as WORKING."""
    record = _FakeMemoryRecord(
        memory_kind="durable_fact",
        retention_class="volatile_fact",
        scope_kind="conversation",
        claim_text="Current temperature is 22C",
    )
    assert classify_memory_type(record) == MemoryType.WORKING


def test_classify_durable_fact() -> None:
    """durable_fact with workspace scope classifies as SEMANTIC."""
    record = _FakeMemoryRecord(
        memory_kind="durable_fact",
        retention_class="project_convention",
        scope_kind="workspace",
        claim_text="Python 3.13 is the minimum version",
    )
    assert classify_memory_type(record) == MemoryType.SEMANTIC
