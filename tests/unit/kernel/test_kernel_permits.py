from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hermit.kernel.authority.grants import CapabilityGrantError, CapabilityGrantService


class _FakeStore:
    def __init__(self) -> None:
        self.created = None
        self.updated: list[tuple[str, dict]] = []
        self.grant = None
        self.lease = None

    def create_capability_grant(self, **kwargs):
        self.created = kwargs
        return SimpleNamespace(grant_id="grant-1")

    def update_capability_grant(self, grant_id: str, **kwargs) -> None:
        self.updated.append((grant_id, kwargs))

    def get_capability_grant(self, grant_id: str):
        return self.grant

    def get_workspace_lease(self, lease_id: str):
        return self.lease


def test_capability_grant_service_issue_and_state_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore()
    service = CapabilityGrantService(store, default_ttl_seconds=300)
    monkeypatch.setattr("hermit.kernel.authority.grants.service.time.time", lambda: 1000.0)

    grant_id = service.issue(
        task_id="task",
        step_id="step",
        step_attempt_id="attempt",
        decision_ref="decision",
        approval_ref=None,
        policy_ref=None,
        issued_to_principal_id="principal_user",
        issued_by_principal_id="principal_kernel",
        workspace_lease_ref="lease-1",
        action_class="write_local",
        resource_scope=["/tmp"],
        idempotency_key="abc",
        constraints={"target_paths": ["/tmp/file.txt"]},
    )
    assert grant_id == "grant-1"
    assert store.created["expires_at"] == 1300.0

    no_ttl = service.issue(
        task_id="task",
        step_id="step",
        step_attempt_id="attempt",
        decision_ref="decision",
        approval_ref=None,
        policy_ref=None,
        issued_to_principal_id="principal_user",
        issued_by_principal_id="principal_kernel",
        workspace_lease_ref=None,
        action_class="write_local",
        resource_scope=["/tmp"],
        idempotency_key=None,
        ttl_seconds=0,
    )
    assert no_ttl == "grant-1"
    assert store.created["expires_at"] is None

    service.consume("grant-1")
    service.mark_uncertain("grant-1")
    service.mark_invalid("grant-1")
    assert store.updated == [
        ("grant-1", {"status": "consumed", "consumed_at": 1000.0}),
        ("grant-1", {"status": "uncertain"}),
        ("grant-1", {"status": "invalid"}),
    ]


@pytest.mark.parametrize(
    "setup,enforce_args,expected_code",
    [
        pytest.param(
            None,
            {
                "grant_id": "missing",
                "task_id": "task",
                "action_class": "write_local",
                "resource_scope": ["/tmp"],
            },
            "missing",
            id="missing-grant",
        ),
        pytest.param(
            SimpleNamespace(
                grant_id="grant-1",
                task_id="task",
                status="consumed",
                expires_at=None,
                action_class="write_local",
                resource_scope=["/tmp"],
                constraints={},
                workspace_lease_ref=None,
            ),
            {
                "grant_id": "grant-1",
                "task_id": "task",
                "action_class": "write_local",
                "resource_scope": ["/tmp"],
            },
            "inactive",
            id="inactive-grant",
        ),
        pytest.param(
            SimpleNamespace(
                grant_id="grant-1",
                task_id="task",
                status="issued",
                expires_at=999.0,
                action_class="write_local",
                resource_scope=["/tmp"],
                constraints={},
                workspace_lease_ref=None,
            ),
            {
                "grant_id": "grant-1",
                "task_id": "task",
                "action_class": "write_local",
                "resource_scope": ["/tmp"],
            },
            "expired",
            id="expired-grant",
        ),
        pytest.param(
            SimpleNamespace(
                grant_id="grant-1",
                task_id="task",
                status="issued",
                expires_at=None,
                action_class="read_local",
                resource_scope=["/tmp"],
                constraints={},
                workspace_lease_ref=None,
            ),
            {
                "grant_id": "grant-1",
                "task_id": "task",
                "action_class": "write_local",
                "resource_scope": ["/tmp"],
            },
            "action_mismatch",
            id="action-mismatch",
        ),
        pytest.param(
            SimpleNamespace(
                grant_id="grant-1",
                task_id="task",
                status="issued",
                expires_at=None,
                action_class="write_local",
                resource_scope=["/tmp"],
                constraints={},
                workspace_lease_ref=None,
            ),
            {
                "grant_id": "grant-1",
                "task_id": "task",
                "action_class": "write_local",
                "resource_scope": ["/etc"],
            },
            "scope_mismatch",
            id="scope-mismatch",
        ),
        pytest.param(
            SimpleNamespace(
                grant_id="grant-1",
                task_id="task",
                status="issued",
                expires_at=None,
                action_class="write_local",
                resource_scope=["/tmp"],
                constraints={
                    "target_paths": ["/tmp/file.txt"],
                    "network_hosts": ["example.com"],
                    "command_preview": "ls /tmp",
                },
                workspace_lease_ref=None,
            ),
            {
                "grant_id": "grant-1",
                "task_id": "task",
                "action_class": "write_local",
                "resource_scope": ["/tmp"],
                "constraints": {"target_paths": ["/tmp/other.txt"]},
            },
            "target_path_mismatch",
            id="target-path-mismatch",
        ),
    ],
)
def test_capability_grant_enforce_error_codes(
    monkeypatch: pytest.MonkeyPatch,
    setup: SimpleNamespace | None,
    enforce_args: dict,
    expected_code: str,
) -> None:
    store = _FakeStore()
    service = CapabilityGrantService(store)
    monkeypatch.setattr("hermit.kernel.authority.grants.service.time.time", lambda: 1000.0)
    store.grant = setup
    with pytest.raises(CapabilityGrantError) as exc_info:
        service.enforce(**enforce_args)
    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    "constraint_input,expected_code",
    [
        pytest.param(
            {"network_hosts": ["bad.example.com"]},
            "network_host_mismatch",
            id="network-host-mismatch",
        ),
        pytest.param(
            {"command_preview": "pwd"},
            "command_mismatch",
            id="command-mismatch",
        ),
    ],
)
def test_capability_grant_constraint_validation(
    monkeypatch: pytest.MonkeyPatch,
    constraint_input: dict,
    expected_code: str,
) -> None:
    store = _FakeStore()
    service = CapabilityGrantService(store)
    monkeypatch.setattr("hermit.kernel.authority.grants.service.time.time", lambda: 1000.0)
    store.grant = SimpleNamespace(
        grant_id="grant-1",
        status="issued",
        expires_at=None,
        action_class="write_local",
        resource_scope=["/tmp"],
        constraints={
            "target_paths": ["/tmp/file.txt"],
            "network_hosts": ["example.com"],
            "command_preview": "ls /tmp",
        },
        workspace_lease_ref=None,
    )
    with pytest.raises(CapabilityGrantError) as exc_info:
        service._validate_constraints(store.grant, constraint_input)
    assert exc_info.value.code == expected_code


def test_capability_grant_service_validates_workspace_leases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _FakeStore()
    service = CapabilityGrantService(store)
    monkeypatch.setattr("hermit.kernel.authority.grants.service.time.time", lambda: 1000.0)

    store.grant = SimpleNamespace(
        grant_id="grant-1",
        task_id="task",
        status="issued",
        expires_at=None,
        action_class="write_local",
        resource_scope=[str(tmp_path)],
        constraints={"lease_root_path": str(tmp_path)},
        workspace_lease_ref="lease-1",
    )
    with pytest.raises(CapabilityGrantError, match="no longer active") as inactive:
        service.enforce(
            "grant-1",
            task_id="task",
            action_class="write_local",
            resource_scope=[str(tmp_path)],
        )
    assert inactive.value.code == "lease_inactive"

    store.lease = SimpleNamespace(status="active", expires_at=999.0, root_path=str(tmp_path))
    with pytest.raises(CapabilityGrantError, match="expired before dispatch") as expired:
        service.enforce(
            "grant-1",
            task_id="task",
            action_class="write_local",
            resource_scope=[str(tmp_path)],
        )
    assert expired.value.code == "lease_expired"

    store.lease = SimpleNamespace(status="active", expires_at=None, root_path=str(tmp_path))
    with pytest.raises(CapabilityGrantError, match="does not cover") as scope:
        service._validate_constraints(
            store.grant,
            {"target_paths": [str(tmp_path.parent / "other.txt")]},
        )
    assert scope.value.code == "lease_scope_mismatch"

    allowed_path = tmp_path / "nested" / "file.txt"
    allowed_path.parent.mkdir()
    allowed_path.write_text("ok", encoding="utf-8")
    service._validate_constraints(store.grant, {"target_paths": [str(allowed_path)]})
