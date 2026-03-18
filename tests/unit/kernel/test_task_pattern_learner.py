"""Tests for task-level pattern learning from completed tasks.

Verifies that:
1. Patterns are learned from completed tasks with 2+ satisfied steps
2. Patterns with fewer than 2 steps are rejected
3. Duplicate patterns are reinforced (not duplicated)
4. Pattern matching by goal keywords works
5. Keywords from multiple tasks are merged
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.execution.controller.pattern_learner import (
    TaskPatternLearner,
    _extract_keywords,
    _pattern_fingerprint,
    _step_fingerprint,
)
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


def _create_completed_task(
    store: KernelStore,
    controller: TaskController,
    *,
    goal: str = "write and test a file",
    steps: list[tuple[str, str]] | None = None,
    workspace_root: str = "/tmp",
) -> str:
    """Create a task with satisfied step attempts and execution contracts."""
    ctx = controller.start_task(
        conversation_id="chat-pattern",
        goal=goal,
        source_channel="chat",
        kind="respond",
        workspace_root=workspace_root,
    )
    task_id = ctx.task_id

    if steps is None:
        steps = [("read_local", "read_file"), ("write_local", "write_file")]

    for i, (action_class, tool_name) in enumerate(steps):
        step = store.create_step(
            task_id=task_id,
            kind="action",
        )
        attempt = store.create_step_attempt(
            task_id=task_id,
            step_id=step.step_id,
            attempt=i + 1,
        )
        contract = store.create_execution_contract(
            task_id=task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            objective=f"{tool_name}: {action_class}",
            expected_effects=[f"action:{action_class}"],
            success_criteria={
                "tool_name": tool_name,
                "action_class": action_class,
                "requires_receipt": True,
            },
            reversibility_class="reversible",
            required_receipt_classes=[action_class],
            drift_budget={"resource_scopes": [], "outside_workspace": False},
            risk_budget={"risk_level": "high", "approval_required": False},
            status="satisfied",
            action_contract_refs=[action_class],
        )
        store.update_step_attempt(
            attempt.step_attempt_id,
            status="succeeded",
            execution_contract_ref=contract.contract_id,
        )

    return task_id


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestPatternHelpers:
    def test_extract_keywords_filters_stop_words(self) -> None:
        kw = _extract_keywords("Write a file to the workspace")
        assert "a" not in kw
        assert "the" not in kw
        assert "write" in kw
        assert "file" in kw
        assert "workspace" in kw

    def test_extract_keywords_handles_empty(self) -> None:
        assert _extract_keywords("") == []
        assert _extract_keywords("a the") == []

    def test_step_fingerprint_deterministic(self) -> None:
        fp1 = _step_fingerprint("write_local", "write_file")
        fp2 = _step_fingerprint("write_local", "write_file")
        assert fp1 == fp2

    def test_step_fingerprint_differs_for_different_actions(self) -> None:
        fp1 = _step_fingerprint("write_local", "write_file")
        fp2 = _step_fingerprint("read_local", "read_file")
        assert fp1 != fp2

    def test_pattern_fingerprint_order_matters(self) -> None:
        fps = [
            _step_fingerprint("read_local", "read_file"),
            _step_fingerprint("write_local", "write_file"),
        ]
        fp1 = _pattern_fingerprint(fps)
        fp2 = _pattern_fingerprint(list(reversed(fps)))
        assert fp1 != fp2


# ---------------------------------------------------------------------------
# Unit tests: pattern learning
# ---------------------------------------------------------------------------


class TestLearnFromCompletedTask:
    def test_learn_creates_task_pattern_memory(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        controller = TaskController(store)
        learner = TaskPatternLearner(store)

        task_id = _create_completed_task(store, controller)
        memory = learner.learn_from_completed_task(task_id)

        assert memory is not None
        assert memory.memory_kind == "task_pattern"
        assert memory.category == "task_pattern"
        assert memory.status == "active"

        sa = memory.structured_assertion
        assert sa["pattern_fingerprint"]
        assert len(sa["step_fingerprints"]) == 2
        assert len(sa["step_descriptions"]) == 2
        assert sa["invocation_count"] == 1
        assert sa["success_count"] == 1
        assert sa["success_rate"] == 1.0
        assert task_id in sa["source_task_refs"]

    def test_learn_skips_single_step_task(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        controller = TaskController(store)
        learner = TaskPatternLearner(store)

        task_id = _create_completed_task(
            store,
            controller,
            steps=[("write_local", "write_file")],
        )
        memory = learner.learn_from_completed_task(task_id)
        assert memory is None

    def test_duplicate_pattern_reinforces(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        controller = TaskController(store)
        learner = TaskPatternLearner(store)

        task_id1 = _create_completed_task(store, controller, goal="write and test first file")
        task_id2 = _create_completed_task(store, controller, goal="write and test second file")

        mem1 = learner.learn_from_completed_task(task_id1)
        mem2 = learner.learn_from_completed_task(task_id2)

        assert mem1 is not None
        assert mem2 is not None
        assert mem1.memory_id == mem2.memory_id

        # Check reinforced counts
        record = store.get_memory_record(mem1.memory_id)
        assert record is not None
        sa = record.structured_assertion
        assert sa["invocation_count"] == 2
        assert sa["success_count"] == 2

    def test_different_step_sequences_create_different_patterns(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        controller = TaskController(store)
        learner = TaskPatternLearner(store)

        task_id1 = _create_completed_task(
            store,
            controller,
            steps=[("read_local", "read_file"), ("write_local", "write_file")],
        )
        task_id2 = _create_completed_task(
            store,
            controller,
            steps=[("write_local", "write_file"), ("execute_command", "bash")],
        )

        mem1 = learner.learn_from_completed_task(task_id1)
        mem2 = learner.learn_from_completed_task(task_id2)

        assert mem1 is not None
        assert mem2 is not None
        assert mem1.memory_id != mem2.memory_id


# ---------------------------------------------------------------------------
# Unit tests: pattern matching
# ---------------------------------------------------------------------------


class TestFindMatchingPattern:
    def test_finds_matching_pattern_by_keywords(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        controller = TaskController(store)
        learner = TaskPatternLearner(store)

        _create_completed_task(store, controller, goal="write and test a file")
        task_id = _create_completed_task(store, controller, goal="write and test a file")
        learner.learn_from_completed_task(task_id)

        pattern = learner.find_matching_pattern("write file and test")
        assert pattern is not None
        assert len(pattern.step_descriptions) == 2

    def test_no_match_for_unrelated_goal(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        controller = TaskController(store)
        learner = TaskPatternLearner(store)

        task_id = _create_completed_task(store, controller, goal="write and test a file")
        learner.learn_from_completed_task(task_id)

        pattern = learner.find_matching_pattern("deploy kubernetes cluster")
        assert pattern is None

    def test_no_match_when_no_patterns(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        learner = TaskPatternLearner(store)
        pattern = learner.find_matching_pattern("write a file")
        assert pattern is None

    def test_keywords_merged_from_multiple_tasks(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        controller = TaskController(store)
        learner = TaskPatternLearner(store)

        task_id1 = _create_completed_task(store, controller, goal="write config file")
        task_id2 = _create_completed_task(store, controller, goal="create settings yaml")

        learner.learn_from_completed_task(task_id1)
        learner.learn_from_completed_task(task_id2)

        # Should match with keywords from either task
        pattern = learner.find_matching_pattern("write settings")
        assert pattern is not None
