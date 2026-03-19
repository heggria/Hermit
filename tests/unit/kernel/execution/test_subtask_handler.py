"""Tests for hermit.kernel.execution.executor.subtask_handler."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.subtask_handler import (
    _DEFAULT_STRATEGY,
    _SPAWN_ENVELOPE_KEY,
    _VALID_STRATEGIES,
    SubtaskSpawner,
    normalize_spawn_descriptors,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attempt_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults: dict[str, Any] = {
        "conversation_id": "conv-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "step_attempt_id": "attempt-1",
        "source_channel": "cli",
    }
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


def _make_store() -> MagicMock:
    store = MagicMock()
    child_step = SimpleNamespace(step_id="child-step-1")
    store.create_step.return_value = child_step
    store.create_step_attempt.return_value = SimpleNamespace(step_attempt_id="child-attempt-1")
    return store


# ---------------------------------------------------------------------------
# normalize_spawn_descriptors
# ---------------------------------------------------------------------------


class TestNormalizeSpawnDescriptors:
    def test_non_dict_returns_none(self) -> None:
        assert normalize_spawn_descriptors("string") is None
        assert normalize_spawn_descriptors(42) is None
        assert normalize_spawn_descriptors(None) is None
        assert normalize_spawn_descriptors([]) is None

    def test_missing_envelope_key_returns_none(self) -> None:
        assert normalize_spawn_descriptors({"other_key": []}) is None

    def test_empty_list_returns_none(self) -> None:
        assert normalize_spawn_descriptors({_SPAWN_ENVELOPE_KEY: []}) is None

    def test_non_list_envelope_returns_none(self) -> None:
        assert normalize_spawn_descriptors({_SPAWN_ENVELOPE_KEY: "string"}) is None

    def test_non_dict_items_skipped(self) -> None:
        value = {_SPAWN_ENVELOPE_KEY: ["string", 42, None]}
        assert normalize_spawn_descriptors(value) is None

    def test_items_without_tool_name_skipped(self) -> None:
        value = {_SPAWN_ENVELOPE_KEY: [{"title": "no tool name"}]}
        assert normalize_spawn_descriptors(value) is None

    def test_empty_tool_name_skipped(self) -> None:
        value = {_SPAWN_ENVELOPE_KEY: [{"tool_name": ""}]}
        assert normalize_spawn_descriptors(value) is None

    def test_whitespace_tool_name_skipped(self) -> None:
        value = {_SPAWN_ENVELOPE_KEY: [{"tool_name": "  "}]}
        assert normalize_spawn_descriptors(value) is None

    def test_valid_single_descriptor(self) -> None:
        value = {_SPAWN_ENVELOPE_KEY: [{"tool_name": "my_tool"}]}
        result = normalize_spawn_descriptors(value)
        assert result is not None
        assert len(result) == 1
        assert result[0]["tool_name"] == "my_tool"
        assert result[0]["join_strategy"] == _DEFAULT_STRATEGY
        assert result[0]["title"] == "my_tool"
        assert result[0]["tool_input"] == {}

    def test_valid_multiple_descriptors(self) -> None:
        value = {
            _SPAWN_ENVELOPE_KEY: [
                {"tool_name": "tool_a", "title": "Task A"},
                {"tool_name": "tool_b", "join_strategy": "any_sufficient"},
            ]
        }
        result = normalize_spawn_descriptors(value)
        assert result is not None
        assert len(result) == 2
        assert result[0]["title"] == "Task A"
        assert result[1]["join_strategy"] == "any_sufficient"

    def test_invalid_strategy_defaults(self) -> None:
        value = {
            _SPAWN_ENVELOPE_KEY: [{"tool_name": "tool_a", "join_strategy": "invalid_strategy"}]
        }
        result = normalize_spawn_descriptors(value)
        assert result is not None
        assert result[0]["join_strategy"] == _DEFAULT_STRATEGY

    def test_all_valid_strategies(self) -> None:
        for strategy in _VALID_STRATEGIES:
            value = {_SPAWN_ENVELOPE_KEY: [{"tool_name": "tool_a", "join_strategy": strategy}]}
            result = normalize_spawn_descriptors(value)
            assert result is not None
            assert result[0]["join_strategy"] == strategy

    def test_tool_input_preserved(self) -> None:
        value = {_SPAWN_ENVELOPE_KEY: [{"tool_name": "tool_a", "tool_input": {"key": "value"}}]}
        result = normalize_spawn_descriptors(value)
        assert result is not None
        assert result[0]["tool_input"] == {"key": "value"}

    def test_none_tool_input_becomes_empty_dict(self) -> None:
        value = {_SPAWN_ENVELOPE_KEY: [{"tool_name": "tool_a", "tool_input": None}]}
        result = normalize_spawn_descriptors(value)
        assert result is not None
        assert result[0]["tool_input"] == {}

    def test_mixed_valid_invalid_items(self) -> None:
        value = {
            _SPAWN_ENVELOPE_KEY: [
                {"tool_name": "valid"},
                "not_a_dict",
                {"tool_name": ""},
                {"tool_name": "also_valid"},
            ]
        }
        result = normalize_spawn_descriptors(value)
        assert result is not None
        assert len(result) == 2
        assert result[0]["tool_name"] == "valid"
        assert result[1]["tool_name"] == "also_valid"


# ---------------------------------------------------------------------------
# SubtaskSpawner
# ---------------------------------------------------------------------------


class TestSubtaskSpawner:
    def test_handle_spawn_creates_child_steps(self) -> None:
        store = _make_store()
        executor = MagicMock()
        spawner = SubtaskSpawner(store=store, executor=executor)
        ctx = _make_attempt_ctx()
        descriptors = [
            {
                "tool_name": "tool_a",
                "tool_input": {"key": "val"},
                "join_strategy": "all_required",
                "title": "Task A",
            }
        ]
        result = spawner.handle_spawn(attempt_ctx=ctx, descriptors=descriptors)
        store.create_step.assert_called_once()
        store.create_step_attempt.assert_called_once()
        assert result.blocked is True
        assert result.suspended is True
        assert result.waiting_kind == "awaiting_subtasks"
        assert result.result_code == "subtasks_spawned"

    def test_handle_spawn_suspends_parent(self) -> None:
        store = _make_store()
        executor = MagicMock()
        spawner = SubtaskSpawner(store=store, executor=executor)
        ctx = _make_attempt_ctx()
        descriptors = [
            {"tool_name": "t", "tool_input": {}, "join_strategy": "all_required", "title": "T"}
        ]
        spawner.handle_spawn(attempt_ctx=ctx, descriptors=descriptors)
        executor._set_attempt_phase.assert_called_once_with(
            ctx, "awaiting_subtasks", reason="subtask_spawned"
        )
        store.update_step_attempt.assert_called_once()
        store.update_step.assert_called_once()
        store.update_task_status.assert_called_once_with(ctx.task_id, "blocked")

    def test_handle_spawn_appends_event(self) -> None:
        store = _make_store()
        executor = MagicMock()
        spawner = SubtaskSpawner(store=store, executor=executor)
        ctx = _make_attempt_ctx()
        descriptors = [
            {"tool_name": "t", "tool_input": {}, "join_strategy": "all_required", "title": "T"}
        ]
        spawner.handle_spawn(attempt_ctx=ctx, descriptors=descriptors)
        store.append_event.assert_called_once()
        event_call = store.append_event.call_args
        assert event_call.kwargs["event_type"] == "subtask.spawned"

    def test_handle_spawn_multiple_children(self) -> None:
        store = MagicMock()
        child_steps = [SimpleNamespace(step_id=f"child-{i}") for i in range(3)]
        store.create_step.side_effect = child_steps
        store.create_step_attempt.return_value = SimpleNamespace(step_attempt_id="ca-1")
        executor = MagicMock()
        spawner = SubtaskSpawner(store=store, executor=executor)
        ctx = _make_attempt_ctx()
        descriptors = [
            {
                "tool_name": f"tool_{i}",
                "tool_input": {},
                "join_strategy": "all_required",
                "title": f"T{i}",
            }
            for i in range(3)
        ]
        result = spawner.handle_spawn(attempt_ctx=ctx, descriptors=descriptors)
        assert store.create_step.call_count == 3
        assert store.create_step_attempt.call_count == 3
        assert result.raw_result["child_step_ids"] == ["child-0", "child-1", "child-2"]

    def test_handle_spawn_returns_correct_model_content(self) -> None:
        store = _make_store()
        executor = MagicMock()
        spawner = SubtaskSpawner(store=store, executor=executor)
        ctx = _make_attempt_ctx()
        descriptors = [
            {"tool_name": "t", "tool_input": {}, "join_strategy": "all_required", "title": "T"}
        ]
        result = spawner.handle_spawn(attempt_ctx=ctx, descriptors=descriptors)
        assert "1 subtask" in result.model_content
        assert result.execution_status == "awaiting_subtasks"
        assert result.state_applied is True
