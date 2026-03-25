"""Tests for StepDataFlowService (data_flow.py) — coordination/data_flow coverage."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.execution.coordination.data_flow import StepDataFlowService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(
    *,
    input_bindings: dict[str, str] | None = None,
    output_ref: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        input_bindings=input_bindings,
        output_ref=output_ref,
    )


def _make_attempt(
    *,
    step_attempt_id: str = "sa-1",
    context: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        step_attempt_id=step_attempt_id,
        context=context,
    )


# ---------------------------------------------------------------------------
# TestResolveInputs
# ---------------------------------------------------------------------------


class TestResolveInputs:
    def test_returns_empty_when_step_not_found(self) -> None:
        store = MagicMock()
        store.get_step.return_value = None
        svc = StepDataFlowService(store)
        result = svc.resolve_inputs("task-1", "step-x")
        assert result == {}

    def test_returns_empty_when_input_bindings_none(self) -> None:
        store = MagicMock()
        store.get_step.return_value = _make_step(input_bindings=None)
        svc = StepDataFlowService(store)
        result = svc.resolve_inputs("task-1", "step-1")
        assert result == {}

    def test_returns_empty_when_input_bindings_empty(self) -> None:
        store = MagicMock()
        store.get_step.return_value = _make_step(input_bindings={})
        svc = StepDataFlowService(store)
        result = svc.resolve_inputs("task-1", "step-1")
        assert result == {}

    def test_skips_binding_without_dot(self) -> None:
        store = MagicMock()
        store.get_step.return_value = _make_step(input_bindings={"local_var": "nodotvalue"})
        svc = StepDataFlowService(store)
        result = svc.resolve_inputs("task-1", "step-1")
        assert result == {}

    def test_handles_binding_with_multiple_dots(self) -> None:
        """Binding 'step.output.ref' should split on first dot: source_key='step', output_field='output.ref'."""
        store = MagicMock()
        store.get_step.return_value = _make_step(
            input_bindings={"local_var": "producer.output.ref"}
        )
        # output_field will be "output.ref", not "output_ref", so it won't match
        source_step = _make_step(output_ref="artifact-1")
        store.get_step_by_node_key.return_value = source_step
        svc = StepDataFlowService(store)
        result = svc.resolve_inputs("task-1", "step-1")
        # "output.ref" != "output_ref", so it should not resolve
        assert result == {}

    def test_resolves_via_key_to_step_id(self) -> None:
        store = MagicMock()
        store.get_step.return_value = _make_step(input_bindings={"data": "producer.output_ref"})
        source_step = _make_step(output_ref="artifact-abc")

        def get_step_side_effect(step_id: str) -> SimpleNamespace | None:
            if step_id == "step-1":
                return _make_step(input_bindings={"data": "producer.output_ref"})
            if step_id == "producer-step-id":
                return source_step
            return None

        store.get_step.side_effect = get_step_side_effect
        svc = StepDataFlowService(store)

        result = svc.resolve_inputs(
            "task-1",
            "step-1",
            key_to_step_id={"producer": "producer-step-id"},
        )
        assert result == {"data": "artifact-abc"}

    def test_resolves_via_node_key_lookup(self) -> None:
        store = MagicMock()
        store.get_step.return_value = _make_step(
            input_bindings={"input_data": "source_node.output_ref"}
        )
        source_step = _make_step(output_ref="artifact-xyz")
        store.get_step_by_node_key.return_value = source_step

        svc = StepDataFlowService(store)
        result = svc.resolve_inputs("task-1", "step-1")
        assert result == {"input_data": "artifact-xyz"}
        store.get_step_by_node_key.assert_called_once_with("task-1", "source_node")

    def test_resolves_via_node_key_when_mapping_misses(self) -> None:
        store = MagicMock()
        store.get_step.return_value = _make_step(input_bindings={"x": "missing_key.output_ref"})
        source_step = _make_step(output_ref="art-1")
        store.get_step_by_node_key.return_value = source_step

        svc = StepDataFlowService(store)
        result = svc.resolve_inputs(
            "task-1",
            "step-1",
            key_to_step_id={"other_key": "other-step-id"},
        )
        assert result == {"x": "art-1"}

    def test_resolves_via_raw_step_id_fallback(self) -> None:
        store = MagicMock()
        step_with_bindings = _make_step(input_bindings={"y": "raw-step-id.output_ref"})
        source_step = _make_step(output_ref="art-fallback")

        def get_step_side_effect(step_id: str) -> SimpleNamespace | None:
            if step_id == "step-1":
                return step_with_bindings
            if step_id == "raw-step-id":
                return source_step
            return None

        store.get_step.side_effect = get_step_side_effect
        store.get_step_by_node_key.return_value = None  # node key fails

        svc = StepDataFlowService(store)
        result = svc.resolve_inputs("task-1", "step-1")
        assert result == {"y": "art-fallback"}

    def test_skips_when_all_resolution_fails(self) -> None:
        store = MagicMock()
        store.get_step.return_value = _make_step(input_bindings={"z": "unknown.output_ref"})

        def get_step_side_effect(step_id: str) -> SimpleNamespace | None:
            if step_id == "step-1":
                return _make_step(input_bindings={"z": "unknown.output_ref"})
            return None

        store.get_step.side_effect = get_step_side_effect
        store.get_step_by_node_key.return_value = None

        svc = StepDataFlowService(store)
        result = svc.resolve_inputs("task-1", "step-1")
        assert result == {}

    def test_returns_output_ref_value(self) -> None:
        store = MagicMock()
        store.get_step.return_value = _make_step(input_bindings={"data": "src.output_ref"})
        source = _make_step(output_ref="artifact-123")
        store.get_step_by_node_key.return_value = source

        svc = StepDataFlowService(store)
        result = svc.resolve_inputs("task-1", "step-1")
        assert result["data"] == "artifact-123"

    def test_skips_when_output_ref_is_none(self) -> None:
        store = MagicMock()
        store.get_step.return_value = _make_step(input_bindings={"data": "src.output_ref"})
        source = _make_step(output_ref=None)
        store.get_step_by_node_key.return_value = source

        svc = StepDataFlowService(store)
        result = svc.resolve_inputs("task-1", "step-1")
        assert result == {}

    def test_skips_non_output_ref_field(self) -> None:
        store = MagicMock()
        store.get_step.return_value = _make_step(input_bindings={"data": "src.some_other_field"})
        source = _make_step(output_ref="artifact-123")
        store.get_step_by_node_key.return_value = source

        svc = StepDataFlowService(store)
        result = svc.resolve_inputs("task-1", "step-1")
        assert result == {}

    def test_handles_multiple_bindings(self) -> None:
        store = MagicMock()
        step = _make_step(
            input_bindings={
                "good": "src1.output_ref",
                "bad_no_dot": "invalid",
                "good2": "src2.output_ref",
                "no_source": "missing.output_ref",
            }
        )
        src1 = _make_step(output_ref="art-1")
        src2 = _make_step(output_ref="art-2")

        def get_step_side_effect(step_id: str) -> SimpleNamespace | None:
            if step_id == "step-1":
                return step
            return None

        store.get_step.side_effect = get_step_side_effect

        def node_key_side_effect(task_id: str, key: str) -> SimpleNamespace | None:
            if key == "src1":
                return src1
            if key == "src2":
                return src2
            return None

        store.get_step_by_node_key.side_effect = node_key_side_effect

        svc = StepDataFlowService(store)
        result = svc.resolve_inputs("task-1", "step-1")
        assert result == {"good": "art-1", "good2": "art-2"}


# ---------------------------------------------------------------------------
# TestInjectResolvedInputs
# ---------------------------------------------------------------------------


class TestInjectResolvedInputs:
    def test_noop_when_resolved_empty(self) -> None:
        store = MagicMock()
        svc = StepDataFlowService(store)
        svc.inject_resolved_inputs("sa-1", {})
        store.get_step_attempt.assert_not_called()

    def test_noop_when_attempt_not_found(self) -> None:
        store = MagicMock()
        store.get_step_attempt.return_value = None
        svc = StepDataFlowService(store)
        svc.inject_resolved_inputs("sa-1", {"x": "art-1"})
        store.update_step_attempt.assert_not_called()

    def test_adds_resolved_inputs_to_context(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(context={})
        store.get_step_attempt.return_value = attempt
        svc = StepDataFlowService(store)

        svc.inject_resolved_inputs("sa-1", {"data": "artifact-1"})

        store.update_step_attempt.assert_called_once()
        call_kwargs = store.update_step_attempt.call_args
        ctx = call_kwargs[1].get("context") or call_kwargs[0][1]
        assert ctx["resolved_inputs"] == {"data": "artifact-1"}

    def test_preserves_existing_context(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(context={"existing_key": "existing_value"})
        store.get_step_attempt.return_value = attempt
        svc = StepDataFlowService(store)

        svc.inject_resolved_inputs("sa-1", {"data": "art-1"})

        call_kwargs = store.update_step_attempt.call_args
        ctx = call_kwargs[1].get("context") or call_kwargs[0][1]
        assert ctx["existing_key"] == "existing_value"
        assert ctx["resolved_inputs"] == {"data": "art-1"}

    def test_handles_none_context(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(context=None)
        store.get_step_attempt.return_value = attempt
        svc = StepDataFlowService(store)

        svc.inject_resolved_inputs("sa-1", {"data": "art-1"})

        store.update_step_attempt.assert_called_once()
        call_kwargs = store.update_step_attempt.call_args
        ctx = call_kwargs[1].get("context") or call_kwargs[0][1]
        assert ctx["resolved_inputs"] == {"data": "art-1"}
