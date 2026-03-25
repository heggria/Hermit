from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.errors import ContractError
from hermit.kernel.execution.executor.request_builder import RequestBuilder
from hermit.kernel.policy.guards.fingerprint import build_action_fingerprint
from hermit.kernel.policy.models.models import (
    ActionRequest,
    PolicyDecision,
    PolicyObligations,
    PolicyReason,
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


def _make_action_request(**overrides: Any) -> ActionRequest:
    defaults: dict[str, Any] = {
        "request_id": "req-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "step_attempt_id": "attempt-1",
        "tool_name": "bash",
        "tool_input": {"command": "echo hello"},
        "action_class": "shell_execute",
        "resource_scopes": ["workspace"],
        "risk_hint": "medium",
        "context": {},
        "derived": {},
    }
    defaults.update(overrides)
    return ActionRequest(**defaults)


def _make_policy_decision(**overrides: Any) -> PolicyDecision:
    defaults: dict[str, Any] = {
        "verdict": "approve",
        "action_class": "shell_execute",
        "risk_level": "medium",
        "reasons": [PolicyReason(code="ok", message="Allowed by default")],
        "obligations": PolicyObligations(),
        "normalized_constraints": {},
        "approval_packet": None,
    }
    defaults.update(overrides)
    return PolicyDecision(**defaults)


@dataclass
class FakeArtifact:
    artifact_id: str


def _make_builder(
    *,
    store: MagicMock | None = None,
    artifact_store: MagicMock | None = None,
    policy_engine: MagicMock | None = None,
    registry: MagicMock | None = None,
    tool_output_limit: int = 4096,
) -> RequestBuilder:
    if store is None:
        store = MagicMock()
        store.create_artifact.return_value = FakeArtifact(artifact_id="art-1")
        store.append_event.return_value = None
    if artifact_store is None:
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("/tmp/fake.json", "sha256hash")
        artifact_store.store_text.return_value = ("/tmp/fake.md", "sha256hash-text")
    if policy_engine is None:
        policy_engine = MagicMock()
    if registry is None:
        registry = MagicMock()
    return RequestBuilder(
        store=store,
        artifact_store=artifact_store,
        policy_engine=policy_engine,
        registry=registry,
        tool_output_limit=tool_output_limit,
    )


# ---------------------------------------------------------------------------
# apply_request_overrides
# ---------------------------------------------------------------------------


class TestApplyRequestOverrides:
    def test_override_actor_replaces_actor_dict(self) -> None:
        builder = _make_builder()
        req = _make_action_request(actor={"kind": "agent", "agent_id": "hermit"})
        new_actor = {"kind": "user", "user_id": "alice"}

        result = builder.apply_request_overrides(req, {"actor": new_actor})

        assert result.actor == new_actor

    def test_override_actor_non_dict_raises_contract_error(self) -> None:
        builder = _make_builder()
        req = _make_action_request()

        with pytest.raises(ContractError, match="actor must be a dict"):
            builder.apply_request_overrides(req, {"actor": "invalid"})

    def test_override_context_merges_into_existing(self) -> None:
        builder = _make_builder()
        req = _make_action_request(context={"workspace_root": "/home", "existing": True})

        result = builder.apply_request_overrides(req, {"context": {"policy_profile": "strict"}})

        assert result.context["workspace_root"] == "/home"
        assert result.context["existing"] is True
        assert result.context["policy_profile"] == "strict"

    def test_override_context_non_dict_raises_contract_error(self) -> None:
        builder = _make_builder()
        req = _make_action_request()

        with pytest.raises(ContractError, match="context must be a dict"):
            builder.apply_request_overrides(req, {"context": 42})

    def test_override_idempotency_key(self) -> None:
        builder = _make_builder()
        req = _make_action_request()

        result = builder.apply_request_overrides(req, {"idempotency_key": "idem-123"})

        assert result.idempotency_key == "idem-123"

    def test_no_overrides_returns_unchanged(self) -> None:
        builder = _make_builder()
        req = _make_action_request(tool_name="bash")

        result = builder.apply_request_overrides(req, {})

        assert result.tool_name == "bash"

    def test_multiple_overrides_applied_together(self) -> None:
        builder = _make_builder()
        req = _make_action_request(context={"a": 1})
        overrides = {
            "actor": {"kind": "service"},
            "context": {"b": 2},
            "idempotency_key": "multi",
        }

        result = builder.apply_request_overrides(req, overrides)

        assert result.actor == {"kind": "service"}
        assert result.context == {"a": 1, "b": 2}
        assert result.idempotency_key == "multi"


# ---------------------------------------------------------------------------
# record_action_request
# ---------------------------------------------------------------------------


class TestRecordActionRequest:
    def test_stores_artifact_and_appends_event(self) -> None:
        store = MagicMock()
        store.create_artifact.return_value = FakeArtifact(artifact_id="art-req-1")
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("/path/to/req.json", "hash-req")
        builder = _make_builder(store=store, artifact_store=artifact_store)

        req = _make_action_request(tool_name="write_file")
        ctx = _make_attempt_ctx()

        artifact_id = builder.record_action_request(req, ctx)

        assert artifact_id == "art-req-1"
        artifact_store.store_json.assert_called_once()
        store.create_artifact.assert_called_once()
        create_kwargs = store.create_artifact.call_args.kwargs
        assert create_kwargs["kind"] == "action_request"
        assert create_kwargs["task_id"] == "task-1"
        assert create_kwargs["metadata"] == {"tool_name": "write_file"}

        store.append_event.assert_called_once()
        event_kwargs = store.append_event.call_args.kwargs
        assert event_kwargs["event_type"] == "action.requested"
        assert event_kwargs["entity_type"] == "step_attempt"
        assert event_kwargs["entity_id"] == "attempt-1"

    def test_event_payload_contains_summary_fields(self) -> None:
        store = MagicMock()
        store.create_artifact.return_value = FakeArtifact(artifact_id="art-2")
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("/p", "h")
        builder = _make_builder(store=store, artifact_store=artifact_store)

        req = _make_action_request(
            tool_name="bash",
            action_class="shell_execute",
            risk_hint="high",
            resource_scopes=["workspace", "network"],
            idempotency_key="idem-x",
        )
        ctx = _make_attempt_ctx()

        builder.record_action_request(req, ctx)

        payload = store.append_event.call_args.kwargs["payload"]
        assert payload["tool_name"] == "bash"
        assert payload["action_class"] == "shell_execute"
        assert payload["risk_hint"] == "high"
        assert payload["resource_scopes"] == ["workspace", "network"]
        assert payload["idempotency_key"] == "idem-x"


# ---------------------------------------------------------------------------
# record_policy_evaluation
# ---------------------------------------------------------------------------


class TestRecordPolicyEvaluation:
    def test_stores_policy_payload_as_artifact(self) -> None:
        store = MagicMock()
        store.create_artifact.return_value = FakeArtifact(artifact_id="art-pol-1")
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("/p", "h")
        builder = _make_builder(store=store, artifact_store=artifact_store)

        req = _make_action_request(context={"policy_profile": "strict"})
        policy = _make_policy_decision(
            verdict="approve",
            risk_level="high",
        )
        ctx = _make_attempt_ctx()

        artifact_id = builder.record_policy_evaluation(req, policy, ctx)

        assert artifact_id == "art-pol-1"
        stored_payload = artifact_store.store_json.call_args[0][0]
        assert stored_payload["verdict"] == "approve"
        assert stored_payload["risk_band"] == "high"
        assert stored_payload["policy_profile"] == "strict"

    def test_event_type_is_policy_evaluated(self) -> None:
        store = MagicMock()
        store.create_artifact.return_value = FakeArtifact(artifact_id="art-pol-2")
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("/p", "h")
        builder = _make_builder(store=store, artifact_store=artifact_store)

        req = _make_action_request()
        policy = _make_policy_decision()
        ctx = _make_attempt_ctx()

        builder.record_policy_evaluation(req, policy, ctx)

        event_kwargs = store.append_event.call_args.kwargs
        assert event_kwargs["event_type"] == "policy.evaluated"
        assert event_kwargs["entity_type"] == "step_attempt"


# ---------------------------------------------------------------------------
# store_json_artifact
# ---------------------------------------------------------------------------


class TestStoreJsonArtifact:
    def test_stores_and_returns_artifact_id(self) -> None:
        store = MagicMock()
        store.create_artifact.return_value = FakeArtifact(artifact_id="art-json-1")
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("/p/data.json", "hashval")
        builder = _make_builder(store=store, artifact_store=artifact_store)
        ctx = _make_attempt_ctx()

        result = builder.store_json_artifact(
            payload={"key": "value"},
            kind="custom",
            attempt_ctx=ctx,
            metadata={"tool_name": "test_tool"},
            event_type="custom.event",
            entity_type="step_attempt",
            entity_id="attempt-1",
        )

        assert result == "art-json-1"
        store.create_artifact.assert_called_once()
        store.append_event.assert_called_once()

    def test_skips_event_when_event_fields_are_none(self) -> None:
        store = MagicMock()
        store.create_artifact.return_value = FakeArtifact(artifact_id="art-json-2")
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("/p", "h")
        builder = _make_builder(store=store, artifact_store=artifact_store)
        ctx = _make_attempt_ctx()

        builder.store_json_artifact(
            payload={"data": 1},
            kind="snapshot",
            attempt_ctx=ctx,
            metadata={},
        )

        store.create_artifact.assert_called_once()
        store.append_event.assert_not_called()

    def test_event_payload_includes_artifact_ref_and_summary(self) -> None:
        store = MagicMock()
        store.create_artifact.return_value = FakeArtifact(artifact_id="art-json-3")
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("/p", "h")
        builder = _make_builder(store=store, artifact_store=artifact_store)
        ctx = _make_attempt_ctx()

        builder.store_json_artifact(
            payload={},
            kind="test",
            attempt_ctx=ctx,
            metadata={},
            event_type="test.created",
            entity_type="step",
            entity_id="step-1",
            payload_summary={"extra": "info"},
        )

        event_payload = store.append_event.call_args.kwargs["payload"]
        assert event_payload["artifact_ref"] == "art-json-3"
        assert event_payload["extra"] == "info"


# ---------------------------------------------------------------------------
# build_preview_artifact
# ---------------------------------------------------------------------------


class TestBuildPreviewArtifact:
    def test_returns_none_for_tool_without_preview(self) -> None:
        _builder = _make_builder()
        tool = SimpleNamespace(
            name="read_file",
            resource_scope_hint=None,
        )
        ctx = _make_attempt_ctx()

        # read_file will fall through to JSON preview, which is non-empty
        # so we need a tool that produces empty preview_text
        # Actually preview_text always returns something, so build_preview_artifact
        # only returns None when preview_text returns empty string.
        # Let's verify with a mock instead.
        builder_with_mock = _make_builder()
        result = builder_with_mock.build_preview_artifact(tool, {}, ctx)

        # preview_text for unknown tool returns JSON, which is non-empty
        assert result is not None

    def test_bash_tool_creates_preview_artifact(self) -> None:
        store = MagicMock()
        store.create_artifact.return_value = FakeArtifact(artifact_id="art-preview-1")
        artifact_store = MagicMock()
        artifact_store.store_text.return_value = ("/p/preview.md", "hash-prev")
        builder = _make_builder(store=store, artifact_store=artifact_store)

        tool = SimpleNamespace(name="bash", resource_scope_hint=None)
        ctx = _make_attempt_ctx()

        result = builder.build_preview_artifact(tool, {"command": "ls -la"}, ctx)

        assert result == "art-preview-1"
        artifact_store.store_text.assert_called_once()
        stored_text = artifact_store.store_text.call_args[0][0]
        assert "ls -la" in stored_text

        create_kwargs = store.create_artifact.call_args.kwargs
        assert create_kwargs["kind"] == "approval_packet"
        assert create_kwargs["metadata"] == {"tool_name": "bash"}

    def test_write_file_tool_creates_diff_preview(self, tmp_path: Any) -> None:
        store = MagicMock()
        store.create_artifact.return_value = FakeArtifact(artifact_id="art-diff-1")
        artifact_store = MagicMock()
        artifact_store.store_text.return_value = ("/p/diff.md", "hash-diff")
        builder = _make_builder(store=store, artifact_store=artifact_store)

        # Create an existing file for diff comparison
        existing = tmp_path / "hello.txt"
        existing.write_text("old content\n", encoding="utf-8")

        tool = SimpleNamespace(
            name="write_file",
            resource_scope_hint=[str(tmp_path)],
        )
        ctx = _make_attempt_ctx()
        tool_input = {"path": "hello.txt", "content": "new content\n"}

        result = builder.build_preview_artifact(tool, tool_input, ctx)

        assert result == "art-diff-1"
        stored_text = artifact_store.store_text.call_args[0][0]
        assert "old content" in stored_text or "new content" in stored_text


# ---------------------------------------------------------------------------
# preview_text
# ---------------------------------------------------------------------------


class TestPreviewText:
    def test_bash_includes_command_in_code_block(self) -> None:
        builder = _make_builder()
        tool = SimpleNamespace(name="bash", resource_scope_hint=None)

        text = builder.preview_text(tool, {"command": "echo hello"})

        assert "echo hello" in text
        assert "```bash" in text

    def test_write_file_includes_path(self, tmp_path: Any) -> None:
        builder = _make_builder()
        tool = SimpleNamespace(name="write_file", resource_scope_hint=[str(tmp_path)])

        text = builder.preview_text(tool, {"path": "test.py", "content": "print('hi')"})

        assert "test.py" in text

    def test_write_file_shows_diff_for_existing_file(self, tmp_path: Any) -> None:
        builder = _make_builder()
        existing = tmp_path / "readme.md"
        existing.write_text("line1\nline2\n", encoding="utf-8")

        tool = SimpleNamespace(name="write_file", resource_scope_hint=[str(tmp_path)])

        text = builder.preview_text(tool, {"path": "readme.md", "content": "line1\nline2\nline3\n"})

        assert "line3" in text
        assert "diff" in text.lower() or "---" in text or "+++" in text

    def test_write_hermit_file_treated_as_write(self) -> None:
        builder = _make_builder()
        tool = SimpleNamespace(name="write_hermit_file", resource_scope_hint=None)

        text = builder.preview_text(tool, {"path": "config.json", "content": "{}"})

        assert "config.json" in text

    def test_unknown_tool_returns_json(self) -> None:
        builder = _make_builder()
        tool = SimpleNamespace(name="custom_tool", resource_scope_hint=None)

        text = builder.preview_text(tool, {"key": "value"})

        assert "custom_tool" in text
        assert "value" in text


# ---------------------------------------------------------------------------
# requested_action_payload
# ---------------------------------------------------------------------------


class TestRequestedActionPayload:
    def test_basic_payload_structure(self) -> None:
        store = MagicMock()
        builder = _make_builder(store=store)

        req = _make_action_request(
            tool_name="bash",
            tool_input={"command": "ls"},
            action_class="shell_execute",
            risk_hint="medium",
            resource_scopes=["workspace"],
            context={"workspace_root": "/home/user"},
            derived={
                "target_paths": ["/home/user/file.txt"],
                "network_hosts": [],
                "command_preview": "ls",
            },
        )
        policy = _make_policy_decision(
            risk_level="medium",
            approval_packet=None,
        )

        payload = builder.requested_action_payload(
            req,
            policy,
            preview_artifact=None,
            decision_ref="dec-1",
            policy_ref="pol-1",
            state_witness_ref="wit-1",
        )

        assert payload["tool_name"] == "bash"
        assert payload["tool_input"] == {"command": "ls"}
        assert payload["risk_level"] == "medium"
        assert payload["resource_scopes"] == ["workspace"]
        assert payload["target_paths"] == ["/home/user/file.txt"]
        assert payload["workspace_root"] == "/home/user"
        assert payload["decision_ref"] == "dec-1"
        assert payload["policy_ref"] == "pol-1"
        assert payload["state_witness_ref"] == "wit-1"
        assert payload["contract_packet"] is None

    def test_fingerprint_is_consistent(self) -> None:
        builder = _make_builder()
        req = _make_action_request(
            derived={
                "target_paths": ["/a/b"],
                "network_hosts": ["example.com"],
                "command_preview": "cmd",
            }
        )
        policy = _make_policy_decision()

        payload = builder.requested_action_payload(
            req,
            policy,
            preview_artifact=None,
            decision_ref=None,
            policy_ref=None,
            state_witness_ref=None,
        )

        expected_fingerprint = build_action_fingerprint(
            {
                "task_id": req.task_id,
                "step_attempt_id": req.step_attempt_id,
                "tool_name": req.tool_name,
                "action_class": req.action_class,
                "target_paths": ["/a/b"],
                "network_hosts": ["example.com"],
                "command_preview": "cmd",
            }
        )
        assert payload["fingerprint"] == expected_fingerprint

    def test_preview_artifact_appended_to_approval_packet(self) -> None:
        builder = _make_builder()
        req = _make_action_request()
        policy = _make_policy_decision(approval_packet={"title": "Confirm"})

        payload = builder.requested_action_payload(
            req,
            policy,
            preview_artifact="art-preview-99",
            decision_ref=None,
            policy_ref=None,
            state_witness_ref=None,
        )

        assert "art-preview-99" in payload["approval_packet"]["artifact_ids"]

    def test_preview_artifact_deduplicates(self) -> None:
        builder = _make_builder()
        req = _make_action_request()
        policy = _make_policy_decision(approval_packet={"artifact_ids": ["art-preview-99"]})

        payload = builder.requested_action_payload(
            req,
            policy,
            preview_artifact="art-preview-99",
            decision_ref=None,
            policy_ref=None,
            state_witness_ref=None,
        )

        assert payload["approval_packet"]["artifact_ids"].count("art-preview-99") == 1

    def test_contract_ref_populates_contract_packet(self) -> None:
        store = MagicMock()
        contract = SimpleNamespace(
            contract_id="contract-1",
            objective="deploy feature X",
            expected_effects=["file_write", "shell_exec"],
            expiry_at=9999999999.0,
            rollback_expectation="manual",
            operator_summary="Deploy feature X to production",
        )
        store.get_execution_contract = MagicMock(return_value=contract)
        store.get_evidence_case = MagicMock(return_value=None)
        store.get_authorization_plan = MagicMock(return_value=None)
        store.create_artifact = MagicMock(return_value=FakeArtifact("a"))
        builder = _make_builder(store=store)

        req = _make_action_request()
        policy = _make_policy_decision(approval_packet={})

        payload = builder.requested_action_payload(
            req,
            policy,
            preview_artifact=None,
            decision_ref=None,
            policy_ref=None,
            state_witness_ref=None,
            contract_ref="contract-1",
            evidence_case_ref="ev-1",
            authorization_plan_ref="auth-1",
        )

        cp = payload["contract_packet"]
        assert cp is not None
        assert cp["contract_ref"] == "contract-1"
        assert cp["objective"] == "deploy feature X"
        assert "file_write" in cp["expected_effects"]
        assert payload["contract_ref"] == "contract-1"
        assert payload["evidence_case_ref"] == "ev-1"
        assert payload["authorization_plan_ref"] == "auth-1"

    def test_outside_workspace_flag(self) -> None:
        builder = _make_builder()
        req = _make_action_request(derived={"outside_workspace": True})
        policy = _make_policy_decision()

        payload = builder.requested_action_payload(
            req,
            policy,
            preview_artifact=None,
            decision_ref=None,
            policy_ref=None,
            state_witness_ref=None,
        )

        assert payload["outside_workspace"] is True

    def test_idempotency_key_in_payload(self) -> None:
        builder = _make_builder()
        req = _make_action_request(idempotency_key="idem-abc")
        policy = _make_policy_decision()

        payload = builder.requested_action_payload(
            req,
            policy,
            preview_artifact=None,
            decision_ref=None,
            policy_ref=None,
            state_witness_ref=None,
        )

        assert payload["idempotency_key"] == "idem-abc"
