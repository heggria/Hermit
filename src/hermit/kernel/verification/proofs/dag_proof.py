from __future__ import annotations

from dataclasses import dataclass, field

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.proofs.proofs import ProofService


@dataclass(frozen=True)
class DAGProofBundle:
    """Proof bundle for a DAG-structured task."""

    task_id: str
    dag_definition_ref: str
    step_receipts: dict[str, list[str]] = field(default_factory=dict)
    join_events: list[str] = field(default_factory=list)
    root_step_ids: list[str] = field(default_factory=list)
    leaf_step_ids: list[str] = field(default_factory=list)


class DAGProofService:
    """Generate proof bundles for DAG-structured tasks."""

    def __init__(self, store: KernelStore, proof_service: ProofService) -> None:
        self._store = store
        self._proof_service = proof_service

    def generate(self, task_id: str) -> DAGProofBundle:
        """Collect all receipts for a DAG task, organized by step topology."""
        steps = self._store.list_steps(task_id=task_id, limit=1000)
        if not steps:
            return DAGProofBundle(task_id=task_id, dag_definition_ref="")

        # Collect all receipts for this task and group by step_id
        all_receipts = self._store.list_receipts(task_id=task_id, limit=1000)
        receipts_by_step: dict[str, list[str]] = {}
        for r in all_receipts:
            sid = r.step_id
            receipts_by_step.setdefault(sid, []).append(r.receipt_id)

        step_receipts: dict[str, list[str]] = {}
        root_ids: list[str] = []
        leaf_ids: list[str] = []
        downstream_deps: set[str] = set()

        for step in steps:
            step_receipts[step.step_id] = receipts_by_step.get(step.step_id, [])
            if not step.depends_on:
                root_ids.append(step.step_id)
            for dep in step.depends_on:
                downstream_deps.add(dep)

        for step in steps:
            if step.step_id not in downstream_deps:
                leaf_ids.append(step.step_id)

        events = self._store.list_events(
            task_id=task_id,
            event_type="step.dependency_satisfied",
            limit=1000,
        )
        join_events = [e["event_id"] for e in events]

        return DAGProofBundle(
            task_id=task_id,
            dag_definition_ref=f"task:{task_id}:dag",
            step_receipts=step_receipts,
            join_events=join_events,
            root_step_ids=root_ids,
            leaf_step_ids=leaf_ids,
        )
