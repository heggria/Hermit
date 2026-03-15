from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hermit.capabilities import CapabilityGrantError, CapabilityGrantService


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
    monkeypatch.setattr("hermit.capabilities.service.time.time", lambda: 1000.0)

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


def test_capability_grant_service_enforce_and_constraint_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    service = CapabilityGrantService(store)
    monkeypatch.setattr("hermit.capabilities.service.time.time", lambda: 1000.0)

    with pytest.raises(CapabilityGrantError, match="not found") as missing:
        service.enforce("missing", action_class="write_local", resource_scope=["/tmp"])
    assert missing.value.code == "missing"

    store.grant = SimpleNamespace(
        grant_id="grant-1",
        status="consumed",
        expires_at=None,
        action_class="write_local",
        resource_scope=["/tmp"],
        constraints={},
        workspace_lease_ref=None,
    )
    with pytest.raises(CapabilityGrantError, match="cannot be dispatched") as inactive:
        service.enforce("grant-1", action_class="write_local", resource_scope=["/tmp"])
    assert inactive.value.code == "inactive"

    store.grant = SimpleNamespace(
        grant_id="grant-1",
        status="issued",
        expires_at=999.0,
        action_class="write_local",
        resource_scope=["/tmp"],
        constraints={},
        workspace_lease_ref=None,
    )
    with pytest.raises(CapabilityGrantError, match="expired before dispatch") as expired:
        service.enforce("grant-1", action_class="write_local", resource_scope=["/tmp"])
    assert expired.value.code == "expired"
    assert store.updated[-1] == ("grant-1", {"status": "invalid"})

    store.grant = SimpleNamespace(
        grant_id="grant-1",
        status="issued",
        expires_at=None,
        action_class="read_local",
        resource_scope=["/tmp"],
        constraints={},
        workspace_lease_ref=None,
    )
    with pytest.raises(CapabilityGrantError, match="only allows") as mismatch:
        service.enforce("grant-1", action_class="write_local", resource_scope=["/tmp"])
    assert mismatch.value.code == "action_mismatch"

    store.grant = SimpleNamespace(
        grant_id="grant-1",
        status="issued",
        expires_at=None,
        action_class="write_local",
        resource_scope=["/tmp"],
        constraints={},
        workspace_lease_ref=None,
    )
    with pytest.raises(CapabilityGrantError, match="does not cover resource scope") as scope:
        service.enforce("grant-1", action_class="write_local", resource_scope=["/etc"])
    assert scope.value.code == "scope_mismatch"

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
    with pytest.raises(CapabilityGrantError, match="target paths") as path_mismatch:
        service.enforce(
            "grant-1",
            action_class="write_local",
            resource_scope=["/tmp"],
            constraints={"target_paths": ["/tmp/other.txt"]},
        )
    assert path_mismatch.value.code == "target_path_mismatch"

    with pytest.raises(CapabilityGrantError, match="network hosts") as host_mismatch:
        service._validate_constraints(store.grant, {"network_hosts": ["bad.example.com"]})
    assert host_mismatch.value.code == "network_host_mismatch"

    with pytest.raises(CapabilityGrantError, match="current command") as command_mismatch:
        service._validate_constraints(store.grant, {"command_preview": "pwd"})
    assert command_mismatch.value.code == "command_mismatch"


def test_capability_grant_service_validates_workspace_leases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _FakeStore()
    service = CapabilityGrantService(store)
    monkeypatch.setattr("hermit.capabilities.service.time.time", lambda: 1000.0)

    store.grant = SimpleNamespace(
        grant_id="grant-1",
        status="issued",
        expires_at=None,
        action_class="write_local",
        resource_scope=[str(tmp_path)],
        constraints={"lease_root_path": str(tmp_path)},
        workspace_lease_ref="lease-1",
    )
    with pytest.raises(CapabilityGrantError, match="no longer active") as inactive:
        service.enforce("grant-1", action_class="write_local", resource_scope=[str(tmp_path)])
    assert inactive.value.code == "lease_inactive"

    store.lease = SimpleNamespace(status="active", expires_at=999.0, root_path=str(tmp_path))
    with pytest.raises(CapabilityGrantError, match="expired before dispatch") as expired:
        service.enforce("grant-1", action_class="write_local", resource_scope=[str(tmp_path)])
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
