"""Memory promotion pipeline: full governed kernel promotion."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from hermit.plugins.builtin.hooks.memory.engine import MemoryEngine
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry

log = structlog.get_logger()

# Module-level lock to prevent TOCTOU race between has_active_task_with_goal()
# and start_task() when multiple threads attempt checkpoint promotion concurrently.
_PROMOTION_LOCK = threading.Lock()


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
    try:
        task_goal = f"Promote durable memory ({mode})"

        # Deduplicate: skip if a checkpoint task with the same goal is already
        # queued, running, or blocked.  Without this guard, checkpoint tasks
        # pile up every 10-20 s and consume all active task slots.
        # The lock prevents a TOCTOU race between the check and task creation.
        with _PROMOTION_LOCK:
            if store.has_active_task_with_goal(task_goal, policy_profile="memory"):
                log.info(
                    "memory_promotion_deduplicated",
                    mode=mode,
                    session_id=session_id,
                    reason="active_task_exists",
                )
                return False

            artifact_store = ArtifactStore(Path(kernel_artifacts_dir))
            controller = TaskController(store)
            # Use a distinct conversation_id so memory tasks never share
            # a conversation with the parent DAG/session task.  Sharing
            # the conversation_id caused the conversation focus to shift
            # to the memory task, stalling the DAG's dispatch loop
            # (see #40).
            memory_conversation_id = (
                f"{session_id}-memory-{uuid.uuid4().hex[:8]}"
                if session_id
                else f"memory-{mode}-{uuid.uuid4().hex[:8]}"
            )
            ctx = controller.start_task(
                conversation_id=memory_conversation_id,
                goal=task_goal,
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
                task_id=ctx.task_id,
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
                status_reason=str(exc),
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
        # GC3: No durable learning without reconciliation.
        # The reconciliation gate is enforced inside promote_from_belief():
        # - Durable scope (global/workspace) requires a valid reconciliation_ref.
        # - Conversation scope allows promotion without reconciliation (ephemeral).
        # - If a durable-scoped belief has no reconciliation, promotion is blocked.
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
            # Index embeddings for newly promoted memories so retrieval can use them
            try:
                from hermit.kernel.context.memory.embeddings import (
                    EmbeddingService,
                    ensure_embedding_schema,
                )

                ensure_embedding_schema(store)
                embedding_svc = EmbeddingService()
                for mem_id in promoted_memories:
                    mem_record = store.get_memory_record(mem_id)
                    if mem_record is not None:
                        embedding_svc.index_memory(mem_id, mem_record.claim_text, store)
            except (ImportError, OSError, RuntimeError):
                import structlog as _log

                _log.get_logger().warning(
                    "embedding_index_failed_non_critical", memory_ids=promoted_memories
                )

            # Post-promotion enrichment: influence_link and episode_index records
            try:
                for mem_id in promoted_memories:
                    store.create_memory_record(
                        task_id=ctx.task_id,
                        conversation_id=ctx.conversation_id,
                        category="enrichment",
                        memory_kind="influence_link",
                        claim_text=(
                            f"Influence link for memory {mem_id} from {ctx.conversation_id}"
                        ),
                        confidence=0.7,
                    )
                store.create_memory_record(
                    task_id=ctx.task_id,
                    conversation_id=ctx.conversation_id,
                    category="enrichment",
                    memory_kind="episode_index",
                    claim_text=(
                        f"Episode index for session "
                        f"{session_id or ctx.conversation_id} "
                        f"({len(promoted_memories)} memories promoted via {mode})"
                    ),
                    confidence=0.7,
                )
            except (OSError, RuntimeError):
                log.warning(
                    "enrichment_records_failed_non_critical",
                    memory_ids=promoted_memories,
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


def create_memory_promotion_handler(runner: Any) -> Callable[..., Any]:
    """Factory returning a callable handler for the ``memory_promotion`` step kind.

    The returned handler is registered on the dispatch service via
    ``register_kind_handler("memory_promotion", handler)`` so that the
    kernel dispatch loop can execute memory promotion steps.

    Parameters
    ----------
    runner:
        The ``AgentRunner`` instance, used to access plugin settings and
        the session manager.
    """

    def _handle_memory_promotion(step_attempt_id: str, **kwargs: Any) -> Any:
        settings = getattr(getattr(runner, "pm", None), "settings", None)
        if settings is None:
            log.warning(
                "memory_promotion_handler_no_settings",
                step_attempt_id=step_attempt_id,
            )
            return None

        store = getattr(runner, "_get_store", lambda: None)()
        if store is None:
            log.warning(
                "memory_promotion_handler_no_store",
                step_attempt_id=step_attempt_id,
            )
            return None

        # Retrieve step attempt context to find the session and goal metadata
        attempt = store.get_step_attempt(step_attempt_id)
        if attempt is None:
            log.warning(
                "memory_promotion_handler_attempt_not_found",
                step_attempt_id=step_attempt_id,
            )
            return None

        task = store.get_task(attempt.task_id)
        session_id = getattr(task, "conversation_id", "") or ""
        session = runner.session_manager.get_or_create(session_id) if session_id else None
        messages = list(getattr(session, "messages", [])) if session else []

        engine = MemoryEngine(settings)
        return promote_memories_via_kernel(
            engine,
            settings,
            session_id=session_id,
            messages=messages,
            used_keywords=set(),
            new_entries=[],
            mode="dispatch",
        )

    return _handle_memory_promotion


__all__ = ["create_memory_promotion_handler", "promote_memories_via_kernel"]
