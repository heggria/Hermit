from __future__ import annotations

from typing import Any

from hermit.kernel.proofs import ProofService
from hermit.kernel.store import KernelStore
from hermit.kernel.topics import build_task_topic

_PROJECTION_SCHEMA_VERSION = "tail-v4"


class ProjectionService:
    def __init__(self, store: KernelStore) -> None:
        self.store = store
        self.proofs = ProofService(store)

    def rebuild_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        proof = self.proofs.build_proof_summary(task_id)
        projection = self.store.build_task_projection(task_id)
        events = self.store.list_events(task_id=task_id, limit=500)
        beliefs = [belief.__dict__ for belief in self.store.list_beliefs(task_id=task_id, limit=200)]
        knowledge = [
            record.__dict__
            for record in self.store.list_memory_records(conversation_id=task.conversation_id, limit=200)
        ]
        rollbacks = []
        for receipt in self.store.list_receipts(task_id=task_id, limit=200):
            rollback = self.store.get_rollback_for_receipt(receipt.receipt_id)
            if rollback is not None:
                rollbacks.append(rollback.__dict__)
        payload = {
            "task": task.__dict__,
            "projection": projection,
            "proof": proof,
            "topic": build_task_topic(events),
            "beliefs": beliefs,
            "knowledge": knowledge,
            "rollbacks": rollbacks,
        }
        self.store.upsert_projection_cache(
            task_id,
            schema_version=_PROJECTION_SCHEMA_VERSION,
            event_head_hash=proof["head_hash"],
            payload=payload,
        )
        return payload

    def rebuild_all(self) -> dict[str, Any]:
        rebuilt: list[str] = []
        for task in self.store.list_tasks(limit=1000):
            self.rebuild_task(task.task_id)
            rebuilt.append(task.task_id)
        return {"rebuilt_tasks": rebuilt, "count": len(rebuilt)}

    def verify_projection(self, task_id: str) -> dict[str, Any]:
        cache = self.store.get_projection_cache(task_id)
        proof = self.proofs.build_proof_summary(task_id)
        if cache is None:
            return {"valid": False, "reason": "missing", "head_hash": proof["head_hash"]}
        return {
            "valid": cache["schema_version"] == _PROJECTION_SCHEMA_VERSION
            and cache["event_head_hash"] == proof["head_hash"],
            "reason": "ok"
            if cache["schema_version"] == _PROJECTION_SCHEMA_VERSION and cache["event_head_hash"] == proof["head_hash"]
            else "stale",
            "head_hash": proof["head_hash"],
            "cached_head_hash": cache["event_head_hash"],
            "schema_version": cache["schema_version"],
        }

    def ensure_task_projection(self, task_id: str) -> dict[str, Any]:
        verification = self.verify_projection(task_id)
        if verification["valid"]:
            cache = self.store.get_projection_cache(task_id)
            assert cache is not None
            return cache["payload"]
        return self.rebuild_task(task_id)
