"""Tests for claim_next_ready_step_attempt inflight guard.

Verifies that a step with an already in-flight attempt (running, dispatching,
reconciling, etc.) is excluded from claim, preventing duplicate execution.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture
def controller(store: KernelStore) -> TaskController:
    return TaskController(store)


def _create_ready_task(
    controller: TaskController,
    store: KernelStore,
    conversation_id: str,
) -> tuple[str, str, str]:
    """Create a task with one ready step attempt claimable by the dispatch loop.

    Returns (task_id, step_id, attempt_id).
    """
    ctx = controller.start_task(
        conversation_id=conversation_id,
        goal="test inflight guard",
        source_channel="test",
        kind="respond",
    )
    # start_task creates step/attempt in "running" status — reset to "ready"
    # and set task to "queued" so claim_next_ready_step_attempt can pick it up.
    store.update_step_attempt(ctx.step_attempt_id, status="ready")
    store.update_step(ctx.step_id, status="ready")
    store.update_task_status(ctx.task_id, "queued")
    return ctx.task_id, ctx.step_id, ctx.step_attempt_id


class TestClaimInflightGuard:
    """claim_next_ready_step_attempt must skip steps with in-flight attempts."""

    @pytest.mark.parametrize(
        "inflight_status",
        ["running", "dispatching", "reconciling", "observing", "contracting", "preflighting"],
    )
    def test_step_with_inflight_attempt_not_claimable(
        self, store: KernelStore, controller: TaskController, inflight_status: str
    ) -> None:
        """If any attempt for a step is in-flight, no other attempt is claimable."""
        # Create two independent tasks so we have two claimable steps
        task1_id, step1_id, attempt1_id = _create_ready_task(
            controller, store, f"task1-{inflight_status}"
        )
        _task2_id, step2_id, _attempt2_id = _create_ready_task(
            controller, store, f"task2-{inflight_status}"
        )

        # Put step1's attempt into inflight status, but leave step as 'ready'
        # to simulate the race condition
        store.update_step_attempt(attempt1_id, status=inflight_status)

        # Insert a second ready attempt for step1 (simulating recovery re-ready)
        dup_id = store._id("attempt")
        store._get_conn().execute(
            """
            INSERT INTO step_attempts (
                step_attempt_id, task_id, step_id, attempt, status,
                context_json, queue_priority, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (dup_id, task1_id, step1_id, 2, "ready", "{}", 0, time.time()),
        )
        store._get_conn().commit()

        # Claim should skip step1 (has inflight attempt) and get step2
        claimed = store.claim_next_ready_step_attempt()
        assert claimed is not None
        assert claimed.step_id == step2_id, (
            f"Expected step2 but got step1 — inflight guard failed for {inflight_status}"
        )

    def test_step_without_inflight_is_claimable(
        self, store: KernelStore, controller: TaskController
    ) -> None:
        """A step with only terminal or ready attempts is still claimable."""
        _task_id, step_id, _attempt_id = _create_ready_task(controller, store, "claimable")

        claimed = store.claim_next_ready_step_attempt()
        assert claimed is not None
        assert claimed.step_id == step_id

    def test_claimed_step_not_double_claimed(
        self, store: KernelStore, controller: TaskController
    ) -> None:
        """After a step is claimed (running), subsequent claims return None."""
        _create_ready_task(controller, store, "single-step")

        claimed = store.claim_next_ready_step_attempt()
        assert claimed is not None

        # The claim set step to running — no more ready attempts
        assert store.claim_next_ready_step_attempt() is None
