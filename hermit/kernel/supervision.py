from __future__ import annotations

from typing import Any

from hermit.kernel.claims import task_claim_status
from hermit.kernel.conversation_projection import ConversationProjectionService
from hermit.kernel.models import IngressRecord, TaskRecord
from hermit.kernel.projections import ProjectionService
from hermit.kernel.rollbacks import RollbackService
from hermit.kernel.store import KernelStore


class SupervisionService:
    def __init__(self, store: KernelStore) -> None:
        self.store = store
        self.projections = ProjectionService(store)
        self.conversation_projections = ConversationProjectionService(store)
        self.rollbacks = RollbackService(store)

    def build_task_case(self, task_id: str) -> dict[str, Any]:
        cached = self.projections.ensure_task_projection(task_id)
        task = self.store.get_task(task_id)
        proof = cached["proof"]
        claims = cached.get("claims") or task_claim_status(self.store, task_id, proof_summary=proof)
        latest_receipt = proof.get("latest_receipt")
        latest_decision = proof.get("latest_decision")
        latest_capability_grant = proof.get("latest_capability_grant")
        latest_workspace_lease = proof.get("latest_workspace_lease")
        approvals = list(cached["projection"]["approvals"].values())
        approvals.sort(key=lambda item: float(item.get("last_event_at") or 0), reverse=True)
        latest_approval = approvals[0] if approvals else None
        target_paths = list(
            (latest_capability_grant or {}).get("constraints", {}).get("target_paths", [])
        )
        latest_memory = cached["knowledge"][0] if cached["knowledge"] else None
        reentry = self._reentry_observability(task_id)
        rollback = None
        if latest_receipt and latest_receipt.get("receipt_id"):
            record = self.store.get_rollback_for_receipt(str(latest_receipt["receipt_id"]))
            rollback = record.__dict__ if record is not None else None
        return {
            "task": cached["task"],
            "projection": {
                "events_processed": cached["projection"]["events_processed"],
                "last_event_seq": cached["projection"]["last_event_seq"],
                "step_count": len(cached["projection"]["steps"]),
                "step_attempt_count": len(cached["projection"]["step_attempts"]),
                "approval_count": len(cached["projection"]["approvals"]),
                "decision_count": len(cached["projection"]["decisions"]),
                "capability_grant_count": len(cached["projection"]["capability_grants"]),
                "workspace_lease_count": len(cached["projection"]["workspace_leases"]),
                "receipt_count": len(cached["projection"]["receipts"]),
                "belief_count": len(cached["projection"]["beliefs"]),
                "memory_count": len(cached["projection"]["memory_records"]),
            },
            "operator_answers": {
                "why_execute": latest_decision["reason"] if latest_decision else None,
                "evidence_refs": list((latest_decision or {}).get("evidence_refs", [])),
                "approval": latest_approval,
                "authority": {
                    "capability_grant": latest_capability_grant,
                    "workspace_lease": latest_workspace_lease,
                    "target_paths": target_paths,
                    "rollback_available": bool((latest_receipt or {}).get("rollback_supported")),
                    "rollback_strategy": (latest_receipt or {}).get("rollback_strategy"),
                },
                "outcome": latest_receipt,
                "proof": proof["chain_verification"],
                "claims": claims,
                "reentry": reentry,
                "knowledge": {
                    "latest_memory": latest_memory,
                    "recent_beliefs": cached["beliefs"][:5],
                },
                "rollback": rollback,
            },
            "ingress_observability": self._build_ingress_observability(task),
        }

    def rollback_receipt(self, receipt_id: str) -> dict[str, Any]:
        return self.rollbacks.execute(receipt_id)

    def _build_ingress_observability(self, task: TaskRecord | None) -> dict[str, Any]:
        if task is None:
            return {
                "conversation": {},
                "task": {
                    "recent_related_ingresses": [],
                    "pending_disambiguations": [],
                },
            }
        conversation_projection = self.conversation_projections.ensure(task.conversation_id)
        focus_task_id = str(conversation_projection.get("focus_task_id", "") or "")
        focus_entry = next(
            (
                entry
                for entry in list(conversation_projection.get("open_tasks", []) or [])
                if str(entry.get("task_id", "") or "") == focus_task_id
            ),
            None,
        )
        return {
            "conversation": {
                "conversation_id": task.conversation_id,
                "focus": {
                    "task_id": focus_task_id,
                    "title": str((focus_entry or {}).get("title", "") or ""),
                    "status": str((focus_entry or {}).get("status", "") or ""),
                    "reason": str(conversation_projection.get("focus_reason", "") or ""),
                },
                "open_tasks": list(conversation_projection.get("open_tasks", []) or []),
                "pending_ingress_count": int(
                    conversation_projection.get("pending_ingress_count", 0) or 0
                ),
                "metrics": dict(conversation_projection.get("ingress_metrics", {}) or {}),
                "recent_ingresses": self._serialize_ingress_list(
                    self.store.list_ingresses(conversation_id=task.conversation_id, limit=5)
                ),
            },
            "task": {
                "task_id": task.task_id,
                "is_focus": focus_task_id == task.task_id,
                "recent_related_ingresses": self._recent_related_ingresses(
                    conversation_id=task.conversation_id,
                    task_id=task.task_id,
                ),
                "pending_disambiguations": self._serialize_ingress_list(
                    self.store.list_ingresses(
                        conversation_id=task.conversation_id,
                        status="pending_disambiguation",
                        limit=5,
                    )
                ),
            },
        }

    def _recent_related_ingresses(
        self, *, conversation_id: str, task_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        related: list[dict[str, Any]] = []
        for ingress in self.store.list_ingresses(conversation_id=conversation_id, limit=50):
            relation = ""
            if ingress.chosen_task_id == task_id:
                relation = "chosen_task"
            elif ingress.parent_task_id == task_id:
                relation = "parent_task"
            if not relation:
                continue
            related.append(self._serialize_ingress(ingress, relation=relation))
            if len(related) >= limit:
                break
        return related

    def _serialize_ingress_list(self, ingresses: list[IngressRecord]) -> list[dict[str, Any]]:
        return [self._serialize_ingress(ingress) for ingress in ingresses]

    def _serialize_ingress(self, ingress: IngressRecord, *, relation: str = "") -> dict[str, Any]:
        rationale = dict(ingress.rationale or {})
        payload = {
            "ingress_id": ingress.ingress_id,
            "status": ingress.status,
            "resolution": ingress.resolution,
            "chosen_task_id": ingress.chosen_task_id,
            "parent_task_id": ingress.parent_task_id,
            "actor_principal_id": ingress.actor_principal_id,
            "source_channel": ingress.source_channel,
            "raw_excerpt": self._trim(str(ingress.raw_text or ""), 240),
            "reply_to_ref": ingress.reply_to_ref,
            "quoted_message_ref": ingress.quoted_message_ref,
            "explicit_task_ref": ingress.explicit_task_ref,
            "referenced_artifact_refs": list(ingress.referenced_artifact_refs),
            "confidence": ingress.confidence,
            "margin": ingress.margin,
            "reason_codes": list(rationale.get("reason_codes", []) or []),
            "resolved_by": str(rationale.get("resolved_by", "") or ""),
            "created_at": ingress.created_at,
            "updated_at": ingress.updated_at,
        }
        if relation:
            payload["relation"] = relation
        return payload

    def _reentry_observability(self, task_id: str) -> dict[str, Any]:
        attempts = self.store.list_step_attempts(task_id=task_id, limit=20)
        recent: list[dict[str, Any]] = []
        required_count = 0
        resolved_count = 0
        for attempt in attempts:
            context = dict(attempt.context or {})
            if bool(context.get("reentry_required")):
                required_count += 1
            if context.get("reentry_resolved_at"):
                resolved_count += 1
            if len(recent) >= 5:
                continue
            if not (
                context.get("reentry_reason")
                or context.get("reentry_boundary")
                or context.get("reentered_via")
                or context.get("recovery_required")
            ):
                continue
            recent.append(
                {
                    "step_attempt_id": attempt.step_attempt_id,
                    "status": attempt.status,
                    "phase": str(context.get("phase", "") or ""),
                    "reentry_reason": str(context.get("reentry_reason", "") or ""),
                    "reentry_boundary": str(context.get("reentry_boundary", "") or ""),
                    "reentered_via": str(context.get("reentered_via", "") or ""),
                    "reentry_required": bool(context.get("reentry_required")),
                    "recovery_required": bool(context.get("recovery_required")),
                    "reentry_requested_at": context.get("reentry_requested_at"),
                    "reentry_resolved_at": context.get("reentry_resolved_at"),
                }
            )
        return {
            "required_count": required_count,
            "resolved_count": resolved_count,
            "recent_attempts": recent,
        }

    @staticmethod
    def _trim(text: str, limit: int) -> str:
        value = " ".join(str(text or "").split())
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 1)].rstrip() + "…"
