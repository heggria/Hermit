"""Tests for EpisodicMemoryService — episode indexing and episodic retrieval."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.context.memory.episodic import EpisodicMemoryService
from hermit.kernel.context.memory.episodic_models import EpisodeIndex
from hermit.kernel.task.models.records import MemoryRecord


def _mem(
    memory_id: str = "mem-1",
    task_id: str = "task-1",
    claim_text: str = "test claim",
    status: str = "active",
    memory_kind: str = "durable_fact",
    confidence: float = 0.8,
    created_at: float | None = None,
    structured_assertion: dict[str, Any] | None = None,
) -> MemoryRecord:
    now = created_at or time.time()
    return MemoryRecord(
        memory_id=memory_id,
        task_id=task_id,
        conversation_id="conv-1",
        category="user_preference",
        claim_text=claim_text,
        status=status,
        confidence=confidence,
        trust_tier="durable",
        retention_class="user_preference",
        memory_kind=memory_kind,
        created_at=now,
        updated_at=now,
        structured_assertion=structured_assertion or {},
    )


def _mock_store(
    memories: list[MemoryRecord] | None = None,
    receipts: list[Any] | None = None,
) -> MagicMock:
    """Build a mock KernelStore with configurable memory listing and receipt listing."""
    store = MagicMock()
    all_memories = list(memories or [])

    def list_memory_records(**kwargs):
        task_id = kwargs.get("task_id")
        status = kwargs.get("status")
        result = all_memories
        if task_id:
            result = [m for m in result if m.task_id == task_id]
        if status:
            result = [m for m in result if m.status == status]
        limit = kwargs.get("limit", 5000)
        return result[:limit]

    store.list_memory_records.side_effect = list_memory_records

    def get_memory_record(mid):
        for m in all_memories:
            if m.memory_id == mid:
                return m
        return None

    store.get_memory_record.side_effect = get_memory_record

    # create_memory_record appends to the list and returns the new record
    def create_memory_record(**kwargs):
        import uuid

        new_mem = _mem(
            memory_id=f"mem-{uuid.uuid4().hex[:8]}",
            task_id=kwargs.get("task_id", ""),
            claim_text=kwargs.get("claim_text", ""),
            memory_kind=kwargs.get("memory_kind", "durable_fact"),
            confidence=kwargs.get("confidence", 0.5),
            structured_assertion=kwargs.get("structured_assertion", {}),
        )
        all_memories.append(new_mem)
        return new_mem

    store.create_memory_record.side_effect = create_memory_record

    # Receipts
    store.list_receipts.return_value = receipts or []

    # update_memory_record updates the status in-place for test verification
    def update_memory_record(mid, **kwargs):
        for m in all_memories:
            if m.memory_id == mid:
                if "status" in kwargs:
                    m.status = kwargs["status"]
                if "invalidation_reason" in kwargs:
                    m.invalidation_reason = kwargs["invalidation_reason"]
                if "invalidated_at" in kwargs:
                    m.invalidated_at = kwargs["invalidated_at"]
                break

    store.update_memory_record.side_effect = update_memory_record

    return store


def test_index_episode_creates_record() -> None:
    """index_episode creates an episode_index memory and returns EpisodeIndex."""
    m1 = _mem(memory_id="mem-a", task_id="task-ep-1", claim_text="Memory A")
    m2 = _mem(memory_id="mem-b", task_id="task-ep-1", claim_text="Memory B")
    store = _mock_store(memories=[m1, m2])

    service = EpisodicMemoryService()
    result = service.index_episode("task-ep-1", store, conversation_id="conv-1")

    assert result is not None
    assert isinstance(result, EpisodeIndex)
    assert result.task_id == "task-ep-1"
    assert len(result.memory_ids) == 2
    assert "mem-a" in result.memory_ids
    assert "mem-b" in result.memory_ids

    # Verify create_memory_record was called with memory_kind="episode_index"
    store.create_memory_record.assert_called_once()
    call_kwargs = store.create_memory_record.call_args[1]
    assert call_kwargs["memory_kind"] == "episode_index"


def test_query_by_episode_returns_task_memories() -> None:
    """query_by_episode returns the memories referenced in an episode index."""
    m1 = _mem(memory_id="mem-a", task_id="task-ep-2", claim_text="Memory A")
    m2 = _mem(memory_id="mem-b", task_id="task-ep-2", claim_text="Memory B")
    episode_index_mem = _mem(
        memory_id="mem-ep",
        task_id="task-ep-2",
        memory_kind="episode_index",
        structured_assertion={
            "episode_id": "ep-test123",
            "task_id": "task-ep-2",
            "memory_ids": ["mem-a", "mem-b"],
            "artifact_ids": [],
            "tool_names": [],
        },
    )
    store = _mock_store(memories=[m1, m2, episode_index_mem])

    service = EpisodicMemoryService()
    results = service.query_by_episode("task-ep-2", store)

    result_ids = {r.memory_id for r in results}
    assert "mem-a" in result_ids
    assert "mem-b" in result_ids


def test_query_by_tool_finds_matching_episodes() -> None:
    """query_by_tool finds memories from episodes that used a specific tool."""
    m1 = _mem(memory_id="mem-tool-1", task_id="task-3", claim_text="Tool memory")
    episode = _mem(
        memory_id="mem-ep-tool",
        task_id="task-3",
        memory_kind="episode_index",
        structured_assertion={
            "episode_id": "ep-tool123",
            "task_id": "task-3",
            "memory_ids": ["mem-tool-1"],
            "artifact_ids": [],
            "tool_names": ["bash", "write_file"],
        },
    )
    store = _mock_store(memories=[m1, episode])

    service = EpisodicMemoryService()
    results = service.query_by_tool("bash", store)

    assert len(results) >= 1
    assert any(r.memory_id == "mem-tool-1" for r in results)
    assert all("tool_match:bash" in r.match_reason for r in results)


def test_query_by_artifact_finds_matching_episodes() -> None:
    """query_by_artifact finds memories from episodes with matching artifacts."""
    m1 = _mem(memory_id="mem-art-1", task_id="task-4", claim_text="Artifact memory")
    episode = _mem(
        memory_id="mem-ep-art",
        task_id="task-4",
        memory_kind="episode_index",
        structured_assertion={
            "episode_id": "ep-art123",
            "task_id": "task-4",
            "memory_ids": ["mem-art-1"],
            "artifact_ids": ["artifact-config-main", "artifact-other"],
            "tool_names": [],
        },
    )
    store = _mock_store(memories=[m1, episode])

    service = EpisodicMemoryService()
    results = service.query_by_artifact("config-main", store)

    assert len(results) >= 1
    assert any(r.memory_id == "mem-art-1" for r in results)
    assert all("artifact_match:config-main" in r.match_reason for r in results)


def test_decay_stale_episodes_invalidates_old() -> None:
    """Episodes older than max_age_days are invalidated by decay_stale_episodes."""
    old_time = time.time() - (60 * 86400)  # 60 days ago
    episode = _mem(
        memory_id="mem-old-ep",
        task_id="task-5",
        memory_kind="episode_index",
        created_at=old_time,
        structured_assertion={
            "episode_id": "ep-old",
            "task_id": "task-5",
            "memory_ids": [],
            "artifact_ids": [],
            "tool_names": [],
        },
    )
    store = _mock_store(memories=[episode])

    service = EpisodicMemoryService()
    count = service.decay_stale_episodes(store, max_age_days=30)

    assert count >= 1
    assert episode.status == "invalidated"
    assert episode.invalidation_reason is not None
    assert "episode_decay" in episode.invalidation_reason


def test_index_episode_returns_none_for_no_memories() -> None:
    """index_episode returns None if the task has no memories."""
    store = _mock_store(memories=[])

    service = EpisodicMemoryService()
    result = service.index_episode("task-nonexistent", store, conversation_id="conv-1")

    assert result is None
    store.create_memory_record.assert_not_called()
