from __future__ import annotations

from typing import Any

from hermit.kernel.store import KernelStore


class ApprovalService:
    def __init__(self, store: KernelStore) -> None:
        self.store = store

    def request(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        approval_type: str,
        requested_action: dict[str, Any],
        request_packet_ref: str | None,
    ) -> str:
        approval = self.store.create_approval(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=step_attempt_id,
            approval_type=approval_type,
            requested_action=requested_action,
            request_packet_ref=request_packet_ref,
        )
        return approval.approval_id

    def approve(self, approval_id: str, *, resolved_by: str = "user") -> None:
        self.store.resolve_approval(
            approval_id,
            status="granted",
            resolved_by=resolved_by,
            resolution={"status": "granted"},
        )

    def deny(self, approval_id: str, *, resolved_by: str = "user", reason: str = "") -> None:
        self.store.resolve_approval(
            approval_id,
            status="denied",
            resolved_by=resolved_by,
            resolution={"status": "denied", "reason": reason},
        )
