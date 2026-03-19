"""Memory promotion pipeline: full governed kernel promotion."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import structlog

from hermit.plugins.builtin.hooks.memory.engine import MemoryEngine
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry

log = structlog.get_logger()


def promote_memories_via_kernel(
    engine: MemoryEngine,
    settings: Any,
    *,
    session_id: str,
    messages: list[dict[str, Any]],
    used_keywords: set[str],
    new_entries: list[MemoryEntry],
    mode: str,
) -> bool:
    """Execute the full governed memory promotion pipeline.

    Creates a kernel task, evaluates policy, issues capability grant,
    records beliefs, promotes to durable memory, and captures rollback targets.
    """
    kernel_db_path = getattr(settings, "kernel_db_path", None)
    kernel_artifacts_dir = getattr(settings, "kernel_artifacts_dir", None)
    if not kernel_db_path or not kernel_artifacts_dir or not new_entries:
        return False

    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.authority.grants import CapabilityGrantError, CapabilityGrantService
    from hermit.kernel.authority.workspaces import (
        WorkspaceLeaseService,
        capture_execution_environment,
    )
    from hermit.kernel.context.memory.knowledge import BeliefService, MemoryRecordService
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.policy import ActionRequest, PolicyEngine
    from hermit.kernel.policy.approvals.decisions import DecisionService
    from hermit.kernel.task.models.records import BeliefRecord
    from hermit.kernel.task.services.controller import TaskController
    from hermit.kernel.verification.receipts.receipts import ReceiptService
    from hermit.plugins.builtin.hooks.memory.hooks_extraction import (
        format_transcript,
        memory_entry_payload,
    )

    store = KernelStore(Path(kernel_db_path))
    ctx = None
    controller = None
    try:
        artifact_store = ArtifactStore(Path(kernel_artifacts_dir))
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id=session_id or f"memory-{mode}",
            goal=f"Promote durable memory ({mode})",
            source_channel=controller.source_from_session(session_id or "memory"),
            kind="memory_promotion",
            policy_profile="memory",
            workspace_root=str(Path(settings.memory_file).parent),
        )
        policy_engine = PolicyEngine()
        decision_service = DecisionService(store)
        belief_service = BeliefService(store)
        memory_service = MemoryRecordService(store, mirror_path=Path(settings.memory_file))
        capability_service = CapabilityGrantService(store)
        workspace_lease_service = WorkspaceLeaseService(store, artifact_store)
        receipt_service = ReceiptService(store, artifact_store)
        request_id = f"memreq_{uuid.uuid4().hex[:12]}"
        holder_principal_id = "memory_hook"
        workspace_root = str(Path(settings.memory_file).parent.resolve())

        transcript = format_transcript(messages)
        transcript_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="memory_evidence.transcript",
            payload={"mode": mode, "transcript": transcript},
            metadata={"mode": mode},
            event_type="memory.evidence.captured",
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        extraction_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="memory_evidence.extraction",
            payload={
                "mode": mode,
                "used_keywords": sorted(used_keywords),
                "new_entries": [memory_entry_payload(entry) for entry in new_entries],
            },
            metadata={"mode": mode, "entry_count": len(new_entries)},
            event_type="memory.extraction.recorded",
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        action_request = ActionRequest(
            request_id=request_id,
            idempotency_key=request_id,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            conversation_id=ctx.conversation_id,
            tool_name="memory_promotion",
            tool_input={
                "mode": mode,
                "entry_count": len(new_entries),
                "entries": [memory_entry_payload(entry) for entry in new_entries],
            },
            action_class="memory_write",
            resource_scopes=["memory_store"],
            risk_hint="medium",
            requires_receipt=True,
            actor={"kind": "kernel", "agent_id": "memory"},
            context={
                "policy_profile": "memory",
                "source_ingress": "memory_hook",
                "workspace_root": str(Path(settings.memory_file).parent),
                "evidence_refs": [transcript_ref, extraction_ref],
                "mode": mode,
            },
            derived={"categories": sorted({entry.category for entry in new_entries})},
        )
        action_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="action_request",
            payload=action_request.to_dict(),
            metadata={"mode": mode, "tool_name": action_request.tool_name},
            event_type="action.requested",
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        policy = policy_engine.evaluate(action_request)
        policy_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="policy_evaluation",
            payload={
                "tool_name": action_request.tool_name,
                "action_class": action_request.action_class,
                "risk_band": policy.risk_level,
                "verdict": policy.verdict,
                "reason": policy.reason,
                "reasons": [reason.to_dict() for reason in policy.reasons],
                "obligations": policy.obligations.to_dict(),
                "policy_profile": "memory",
            },
            metadata={"mode": mode, "tool_name": action_request.tool_name},
            event_type="policy.evaluated",
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        if policy.verdict == "deny" or policy.obligations.require_approval:
            controller.finalize_result(ctx, status="failed")
            return False

        decision_id = decision_service.record(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_type="memory_promotion",
            verdict=policy.verdict,
            reason=policy.reason or "Evidence-bound durable memory promotion allowed.",
            evidence_refs=[transcript_ref, extraction_ref, action_ref, policy_ref],
            policy_ref=policy_ref,
            action_type="memory_write",
            decided_by="memory_hook",
        )
        workspace_lease = workspace_lease_service.acquire(
            task_id=ctx.task_id,
            step_attempt_id=ctx.step_attempt_id,
            workspace_id="memory_store",
            root_path=workspace_root,
            holder_principal_id=holder_principal_id,
            mode="mutable",
            resource_scope=["memory_store", str(Path(settings.memory_file).resolve())],
        )
        capability_grant_id = capability_service.issue(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_ref=decision_id,
            approval_ref=None,
            policy_ref=policy_ref,
            issued_to_principal_id=holder_principal_id,
            issued_by_principal_id="kernel",
            workspace_lease_ref=workspace_lease.lease_id,
            action_class="memory_write",
            resource_scope=["memory_store"],
            idempotency_key=request_id,
            constraints={
                "lease_root_path": workspace_root,
                "mode": mode,
                "entry_count": len(new_entries),
                "categories": sorted({entry.category for entry in new_entries}),
            },
        )
        store.update_step_attempt(
            ctx.step_attempt_id,
            status="dispatching",
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease.lease_id,
        )
        store.update_step(ctx.step_id, status="dispatching")
        try:
            capability_service.enforce(
                capability_grant_id,
                action_class="memory_write",
                resource_scope=["memory_store"],
                constraints={
                    "lease_root_path": workspace_root,
                    "mode": mode,
                    "entry_count": len(new_entries),
                    "categories": sorted({entry.category for entry in new_entries}),
                },
            )
        except CapabilityGrantError as exc:
            store.append_event(
                event_type="dispatch.denied",
                entity_type="capability_grant",
                entity_id=capability_grant_id,
                task_id=ctx.task_id,
                step_id=ctx.step_id,
                actor="kernel",
                payload={
                    "capability_grant_ref": capability_grant_id,
                    "decision_ref": decision_id,
                    "error_code": exc.code,
                    "error": str(exc),
                    "tool_name": action_request.tool_name,
                },
            )
            store.update_step_attempt(
                ctx.step_attempt_id,
                status="failed",
                waiting_reason=str(exc),
                decision_id=decision_id,
                capability_grant_id=capability_grant_id,
                workspace_lease_id=workspace_lease.lease_id,
            )
            store.update_step(ctx.step_id, status="failed")
            controller.finalize_result(ctx, status="failed")
            return False

        belief_records: list[BeliefRecord] = []
        promoted_beliefs: list[str] = []
        promoted_memories: list[str] = []
        for entry in new_entries:
            belief = belief_service.record(
                task_id=ctx.task_id,
                conversation_id=ctx.conversation_id,
                scope_kind="conversation",
                scope_ref=ctx.conversation_id,
                category=entry.category,
                content=entry.content,
                confidence=entry.confidence,
                evidence_refs=[transcript_ref, extraction_ref, action_ref],
                supersedes=list(entry.supersedes),
                validation_basis="memory_hook_extraction",
            )
            belief_records.append(belief)
            promoted_beliefs.append(belief.belief_id)

        output_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="memory_promotion_result",
            payload={
                "mode": mode,
                "used_keywords": sorted(used_keywords),
                "new_entries": [memory_entry_payload(entry) for entry in new_entries],
            },
            metadata={"mode": mode, "entry_count": len(new_entries)},
            event_type="memory.promoted",
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        env_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="environment",
            payload=capture_execution_environment(cwd=Path(settings.memory_file).parent),
            metadata={"mode": mode},
            entity_type="step_attempt",
            event_type=None,
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        receipt_id = receipt_service.issue(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            action_type="memory_write",
            input_refs=[transcript_ref, extraction_ref],
            environment_ref=env_ref,
            policy_result=policy.to_dict(),
            approval_ref=None,
            output_refs=[output_ref],
            result_summary=f"Promoted {len(new_entries)} durable memory entries via {mode}.",
            result_code="succeeded",
            decision_ref=decision_id,
            capability_grant_ref=capability_grant_id,
            workspace_lease_ref=workspace_lease.lease_id,
            policy_ref=policy_ref,
            idempotency_key=request_id,
            rollback_supported=False,
            rollback_strategy="supersede_or_invalidate",
            observed_effect_summary=f"Prepared {len(new_entries)} durable memory candidate(s).",
            reconciliation_required=True,
        )
        reconciliation = store.create_reconciliation(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            contract_ref=f"memory_write:{ctx.step_attempt_id}",
            receipt_refs=[receipt_id],
            observed_output_refs=[output_ref],
            intended_effect_summary=f"Promote {len(new_entries)} durable memory candidate(s).",
            authorized_effect_summary=f"Promote {len(new_entries)} durable memory candidate(s).",
            observed_effect_summary=f"Prepared {len(new_entries)} durable memory candidate(s).",
            receipted_effect_summary=f"Promoted {len(new_entries)} durable memory entries via {mode}.",
            result_class="satisfied",
            confidence_delta=0.2,
            recommended_resolution="promote_learning",
            operator_summary=f"satisfied: durable memory promotion via {mode}",
        )
        store.update_step_attempt(
            ctx.step_attempt_id,
            reconciliation_ref=reconciliation.reconciliation_id,
        )
        store.append_event(
            event_type="reconciliation.closed",
            entity_type="step_attempt",
            entity_id=ctx.step_attempt_id,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            actor="kernel",
            payload={
                "reconciliation_ref": reconciliation.reconciliation_id,
                "receipt_ref": receipt_id,
                "result_class": reconciliation.result_class,
            },
        )
        for belief in belief_records:
            memory = memory_service.promote_from_belief(
                belief=belief,
                conversation_id=ctx.conversation_id,
                workspace_root=str(Path(settings.memory_file).parent),
                reconciliation_ref=reconciliation.reconciliation_id,
            )
            if memory is not None:
                promoted_memories.append(memory.memory_id)
        if promoted_memories:
            memory_service.export_mirror(Path(settings.memory_file))
            _enrich_promoted_memories(
                promoted_memories,
                store,
                task_id=ctx.task_id,
                conversation_id=ctx.conversation_id,
                decision_id=decision_id,
            )

        rollback_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="rollback.memory_targets",
            payload={"belief_ids": promoted_beliefs, "memory_ids": promoted_memories},
            metadata={"mode": mode, "entry_count": len(promoted_memories)},
            event_type="memory.rollback_captured",
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        store.update_receipt_rollback_fields(
            receipt_id,
            rollback_supported=True,
            rollback_strategy="supersede_or_invalidate",
            rollback_artifact_refs=[rollback_ref],
        )
        capability_service.consume(capability_grant_id)
        controller.finalize_result(ctx, status="succeeded")
        return True
    except Exception:
        log.error("memory_promotion_failed", mode=mode, exc_info=True)
        if ctx is not None and controller is not None:
            try:
                controller.finalize_result(ctx, status="failed")
            except Exception:
                log.error("memory_promotion_finalize_failed", task_id=ctx.task_id, exc_info=True)
        return False
    finally:
        store.close()


def _store_memory_artifact(
    store: Any,
    artifact_store: Any,
    *,
    task_id: str,
    step_id: str,
    kind: str,
    payload: Any,
    metadata: dict[str, Any],
    task_context: Any,
    event_type: str | None,
    entity_id: str,
    entity_type: str = "step_attempt",
) -> str:
    uri, content_hash = artifact_store.store_json(payload)
    artifact = store.create_artifact(
        task_id=task_id,
        step_id=step_id,
        kind=kind,
        uri=uri,
        content_hash=content_hash,
        producer="memory_hook",
        retention_class="audit",
        trust_tier="observed",
        metadata=metadata,
    )
    if event_type:
        store.append_event(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            task_id=task_context.task_id,
            step_id=task_context.step_id,
            actor="kernel",
            payload={"artifact_ref": artifact.artifact_id, **metadata},
        )
    return artifact.artifact_id


def _enrich_promoted_memories(
    promoted_memory_ids: list[str],
    store: Any,
    *,
    task_id: str,
    conversation_id: str,
    decision_id: str,
) -> None:
    """Post-promotion enrichment: index, graph, procedural, episodic, lineage.

    Each service runs in its own try/except so a failure in one
    never blocks the others or the promotion pipeline.
    """
    from hermit.plugins.builtin.hooks.memory.services import get_services

    try:
        services = get_services(store)
    except Exception:
        log.warning("enrich_services_init_failed", exc_info=True)
        return

    # Collect MemoryRecord objects for per-memory enrichment
    records = []
    for mid in promoted_memory_ids:
        rec = store.get_memory_record(mid)
        if rec is not None:
            records.append(rec)

    # 1. Embedding index — per memory
    for rec in records:
        try:
            services.embedding.index_memory(rec.memory_id, rec.claim_text, store)
        except Exception:
            log.warning("enrich_embedding_failed", memory_id=rec.memory_id, exc_info=True)

    # 2. Knowledge graph — per memory
    for rec in records:
        try:
            triples = services.graph.extract_entities(rec)
            if triples:
                services.graph.store_triples(triples, store)
                services.graph.auto_link(rec.memory_id, store)
        except Exception:
            log.warning("enrich_graph_failed", memory_id=rec.memory_id, exc_info=True)

    # 3. Procedural extraction — per memory
    for rec in records:
        try:
            proc = services.procedural.extract_procedure(rec)
            if proc is not None:
                services.procedural.save_procedure(proc, store)
        except Exception:
            log.warning("enrich_procedural_failed", memory_id=rec.memory_id, exc_info=True)

    # 4. Episodic index — once per batch (indexes task-level episode)
    try:
        services.episodic.index_episode(task_id, store, conversation_id=conversation_id)
    except Exception:
        log.warning("enrich_episodic_failed", task_id=task_id, exc_info=True)

    # 5. Memory lineage — once per batch
    try:
        services.lineage.record_influence(
            context_pack_id="",
            decision_ids=[decision_id],
            memory_ids=promoted_memory_ids,
            store=store,
            task_id=task_id,
            conversation_id=conversation_id,
        )
    except Exception:
        log.warning("enrich_lineage_failed", task_id=task_id, exc_info=True)

    log.debug(
        "post_promotion_enrichment_done",
        memory_count=len(records),
        task_id=task_id,
    )


__all__ = ["promote_memories_via_kernel"]
