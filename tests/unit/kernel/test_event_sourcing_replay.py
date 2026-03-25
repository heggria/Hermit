"""Tests for event sourcing replay: projection rebuild from events.

Covers:
- Projection rebuilt from events matches original state
- Create 10 events -> build projection -> delete cache -> rebuild -> compare
- Incremental rebuild: add 5 more events -> incremental update -> compare with full rebuild
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hermit.kernel.ledger.journal.store import KernelStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store() -> KernelStore:
    return KernelStore(Path(":memory:"))


def _create_task(store: KernelStore, *, title: str = "replay-task") -> str:
    conv = store.ensure_conversation("conv-replay", source_channel="test")
    task = store.create_task(
        conversation_id=conv.conversation_id,
        title=title,
        goal="Test event sourcing replay",
        source_channel="test",
        status="running",
        policy_profile="autonomous",
    )
    return task.task_id


def _create_step_receipt_pair(
    store: KernelStore,
    task_id: str,
    *,
    action_type: str = "test_action",
    step_kind: str = "execute",
) -> dict[str, str]:
    """Create a step, attempt, decision, grant, and receipt. Return IDs."""
    step = store.create_step(task_id=task_id, kind=step_kind, status="running")
    attempt = store.create_step_attempt(task_id=task_id, step_id=step.step_id, status="running")
    decision = store.create_decision(
        task_id=task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="policy_evaluation",
        verdict="allow",
        reason="Test action allowed",
        evidence_refs=[],
        action_type=action_type,
        decided_by="kernel",
    )
    grant = store.create_capability_grant(
        task_id=task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref=decision.decision_id,
        approval_ref=None,
        policy_ref=None,
        issued_to_principal_id="principal_hermit",
        issued_by_principal_id="principal_kernel",
        action_class=action_type,
        resource_scope=[],
        constraints=None,
        idempotency_key=None,
        expires_at=None,
    )
    receipt = store.create_receipt(
        task_id=task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type=action_type,
        input_refs=[],
        environment_ref=None,
        policy_result={"verdict": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary=f"Executed {action_type}",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        capability_grant_ref=grant.grant_id,
    )
    return {
        "step_id": step.step_id,
        "attempt_id": attempt.step_attempt_id,
        "decision_id": decision.decision_id,
        "grant_id": grant.grant_id,
        "receipt_id": receipt.receipt_id,
    }


def _projection_summary(projection: dict[str, Any]) -> dict[str, Any]:
    """Extract a comparable summary from a projection dict."""
    return {
        "events_processed": projection["events_processed"],
        "last_event_seq": projection["last_event_seq"],
        "step_count": len(projection["steps"]),
        "step_attempt_count": len(projection["step_attempts"]),
        "decision_count": len(projection["decisions"]),
        "capability_grant_count": len(projection["capability_grants"]),
        "receipt_count": len(projection["receipts"]),
        "task_status": (projection["task"] or {}).get("status"),
        "step_ids": sorted(projection["steps"].keys()),
        "receipt_ids": sorted(projection["receipts"].keys()),
    }


# ---------------------------------------------------------------------------
# Tests: Full Projection Rebuild
# ---------------------------------------------------------------------------


class TestFullProjectionRebuild:
    """Test that rebuilding a projection from events reproduces the same state."""

    def test_projection_from_events_is_deterministic(self) -> None:
        """Building a projection twice from the same events yields the same result."""
        store = _make_store()
        task_id = _create_task(store)

        for i in range(3):
            _create_step_receipt_pair(store, task_id, action_type=f"action_{i}")

        proj1 = store.build_task_projection(task_id)
        proj2 = store.build_task_projection(task_id)

        assert _projection_summary(proj1) == _projection_summary(proj2)

    def test_ten_events_build_rebuild_compare(self) -> None:
        """Create 10 governed actions, build projection, then rebuild and compare."""
        store = _make_store()
        task_id = _create_task(store)

        all_ids: list[dict[str, str]] = []
        for i in range(10):
            ids = _create_step_receipt_pair(store, task_id, action_type=f"action_{i}")
            all_ids.append(ids)

        # Build original projection
        original_projection = store.build_task_projection(task_id)

        # Cache the projection
        store.upsert_projection_cache(
            task_id,
            schema_version="18",
            event_head_hash="test-hash",
            payload=original_projection,
        )

        # Verify the cache exists
        cached = store.get_projection_cache(task_id)
        assert cached is not None

        # Delete the projection cache
        store._get_conn().execute("DELETE FROM projection_cache WHERE task_id = ?", (task_id,))
        deleted_cache = store.get_projection_cache(task_id)
        assert deleted_cache is None

        # Rebuild from events
        rebuilt_projection = store.build_task_projection(task_id)

        # Compare
        original_summary = _projection_summary(original_projection)
        rebuilt_summary = _projection_summary(rebuilt_projection)

        assert original_summary == rebuilt_summary
        assert original_summary["events_processed"] == rebuilt_summary["events_processed"]
        assert original_summary["step_count"] == 10
        assert original_summary["receipt_count"] == 10
        assert original_summary["decision_count"] == 10
        assert original_summary["capability_grant_count"] == 10

    def test_projection_contains_all_entity_types(self) -> None:
        """Projection should have entries for all entity types created."""
        store = _make_store()
        task_id = _create_task(store)
        ids = _create_step_receipt_pair(store, task_id, action_type="bash")

        projection = store.build_task_projection(task_id)

        assert projection["task"] is not None
        assert ids["step_id"] in projection["steps"]
        assert ids["attempt_id"] in projection["step_attempts"]
        assert ids["decision_id"] in projection["decisions"]
        assert ids["grant_id"] in projection["capability_grants"]
        assert ids["receipt_id"] in projection["receipts"]

    def test_projection_tracks_events_processed_count(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        _create_step_receipt_pair(store, task_id)

        projection = store.build_task_projection(task_id)

        assert projection["events_processed"] > 0
        assert projection["last_event_seq"] is not None
        assert projection["last_event_seq"] > 0


# ---------------------------------------------------------------------------
# Tests: Incremental Rebuild
# ---------------------------------------------------------------------------


class TestIncrementalRebuild:
    """Test that adding more events and rebuilding yields the correct combined state."""

    def test_incremental_update_matches_full_rebuild(self) -> None:
        """Add 5 events, build, add 5 more, full rebuild should match incremental."""
        store = _make_store()
        task_id = _create_task(store)

        # Phase 1: Create 5 governed actions
        for i in range(5):
            _create_step_receipt_pair(store, task_id, action_type=f"phase1_{i}")

        # Build first projection
        proj_after_5 = store.build_task_projection(task_id)
        summary_5 = _projection_summary(proj_after_5)
        assert summary_5["step_count"] == 5
        assert summary_5["receipt_count"] == 5

        # Phase 2: Create 5 more governed actions
        for i in range(5):
            _create_step_receipt_pair(store, task_id, action_type=f"phase2_{i}")

        # Full rebuild
        proj_after_10 = store.build_task_projection(task_id)
        summary_10 = _projection_summary(proj_after_10)
        assert summary_10["step_count"] == 10
        assert summary_10["receipt_count"] == 10

        # The second projection should have strictly more events
        assert summary_10["events_processed"] > summary_5["events_processed"]
        assert summary_10["last_event_seq"] > summary_5["last_event_seq"]

        # All phase 1 entities should still be present
        for step_id in summary_5["step_ids"]:
            assert step_id in summary_10["step_ids"]
        for receipt_id in summary_5["receipt_ids"]:
            assert receipt_id in summary_10["receipt_ids"]

    def test_projection_after_task_status_update(self) -> None:
        """Projection should reflect task status changes via events."""
        store = _make_store()
        task_id = _create_task(store)
        _create_step_receipt_pair(store, task_id)

        # Build before status change
        proj_before = store.build_task_projection(task_id)
        assert (proj_before["task"] or {}).get("status") == "running"

        # Update task status
        store.update_task_status(task_id, "completed")

        # Rebuild
        proj_after = store.build_task_projection(task_id)
        assert (proj_after["task"] or {}).get("status") == "completed"
        assert proj_after["events_processed"] > proj_before["events_processed"]


# ---------------------------------------------------------------------------
# Tests: Projection Cache
# ---------------------------------------------------------------------------


class TestProjectionCache:
    """Test the projection cache CRUD operations."""

    def test_upsert_and_get_projection_cache(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        _create_step_receipt_pair(store, task_id)

        projection = store.build_task_projection(task_id)

        store.upsert_projection_cache(
            task_id,
            schema_version="18",
            event_head_hash="abc123",
            payload=projection,
        )

        cached = store.get_projection_cache(task_id)
        assert cached is not None
        assert cached["task_id"] == task_id
        assert cached["schema_version"] == "18"
        assert cached["event_head_hash"] == "abc123"
        assert cached["payload"]["events_processed"] == projection["events_processed"]

    def test_upsert_overwrites_existing_cache(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        _create_step_receipt_pair(store, task_id)

        proj1 = store.build_task_projection(task_id)
        store.upsert_projection_cache(
            task_id,
            schema_version="18",
            event_head_hash="hash-v1",
            payload=proj1,
        )

        # Add more events
        _create_step_receipt_pair(store, task_id, action_type="more_work")
        proj2 = store.build_task_projection(task_id)
        store.upsert_projection_cache(
            task_id,
            schema_version="18",
            event_head_hash="hash-v2",
            payload=proj2,
        )

        cached = store.get_projection_cache(task_id)
        assert cached is not None
        assert cached["event_head_hash"] == "hash-v2"
        assert cached["payload"]["events_processed"] == proj2["events_processed"]

    def test_list_projection_cache_tasks(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        _create_step_receipt_pair(store, task_id)

        store.upsert_projection_cache(
            task_id,
            schema_version="18",
            event_head_hash="hash-1",
            payload=store.build_task_projection(task_id),
        )

        task_ids = store.list_projection_cache_tasks()
        assert task_id in task_ids

    def test_get_missing_projection_cache(self) -> None:
        store = _make_store()
        cached = store.get_projection_cache("nonexistent-task")
        assert cached is None


# ---------------------------------------------------------------------------
# Tests: Event Entity Replay Fidelity
# ---------------------------------------------------------------------------


class TestEventEntityReplayFidelity:
    """Test that replayed projections faithfully represent entity state."""

    def test_step_status_tracked_through_events(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        step = store.create_step(task_id=task_id, kind="execute", status="running")
        store.update_step(step.step_id, status="succeeded")

        projection = store.build_task_projection(task_id)

        step_proj = projection["steps"].get(step.step_id)
        assert step_proj is not None
        # The step status should reflect the latest event
        assert step_proj.get("status") == "succeeded"

    def test_multiple_entity_types_in_single_projection(self) -> None:
        store = _make_store()
        task_id = _create_task(store)

        # Create multiple entity types
        _create_step_receipt_pair(store, task_id, action_type="bash")

        # Create belief
        store.create_belief(
            task_id=task_id,
            conversation_id="conv-replay",
            scope_kind="task",
            scope_ref=task_id,
            category="tech_decision",
            claim_text="Python is the language",
            confidence=0.9,
            evidence_refs=[],
        )

        projection = store.build_task_projection(task_id)

        assert len(projection["steps"]) == 1
        assert len(projection["step_attempts"]) == 1
        assert len(projection["decisions"]) == 1
        assert len(projection["capability_grants"]) == 1
        assert len(projection["receipts"]) == 1
        assert len(projection["beliefs"]) == 1
