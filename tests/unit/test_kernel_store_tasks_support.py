from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.ledger.journal.store_support import _UNSET, _json_loads
from hermit.kernel.verification.proofs.proofs import ProofService


def test_store_support_json_loads_handles_empty_and_invalid() -> None:
    assert _json_loads(None) == {}
    assert _json_loads("") == {}
    assert _json_loads("{bad") == {}
    assert _json_loads('{"ok": true}') == {"ok": True}
    assert _UNSET is not None


def test_kernel_store_task_flow_covers_conversations_tasks_steps_attempts_and_events(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "state.db")

    conversation = store.ensure_conversation("conv-1", source_channel="chat", source_ref="thread-1")
    same_conversation = store.ensure_conversation("conv-1", source_channel="chat")
    assert conversation.conversation_id == "conv-1"
    assert same_conversation.conversation_id == "conv-1"
    assert store.get_conversation("conv-1") is not None
    assert store.list_conversations() == ["conv-1"]

    store.update_conversation_metadata("conv-1", {"topic": "testing"})
    store.update_conversation_usage(
        "conv-1",
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=3,
        cache_creation_tokens=4,
        last_task_id=None,
    )
    updated_conversation = store.get_conversation("conv-1")
    assert updated_conversation is not None
    assert updated_conversation.metadata["topic"] == "testing"
    assert updated_conversation.total_output_tokens == 20

    task_one = store.create_task(
        conversation_id="conv-1",
        title="Task One",
        goal="First goal",
        source_channel="chat",
        requested_by="tester",
        task_contract_ref="contract.task/respond",
    )
    task_two = store.create_task(
        conversation_id="conv-1",
        title="Task Two",
        goal="Second goal",
        source_channel="chat",
    )
    fetched_task = store.get_task(task_one.task_id)
    assert fetched_task is not None
    assert fetched_task.title == "Task One"
    assert fetched_task.task_contract_ref == "contract.task/respond"
    assert store.get_last_task_for_conversation("conv-1").task_id == task_two.task_id
    assert [task.task_id for task in store.list_tasks(conversation_id="conv-1", limit=10)][
        0
    ] == task_two.task_id

    store.update_task_status(task_one.task_id, "blocked")
    blocked_tasks = store.list_tasks(status="blocked", limit=10)
    assert [task.task_id for task in blocked_tasks] == [task_one.task_id]

    step = store.create_step(
        task_id=task_one.task_id,
        kind="respond",
        title="Respond to task",
        contract_ref="contract.step/respond",
        depends_on=["step_prev"],
        max_attempts=3,
    )
    assert store.get_step(step.step_id) is not None
    store.update_step(step.step_id, status="completed", output_ref="artifact-1", finished_at=123.0)
    updated_step = store.get_step(step.step_id)
    assert updated_step is not None
    assert updated_step.status == "completed"
    assert updated_step.output_ref == "artifact-1"
    assert updated_step.title == "Respond to task"
    assert updated_step.contract_ref == "contract.step/respond"
    assert updated_step.depends_on == ["step_prev"]
    assert updated_step.max_attempts == 3
    store.update_step("missing-step", status="ignored")

    attempt = store.create_step_attempt(
        task_id=task_one.task_id,
        step_id=step.step_id,
        attempt=2,
        context={"phase": "draft"},
        context_pack_ref="artifact_context_pack",
        working_state_ref="artifact_working_state",
        environment_ref="artifact_environment",
        action_request_ref="artifact_action_request",
        policy_result_ref="artifact_policy_result",
        approval_packet_ref="artifact_approval_packet",
        pending_execution_ref="artifact_pending_execution",
        idempotency_key="attempt-idem",
        executor_mode="tool_executor",
        policy_version="rules-v1",
        resume_from_ref="artifact_runtime_snapshot",
    )
    assert store.get_step_attempt(attempt.step_attempt_id) is not None
    store.update_step_attempt(
        attempt.step_attempt_id,
        status="blocked",
        context={"phase": "review"},
        waiting_reason="approval",
        approval_id="approval-1",
        decision_id="decision-1",
        capability_grant_id="grant-1",
        state_witness_ref="witness-1",
        context_pack_ref="artifact_context_pack_v2",
        working_state_ref="artifact_working_state_v2",
        environment_ref="artifact_environment_v2",
        action_request_ref="artifact_action_request_v2",
        policy_result_ref="artifact_policy_result_v2",
        approval_packet_ref="artifact_approval_packet_v2",
        pending_execution_ref="artifact_pending_execution_v2",
        idempotency_key="attempt-idem-v2",
        executor_mode="compiled_provider_input",
        policy_version="rules-v2",
        resume_from_ref="artifact_runtime_snapshot_v2",
        finished_at=456.0,
    )
    updated_attempt = store.get_step_attempt(attempt.step_attempt_id)
    assert updated_attempt is not None
    assert updated_attempt.status == "blocked"
    assert updated_attempt.context == {"phase": "review"}
    assert updated_attempt.waiting_reason == "approval"
    assert updated_attempt.capability_grant_id == "grant-1"
    assert updated_attempt.context_pack_ref == "artifact_context_pack_v2"
    assert updated_attempt.working_state_ref == "artifact_working_state_v2"
    assert updated_attempt.environment_ref == "artifact_environment_v2"
    assert updated_attempt.action_request_ref == "artifact_action_request_v2"
    assert updated_attempt.policy_result_ref == "artifact_policy_result_v2"
    assert updated_attempt.approval_packet_ref == "artifact_approval_packet_v2"
    assert updated_attempt.pending_execution_ref == "artifact_pending_execution_v2"
    assert updated_attempt.idempotency_key == "attempt-idem-v2"
    assert updated_attempt.executor_mode == "compiled_provider_input"
    assert updated_attempt.policy_version == "rules-v2"
    assert updated_attempt.resume_from_ref == "artifact_runtime_snapshot_v2"

    store.update_step_attempt(
        attempt.step_attempt_id,
        status="running",
        context=updated_attempt.context,
        waiting_reason=updated_attempt.waiting_reason,
        approval_id=updated_attempt.approval_id,
        decision_id=updated_attempt.decision_id,
        capability_grant_id=updated_attempt.capability_grant_id,
        state_witness_ref=updated_attempt.state_witness_ref,
        finished_at=updated_attempt.finished_at,
    )
    unchanged_attempt = store.get_step_attempt(attempt.step_attempt_id)
    assert unchanged_attempt is not None
    assert unchanged_attempt.context == {"phase": "review"}
    assert unchanged_attempt.waiting_reason == "approval"
    store.update_step_attempt("missing-attempt", status="ignored")

    custom_event_id = store.append_event(
        event_type="custom.event",
        entity_type="task",
        entity_id=task_one.task_id,
        task_id=task_one.task_id,
        step_id=step.step_id,
        actor="tester",
        payload={"extra": True},
        causation_id="cause-1",
        correlation_id="corr-1",
    )
    assert custom_event_id.startswith("event_")

    all_events = store.list_events(limit=20)
    task_events = store.list_events(task_id=task_one.task_id, limit=20)
    assert all_events
    assert any(event["event_type"] == "custom.event" for event in all_events)
    assert all(event["task_id"] == task_one.task_id for event in task_events)
    assert any(event["payload"].get("status") == "blocked" for event in task_events)
    assert all(event["event_hash"] for event in task_events)
    assert task_events[0]["prev_event_hash"] in {None, ""}
    assert all(event["hash_chain_algo"] == "sha256-v1" for event in task_events)

    projection = store.build_task_projection(task_one.task_id)
    assert projection["task"]["task_id"] == task_one.task_id
    assert projection["task"]["status"] == "blocked"
    assert projection["steps"][step.step_id]["status"] == "completed"
    assert projection["step_attempts"][attempt.step_attempt_id]["status"] == "running"
    assert projection["step_attempts"][attempt.step_attempt_id]["capability_grant_id"] == "grant-1"
    assert projection["events_processed"] >= len(task_events)


def test_kernel_store_tolerates_foreign_object_sentinels_in_optional_task_fields(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-sentinel", source_channel="scheduler")

    task = store.create_task(
        conversation_id="conv-sentinel",
        title="Sentinel Task",
        goal="Avoid sqlite binding crashes",
        source_channel="scheduler",
        parent_task_id=object(),  # type: ignore[arg-type]
        task_contract_ref=object(),  # type: ignore[arg-type]
    )

    persisted = store.get_task(task.task_id)
    assert persisted is not None
    assert persisted.parent_task_id is None
    assert persisted.task_contract_ref is None


def test_kernel_store_tolerates_foreign_object_sentinels_in_step_attempt_updates(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-attempt-sentinel", source_channel="scheduler")
    task = store.create_task(
        conversation_id="conv-attempt-sentinel",
        title="Attempt Sentinel",
        goal="Keep previous refs when invalid sentinels leak through",
        source_channel="scheduler",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        context={"phase": "draft"},
        context_pack_ref="artifact_context_pack",
        working_state_ref="artifact_working_state",
        environment_ref="artifact_environment",
    )

    store.update_step_attempt(
        attempt.step_attempt_id,
        context=object(),  # type: ignore[arg-type]
        context_pack_ref=object(),  # type: ignore[arg-type]
        working_state_ref=object(),  # type: ignore[arg-type]
        environment_ref=object(),  # type: ignore[arg-type]
    )

    updated = store.get_step_attempt(attempt.step_attempt_id)
    assert updated is not None
    assert updated.context == {"phase": "draft"}
    assert updated.context_pack_ref == "artifact_context_pack"
    assert updated.working_state_ref == "artifact_working_state"
    assert updated.environment_ref == "artifact_environment"


def test_kernel_store_tolerates_foreign_object_sentinels_in_ingress_updates(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-ingress-sentinel", source_channel="scheduler")
    ingress = store.create_ingress(
        conversation_id="conv-ingress-sentinel",
        source_channel="scheduler",
        raw_text="run the job",
        normalized_text="run the job",
    )

    store.update_ingress(
        ingress.ingress_id,
        status=object(),  # type: ignore[arg-type]
        resolution=object(),  # type: ignore[arg-type]
        chosen_task_id=object(),  # type: ignore[arg-type]
        parent_task_id=object(),  # type: ignore[arg-type]
        confidence=object(),  # type: ignore[arg-type]
        margin=object(),  # type: ignore[arg-type]
        rationale=object(),  # type: ignore[arg-type]
    )

    updated = store.get_ingress(ingress.ingress_id)
    assert updated is not None
    assert updated.status == "received"
    assert updated.resolution == "none"
    assert updated.chosen_task_id is None
    assert updated.parent_task_id is None
    assert updated.confidence is None
    assert updated.margin is None
    assert updated.rationale == {}


def test_kernel_store_backfills_event_hash_chain_when_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = KernelStore(db_path)
    store.ensure_conversation("conv-proof", source_channel="chat")
    task = store.create_task(
        conversation_id="conv-proof",
        title="Proof Task",
        goal="Backfill event hash chain",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    with store._lock, store._conn:  # type: ignore[attr-defined]
        store._conn.execute(  # type: ignore[attr-defined]
            "UPDATE events SET event_hash = NULL, prev_event_hash = NULL, hash_chain_algo = NULL"
        )
    store.close()

    reopened = KernelStore(db_path)
    try:
        proof = ProofService(reopened).verify_task_chain(task.task_id)
        events = reopened.list_events(task_id=task.task_id, limit=20)
        assert proof["valid"] is True
        assert all(event["event_hash"] for event in events)
        assert all(event["hash_chain_algo"] == "sha256-v1" for event in events)
    finally:
        reopened.close()


def test_kernel_store_accepts_schema_version_5_for_additive_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = KernelStore(db_path)
    try:
        with store._lock, store._conn:  # type: ignore[attr-defined]
            store._conn.execute(  # type: ignore[attr-defined]
                "UPDATE kernel_meta SET value = '5' WHERE key = 'schema_version'"
            )
    finally:
        store.close()

    reopened = KernelStore(db_path)
    try:
        assert reopened.schema_version() == "7"
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM kernel_meta WHERE key = 'schema_version'"
            ).fetchone()
        assert row is not None
        assert row[0] == "7"
    finally:
        reopened.close()


def test_kernel_store_round_trips_canonical_ledger_fields(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-canonical", source_channel="chat")
    task = store.create_task(
        conversation_id="conv-canonical",
        title="Canonical Task",
        goal="Round-trip canonical fields",
        source_channel="chat",
        task_contract_ref="contract.task/canonical",
    )
    step = store.create_step(
        task_id=task.task_id,
        kind="respond",
        title="Canonical step",
        contract_ref="contract.step/canonical",
        depends_on=["step_seed"],
        max_attempts=4,
    )
    attempt = store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        context={"phase": "canonical"},
        context_pack_ref="artifact_context_pack",
        working_state_ref="artifact_working_state",
        environment_ref="artifact_environment",
        action_request_ref="artifact_action_request",
        policy_result_ref="artifact_policy_result",
        approval_packet_ref="artifact_approval_packet",
        idempotency_key="idem-canonical",
        executor_mode="tool_executor",
        policy_version="rules-v3",
        resume_from_ref="artifact_runtime_snapshot",
    )
    payload_path = tmp_path / "artifact.json"
    payload_path.write_text('{"ok": true}', encoding="utf-8")
    artifact = store.create_artifact(
        task_id=task.task_id,
        step_id=step.step_id,
        kind="working_state/v1",
        uri=str(payload_path),
        content_hash="hash-artifact",
        producer="test",
        artifact_class="working_state",
        media_type="application/json",
        byte_size=payload_path.stat().st_size,
        sensitivity_class="operator_internal",
        lineage_ref="artifact_context_pack",
    )
    decision = store.create_decision(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="execution_authorization",
        verdict="allow",
        reason="Policy allowed canonical execution.",
        summary="Canonical allow",
        rationale="Policy allowed canonical execution.",
        risk_level="medium",
        reversible=True,
        evidence_refs=[artifact.artifact_id],
        policy_ref="artifact_policy_result",
        action_type="write_local",
    )
    approval = store.create_approval(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        approval_type="write_local",
        requested_action={"summary": "Approve canonical write"},
        request_packet_ref="artifact_approval_packet_legacy",
        requested_action_ref="artifact_action_request",
        approval_packet_ref="artifact_approval_packet",
        policy_result_ref="artifact_policy_result",
        decision_ref=decision.decision_id,
        expires_at=123.0,
    )
    receipt = store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        receipt_class="write_local",
        input_refs=["artifact_input"],
        environment_ref="artifact_environment",
        policy_result={"decision": "allow"},
        approval_ref=approval.approval_id,
        output_refs=["artifact_output"],
        result_summary="canonical result",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        capability_grant_ref="grant_1",
        workspace_lease_ref="lease_1",
        policy_ref="artifact_policy_result",
        action_request_ref="artifact_action_request",
        policy_result_ref="artifact_policy_result",
        verifiability="baseline_verifiable",
        signer_ref="local-hmac",
    )

    fetched_artifact = store.get_artifact(artifact.artifact_id)
    assert fetched_artifact is not None
    assert fetched_artifact.artifact_class == "working_state"
    assert fetched_artifact.media_type == "application/json"
    assert fetched_artifact.byte_size == payload_path.stat().st_size
    assert fetched_artifact.sensitivity_class == "operator_internal"
    assert fetched_artifact.lineage_ref == "artifact_context_pack"

    fetched_decision = store.get_decision(decision.decision_id)
    assert fetched_decision is not None
    assert fetched_decision.summary == "Canonical allow"
    assert fetched_decision.rationale == "Policy allowed canonical execution."
    assert fetched_decision.risk_level == "medium"
    assert fetched_decision.reversible is True

    fetched_approval = store.get_approval(approval.approval_id)
    assert fetched_approval is not None
    assert fetched_approval.requested_action_ref == "artifact_action_request"
    assert fetched_approval.approval_packet_ref == "artifact_approval_packet"
    assert fetched_approval.policy_result_ref == "artifact_policy_result"
    assert fetched_approval.expires_at == 123.0

    fetched_receipt = store.get_receipt(receipt.receipt_id)
    assert fetched_receipt is not None
    assert fetched_receipt.receipt_class == "write_local"
    assert fetched_receipt.action_request_ref == "artifact_action_request"
    assert fetched_receipt.policy_result_ref == "artifact_policy_result"
    assert fetched_receipt.verifiability == "baseline_verifiable"
    assert fetched_receipt.signer_ref == "local-hmac"


def test_proof_service_detects_tampered_event_chain(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-tamper", source_channel="chat")
    task = store.create_task(
        conversation_id="conv-tamper",
        title="Tampered Task",
        goal="Detect event tampering",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    events = store.list_events(task_id=task.task_id, limit=20)
    assert events
    tampered_event_id = events[-1]["event_id"]
    with store._lock, store._conn:  # type: ignore[attr-defined]
        store._conn.execute(  # type: ignore[attr-defined]
            "UPDATE events SET payload_json = ? WHERE event_id = ?",
            ('{"tampered":true}', tampered_event_id),
        )
    proof = ProofService(store).verify_task_chain(task.task_id)
    assert proof["valid"] is False
    assert proof["broken_at_event_id"] == tampered_event_id


def test_proof_service_exports_signed_bundles_and_inclusion_proofs(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-signed", source_channel="chat")
    task = store.create_task(
        conversation_id="conv-signed",
        title="Signed Proof Task",
        goal="Export signed proof bundle",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    first_attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    second_attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    first_receipt = store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=first_attempt.step_attempt_id,
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="first",
        result_code="succeeded",
    )
    second_receipt = store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=second_attempt.step_attempt_id,
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="second",
        result_code="succeeded",
    )

    service = ProofService(store, signing_secret="proof-secret", signing_key_id="test-key")
    export = service.export_task_proof(task.task_id)

    assert export["proof_mode"] == "signed_with_inclusion_proof"
    assert export["receipt_merkle_root"]
    assert export["signature"]["key_id"] == "test-key"
    assert set(export["receipt_inclusion_proofs"]) == {
        first_receipt.receipt_id,
        second_receipt.receipt_id,
    }
    summary = service.build_proof_summary(task.task_id)
    assert summary["proof_mode"] == "signed_with_inclusion_proof"
    assert summary["proof_coverage"]["signature_coverage"]["signed_receipts"] == 2
    assert summary["proof_coverage"]["inclusion_proof_coverage"]["proved_receipts"] == 2
    refreshed_first = store.get_receipt(first_receipt.receipt_id)
    refreshed_second = store.get_receipt(second_receipt.receipt_id)
    assert (
        refreshed_first is not None and refreshed_first.proof_mode == "signed_with_inclusion_proof"
    )
    assert (
        refreshed_second is not None
        and refreshed_second.proof_mode == "signed_with_inclusion_proof"
    )


def test_context_manifest_scopes_memory_refs_to_the_task_conversation(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-proof", source_channel="chat")
    store.ensure_conversation("conv-other", source_channel="chat")
    task = store.create_task(
        conversation_id="conv-proof",
        title="Scoped Proof Task",
        goal="Export scoped manifest",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    receipt = store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="done",
        result_code="succeeded",
    )
    relevant_memory = store.create_memory_record(
        task_id=task.task_id,
        conversation_id="conv-proof",
        category="fact",
        content="Scoped memory",
        status="active",
    )
    store.create_memory_record(
        task_id="task_other",
        conversation_id="conv-other",
        category="fact",
        content="Foreign memory",
        status="active",
    )
    store.create_memory_record(
        task_id=task.task_id,
        conversation_id="conv-proof",
        category="fact",
        content="Expired memory",
        status="active",
        expires_at=1.0,
    )

    service = ProofService(store)
    bundle_ref = service.ensure_receipt_bundle(receipt.receipt_id)

    bundle_artifact = store.get_artifact(bundle_ref)
    assert bundle_artifact is not None
    bundle_payload = json.loads(Path(bundle_artifact.uri).read_text(encoding="utf-8"))
    context_manifest = store.get_artifact(bundle_payload["context_manifest_ref"])
    assert context_manifest is not None
    manifest_payload = json.loads(Path(context_manifest.uri).read_text(encoding="utf-8"))
    assert manifest_payload["memory_refs"] == [relevant_memory.memory_id]
