"""Tests for DelegationStoreMixin — cover CRUD operations."""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.delegation import DelegationRecord, DelegationScope


def _setup(tmp_path: Path) -> KernelStore:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    return store


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


def test_create_delegation(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    scope = _mk_scope()
    record = store.create_delegation(
        delegation_id="del-1",
        parent_task_id="parent-1",
        child_task_id="child-1",
        delegated_principal_id="agent-1",
        scope=scope,
        delegation_grant_ref="grant-ref-1",
    )
    assert isinstance(record, DelegationRecord)
    assert record.delegation_id == "del-1"
    assert record.parent_task_id == "parent-1"
    assert record.child_task_id == "child-1"
    assert record.delegated_principal_id == "agent-1"
    assert record.status == "active"
    assert record.delegation_grant_ref == "grant-ref-1"
    assert record.scope.allowed_action_classes == ["read", "write"]
    assert record.scope.max_steps == 10
    assert record.scope.budget_tokens == 5000
    assert record.created_at > 0


def test_create_delegation_custom_timestamp(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    scope = _mk_scope()
    record = store.create_delegation(
        delegation_id="del-ts",
        parent_task_id="p1",
        child_task_id="c1",
        delegated_principal_id="agent-1",
        scope=scope,
        created_at=1000.0,
    )
    assert record.created_at == 1000.0
    assert record.updated_at == 1000.0


# ── get_delegation_record ──────────────────────────────────────


def test_get_delegation_record(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    scope = _mk_scope()
    store.create_delegation(
        delegation_id="del-get",
        parent_task_id="p1",
        child_task_id="c1",
        delegated_principal_id="agent-1",
        scope=scope,
    )
    record = store.get_delegation_record("del-get")
    assert record is not None
    assert record.delegation_id == "del-get"


def test_get_delegation_record_not_found(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    assert store.get_delegation_record("nonexistent") is None


# ── find_delegation_by_pair ────────────────────────────────────


def test_find_delegation_by_pair(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    scope = _mk_scope()
    store.create_delegation(
        delegation_id="del-pair",
        parent_task_id="p1",
        child_task_id="c1",
        delegated_principal_id="agent-1",
        scope=scope,
    )
    record = store.find_delegation_by_pair("p1", "c1")
    assert record is not None
    assert record.delegation_id == "del-pair"


def test_find_delegation_by_pair_not_found(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    assert store.find_delegation_by_pair("p1", "c999") is None


# ── find_delegation_by_child ───────────────────────────────────


def test_find_delegation_by_child(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    scope = _mk_scope()
    store.create_delegation(
        delegation_id="del-child",
        parent_task_id="p1",
        child_task_id="c1",
        delegated_principal_id="agent-1",
        scope=scope,
    )
    record = store.find_delegation_by_child("c1")
    assert record is not None
    assert record.parent_task_id == "p1"


def test_find_delegation_by_child_not_found(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    assert store.find_delegation_by_child("nonexistent") is None


# ── list_delegations_for_parent ────────────────────────────────


def test_list_delegations_for_parent(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    scope = _mk_scope()
    store.create_delegation(
        delegation_id="del-l1",
        parent_task_id="p1",
        child_task_id="c1",
        delegated_principal_id="agent-1",
        scope=scope,
    )
    store.create_delegation(
        delegation_id="del-l2",
        parent_task_id="p1",
        child_task_id="c2",
        delegated_principal_id="agent-2",
        scope=scope,
    )
    records = store.list_delegations_for_parent("p1")
    assert len(records) == 2
    assert records[0].child_task_id == "c1"
    assert records[1].child_task_id == "c2"


def test_list_delegations_for_parent_empty(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    assert store.list_delegations_for_parent("nonexistent") == []


# ── update_delegation_status ───────────────────────────────────


def test_update_delegation_status(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    scope = _mk_scope()
    store.create_delegation(
        delegation_id="del-upd",
        parent_task_id="p1",
        child_task_id="c1",
        delegated_principal_id="agent-1",
        scope=scope,
    )
    store.update_delegation_status("del-upd", status="recalled", recall_reason="budget_exceeded")
    record = store.get_delegation_record("del-upd")
    assert record is not None
    assert record.status == "recalled"
    assert record.recall_reason == "budget_exceeded"


# ── _delegation_from_row scope parsing ─────────────────────────


def test_delegation_scope_empty_fields(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    scope = DelegationScope()  # all defaults
    record = store.create_delegation(
        delegation_id="del-empty-scope",
        parent_task_id="p1",
        child_task_id="c1",
        delegated_principal_id="agent-1",
        scope=scope,
    )
    assert record.scope.allowed_action_classes == []
    assert record.scope.allowed_resource_scopes == []
    assert record.scope.max_steps == 0
    assert record.scope.budget_tokens == 0
