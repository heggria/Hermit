"""Tests for DelegationStoreMixin — cover CRUD operations."""

from __future__ import annotations

import uuid

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.delegation import DelegationRecord, DelegationScope


def _uid(prefix: str = "id") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _mk_scope(**kwargs) -> DelegationScope:
    defaults = {
        "allowed_action_classes": ["read", "write"],
        "allowed_resource_scopes": ["/project"],
        "max_steps": 10,
        "budget_tokens": 5000,
    }
    defaults.update(kwargs)
    return DelegationScope(**defaults)


# ── create_delegation ──────────────────────────────────────────


def test_create_delegation(shared_store: KernelStore) -> None:
    scope = _mk_scope()
    did = _uid("del")
    record = shared_store.create_delegation(
        delegation_id=did,
        parent_task_id=_uid("parent"),
        child_task_id=_uid("child"),
        delegated_principal_id="agent-1",
        scope=scope,
        delegation_grant_ref="grant-ref-1",
    )
    assert isinstance(record, DelegationRecord)
    assert record.delegation_id == did
    assert record.status == "active"
    assert record.delegation_grant_ref == "grant-ref-1"
    assert record.scope.allowed_action_classes == ["read", "write"]
    assert record.scope.max_steps == 10
    assert record.scope.budget_tokens == 5000
    assert record.created_at > 0


def test_create_delegation_custom_timestamp(shared_store: KernelStore) -> None:
    scope = _mk_scope()
    record = shared_store.create_delegation(
        delegation_id=_uid("del"),
        parent_task_id=_uid("p"),
        child_task_id=_uid("c"),
        delegated_principal_id="agent-1",
        scope=scope,
        created_at=1000.0,
    )
    assert record.created_at == 1000.0
    assert record.updated_at == 1000.0


# ── get_delegation_record ──────────────────────────────────────


def test_get_delegation_record(shared_store: KernelStore) -> None:
    scope = _mk_scope()
    did = _uid("del")
    shared_store.create_delegation(
        delegation_id=did,
        parent_task_id=_uid("p"),
        child_task_id=_uid("c"),
        delegated_principal_id="agent-1",
        scope=scope,
    )
    record = shared_store.get_delegation_record(did)
    assert record is not None
    assert record.delegation_id == did


def test_get_delegation_record_not_found(shared_store: KernelStore) -> None:
    assert shared_store.get_delegation_record("nonexistent") is None


# ── find_delegation_by_pair ────────────────────────────────────


def test_find_delegation_by_pair(shared_store: KernelStore) -> None:
    scope = _mk_scope()
    did = _uid("del")
    pid = _uid("p")
    cid = _uid("c")
    shared_store.create_delegation(
        delegation_id=did,
        parent_task_id=pid,
        child_task_id=cid,
        delegated_principal_id="agent-1",
        scope=scope,
    )
    record = shared_store.find_delegation_by_pair(pid, cid)
    assert record is not None
    assert record.delegation_id == did


def test_find_delegation_by_pair_not_found(shared_store: KernelStore) -> None:
    assert shared_store.find_delegation_by_pair(_uid("p"), "c999") is None


# ── find_delegation_by_child ───────────────────────────────────


def test_find_delegation_by_child(shared_store: KernelStore) -> None:
    scope = _mk_scope()
    pid = _uid("p")
    cid = _uid("c")
    shared_store.create_delegation(
        delegation_id=_uid("del"),
        parent_task_id=pid,
        child_task_id=cid,
        delegated_principal_id="agent-1",
        scope=scope,
    )
    record = shared_store.find_delegation_by_child(cid)
    assert record is not None
    assert record.parent_task_id == pid


def test_find_delegation_by_child_not_found(shared_store: KernelStore) -> None:
    assert shared_store.find_delegation_by_child("nonexistent") is None


# ── list_delegations_for_parent ────────────────────────────────


def test_list_delegations_for_parent(shared_store: KernelStore) -> None:
    scope = _mk_scope()
    pid = _uid("p")
    cid1 = _uid("c")
    cid2 = _uid("c")
    shared_store.create_delegation(
        delegation_id=_uid("del"),
        parent_task_id=pid,
        child_task_id=cid1,
        delegated_principal_id="agent-1",
        scope=scope,
    )
    shared_store.create_delegation(
        delegation_id=_uid("del"),
        parent_task_id=pid,
        child_task_id=cid2,
        delegated_principal_id="agent-2",
        scope=scope,
    )
    records = shared_store.list_delegations_for_parent(pid)
    assert len(records) == 2
    child_ids = {r.child_task_id for r in records}
    assert cid1 in child_ids
    assert cid2 in child_ids


def test_list_delegations_for_parent_empty(shared_store: KernelStore) -> None:
    assert shared_store.list_delegations_for_parent("nonexistent") == []


# ── update_delegation_status ───────────────────────────────────


def test_update_delegation_status(shared_store: KernelStore) -> None:
    scope = _mk_scope()
    did = _uid("del")
    shared_store.create_delegation(
        delegation_id=did,
        parent_task_id=_uid("p"),
        child_task_id=_uid("c"),
        delegated_principal_id="agent-1",
        scope=scope,
    )
    shared_store.update_delegation_status(did, status="recalled", recall_reason="budget_exceeded")
    record = shared_store.get_delegation_record(did)
    assert record is not None
    assert record.status == "recalled"
    assert record.recall_reason == "budget_exceeded"


# ── _delegation_from_row scope parsing ─────────────────────────


def test_delegation_scope_empty_fields(shared_store: KernelStore) -> None:
    scope = DelegationScope()  # all defaults
    record = shared_store.create_delegation(
        delegation_id=_uid("del"),
        parent_task_id=_uid("p"),
        child_task_id=_uid("c"),
        delegated_principal_id="agent-1",
        scope=scope,
    )
    assert record.scope.allowed_action_classes == []
    assert record.scope.allowed_resource_scopes == []
    assert record.scope.max_steps == 0
    assert record.scope.budget_tokens == 0
