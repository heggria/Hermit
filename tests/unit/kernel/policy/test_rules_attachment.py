"""Tests for kernel/policy/guards/rules_attachment.py — attachment ingest rules."""

from __future__ import annotations

import pytest

from hermit.kernel.policy.guards.rules_attachment import evaluate_attachment_rules
from hermit.kernel.policy.models.models import ActionRequest


def _make_request(
    action_class: str = "attachment_ingest",
    actor_kind: str = "adapter",
    actor_id: str = "feishu_adapter",
    risk_hint: str = "medium",
) -> ActionRequest:
    return ActionRequest(
        request_id="req-test-001",
        action_class=action_class,
        risk_hint=risk_hint,
        actor={"kind": actor_kind, "agent_id": actor_id},
    )


class TestNonAttachmentIngestReturnsNone:
    @pytest.mark.parametrize(
        "action_class",
        ["read_local", "write_local", "execute_command", "unknown", ""],
    )
    def test_non_attachment_returns_none(self, action_class: str) -> None:
        result = evaluate_attachment_rules(_make_request(action_class=action_class))
        assert result is None


class TestAdapterOwnedAttachmentIngest:
    def test_feishu_adapter_allowed_with_receipt(self) -> None:
        result = evaluate_attachment_rules(_make_request())
        assert result is not None
        assert len(result) == 1
        assert result[0].verdict == "allow_with_receipt"

    def test_feishu_adapter_reason_code(self) -> None:
        result = evaluate_attachment_rules(_make_request())
        assert result is not None
        assert result[0].reasons[0].code == "attachment_ingest_adapter"

    def test_feishu_adapter_requires_receipt(self) -> None:
        result = evaluate_attachment_rules(_make_request())
        assert result is not None
        assert result[0].obligations.require_receipt is True

    def test_feishu_adapter_risk_level_from_hint(self) -> None:
        result = evaluate_attachment_rules(_make_request(risk_hint="low"))
        assert result is not None
        assert result[0].risk_level == "low"

    def test_feishu_adapter_risk_level_defaults_to_medium(self) -> None:
        result = evaluate_attachment_rules(_make_request(risk_hint=""))
        assert result is not None
        assert result[0].risk_level == "medium"


class TestNonAdapterAttachmentIngest:
    def test_non_adapter_denied(self) -> None:
        result = evaluate_attachment_rules(_make_request(actor_kind="agent", actor_id="hermit"))
        assert result is not None
        assert len(result) == 1
        assert result[0].verdict == "deny"

    def test_non_adapter_reason_code(self) -> None:
        result = evaluate_attachment_rules(_make_request(actor_kind="agent", actor_id="hermit"))
        assert result is not None
        assert result[0].reasons[0].code == "attachment_ingest_denied"

    def test_non_adapter_error_severity(self) -> None:
        result = evaluate_attachment_rules(_make_request(actor_kind="agent", actor_id="hermit"))
        assert result is not None
        assert result[0].reasons[0].severity == "error"

    def test_non_adapter_no_receipt_required(self) -> None:
        result = evaluate_attachment_rules(_make_request(actor_kind="agent", actor_id="hermit"))
        assert result is not None
        assert result[0].obligations.require_receipt is False

    def test_non_adapter_risk_level_defaults_to_high(self) -> None:
        result = evaluate_attachment_rules(
            _make_request(actor_kind="agent", actor_id="hermit", risk_hint="")
        )
        assert result is not None
        assert result[0].risk_level == "high"

    def test_wrong_adapter_id_denied(self) -> None:
        result = evaluate_attachment_rules(
            _make_request(actor_kind="adapter", actor_id="other_adapter")
        )
        assert result is not None
        assert result[0].verdict == "deny"


class TestEdgeCasesActorFields:
    def test_empty_actor_kind(self) -> None:
        req = ActionRequest(
            request_id="req-test",
            action_class="attachment_ingest",
            actor={"kind": "", "agent_id": "feishu_adapter"},
        )
        result = evaluate_attachment_rules(req)
        assert result is not None
        assert result[0].verdict == "deny"

    def test_missing_actor_kind_key(self) -> None:
        req = ActionRequest(
            request_id="req-test",
            action_class="attachment_ingest",
            actor={"agent_id": "feishu_adapter"},
        )
        result = evaluate_attachment_rules(req)
        assert result is not None
        assert result[0].verdict == "deny"

    def test_missing_actor_agent_id_key(self) -> None:
        req = ActionRequest(
            request_id="req-test",
            action_class="attachment_ingest",
            actor={"kind": "adapter"},
        )
        result = evaluate_attachment_rules(req)
        assert result is not None
        assert result[0].verdict == "deny"
