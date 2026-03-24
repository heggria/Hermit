from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hermit.kernel.context.memory.governance import MemoryGovernanceService
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import BeliefRecord, MemoryRecord
from hermit.plugins.builtin.hooks.memory.engine import MemoryEngine
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry

if TYPE_CHECKING:
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.verification.receipts.receipts import ReceiptService


class BeliefService:
    def __init__(self, store: KernelStore) -> None:
        self.store = store

    def record(
        self,
        *,
        task_id: str,
        conversation_id: str | None,
        scope_kind: str,
        scope_ref: str,
        category: str,
        content: str,
        confidence: float,
        evidence_refs: list[str],
        trust_tier: str = "observed",
        supersedes: list[str] | None = None,
        contradicts: list[str] | None = None,
        evidence_case_ref: str | None = None,
        epistemic_origin: str = "observation",
        freshness_class: str | None = None,
        validation_basis: str | None = None,
        structured_assertion: dict[str, Any] | None = None,
    ) -> BeliefRecord:
        return self.store.create_belief(
            task_id=task_id,
            conversation_id=conversation_id,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            category=category,
            claim_text=content,
            structured_assertion=structured_assertion,
            confidence=confidence,
            trust_tier=trust_tier,
            evidence_refs=evidence_refs,
            supersedes=supersedes,
            contradicts=contradicts,
            evidence_case_ref=evidence_case_ref,
            epistemic_origin=epistemic_origin,
            freshness_class=freshness_class,
            validation_basis=validation_basis,
            last_validated_at=time.time() if validation_basis else None,
        )

    def supersede(self, belief_id: str, superseded_contents: list[str]) -> None:
        self.store.update_belief(belief_id, status="superseded", supersedes=superseded_contents)

    def contradict(self, belief_id: str, contradicting_ids: list[str]) -> None:
        self.store.update_belief(belief_id, status="contradicted", contradicts=contradicting_ids)

    def invalidate(self, belief_id: str) -> None:
        self.store.update_belief(belief_id, status="invalidated", invalidated_at=time.time())


class MemoryRecordService:
    def __init__(
        self,
        store: KernelStore,
        *,
        mirror_path: Path | None = None,
        receipt_service: ReceiptService | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self.store = store
        self.mirror_path = mirror_path
        self.governance = MemoryGovernanceService()
        self._receipt_service = receipt_service
        self._artifact_store = artifact_store

    def promote_from_belief(
        self,
        *,
        belief: BeliefRecord,
        conversation_id: str | None,
        workspace_root: str = "",
        reconciliation_ref: str | None = None,
    ) -> MemoryRecord | None:
        blocking_reason: str = ""
        if reconciliation_ref:
            resolved_reconciliation_ref = reconciliation_ref
        else:
            resolved_reconciliation_ref, blocking_reason = self._eligible_reconciliation_ref(
                belief.task_id
            )

        # GC3: No durable learning without reconciliation.
        # Classify first to determine target scope, then enforce the gate.
        classification = self.governance.classify_belief(belief, workspace_root=workspace_root)
        is_durable_scope = classification.scope_kind in ("global", "workspace")

        if is_durable_scope:
            # Durable memories (global/workspace) require a valid reconciliation_ref.
            if resolved_reconciliation_ref is None:
                self.store.update_belief(
                    belief.belief_id,
                    promotion_candidate=False,
                    validation_basis=f"promotion_blocked:{blocking_reason or 'durable_requires_reconciliation'}",
                )
                return None
            # Validate that the reconciliation_ref points to a valid, non-invalidated
            # reconciliation with result_class == "satisfied".
            validation_reason = self._validate_reconciliation_ref(resolved_reconciliation_ref)
            if validation_reason:
                self.store.update_belief(
                    belief.belief_id,
                    promotion_candidate=False,
                    validation_basis=f"promotion_blocked:{validation_reason}",
                )
                return None
        else:
            # Conversation-scoped (ephemeral) memories are allowed without reconciliation;
            # they serve as working memory and do not persist durably.
            pass
        existing = self.store.list_memory_records(status="active", limit=500)
        duplicate_record, superseded_records = self.governance.find_superseded_records(
            classification=classification,
            claim_text=belief.claim_text,
            active_records=existing,
            entry_from_record=self._entry_from_memory,
        )
        validation_basis = (
            f"reconciliation:{resolved_reconciliation_ref}"
            if resolved_reconciliation_ref
            else "ephemeral_working_memory"
        )
        if duplicate_record is not None:
            self.store.update_belief(
                belief.belief_id,
                memory_ref=duplicate_record.memory_id,
                promotion_candidate=False,
                validation_basis=validation_basis,
                last_validated_at=time.time(),
            )
            return duplicate_record
        supersedes = [record.claim_text for record in superseded_records]
        belief_assertion = dict(belief.structured_assertion)
        try:
            importance = int(belief_assertion.pop("importance", 5))
        except (ValueError, TypeError):
            importance = 5
        memory = self.store.create_memory_record(
            task_id=belief.task_id,
            conversation_id=conversation_id,
            category=classification.category,
            claim_text=belief.claim_text,
            structured_assertion={
                **dict(classification.structured_assertion or {}),
                **belief_assertion,
            },
            scope_kind=classification.scope_kind,
            scope_ref=classification.scope_ref,
            promotion_reason=classification.promotion_reason,
            retention_class=classification.retention_class,
            status="active",
            confidence=belief.confidence,
            trust_tier="durable" if is_durable_scope else "observed",
            evidence_refs=list(belief.evidence_refs),
            supersedes=supersedes,
            supersedes_memory_ids=[record.memory_id for record in superseded_records],
            source_belief_ref=belief.belief_id,
            expires_at=classification.expires_at,
            memory_kind="contract_template"
            if classification.category == "contract_template"
            else "durable_fact",
            validation_basis=validation_basis,
            last_validated_at=time.time(),
            learned_from_reconciliation_ref=resolved_reconciliation_ref,
            importance=importance,
        )
        self.store.update_belief(
            belief.belief_id,
            memory_ref=memory.memory_id,
            promotion_candidate=False,
            validation_basis=validation_basis,
            last_validated_at=time.time(),
        )
        for record in superseded_records:
            self.store.update_memory_record(
                record.memory_id,
                status="invalidated",
                supersedes=list({*record.supersedes, memory.claim_text}),
                superseded_by_memory_id=memory.memory_id,
                invalidation_reason="superseded",
                invalidated_at=time.time(),
                supersession_reason=validation_basis,
            )
        self._issue_memory_write_receipt(
            belief=belief,
            memory=memory,
            superseded_records=superseded_records,
        )
        return memory

    def invalidate(self, memory_id: str) -> None:
        self._issue_memory_invalidate_receipt(memory_id)
        self.store.update_memory_record(memory_id, status="invalidated", invalidated_at=time.time())

    def invalidate_by_reconciliation(self, reconciliation_ref: str, result_class: str) -> list[str]:
        if result_class != "violated":
            return []
        invalidated_ids: list[str] = []
        for record in self.store.list_memory_records(status="active", limit=5000):
            learned_ref = str(getattr(record, "learned_from_reconciliation_ref", "") or "").strip()
            if learned_ref == reconciliation_ref:
                self.store.update_memory_record(
                    record.memory_id,
                    status="invalidated",
                    invalidation_reason=f"reconciliation_violated:{reconciliation_ref}",
                    invalidated_at=time.time(),
                )
                invalidated_ids.append(record.memory_id)
        return invalidated_ids

    def reconcile_active_records(self) -> dict[str, int]:
        active_records = sorted(
            self.store.list_memory_records(status="active", limit=5000),
            key=lambda record: (float(record.updated_at or 0.0), float(record.created_at or 0.0)),
        )
        accepted: list[MemoryRecord] = []
        superseded_count = 0
        duplicate_count = 0
        for record in active_records:
            classification = self.governance.classify_claim(
                category=record.category,
                claim_text=record.claim_text,
                conversation_id=record.conversation_id,
                workspace_root=record.scope_ref if record.scope_kind == "workspace" else "",
                promotion_reason=record.promotion_reason,
            )
            duplicate_record, superseded_records = self.governance.find_superseded_records(
                classification=classification,
                claim_text=record.claim_text,
                active_records=accepted,
                entry_from_record=self._entry_from_memory,
            )
            if duplicate_record is not None:
                self.store.update_memory_record(
                    record.memory_id,
                    status="invalidated",
                    supersedes=list({*record.supersedes, duplicate_record.claim_text}),
                    superseded_by_memory_id=duplicate_record.memory_id,
                    invalidation_reason="duplicate",
                    invalidated_at=time.time(),
                )
                duplicate_count += 1
                continue
            for superseded in superseded_records:
                self.store.update_memory_record(
                    superseded.memory_id,
                    status="invalidated",
                    supersedes=list({*superseded.supersedes, record.claim_text}),
                    superseded_by_memory_id=record.memory_id,
                    invalidation_reason="superseded",
                    invalidated_at=time.time(),
                )
                accepted = [entry for entry in accepted if entry.memory_id != superseded.memory_id]
                superseded_count += 1
            accepted.append(self.store.get_memory_record(record.memory_id) or record)
        return {
            "active_count": len(accepted),
            "superseded_count": superseded_count,
            "duplicate_count": duplicate_count,
        }

    def export_mirror(self, path: Path | None = None) -> Path | None:
        mirror = path or self.mirror_path
        if mirror is None:
            return None
        engine = MemoryEngine(mirror)
        categories: dict[str, list[MemoryEntry]] = {}
        for record in self.store.list_memory_records(status="active", limit=1000):
            categories.setdefault(record.category, []).append(self._entry_from_memory(record))
        engine.save(categories)
        return mirror

    def active_categories(
        self, *, conversation_id: str | None = None
    ) -> dict[str, list[MemoryEntry]]:
        categories: dict[str, list[MemoryEntry]] = {}
        for record in self.store.list_memory_records(
            status="active", conversation_id=conversation_id, limit=1000
        ):
            categories.setdefault(record.category, []).append(self._entry_from_memory(record))
        return categories

    def _issue_memory_write_receipt(
        self,
        *,
        belief: BeliefRecord,
        memory: MemoryRecord,
        superseded_records: list[MemoryRecord],
    ) -> str | None:
        if self._receipt_service is None or self._artifact_store is None:
            return None
        prestate = {
            "belief_ids": [belief.belief_id],
            "memory_ids": [r.memory_id for r in superseded_records],
        }
        prestate_uri, prestate_hash = self._artifact_store.store_json(prestate)
        prestate_artifact = self.store.create_artifact(
            task_id=belief.task_id,
            step_id="memory_promotion",
            kind="prestate.memory_write",
            uri=prestate_uri,
            content_hash=prestate_hash,
            producer="memory_record_service",
            retention_class="audit",
            trust_tier="observed",
            metadata={"memory_id": memory.memory_id},
        )
        return self._receipt_service.issue(
            task_id=belief.task_id,
            step_id="memory_promotion",
            step_attempt_id=f"promote:{belief.belief_id}",
            action_type="memory_write",
            input_refs=[belief.belief_id],
            environment_ref=None,
            policy_result={"verdict": "allow", "reason": "belief_promotion"},
            approval_ref=None,
            output_refs=[memory.memory_id],
            result_summary=f"Promoted belief {belief.belief_id} to memory {memory.memory_id}",
            result_code="succeeded",
            rollback_supported=True,
            rollback_strategy="supersede_or_invalidate",
            rollback_artifact_refs=[prestate_artifact.artifact_id],
        )

    def _issue_memory_invalidate_receipt(self, memory_id: str) -> str | None:
        if self._receipt_service is None:
            return None
        record = self.store.get_memory_record(memory_id)
        if record is None:
            return None
        return self._receipt_service.issue(
            task_id=record.task_id,
            step_id="memory_invalidation",
            step_attempt_id=f"invalidate:{memory_id}",
            action_type="memory_invalidate",
            input_refs=[memory_id],
            environment_ref=None,
            policy_result={"verdict": "allow", "reason": "memory_invalidation"},
            approval_ref=None,
            output_refs=[],
            result_summary=f"Invalidated memory {memory_id}",
            result_code="succeeded",
            rollback_supported=False,
        )

    @staticmethod
    def _entry_from_memory(record: MemoryRecord) -> MemoryEntry:
        return MemoryEntry(
            category=record.category,
            content=record.claim_text,
            score=8 if record.trust_tier in {"durable", "bootstrap"} else 5,
            locked=record.trust_tier in {"durable", "bootstrap"},
            confidence=record.confidence,
            supersedes=list(record.supersedes),
            scope_kind=record.scope_kind,
            scope_ref=record.scope_ref,
            retention_class=record.retention_class,
        )

    def _eligible_reconciliation_ref(self, task_id: str) -> tuple[str | None, str]:
        if not hasattr(self.store, "list_reconciliations"):
            return None, "reconciliation_missing"
        found_any = False
        for reconciliation in self.store.list_reconciliations(task_id=task_id, limit=50):
            found_any = True
            if str(reconciliation.result_class or "") == "satisfied":
                return reconciliation.reconciliation_id, ""
        if found_any:
            return None, "reconciliation_not_satisfied"
        return None, "reconciliation_missing"

    def _validate_reconciliation_ref(self, reconciliation_ref: str) -> str:
        """Validate that a reconciliation_ref points to a valid, non-invalidated reconciliation.

        Returns an empty string if valid, or a descriptive reason string if invalid.
        """
        if not hasattr(self.store, "get_reconciliation"):
            # Store does not support reconciliation lookup; fall back to trusting the ref.
            return ""
        reconciliation = self.store.get_reconciliation(reconciliation_ref)
        if reconciliation is None:
            return "reconciliation_not_found"
        result_class = str(reconciliation.result_class or "").strip()
        if result_class == "violated":
            return "reconciliation_violated"
        if result_class not in ("satisfied", "ambiguous"):
            return f"reconciliation_invalid_result_class:{result_class}"
        return ""
