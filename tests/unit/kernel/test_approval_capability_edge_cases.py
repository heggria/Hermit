"""Approval, capability grant, workspace lease, and policy guard edge case tests."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    s = KernelStore(tmp_path / "state.db")
    s.ensure_conversation("conv-1", source_channel="test")
    t = s.create_task(
        conversation_id="conv-1",
        title="test",
        goal="test",
        source_channel="test",
        priority="normal",
    )
    step = s.create_step(task_id=t.task_id, kind="execute", title="do stuff")
    att = s.create_step_attempt(task_id=t.task_id, step_id=step.step_id)
    s._test_ids = {
        "task_id": t.task_id,
        "step_id": step.step_id,
        "attempt_id": att.step_attempt_id,
    }
    return s


def _ids(store: KernelStore) -> dict:
    return store._test_ids


# ════════════════════════════════════════════════════════════════════════
# Task 5: Approval lifecycle
# ════════════════════════════════════════════════════════════════════════


class TestApprovalLifecycle:
    def test_create_and_approve(self, store: KernelStore) -> None:
        ids = _ids(store)
        a = store.create_approval(
            task_id=ids["task_id"],
            step_id=ids["step_id"],
            step_attempt_id=ids["attempt_id"],
            approval_type="execution_authorization",
            requested_action={"action_class": "execute_command"},
            request_packet_ref=None,
        )
        assert a.status == "pending"
        ok = store.resolve_approval(
            a.approval_id,
            status="approved",
            resolved_by="operator",
            resolution={"reason": "test"},
        )
        assert ok is True
        fetched = store.get_approval(a.approval_id)
        assert fetched.status == "approved"

    def test_create_and_deny(self, store: KernelStore) -> None:
        ids = _ids(store)
        a = store.create_approval(
            task_id=ids["task_id"],
            step_id=ids["step_id"],
            step_attempt_id=ids["attempt_id"],
            approval_type="execution_authorization",
            requested_action={"action_class": "write_file"},
            request_packet_ref=None,
        )
        ok = store.resolve_approval(
            a.approval_id,
            status="denied",
            resolved_by="operator",
            resolution={"reason": "too risky"},
        )
        assert ok is True
        fetched = store.get_approval(a.approval_id)
        assert fetched.status == "denied"

    def test_approve_already_denied_fails(self, store: KernelStore) -> None:
        ids = _ids(store)
        a = store.create_approval(
            task_id=ids["task_id"],
            step_id=ids["step_id"],
            step_attempt_id=ids["attempt_id"],
            approval_type="execution_authorization",
            requested_action={},
            request_packet_ref=None,
        )
        store.resolve_approval(
            a.approval_id,
            status="denied",
            resolved_by="op",
            resolution={},
        )
        # Try to approve after denial — should fail (expected_status mismatch)
        ok = store.resolve_approval(
            a.approval_id,
            status="approved",
            resolved_by="op",
            resolution={},
            expected_status="pending",
        )
        assert ok is False

    def test_decision_recorded_on_approval(self, store: KernelStore) -> None:
        ids = _ids(store)
        d = store.create_decision(
            task_id=ids["task_id"],
            step_id=ids["step_id"],
            step_attempt_id=ids["attempt_id"],
            decision_type="approval_resolution",
            verdict="allow",
            reason="approved for testing",
        )
        assert d.decision_id
        assert d.verdict == "allow"


# ════════════════════════════════════════════════════════════════════════
# Task 6: Capability grant lifecycle
# ════════════════════════════════════════════════════════════════════════


class TestCapabilityGrantLifecycle:
    def test_issue_grant_with_decision(self, store: KernelStore) -> None:
        ids = _ids(store)
        d = store.create_decision(
            task_id=ids["task_id"],
            step_id=ids["step_id"],
            step_attempt_id=ids["attempt_id"],
            decision_type="execution_authorization",
            verdict="allow",
            reason="test",
        )
        g = store.create_capability_grant(
            task_id=ids["task_id"],
            step_id=ids["step_id"],
            step_attempt_id=ids["attempt_id"],
            decision_ref=d.decision_id,
            approval_ref=None,
            policy_ref=None,
            action_class="execute_command",
            resource_scope=["workspace:/tmp"],
            constraints=None,
            idempotency_key=None,
            expires_at=None,
        )
        assert g.grant_id
        assert g.action_class == "execute_command"
        assert g.status == "issued"

    def test_grant_without_approval_ref(self, store: KernelStore) -> None:
        ids = _ids(store)
        d = store.create_decision(
            task_id=ids["task_id"],
            step_id=ids["step_id"],
            step_attempt_id=ids["attempt_id"],
            decision_type="auto_approve",
            verdict="allow",
            reason="autonomous",
        )
        g = store.create_capability_grant(
            task_id=ids["task_id"],
            step_id=ids["step_id"],
            step_attempt_id=ids["attempt_id"],
            decision_ref=d.decision_id,
            approval_ref=None,
            policy_ref=None,
            action_class="read_file",
            resource_scope=["workspace:*"],
            constraints=None,
            idempotency_key=None,
            expires_at=None,
        )
        assert g.grant_id
        assert g.approval_ref is None

    def test_grant_resource_scope_list(self, store: KernelStore) -> None:
        ids = _ids(store)
        d = store.create_decision(
            task_id=ids["task_id"],
            step_id=ids["step_id"],
            step_attempt_id=ids["attempt_id"],
            decision_type="execution_authorization",
            verdict="allow",
            reason="test",
        )
        g = store.create_capability_grant(
            task_id=ids["task_id"],
            step_id=ids["step_id"],
            step_attempt_id=ids["attempt_id"],
            decision_ref=d.decision_id,
            approval_ref=None,
            policy_ref=None,
            action_class="write_file",
            resource_scope=["path:/tmp/a.txt", "path:/tmp/b.txt"],
            constraints={"max_size": 1024},
            idempotency_key=None,
            expires_at=None,
        )
        assert len(g.resource_scope) == 2
        assert g.constraints == {"max_size": 1024}

    def test_list_grants_for_task(self, store: KernelStore) -> None:
        ids = _ids(store)
        d = store.create_decision(
            task_id=ids["task_id"],
            step_id=ids["step_id"],
            step_attempt_id=ids["attempt_id"],
            decision_type="execution_authorization",
            verdict="allow",
            reason="test",
        )
        for i in range(3):
            store.create_capability_grant(
                task_id=ids["task_id"],
                step_id=ids["step_id"],
                step_attempt_id=ids["attempt_id"],
                decision_ref=d.decision_id,
                approval_ref=None,
                policy_ref=None,
                action_class=f"action_{i}",
                resource_scope=[],
                constraints=None,
                idempotency_key=None,
                expires_at=None,
            )
        grants = store.list_capability_grants(task_id=ids["task_id"])
        assert len(grants) >= 3


# ════════════════════════════════════════════════════════════════════════
# Task 7: Workspace lease
# ════════════════════════════════════════════════════════════════════════


class TestWorkspaceLease:
    def test_acquire_lease(self, store: KernelStore) -> None:
        ids = _ids(store)
        lease = store.create_workspace_lease(
            task_id=ids["task_id"],
            step_attempt_id=ids["attempt_id"],
            workspace_id="ws-1",
            root_path="/Users/beta/work/Hermit",
            holder_principal_id="principal_user",
            mode="mutable",
            resource_scope=["workspace:*"],
            environment_ref=None,
            expires_at=time.time() + 3600,
        )
        assert lease.lease_id
        assert lease.mode == "mutable"
        assert lease.status == "active"

    def test_lease_with_readonly_mode(self, store: KernelStore) -> None:
        ids = _ids(store)
        lease = store.create_workspace_lease(
            task_id=ids["task_id"],
            step_attempt_id=ids["attempt_id"],
            workspace_id="ws-2",
            root_path="/Users/beta/work/Hermit",
            holder_principal_id="principal_user",
            mode="readonly",
            resource_scope=["workspace:*"],
            environment_ref=None,
            expires_at=None,
        )
        assert lease.mode == "readonly"

    def test_lease_ttl_fields(self, store: KernelStore) -> None:
        ids = _ids(store)
        exp = time.time() + 60
        lease = store.create_workspace_lease(
            task_id=ids["task_id"],
            step_attempt_id=ids["attempt_id"],
            workspace_id="ws-3",
            root_path="/tmp",
            holder_principal_id="principal_kernel",
            mode="mutable",
            resource_scope=[],
            environment_ref="env-test",
            expires_at=exp,
        )
        assert lease.expires_at is not None
        assert abs(lease.expires_at - exp) < 1.0

    def test_list_leases_for_task(self, store: KernelStore) -> None:
        ids = _ids(store)
        for i in range(2):
            store.create_workspace_lease(
                task_id=ids["task_id"],
                step_attempt_id=ids["attempt_id"],
                workspace_id=f"ws-list-{i}",
                root_path="/tmp",
                holder_principal_id="principal_user",
                mode="mutable",
                resource_scope=[],
                environment_ref=None,
                expires_at=None,
            )
        leases = store.list_workspace_leases(task_id=ids["task_id"])
        assert len(leases) >= 2


# ════════════════════════════════════════════════════════════════════════
# Task 8: Policy guard structure
# ════════════════════════════════════════════════════════════════════════


class TestPolicyGuardStructure:
    def test_guard_files_exist(self) -> None:
        from pathlib import Path

        guard_dir = Path("src/hermit/kernel/policy/guards")
        expected = [
            "rules_readonly.py",
            "rules_filesystem.py",
            "rules_shell.py",
            "rules_governance.py",
        ]
        for name in expected:
            assert (guard_dir / name).exists(), f"Guard file {name} missing"

    def test_readonly_guard_importable(self) -> None:
        from hermit.kernel.policy.guards import rules_readonly

        assert hasattr(rules_readonly, "evaluate_readonly_rules")

    def test_filesystem_guard_importable(self) -> None:
        from hermit.kernel.policy.guards import rules_filesystem

        assert rules_filesystem is not None

    def test_shell_guard_importable(self) -> None:
        from hermit.kernel.policy.guards import rules_shell

        assert rules_shell is not None
