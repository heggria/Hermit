from __future__ import annotations

import time
from typing import Any

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import (
    BlackboardEntryStatus,
    BlackboardEntryType,
    BlackboardRecord,
)


class BlackboardService:
    """Task-scoped typed blackboard for structured inter-step communication."""

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    def post(
        self,
        *,
        task_id: str,
        step_id: str,
        entry_type: str,
        content: dict[str, Any],
        confidence: float = 0.5,
        step_attempt_id: str | None = None,
    ) -> BlackboardRecord:
        """Post a new entry to the blackboard."""
        if entry_type not in BlackboardEntryType.__members__:
            raise ValueError(
                f"Invalid entry_type: {entry_type}. "
                f"Must be one of {list(BlackboardEntryType.__members__)}"
            )
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {confidence}")
        entry_id = self._store.generate_id("bb")
        now = time.time()
        record = BlackboardRecord(
            entry_id=entry_id,
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=step_attempt_id,
            entry_type=entry_type,
            content=dict(content),
            confidence=confidence,
            status=BlackboardEntryStatus.active,
            created_at=now,
        )
        self._store.insert_blackboard_entry(record)
        self._store.append_event(
            event_type="blackboard.entry_posted",
            entity_type="blackboard",
            entity_id=entry_id,
            task_id=task_id,
            step_id=step_id,
            actor="kernel",
            payload={
                "entry_type": entry_type,
                "confidence": confidence,
            },
        )
        return record

    def query(
        self,
        task_id: str,
        *,
        entry_type: str | None = None,
        status: str | None = None,
    ) -> list[BlackboardRecord]:
        """Query blackboard entries for a task."""
        return self._store.query_blackboard_entries(
            task_id=task_id,
            entry_type=entry_type,
            status=status,
        )

    def supersede(
        self,
        entry_id: str,
        *,
        new_entry: BlackboardRecord,
    ) -> BlackboardRecord:
        """Mark an existing entry as superseded and link the new entry."""
        old = self._store.get_blackboard_entry(entry_id)
        if old is None:
            raise ValueError(f"Blackboard entry not found: {entry_id}")
        new_record = BlackboardRecord(
            entry_id=new_entry.entry_id,
            task_id=new_entry.task_id,
            step_id=new_entry.step_id,
            step_attempt_id=new_entry.step_attempt_id,
            entry_type=new_entry.entry_type,
            content=dict(new_entry.content),
            confidence=new_entry.confidence,
            supersedes_entry_id=entry_id,
            status=BlackboardEntryStatus.active,
            created_at=new_entry.created_at or time.time(),
        )
        self._store.update_blackboard_entry_status(entry_id, BlackboardEntryStatus.superseded)
        self._store.insert_blackboard_entry(new_record)
        self._store.append_event(
            event_type="blackboard.entry_superseded",
            entity_type="blackboard",
            entity_id=entry_id,
            task_id=old.task_id,
            step_id=old.step_id,
            actor="kernel",
            payload={
                "superseded_by": new_record.entry_id,
            },
        )
        return new_record

    def resolve(
        self,
        entry_id: str,
        *,
        resolution: str,
    ) -> BlackboardRecord:
        """Mark an entry as resolved."""
        old = self._store.get_blackboard_entry(entry_id)
        if old is None:
            raise ValueError(f"Blackboard entry not found: {entry_id}")
        self._store.update_blackboard_entry_status(
            entry_id, BlackboardEntryStatus.resolved, resolution=resolution
        )
        self._store.append_event(
            event_type="blackboard.entry_resolved",
            entity_type="blackboard",
            entity_id=entry_id,
            task_id=old.task_id,
            step_id=old.step_id,
            actor="kernel",
            payload={
                "resolution": resolution,
            },
        )
        updated = self._store.get_blackboard_entry(entry_id)
        if updated is None:
            raise RuntimeError(
                f"Blackboard entry {entry_id!r} disappeared after status update — "
                "possible store consistency failure"
            )
        return updated


__all__ = ["BlackboardService"]
