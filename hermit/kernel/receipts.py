from __future__ import annotations

from typing import Any

from hermit.kernel.store import KernelStore


class ReceiptService:
    def __init__(self, store: KernelStore) -> None:
        self.store = store

    def issue(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        action_type: str,
        input_refs: list[str],
        environment_ref: str | None,
        policy_result: dict[str, Any],
        approval_ref: str | None,
        output_refs: list[str],
        result_summary: str,
    ) -> str:
        receipt = self.store.create_receipt(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=step_attempt_id,
            action_type=action_type,
            input_refs=input_refs,
            environment_ref=environment_ref,
            policy_result=policy_result,
            approval_ref=approval_ref,
            output_refs=output_refs,
            result_summary=result_summary,
        )
        return receipt.receipt_id
