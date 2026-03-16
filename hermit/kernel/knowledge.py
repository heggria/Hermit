from __future__ import annotations

import time
from pathlib import Path

from hermit.builtin.memory.engine import MemoryEngine
from hermit.builtin.memory.types import MemoryEntry
from hermit.kernel.memory_governance import MemoryGovernanceService
from hermit.kernel.models import BeliefRecord, MemoryRecord
from hermit.kernel.store import KernelStore


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
    ) -> BeliefRecord:
        return self.store.create_belief(
            task_id=task_id,
            conversation_id=conversation_id,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            category=category,
            claim_text=content,
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
    def __init__(self, store: KernelStore, *, mirror_path: Path | None = None) -> None:
        self.store = store
        self.mirror_path = mirror_path
        self.governance = MemoryGovernanceService()

    def promote_from_belief(
        self,
        *,
        belief: BeliefRecord,
        conversation_id: str | None,
        workspace_root: str = "",
        reconciliation_ref: str | None = None,
    ) -> MemoryRecord | None:
        resolved_reconciliation_ref = reconciliation_ref or self._eligible_reconciliation_ref(
            belief.task_id
        )
        if resolved_reconciliation_ref is None:
            self.store.update_belief(
                belief.belief_id,
                promotion_candidate=False,
                validation_basis="promotion_blocked:reconciliation_missing",
            )
            return None
        classification = self.governance.classify_belief(belief, workspace_root=workspace_root)
        existing = self.store.list_memory_records(status="active", limit=500)
        duplicate_record, superseded_records = self.governance.find_superseded_records(
            classification=classification,
            claim_text=belief.claim_text,
            active_records=existing,
            entry_from_record=self._entry_from_memory,
        )
        if duplicate_record is not None:
            self.store.update_belief(
                belief.belief_id,
                memory_ref=duplicate_record.memory_id,
                promotion_candidate=False,
                validation_basis=f"reconciliation:{resolved_reconciliation_ref}",
                last_validated_at=time.time(),
            )
            return duplicate_record
        supersedes = [record.claim_text for record in superseded_records]
        memory = self.store.create_memory_record(
            task_id=belief.task_id,
            conversation_id=conversation_id,
            category=classification.category,
            claim_text=belief.claim_text,
            structured_assertion={
                **dict(classification.structured_assertion or {}),
                **dict(belief.structured_assertion),
            },
            scope_kind=classification.scope_kind,
            scope_ref=classification.scope_ref,
            promotion_reason=classification.promotion_reason,
            retention_class=classification.retention_class,
            status="active",
            confidence=belief.confidence,
            trust_tier="durable",
            evidence_refs=list(belief.evidence_refs),
            supersedes=supersedes,
            supersedes_memory_ids=[record.memory_id for record in superseded_records],
            source_belief_ref=belief.belief_id,
            expires_at=classification.expires_at,
            memory_kind="contract_template"
            if classification.category == "contract_template"
            else "durable_fact",
            validation_basis=f"reconciliation:{resolved_reconciliation_ref}",
            last_validated_at=time.time(),
            learned_from_reconciliation_ref=resolved_reconciliation_ref,
        )
        self.store.update_belief(
            belief.belief_id,
            memory_ref=memory.memory_id,
            promotion_candidate=False,
            validation_basis=f"reconciliation:{resolved_reconciliation_ref}",
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
                supersession_reason=f"reconciliation:{resolved_reconciliation_ref}",
            )
        return memory

    def invalidate(self, memory_id: str) -> None:
        self.store.update_memory_record(memory_id, status="invalidated", invalidated_at=time.time())

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

    def _eligible_reconciliation_ref(self, task_id: str) -> str | None:
        if not hasattr(self.store, "list_reconciliations"):
            return None
        for reconciliation in self.store.list_reconciliations(task_id=task_id, limit=50):
            if str(reconciliation.result_class or "") == "satisfied":
                return reconciliation.reconciliation_id
        return None
