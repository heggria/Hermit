"""Tests for KernelLedgerStoreMixin uncovered paths — target 95%+ on store_ledger.py."""

from __future__ import annotations

import time
from pathlib import Path

from hermit.kernel.ledger.events.store_ledger import KernelLedgerStoreMixin
from hermit.kernel.ledger.journal.store import KernelStore


def _setup(tmp_path: Path) -> KernelStore:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    return store


def _mk_task(store: KernelStore, **kwargs):
    defaults = {
        "conversation_id": "conv-1",
        "title": "Ledger Task",
        "goal": "Cover ledger gaps",
        "source_channel": "chat",
    }
    defaults.update(kwargs)
    return store.create_task(**defaults)


def _mk_step_attempt(store: KernelStore, task_id: str):
    step = store.create_step(task_id=task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task_id, step_id=step.step_id)
    return step, attempt


# ── Artifact auto-derived fields ──────────────────────────────────


def test_artifact_class_for_kind() -> None:
    # "context.pack/v1" -> prefix is "context.pack", dot replaced -> "context_pack"
    assert KernelLedgerStoreMixin._artifact_class_for_kind("context.pack/v1") == "context_pack"
    assert KernelLedgerStoreMixin._artifact_class_for_kind("working_state") == "working_state"
    assert KernelLedgerStoreMixin._artifact_class_for_kind("") == "artifact"
    assert KernelLedgerStoreMixin._artifact_class_for_kind("a.b") == "a_b"
    assert KernelLedgerStoreMixin._artifact_class_for_kind("simple") == "simple"
    # Slash separates prefix from rest, dot gets replaced with underscore
    assert KernelLedgerStoreMixin._artifact_class_for_kind("receipt.bundle") == "receipt_bundle"


def test_artifact_media_type_guessed_from_uri() -> None:
    assert (
        KernelLedgerStoreMixin._artifact_media_type(kind="x", uri="file.json") == "application/json"
    )
    assert KernelLedgerStoreMixin._artifact_media_type(kind="x", uri="file.txt") == "text/plain"


def test_artifact_media_type_context_pack() -> None:
    result = KernelLedgerStoreMixin._artifact_media_type(kind="context.pack/v1", uri="noext")
    assert result == "application/json"


def test_artifact_media_type_action_request_kinds() -> None:
    for kind in ("action_request", "policy_evaluation", "environment", "environment.snapshot"):
        result = KernelLedgerStoreMixin._artifact_media_type(kind=kind, uri="noext")
        assert result == "application/json", f"Failed for kind={kind}"


def test_artifact_media_type_approval_kinds() -> None:
    for kind in ("approval_packet", "receipt.bundle", "context.manifest"):
        result = KernelLedgerStoreMixin._artifact_media_type(kind=kind, uri="noext")
        assert result == "application/json", f"Failed for kind={kind}"


def test_artifact_media_type_runtime_prefix() -> None:
    result = KernelLedgerStoreMixin._artifact_media_type(kind="runtime.snapshot", uri="noext")
    assert result == "application/json"


def test_artifact_media_type_unknown() -> None:
    result = KernelLedgerStoreMixin._artifact_media_type(kind="custom_thing", uri="noext")
    assert result is None


def test_artifact_byte_size_existing_file(tmp_path: Path) -> None:
    f = tmp_path / "test.txt"
    f.write_text("hello", encoding="utf-8")
    assert KernelLedgerStoreMixin._artifact_byte_size(str(f)) == 5


def test_artifact_byte_size_missing_file() -> None:
    assert KernelLedgerStoreMixin._artifact_byte_size("/nonexistent/path.txt") is None


def test_artifact_sensitivity() -> None:
    assert KernelLedgerStoreMixin._artifact_sensitivity("audit") == "operator_internal"
    assert KernelLedgerStoreMixin._artifact_sensitivity("task") == "operator_internal"
    assert KernelLedgerStoreMixin._artifact_sensitivity("default") == "default"
    assert KernelLedgerStoreMixin._artifact_sensitivity("other") == "default"


def test_create_artifact_auto_derives_fields(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    f = tmp_path / "artifact.json"
    f.write_text('{"data": 1}', encoding="utf-8")

    artifact = store.create_artifact(
        task_id=task.task_id,
        step_id=None,
        kind="context.pack/v2",
        uri=str(f),
        content_hash="hash-auto",
        producer="test",
        retention_class="audit",
    )
    assert artifact.artifact_class == "context_pack"
    assert artifact.media_type == "application/json"
    assert artifact.byte_size == f.stat().st_size
    assert artifact.sensitivity_class == "operator_internal"


def test_create_artifact_with_lineage_from_metadata(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")

    artifact = store.create_artifact(
        task_id=task.task_id,
        step_id=None,
        kind="text",
        uri=str(f),
        content_hash="h",
        producer="test",
        metadata={"lineage_ref": "some-parent"},
    )
    assert artifact.lineage_ref == "some-parent"


# ── list_artifacts ─────────────────────────────────────────────────


def test_list_artifacts_global(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    store.create_artifact(
        task_id=task.task_id,
        step_id=None,
        kind="text",
        uri=str(f),
        content_hash="h1",
        producer="test",
    )
    all_artifacts = store.list_artifacts()
    assert len(all_artifacts) >= 1


def test_list_artifacts_for_tasks(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    t1 = _mk_task(store, title="T1")
    t2 = _mk_task(store, title="T2")
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    for t in [t1, t2]:
        for i in range(3):
            store.create_artifact(
                task_id=t.task_id,
                step_id=None,
                kind="text",
                uri=str(f),
                content_hash=f"h-{t.task_id}-{i}",
                producer="test",
            )

    result = store.list_artifacts_for_tasks([t1.task_id, t2.task_id], limit_per_task=2)
    assert t1.task_id in result
    assert t2.task_id in result
    assert len(result[t1.task_id]) <= 2

    assert store.list_artifacts_for_tasks([]) == {}


# ── Principals ─────────────────────────────────────────────────────


def test_list_principals(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    _mk_task(store)  # This creates a principal via _ensure_principal_id
    principals = store.list_principals()
    assert len(principals) >= 1

    # With status filter
    active = store.list_principals(status="active")
    assert len(active) >= 1

    # Get a known principal
    if principals:
        p = store.get_principal(principals[0].principal_id)
        assert p is not None


# ── Decisions ──────────────────────────────────────────────────────


def test_create_decision_with_all_fields(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)

    decision = store.create_decision(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="execution_authorization",
        verdict="allow",
        reason="Policy allows it",
        summary="Allow write",
        rationale="Full policy rationale",
        risk_level="low",
        reversible=False,
        evidence_refs=["ev-1", "ev-2"],
        policy_ref="policy-1",
        approval_ref="approval-1",
        contract_ref="contract-1",
        authorization_plan_ref="plan-1",
        evidence_case_ref="case-1",
        reconciliation_ref="recon-1",
        action_type="write_file",
    )
    assert decision.summary == "Allow write"
    assert decision.rationale == "Full policy rationale"
    assert decision.risk_level == "low"
    assert decision.reversible is False
    assert decision.action_type == "write_file"


def test_list_decisions_global(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)
    store.create_decision(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="auth",
        verdict="allow",
        reason="ok",
    )
    decisions = store.list_decisions()
    assert len(decisions) >= 1


# ── Capability Grants ─────────────────────────────────────────────


def test_capability_grant_lifecycle(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)

    grant = store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref="decision-1",
        approval_ref="approval-1",
        policy_ref="policy-1",
        action_class="write_file",
        resource_scope=["workspace:/tmp"],
        constraints={"max_size": 1024},
        idempotency_key="idem-1",
        expires_at=time.time() + 3600,
        parent_grant_ref="parent-grant-1",
    )
    assert grant.status == "issued"
    assert grant.action_class == "write_file"
    assert grant.resource_scope == ["workspace:/tmp"]
    assert grant.parent_grant_ref == "parent-grant-1"

    # Consume
    now = time.time()
    store.update_capability_grant(grant.grant_id, status="consumed", consumed_at=now)
    consumed = store.get_capability_grant(grant.grant_id)
    assert consumed is not None
    assert consumed.status == "consumed"
    assert consumed.consumed_at == now

    # Revoke
    store.update_capability_grant(grant.grant_id, status="revoked", revoked_at=now + 1)
    revoked = store.get_capability_grant(grant.grant_id)
    assert revoked is not None
    assert revoked.status == "revoked"


def test_list_capability_grants_global(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)
    store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref="d1",
        approval_ref=None,
        policy_ref=None,
        action_class="write",
        resource_scope=[],
        constraints=None,
        idempotency_key=None,
        expires_at=None,
    )
    grants = store.list_capability_grants()
    assert len(grants) >= 1


def test_list_capability_grants_by_parent(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)
    parent = store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref="d1",
        approval_ref=None,
        policy_ref=None,
        action_class="parent_action",
        resource_scope=[],
        constraints=None,
        idempotency_key=None,
        expires_at=None,
    )
    child = store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref="d2",
        approval_ref=None,
        policy_ref=None,
        action_class="child_action",
        resource_scope=[],
        constraints=None,
        idempotency_key=None,
        expires_at=None,
        parent_grant_ref=parent.grant_id,
    )
    children = store.list_capability_grants_by_parent(parent_grant_ref=parent.grant_id)
    assert len(children) == 1
    assert children[0].grant_id == child.grant_id


def test_update_capability_grant_missing(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    # Should not raise
    store.update_capability_grant("nonexistent", status="consumed")


# ── Workspace Leases ──────────────────────────────────────────────


def test_workspace_lease_lifecycle(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    _step, attempt = _mk_step_attempt(store, task.task_id)

    lease = store.create_workspace_lease(
        task_id=task.task_id,
        step_attempt_id=attempt.step_attempt_id,
        workspace_id="ws-1",
        root_path="/tmp/ws",
        holder_principal_id="kernel",
        mode="exclusive",
        resource_scope=["scope-1"],
        environment_ref="env-1",
        expires_at=time.time() + 3600,
        metadata={"key": "value"},
    )
    assert lease.workspace_id == "ws-1"
    assert lease.mode == "exclusive"

    # Update
    now = time.time()
    store.update_workspace_lease(
        lease.lease_id,
        status="released",
        released_at=now,
        expires_at=None,
    )
    updated = store.get_workspace_lease(lease.lease_id)
    assert updated is not None
    assert updated.status == "released"
    assert updated.released_at == now


def test_list_workspace_leases_filters(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    _step, attempt = _mk_step_attempt(store, task.task_id)

    store.create_workspace_lease(
        task_id=task.task_id,
        step_attempt_id=attempt.step_attempt_id,
        workspace_id="ws-filter",
        root_path="/tmp",
        holder_principal_id="kernel",
        mode="shared",
        resource_scope=[],
        environment_ref=None,
        expires_at=None,
    )

    by_task = store.list_workspace_leases(task_id=task.task_id)
    assert len(by_task) >= 1

    by_attempt = store.list_workspace_leases(step_attempt_id=attempt.step_attempt_id)
    assert len(by_attempt) >= 1

    by_ws = store.list_workspace_leases(workspace_id="ws-filter")
    assert len(by_ws) >= 1

    by_status = store.list_workspace_leases(status="active")
    assert len(by_status) >= 1


def test_update_workspace_lease_missing(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    store.update_workspace_lease("nonexistent", status="released")


# ── Beliefs ────────────────────────────────────────────────────────


def test_create_and_update_belief(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)

    belief = store.create_belief(
        task_id=task.task_id,
        conversation_id="conv-1",
        scope_kind="task",
        scope_ref=task.task_id,
        category="fact",
        claim_text="The server runs on port 8080",
        structured_assertion={"port": 8080},
        promotion_candidate=True,
        confidence=0.9,
        trust_tier="verified",
        evidence_refs=["ev-1"],
        evidence_case_ref="case-1",
        supersedes=["old-belief-1"],
        contradicts=["other-belief-1"],
        epistemic_origin="reported",
        freshness_class="recent",
        last_validated_at=time.time(),
        validation_basis="test",
        supersession_reason="updated info",
        memory_ref="mem-1",
    )
    assert belief.claim_text == "The server runs on port 8080"
    assert belief.confidence == 0.9
    assert belief.evidence_refs == ["ev-1"]
    assert belief.supersedes == ["old-belief-1"]
    assert belief.contradicts == ["other-belief-1"]

    # Update
    store.update_belief(
        belief.belief_id,
        status="invalidated",
        memory_ref="mem-2",
        evidence_case_ref="case-2",
        contradicts=["c1", "c2"],
        supersedes=["s1"],
        invalidated_at=time.time(),
        promotion_candidate=False,
        last_validated_at=time.time(),
        validation_basis="recheck",
        supersession_reason="new data",
    )
    updated = store.get_belief(belief.belief_id)
    assert updated is not None
    assert updated.status == "invalidated"
    assert updated.memory_ref == "mem-2"
    assert updated.promotion_candidate is False


def test_list_beliefs_filters(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    store.create_belief(
        task_id=task.task_id,
        conversation_id="conv-1",
        scope_kind="task",
        scope_ref=task.task_id,
        category="fact",
        claim_text="test belief",
    )

    by_task = store.list_beliefs(task_id=task.task_id)
    assert len(by_task) >= 1

    by_scope = store.list_beliefs(scope_kind="task")
    assert len(by_scope) >= 1

    by_ref = store.list_beliefs(scope_ref=task.task_id)
    assert len(by_ref) >= 1

    by_status = store.list_beliefs(status="active")
    assert len(by_status) >= 1


def test_update_belief_missing(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    store.update_belief("nonexistent", status="invalidated")


# ── Memory Records ─────────────────────────────────────────────────


def test_create_memory_record_with_auto_classification(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)

    # Default params trigger auto-classification path
    mem = store.create_memory_record(
        task_id=task.task_id,
        conversation_id="conv-1",
        category="fact",
        claim_text="The API key is rotated weekly",
    )
    assert mem is not None
    assert mem.status == "active"


def test_create_memory_record_superseded_normalization(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)

    mem = store.create_memory_record(
        task_id=task.task_id,
        conversation_id="conv-1",
        category="fact",
        claim_text="Old fact",
        status="superseded",
        scope_kind="global",
        scope_ref="global",
        promotion_reason="manual",
        retention_class="durable",
    )
    assert mem.status == "invalidated"  # superseded normalized to invalidated
    assert mem.invalidation_reason == "superseded"


def test_create_memory_record_explicit_scope(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)

    mem = store.create_memory_record(
        task_id=task.task_id,
        conversation_id="conv-1",
        category="preference",
        claim_text="User prefers dark mode",
        scope_kind="user",
        scope_ref="user-123",
        promotion_reason="explicit",
        retention_class="durable",
        memory_kind="preference",
        supersedes=["old-1"],
        supersedes_memory_ids=["mem-old-1"],
        superseded_by_memory_id=None,
        source_belief_ref="belief-1",
        expires_at=time.time() + 86400,
    )
    assert mem.scope_kind == "user"
    assert mem.scope_ref == "user-123"
    assert mem.supersedes == ["old-1"]


def test_list_memory_records_active_filter(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)

    # Active, not expired
    store.create_memory_record(
        task_id=task.task_id,
        conversation_id="conv-1",
        category="fact",
        claim_text="Active mem",
        scope_kind="global",
        scope_ref="g",
        promotion_reason="manual",
        retention_class="durable",
    )
    # Active, but expired
    store.create_memory_record(
        task_id=task.task_id,
        conversation_id="conv-1",
        category="fact",
        claim_text="Expired mem",
        scope_kind="global",
        scope_ref="g",
        promotion_reason="manual",
        retention_class="durable",
        expires_at=1.0,  # past
    )

    active = store.list_memory_records(status="active")
    # The expired one should be excluded
    assert all(m.expires_at is None or m.expires_at > time.time() for m in active)


def test_list_memory_records_filters(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    store.create_memory_record(
        task_id=task.task_id,
        conversation_id="conv-1",
        category="fact",
        claim_text="Filtered mem",
        scope_kind="task",
        scope_ref=task.task_id,
        promotion_reason="manual",
        retention_class="durable",
        memory_kind="durable_fact",
    )

    by_conv = store.list_memory_records(conversation_id="conv-1")
    assert len(by_conv) >= 1

    by_scope_kind = store.list_memory_records(scope_kind="task")
    assert len(by_scope_kind) >= 1

    by_scope_ref = store.list_memory_records(scope_ref=task.task_id)
    assert len(by_scope_ref) >= 1

    by_kind = store.list_memory_records(memory_kind="durable_fact")
    assert len(by_kind) >= 1

    by_task = store.list_memory_records(task_id=task.task_id)
    assert len(by_task) >= 1


def test_update_memory_record_superseded_normalization(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    mem = store.create_memory_record(
        task_id=task.task_id,
        conversation_id="conv-1",
        category="fact",
        claim_text="Will be superseded",
        scope_kind="global",
        scope_ref="g",
        promotion_reason="manual",
        retention_class="durable",
    )

    store.update_memory_record(mem.memory_id, status="superseded")
    updated = store.get_memory_record(mem.memory_id)
    assert updated is not None
    assert updated.status == "invalidated"
    assert updated.invalidation_reason == "superseded"


def test_update_memory_record_various_fields(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    mem = store.create_memory_record(
        task_id=task.task_id,
        conversation_id="conv-1",
        category="fact",
        claim_text="Update test",
        scope_kind="global",
        scope_ref="g",
        promotion_reason="manual",
        retention_class="durable",
    )
    now = time.time()
    store.update_memory_record(
        mem.memory_id,
        supersedes=["s1"],
        supersedes_memory_ids=["m1"],
        superseded_by_memory_id="m-new",
        invalidation_reason="manual",
        invalidated_at=now,
        expires_at=now + 3600,
        validation_basis="recheck",
        last_validated_at=now,
        supersession_reason="new info",
        learned_from_reconciliation_ref="recon-1",
        structured_assertion={"key": "val"},
        freshness_class="stale",
        last_accessed_at=now,
        confidence=0.95,
    )
    updated = store.get_memory_record(mem.memory_id)
    assert updated is not None
    assert updated.supersedes == ["s1"]
    assert updated.superseded_by_memory_id == "m-new"
    assert updated.confidence == 0.95


def test_update_memory_record_missing(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    store.update_memory_record("nonexistent", status="active")


# ── Rollbacks ──────────────────────────────────────────────────────


def test_rollback_lifecycle(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)

    rollback = store.create_rollback(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        receipt_ref="receipt-1",
        action_type="write_file",
        strategy="undo_write",
        artifact_refs=["art-1"],
    )
    assert rollback.status == "not_requested"

    # Get by receipt
    by_receipt = store.get_rollback_for_receipt("receipt-1")
    assert by_receipt is not None
    assert by_receipt.rollback_id == rollback.rollback_id

    # Update to succeeded — auto-sets executed_at
    store.update_rollback(rollback.rollback_id, status="succeeded", result_summary="rolled back")
    updated = store.get_rollback(rollback.rollback_id)
    assert updated is not None
    assert updated.status == "succeeded"
    assert updated.executed_at is not None
    assert updated.result_summary == "rolled back"


def test_update_rollback_with_explicit_executed_at(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)

    rollback = store.create_rollback(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        receipt_ref="r2",
        action_type="bash",
        strategy="revert",
    )
    store.update_rollback(
        rollback.rollback_id,
        status="failed",
        result_summary="rollback failed",
        executed_at=123.0,
    )
    updated = store.get_rollback(rollback.rollback_id)
    assert updated is not None
    assert updated.executed_at == 123.0


def test_update_rollback_non_terminal_no_auto_executed_at(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)

    rollback = store.create_rollback(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        receipt_ref="r3",
        action_type="bash",
        strategy="revert",
    )
    store.update_rollback(rollback.rollback_id, status="pending")
    updated = store.get_rollback(rollback.rollback_id)
    assert updated is not None
    assert updated.executed_at is None


def test_update_rollback_missing(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    store.update_rollback("nonexistent", status="failed")


def test_get_rollback_for_receipt_missing(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    assert store.get_rollback_for_receipt("nonexistent") is None


# ── Approvals ──────────────────────────────────────────────────────


def test_approval_lifecycle(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)

    approval = store.create_approval(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        approval_type="write_file",
        requested_action={"summary": "Write to disk"},
        request_packet_ref="packet-1",
        requested_action_ref="action-ref-1",
        approval_packet_ref="approval-packet-1",
        policy_result_ref="policy-1",
        requested_contract_ref="contract-1",
        authorization_plan_ref="plan-1",
        evidence_case_ref="case-1",
        drift_expiry=time.time() + 300,
        fallback_contract_refs=["fallback-1"],
        decision_ref="decision-1",
        state_witness_ref="witness-1",
        expires_at=time.time() + 3600,
    )
    assert approval.status == "pending"
    assert approval.requested_action_ref == "action-ref-1"
    assert approval.drift_expiry is not None
    assert approval.state_witness_ref == "witness-1"

    # Resolve
    store.resolve_approval(
        approval.approval_id,
        status="approved",
        resolved_by="operator",
        resolution={"reason": "looks good"},
    )
    resolved = store.get_approval(approval.approval_id)
    assert resolved is not None
    assert resolved.status == "approved"
    assert resolved.resolved_at is not None

    # Update resolution
    store.update_approval_resolution(approval.approval_id, {"reason": "updated"})
    updated = store.get_approval(approval.approval_id)
    assert updated is not None
    assert updated.resolution.get("reason") == "updated"

    # Consume
    store.consume_approval(approval.approval_id)
    consumed = store.get_approval(approval.approval_id)
    assert consumed is not None
    assert consumed.status == "consumed"


def test_list_approvals_by_conversation(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)
    store.create_approval(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        approval_type="test",
        requested_action={"x": 1},
        request_packet_ref=None,
    )
    approvals = store.list_approvals(conversation_id="conv-1")
    assert len(approvals) >= 1


def test_get_latest_pending_approval(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)
    a = store.create_approval(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        approval_type="test",
        requested_action={"x": 1},
        request_packet_ref=None,
    )
    latest = store.get_latest_pending_approval("conv-1")
    assert latest is not None
    assert latest.approval_id == a.approval_id


def test_resolve_approval_missing(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    store.resolve_approval("nonexistent", status="approved", resolved_by="x", resolution={})


def test_consume_approval_missing(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    store.consume_approval("nonexistent")


def test_update_approval_resolution_missing(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    store.update_approval_resolution("nonexistent", {"x": 1})


# ── Receipts ───────────────────────────────────────────────────────


def test_receipt_with_all_fields(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)

    receipt = store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_file",
        receipt_class="write_local",
        input_refs=["in-1"],
        environment_ref="env-1",
        policy_result={"decision": "allow"},
        approval_ref="approval-1",
        output_refs=["out-1"],
        result_summary="wrote file",
        result_code="succeeded",
        decision_ref="decision-1",
        capability_grant_ref="grant-1",
        workspace_lease_ref="lease-1",
        policy_ref="policy-1",
        action_request_ref="action-req-1",
        policy_result_ref="policy-result-1",
        contract_ref="contract-1",
        authorization_plan_ref="plan-1",
        witness_ref="witness-1",
        idempotency_key="idem-1",
        receipt_bundle_ref="bundle-1",
        proof_mode="signed",
        verifiability="full",
        signature="sig-abc",
        signer_ref="signer-1",
        rollback_supported=True,
        rollback_strategy="undo_write",
        rollback_status="not_requested",
        rollback_ref="rollback-1",
        rollback_artifact_refs=["roll-art-1"],
        observed_effect_summary="file created",
        reconciliation_required=True,
    )
    assert receipt.receipt_class == "write_local"
    assert receipt.rollback_supported is True
    assert receipt.reconciliation_required is True
    assert receipt.signature == "sig-abc"


def test_update_receipt_proof_fields(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)
    receipt = store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write",
        input_refs=[],
        environment_ref=None,
        policy_result={},
        approval_ref=None,
        output_refs=[],
        result_summary="done",
        result_code="succeeded",
    )

    store.update_receipt_proof_fields(
        receipt.receipt_id,
        receipt_bundle_ref="bundle-new",
        proof_mode="signed_with_merkle",
        verifiability="full",
        signature="new-sig",
        signer_ref="signer-new",
    )
    updated = store.get_receipt(receipt.receipt_id)
    assert updated is not None
    assert updated.receipt_bundle_ref == "bundle-new"
    assert updated.proof_mode == "signed_with_merkle"
    assert updated.signature == "new-sig"


def test_update_receipt_proof_fields_missing(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    store.update_receipt_proof_fields("nonexistent", proof_mode="x")


def test_update_receipt_rollback_fields(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)
    receipt = store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write",
        input_refs=[],
        environment_ref=None,
        policy_result={},
        approval_ref=None,
        output_refs=[],
        result_summary="done",
        result_code="succeeded",
    )

    store.update_receipt_rollback_fields(
        receipt.receipt_id,
        rollback_supported=True,
        rollback_strategy="undo_write",
        rollback_status="succeeded",
        rollback_ref="rollback-new",
        rollback_artifact_refs=["roll-art-1", "roll-art-2"],
    )
    updated = store.get_receipt(receipt.receipt_id)
    assert updated is not None
    assert updated.rollback_supported is True
    assert updated.rollback_strategy == "undo_write"
    assert updated.rollback_status == "succeeded"
    assert updated.rollback_ref == "rollback-new"
    assert updated.rollback_artifact_refs == ["roll-art-1", "roll-art-2"]


def test_update_receipt_rollback_fields_missing(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    store.update_receipt_rollback_fields("nonexistent", rollback_status="x")


def test_list_receipts_global(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step, attempt = _mk_step_attempt(store, task.task_id)
    store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write",
        input_refs=[],
        environment_ref=None,
        policy_result={},
        approval_ref=None,
        output_refs=[],
        result_summary="done",
        result_code="succeeded",
    )
    receipts = store.list_receipts()
    assert len(receipts) >= 1


def test_list_events_with_event_type_filter(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    events = store.list_events(task_id=task.task_id, event_type="task.created")
    assert len(events) >= 1
    assert all(e["event_type"] == "task.created" for e in events)


def test_list_events_with_after_event_seq(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    store.create_step(task_id=task.task_id, kind="a")

    all_events = store.list_events(task_id=task.task_id, limit=100)
    assert len(all_events) >= 2

    after_first = store.list_events(
        task_id=task.task_id,
        after_event_seq=all_events[0]["event_seq"],
    )
    assert len(after_first) < len(all_events)
