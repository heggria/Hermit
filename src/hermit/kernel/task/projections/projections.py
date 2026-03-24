from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from hermit.kernel.artifacts.lineage.claims import task_claim_status
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.planning import PlanningService
from hermit.kernel.task.services.topics import build_task_topic
from hermit.kernel.task.state.outcomes import build_task_outcome
from hermit.kernel.verification.proofs.proofs import ProofService

_PROJECTION_SCHEMA_VERSION = "tail-v8"


class ProjectionService:
    def __init__(self, store: KernelStore) -> None:
        self.store = store
        self.proofs = ProofService(store)

    def rebuild_task(self, task_id: str) -> dict[str, Any]:
        cache = self.store.get_projection_cache(task_id)
        if cache is not None and cache["schema_version"] == _PROJECTION_SCHEMA_VERSION:
            payload = self._incremental_rebuild(task_id, cache["payload"])
        else:
            payload = self._full_rebuild(task_id)
        proof = self.proofs.build_proof_summary(task_id)
        self.store.upsert_projection_cache(
            task_id,
            schema_version=_PROJECTION_SCHEMA_VERSION,
            event_head_hash=proof["head_hash"],
            payload=payload,
        )
        return payload

    def _full_rebuild(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        return self._assemble_payload(
            task_id, task.conversation_id, previous_tool_history=cast(list[dict[str, Any]], [])
        )

    def _incremental_rebuild(self, task_id: str, cached_payload: dict[str, Any]) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        previous_tool_history = list(cached_payload.get("tool_history", []))
        last_seq = int(cached_payload.get("tool_history_event_seq", 0) or 0)
        new_tool_history = self._tool_history_from_events(
            self.store.list_events(task_id=task_id, after_event_seq=last_seq, limit=500)
        )
        return self._assemble_payload(
            task_id,
            task.conversation_id,
            previous_tool_history=previous_tool_history + new_tool_history,
        )

    def _assemble_payload(
        self,
        task_id: str,
        conversation_id: str,
        *,
        previous_tool_history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        proof = self.proofs.build_proof_summary(task_id)
        projection = self.store.build_task_projection(task_id)
        events = self.store.list_events(task_id=task_id, limit=500)
        planning = PlanningService(self.store)
        planning_state = planning.state_for_task(task_id)
        beliefs = [
            belief.__dict__ for belief in self.store.list_beliefs(task_id=task_id, limit=200)
        ]
        knowledge = [
            record.__dict__
            for record in self.store.list_memory_records(conversation_id=conversation_id, limit=200)
        ]
        latest_context_pack_ref = None
        latest_working_state_ref = None
        for attempt in self.store.list_step_attempts(task_id=task_id, limit=200):
            if latest_context_pack_ref is None and attempt.context_pack_ref:
                latest_context_pack_ref = attempt.context_pack_ref
            if latest_working_state_ref is None and attempt.working_state_ref:
                latest_working_state_ref = attempt.working_state_ref
            if latest_context_pack_ref is not None and latest_working_state_ref is not None:
                break
        if latest_context_pack_ref is None:
            for artifact in reversed(self.store.list_artifacts(task_id=task_id, limit=200)):
                if artifact.kind.startswith("context.pack/"):
                    latest_context_pack_ref = artifact.artifact_id
                    break
        latest_planning_decision_id = None
        for decision in self.store.list_decisions(task_id=task_id, limit=200):
            if decision.decision_type == "planning":
                latest_planning_decision_id = decision.decision_id
                break
        current_tool_history = previous_tool_history or self._tool_history_from_events(events)
        if previous_tool_history:
            seen = {
                (entry["event_seq"], entry["tool_name"], entry["key_input"])
                for entry in previous_tool_history
            }
            for entry in self._tool_history_from_events(events):
                key = (entry["event_seq"], entry["tool_name"], entry["key_input"])
                if key not in seen:
                    current_tool_history.append(entry)
                    seen.add(key)
        rollbacks: list[dict[str, Any]] = []
        for receipt in self.store.list_receipts(task_id=task_id, limit=200):
            rollback = self.store.get_rollback_for_receipt(receipt.receipt_id)
            if rollback is not None:
                rollbacks.append(rollback.__dict__)
        payload = {
            "task": task.__dict__,
            "projection": projection,
            "proof": proof,
            "claims": task_claim_status(self.store, task_id, proof_summary=proof),
            "topic": build_task_topic(events),
            "outcome": build_task_outcome(
                store=self.store,
                task_id=task_id,
                status=str(task.status or ""),
                events=events,
            ),
            "beliefs": beliefs,
            "knowledge": knowledge,
            "latest_context_pack_ref": latest_context_pack_ref,
            "latest_working_state_ref": latest_working_state_ref,
            "planning": planning_state.to_dict(),
            "selected_plan_ref": planning_state.selected_plan_ref,
            "latest_plan_artifact_refs": planning.latest_plan_artifact_refs(task_id),
            "latest_planning_decision_id": latest_planning_decision_id,
            "tool_history": current_tool_history,
            "tool_history_event_seq": int(events[-1]["event_seq"]) if events else 0,
            "rollbacks": rollbacks,
            "contract_loop": {
                "execution_contract_refs": list(projection.get("execution_contracts", {}).keys()),
                "evidence_case_refs": list(projection.get("evidence_cases", {}).keys()),
                "authorization_plan_refs": list(projection.get("authorization_plans", {}).keys()),
                "reconciliation_refs": list(projection.get("reconciliations", {}).keys()),
                "latest_execution_contract_ref": proof.get("latest_execution_contract", {})
                and proof["latest_execution_contract"].get("contract_id"),
                "latest_reconciliation_ref": proof.get("latest_reconciliation", {})
                and proof["latest_reconciliation"].get("reconciliation_id"),
                "latest_reconciliation_result": proof.get("latest_reconciliation", {})
                and proof["latest_reconciliation"].get("result_class"),
            },
        }
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
        is_valid = (
            cache["schema_version"] == _PROJECTION_SCHEMA_VERSION
            and cache["event_head_hash"] == proof["head_hash"]
        )
        return {
            "valid": is_valid,
            "reason": "ok" if is_valid else "stale",
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

    def _tool_history_from_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        for event in events:
            if event["event_type"] != "action.requested":
                continue
            payload = dict(event["payload"])
            tool_name = str(payload.get("tool_name") or "")
            if not tool_name:
                continue
            tool_input = self._tool_input_from_event(payload.get("artifact_ref"))
            history.append(
                {
                    "event_seq": int(event["event_seq"]),
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "key_input": json.dumps(self._key_input(tool_input), ensure_ascii=False),
                    "occurred_at": float(event["occurred_at"]),
                }
            )
        return history

    def _tool_input_from_event(self, artifact_ref: Any) -> dict[str, Any]:
        if not artifact_ref:
            return {}
        artifact = self.store.get_artifact(str(artifact_ref))
        if artifact is None:
            return {}
        try:
            raw = Path(artifact.uri).read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return {}
        data_d: dict[str, Any] = cast(dict[str, Any], data) if isinstance(data, dict) else {}
        _tool_input_any: Any = data_d.get("tool_input", {})
        if isinstance(_tool_input_any, dict):
            return dict(cast(dict[str, Any], _tool_input_any))
        return {}

    @staticmethod
    def _key_input(tool_input: dict[str, Any]) -> Any:
        if not tool_input:
            return ""
        first_value = next(iter(tool_input.values()))
        return first_value
