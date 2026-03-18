"""Tests for the SteeringDirective model, store methods, and SteeringProtocol."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.signals.models import EvidenceSignal, SteeringDirective
from hermit.kernel.signals.steering import SteeringProtocol


@pytest.fixture()
def store() -> KernelStore:
    return KernelStore(Path(":memory:"))


@pytest.fixture()
def protocol(store: KernelStore) -> SteeringProtocol:
    return SteeringProtocol(store)


def _make_directive(**overrides: object) -> SteeringDirective:
    defaults: dict = dict(
        task_id="task_001",
        steering_type="scope",
        directive="Focus on error handling first",
        evidence_refs=["artifact://review/pr-42"],
        issued_by="operator",
    )
    defaults.update(overrides)
    return SteeringDirective(**defaults)


class TestSteeringDirectiveModel:
    def test_auto_id_and_timestamp(self) -> None:
        sd = SteeringDirective(task_id="t1", steering_type="scope", directive="do X")
        assert sd.directive_id.startswith("sig_steer_")
        assert sd.created_at > 0

    def test_explicit_id_preserved(self) -> None:
        sd = SteeringDirective(
            directive_id="custom_steer", task_id="t1", steering_type="scope", directive="do X"
        )
        assert sd.directive_id == "custom_steer"

    def test_to_signal_roundtrip(self) -> None:
        sd = _make_directive()
        sig = sd.to_signal()
        assert sig.signal_id == sd.directive_id
        assert sig.source_kind == "steering:scope"
        assert sig.task_id == "task_001"
        assert sig.summary == sd.directive
        assert sig.suggested_goal == sd.directive
        assert sig.confidence == 1.0
        assert sig.metadata["issued_by"] == "operator"
        assert sig.metadata["steering_type"] == "scope"

    def test_from_signal_roundtrip(self) -> None:
        original = _make_directive(metadata={"custom_key": "val"})
        sig = original.to_signal()
        reconstructed = SteeringDirective.from_signal(sig)
        assert reconstructed.directive_id == original.directive_id
        assert reconstructed.task_id == original.task_id
        assert reconstructed.steering_type == "scope"
        assert reconstructed.directive == original.directive
        assert reconstructed.evidence_refs == original.evidence_refs
        assert reconstructed.issued_by == "operator"
        assert reconstructed.disposition == "pending"
        assert reconstructed.metadata.get("custom_key") == "val"

    def test_from_signal_extracts_steering_type_from_source_kind(self) -> None:
        sig = EvidenceSignal(
            signal_id="sig_x",
            source_kind="steering:constraint",
            source_ref="task://t1",
            task_id="t1",
            summary="limit scope",
            metadata={},
        )
        sd = SteeringDirective.from_signal(sig)
        assert sd.steering_type == "constraint"


class TestSteeringStore:
    def test_create_and_list(self, store: KernelStore) -> None:
        sd = _make_directive()
        store.create_steering(sd)
        fetched = store.get_signal(sd.directive_id)
        assert fetched is not None
        assert fetched.source_kind == "steering:scope"

    def test_list_steerings_for_task(self, store: KernelStore) -> None:
        store.create_steering(_make_directive(task_id="t1"))
        store.create_steering(_make_directive(task_id="t2"))
        results = store.list_steerings_for_task("t1")
        assert len(results) == 1
        assert results[0].task_id == "t1"

    def test_list_steerings_filter_disposition(self, store: KernelStore) -> None:
        sd1 = _make_directive(task_id="t1")
        sd2 = _make_directive(task_id="t1")
        store.create_steering(sd1)
        store.create_steering(sd2)
        store.update_steering_disposition(sd1.directive_id, "applied")
        pending = store.list_steerings_for_task("t1", disposition="pending")
        assert len(pending) == 1
        assert pending[0].directive_id == sd2.directive_id

    def test_active_steerings_for_task(self, store: KernelStore) -> None:
        sd1 = _make_directive(task_id="t1")
        sd2 = _make_directive(task_id="t1")
        sd3 = _make_directive(task_id="t1")
        store.create_steering(sd1)
        store.create_steering(sd2)
        store.create_steering(sd3)
        store.update_steering_disposition(sd2.directive_id, "rejected")
        active = store.active_steerings_for_task("t1")
        assert len(active) == 2
        active_ids = {d.directive_id for d in active}
        assert sd1.directive_id in active_ids
        assert sd3.directive_id in active_ids

    def test_update_steering_disposition_with_applied_at(self, store: KernelStore) -> None:
        sd = _make_directive()
        store.create_steering(sd)
        store.update_steering_disposition(sd.directive_id, "applied", applied_at=12345.0)
        fetched = store.get_signal(sd.directive_id)
        assert fetched is not None
        assert fetched.disposition == "applied"
        meta = fetched.metadata
        assert meta.get("applied_at") == 12345.0


class TestSteeringProtocol:
    def test_issue(self, protocol: SteeringProtocol, store: KernelStore) -> None:
        sd = _make_directive()
        result = protocol.issue(sd)
        assert result.directive_id == sd.directive_id
        fetched = store.get_signal(sd.directive_id)
        assert fetched is not None
        events = store.list_events(task_id="task_001", limit=10)
        event_types = [e["event_type"] for e in events]
        assert "steering.issued" in event_types

    def test_acknowledge(self, protocol: SteeringProtocol, store: KernelStore) -> None:
        sd = _make_directive()
        protocol.issue(sd)
        protocol.acknowledge(sd.directive_id)
        fetched = store.get_signal(sd.directive_id)
        assert fetched is not None
        assert fetched.disposition == "acknowledged"
        events = store.list_events(task_id="task_001", limit=20)
        event_types = [e["event_type"] for e in events]
        assert "steering.acknowledged" in event_types

    def test_apply(self, protocol: SteeringProtocol, store: KernelStore) -> None:
        sd = _make_directive()
        protocol.issue(sd)
        protocol.apply(sd.directive_id)
        fetched = store.get_signal(sd.directive_id)
        assert fetched is not None
        assert fetched.disposition == "applied"
        assert fetched.metadata.get("applied_at") is not None
        events = store.list_events(task_id="task_001", limit=20)
        event_types = [e["event_type"] for e in events]
        assert "steering.applied" in event_types

    def test_reject(self, protocol: SteeringProtocol, store: KernelStore) -> None:
        sd = _make_directive()
        protocol.issue(sd)
        protocol.reject(sd.directive_id, reason="not relevant")
        fetched = store.get_signal(sd.directive_id)
        assert fetched is not None
        assert fetched.disposition == "rejected"
        events = store.list_events(task_id="task_001", limit=20)
        event_types = [e["event_type"] for e in events]
        assert "steering.rejected" in event_types

    def test_supersede(self, protocol: SteeringProtocol, store: KernelStore) -> None:
        old = _make_directive()
        protocol.issue(old)
        new = _make_directive(directive="Updated direction")
        result = protocol.supersede(old.directive_id, new)
        assert result.supersedes_id == old.directive_id
        old_fetched = store.get_signal(old.directive_id)
        assert old_fetched is not None
        assert old_fetched.disposition == "superseded"
        new_fetched = store.get_signal(new.directive_id)
        assert new_fetched is not None
        assert new_fetched.disposition == "pending"
        events = store.list_events(task_id="task_001", limit=30)
        event_types = [e["event_type"] for e in events]
        assert "steering.superseded" in event_types

    def test_active_for_task(self, protocol: SteeringProtocol, store: KernelStore) -> None:
        sd1 = _make_directive(task_id="t1")
        sd2 = _make_directive(task_id="t1")
        protocol.issue(sd1)
        protocol.issue(sd2)
        protocol.reject(sd2.directive_id, reason="wrong")
        active = protocol.active_for_task("t1")
        assert len(active) == 1
        assert active[0].directive_id == sd1.directive_id


class TestSteeringContextIntegration:
    def test_active_steerings_in_context_pack(self, store: KernelStore) -> None:
        """Verify that active steerings are retrievable for context compilation."""
        protocol = SteeringProtocol(store)
        sd1 = _make_directive(task_id="t1", steering_type="scope", directive="focus on auth")
        sd2 = _make_directive(task_id="t1", steering_type="constraint", directive="no DB changes")
        sd3 = _make_directive(task_id="t1", steering_type="priority", directive="security first")
        protocol.issue(sd1)
        protocol.issue(sd2)
        protocol.issue(sd3)
        protocol.apply(sd1.directive_id)

        active = store.active_steerings_for_task("t1")
        assert len(active) == 3
        steerings_payload = [
            {
                "directive_id": d.directive_id,
                "steering_type": d.steering_type,
                "directive": d.directive,
                "disposition": d.disposition,
                "issued_by": d.issued_by,
                "created_at": d.created_at,
            }
            for d in active
        ]
        assert len(steerings_payload) == 3
        types = {s["steering_type"] for s in steerings_payload}
        assert types == {"scope", "constraint", "priority"}


class TestIngressAutoUpgrade:
    """Gap 1: /steer prefix in append_note auto-creates a SteeringDirective."""

    def test_steer_prefix_creates_directive(self, store: KernelStore) -> None:
        from hermit.kernel.task.services.controller import TaskController

        ctrl = TaskController(store)
        task_ctx = ctrl.start_task(
            conversation_id="conv1",
            goal="test task",
            source_channel="cli",
            kind="respond",
        )
        ctrl.append_note(
            task_id=task_ctx.task_id,
            source_channel="cli",
            raw_text="/steer focus on the API layer only",
            prompt="/steer focus on the API layer only",
        )
        directives = store.active_steerings_for_task(task_ctx.task_id)
        assert len(directives) == 1
        assert directives[0].steering_type == "scope"
        assert directives[0].directive == "focus on the API layer only"

    def test_steer_prefix_with_type(self, store: KernelStore) -> None:
        from hermit.kernel.task.services.controller import TaskController

        ctrl = TaskController(store)
        task_ctx = ctrl.start_task(
            conversation_id="conv2",
            goal="test task",
            source_channel="cli",
            kind="respond",
        )
        ctrl.append_note(
            task_id=task_ctx.task_id,
            source_channel="cli",
            raw_text="/steer --type constraint no database migrations",
            prompt="/steer --type constraint no database migrations",
        )
        directives = store.active_steerings_for_task(task_ctx.task_id)
        assert len(directives) == 1
        assert directives[0].steering_type == "constraint"
        assert directives[0].directive == "no database migrations"

    def test_regular_note_does_not_create_directive(self, store: KernelStore) -> None:
        from hermit.kernel.task.services.controller import TaskController

        ctrl = TaskController(store)
        task_ctx = ctrl.start_task(
            conversation_id="conv3",
            goal="test task",
            source_channel="cli",
            kind="respond",
        )
        ctrl.append_note(
            task_id=task_ctx.task_id,
            source_channel="cli",
            raw_text="just a regular note",
            prompt="just a regular note",
        )
        directives = store.active_steerings_for_task(task_ctx.task_id)
        assert len(directives) == 0

    def test_steer_prefix_also_creates_note_event(self, store: KernelStore) -> None:
        """A /steer message still creates the normal note event in addition to the directive."""
        from hermit.kernel.task.services.controller import TaskController

        ctrl = TaskController(store)
        task_ctx = ctrl.start_task(
            conversation_id="conv4",
            goal="test task",
            source_channel="cli",
            kind="respond",
        )
        ctrl.append_note(
            task_id=task_ctx.task_id,
            source_channel="cli",
            raw_text="/steer focus on auth",
            prompt="/steer focus on auth",
        )
        events = store.list_events(task_id=task_ctx.task_id, limit=50)
        event_types = [e["event_type"] for e in events]
        assert "task.note.appended" in event_types
        assert "steering.issued" in event_types


class TestAutoAcknowledge:
    """Gap 2: Pending steerings are auto-acknowledged when compiled into context."""

    def test_pending_becomes_acknowledged_on_fetch(self, store: KernelStore) -> None:
        from hermit.kernel.context.injection.provider_input import ProviderInputCompiler

        protocol = SteeringProtocol(store)
        sd = _make_directive(task_id="t1")
        protocol.issue(sd)
        assert store.get_signal(sd.directive_id).disposition == "pending"

        compiler = ProviderInputCompiler(store)
        items = compiler._active_steerings("t1")
        assert len(items) == 1
        assert items[0]["disposition"] == "acknowledged"

        fetched = store.get_signal(sd.directive_id)
        assert fetched.disposition == "acknowledged"

    def test_already_acknowledged_not_changed(self, store: KernelStore) -> None:
        from hermit.kernel.context.injection.provider_input import ProviderInputCompiler

        protocol = SteeringProtocol(store)
        sd = _make_directive(task_id="t1")
        protocol.issue(sd)
        protocol.acknowledge(sd.directive_id)

        compiler = ProviderInputCompiler(store)
        items = compiler._active_steerings("t1")
        assert items[0]["disposition"] == "acknowledged"


class TestAutoApplyOnFinalize:
    """Gap 3: Acknowledged steerings are auto-applied when task completes."""

    def test_acknowledged_becomes_applied_on_finalize(self, store: KernelStore) -> None:
        from hermit.kernel.task.services.controller import TaskController

        ctrl = TaskController(store)
        task_ctx = ctrl.start_task(
            conversation_id="conv5",
            goal="test",
            source_channel="cli",
            kind="respond",
        )
        protocol = SteeringProtocol(store)
        sd = _make_directive(task_id=task_ctx.task_id)
        protocol.issue(sd)
        protocol.acknowledge(sd.directive_id)

        ctrl.finalize_result(task_ctx, status="succeeded", result_text="done")

        fetched = store.get_signal(sd.directive_id)
        assert fetched.disposition == "applied"
        assert fetched.metadata.get("applied_at") is not None

    def test_pending_not_applied_on_finalize(self, store: KernelStore) -> None:
        from hermit.kernel.task.services.controller import TaskController

        ctrl = TaskController(store)
        task_ctx = ctrl.start_task(
            conversation_id="conv6",
            goal="test",
            source_channel="cli",
            kind="respond",
        )
        protocol = SteeringProtocol(store)
        sd = _make_directive(task_id=task_ctx.task_id)
        protocol.issue(sd)
        # Do NOT acknowledge — still pending

        ctrl.finalize_result(task_ctx, status="succeeded", result_text="done")

        fetched = store.get_signal(sd.directive_id)
        assert fetched.disposition == "pending"


class TestStructuredRendering:
    """Gap 4: Active steerings render as a dedicated <steering_directives> block."""

    def test_steering_directives_block_rendered(self) -> None:
        from hermit.kernel.context.injection.provider_input import ProviderInputCompiler

        compiler = ProviderInputCompiler.__new__(ProviderInputCompiler)
        context_pack = {
            "task_summary": {},
            "step_summary": {},
            "policy_summary": {},
            "working_state": {},
            "carry_forward": None,
            "continuation_guidance": None,
            "selected_beliefs": [],
            "retrieval_memory": [],
            "relevant_artifact_refs": [],
            "ingress_artifact_refs": [],
            "focus_summary": None,
            "bound_ingress_deltas": [],
            "active_steerings": [
                {
                    "directive_id": "sig_steer_abc",
                    "steering_type": "scope",
                    "directive": "Focus on API layer",
                    "disposition": "acknowledged",
                    "issued_by": "operator",
                    "created_at": time.time(),
                },
            ],
            "session_projection_ref": None,
        }
        rendered = compiler._render_message(
            projection_payload={"summary": "test"},
            context_pack=context_pack,
            current_request="do something",
            normalized_prompt="do something",
            ingress_artifact_refs=[],
        )
        assert "<steering_directives>" in rendered
        assert "</steering_directives>" in rendered
        assert "sig_steer_abc" in rendered
        assert "Focus on API layer" in rendered
        assert "You MUST incorporate" in rendered

    def test_no_steering_block_when_empty(self) -> None:
        from hermit.kernel.context.injection.provider_input import ProviderInputCompiler

        compiler = ProviderInputCompiler.__new__(ProviderInputCompiler)
        context_pack = {
            "task_summary": {},
            "step_summary": {},
            "policy_summary": {},
            "working_state": {},
            "carry_forward": None,
            "continuation_guidance": None,
            "selected_beliefs": [],
            "retrieval_memory": [],
            "relevant_artifact_refs": [],
            "ingress_artifact_refs": [],
            "focus_summary": None,
            "bound_ingress_deltas": [],
            "active_steerings": [],
            "session_projection_ref": None,
        }
        rendered = compiler._render_message(
            projection_payload={"summary": "test"},
            context_pack=context_pack,
            current_request="do something",
            normalized_prompt="do something",
            ingress_artifact_refs=[],
        )
        assert "<steering_directives>" not in rendered
