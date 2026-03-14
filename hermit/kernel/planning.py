from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from hermit.kernel.context import TaskExecutionContext
from hermit.kernel.decisions import DecisionService
from hermit.kernel.store import KernelStore

_PLANNING_META_KEY = "pending_planning"
_EXPLICIT_PLAN_RE = re.compile(
    r"(先规划一下|先计划一下|先给个计划|先别执行|不要执行先规划|进入规划模式|先做规划|先出计划|plan\s+first)",
    re.IGNORECASE,
)


@dataclass
class PlanningState:
    planning_mode: bool = False
    candidate_plan_refs: list[str] = field(default_factory=list)
    selected_plan_ref: str | None = None
    plan_status: str = "none"
    latest_planning_decision_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "planning_mode": self.planning_mode,
            "candidate_plan_refs": list(self.candidate_plan_refs),
            "selected_plan_ref": self.selected_plan_ref,
            "plan_status": self.plan_status,
            "latest_planning_decision_id": self.latest_planning_decision_id,
        }


class PlanningService:
    def __init__(self, store: KernelStore, artifact_store: Any | None = None) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.decision_service = DecisionService(store)

    @staticmethod
    def planning_requested(text: str) -> bool:
        return bool(_EXPLICIT_PLAN_RE.search(text or ""))

    def pending_for_conversation(self, conversation_id: str) -> bool:
        conversation = self.store.ensure_conversation(conversation_id, source_channel="chat")
        return bool(dict(conversation.metadata).get(_PLANNING_META_KEY, False))

    def set_pending_for_conversation(self, conversation_id: str, *, enabled: bool) -> None:
        conversation = self.store.ensure_conversation(conversation_id, source_channel="chat")
        metadata = dict(conversation.metadata)
        if enabled:
            metadata[_PLANNING_META_KEY] = True
        else:
            metadata.pop(_PLANNING_META_KEY, None)
        self.store.update_conversation_metadata(conversation_id, metadata)

    def state_for_task(self, task_id: str) -> PlanningState:
        state = PlanningState()
        for event in self.store.list_events(task_id=task_id, limit=500):
            event_type = str(event.get("event_type", "") or "")
            payload = dict(event.get("payload", {}) or {})
            if event_type == "planning.entered":
                state.planning_mode = True
                state.plan_status = str(payload.get("plan_status", "none") or "none")
            elif event_type == "planning.exited":
                state.planning_mode = False
            elif event_type == "plan.artifact_created":
                artifact_ref = str(payload.get("artifact_ref", "") or "").strip()
                if artifact_ref and artifact_ref not in state.candidate_plan_refs:
                    state.candidate_plan_refs.append(artifact_ref)
                if artifact_ref:
                    state.selected_plan_ref = artifact_ref
                    state.plan_status = "drafted"
            elif event_type == "plan.selected":
                artifact_ref = str(payload.get("artifact_ref", "") or "").strip()
                if artifact_ref and artifact_ref not in state.candidate_plan_refs:
                    state.candidate_plan_refs.append(artifact_ref)
                state.selected_plan_ref = artifact_ref or state.selected_plan_ref
                state.plan_status = "selected"
            elif event_type == "plan.confirmed":
                state.plan_status = "executing"
                state.planning_mode = False
            elif event_type == "decision.recorded" and str(payload.get("decision_type", "")) == "planning":
                state.latest_planning_decision_id = str(event.get("entity_id", "") or "") or state.latest_planning_decision_id
                if state.plan_status == "drafted":
                    state.plan_status = "selected"
        return state

    def enter_planning(self, task_id: str, *, actor: str = "user") -> PlanningState:
        current = self.state_for_task(task_id)
        if current.planning_mode:
            return current
        self.store.append_event(
            event_type="planning.entered",
            entity_type="task",
            entity_id=task_id,
            task_id=task_id,
            actor=actor,
            payload={
                "planning_mode": True,
                "candidate_plan_refs": list(current.candidate_plan_refs),
                "selected_plan_ref": current.selected_plan_ref,
                "plan_status": current.plan_status,
            },
        )
        return self.state_for_task(task_id)

    def exit_planning(self, task_id: str, *, actor: str = "user") -> PlanningState:
        current = self.state_for_task(task_id)
        self.store.append_event(
            event_type="planning.exited",
            entity_type="task",
            entity_id=task_id,
            task_id=task_id,
            actor=actor,
            payload={
                "planning_mode": False,
                "candidate_plan_refs": list(current.candidate_plan_refs),
                "selected_plan_ref": current.selected_plan_ref,
                "plan_status": current.plan_status,
            },
        )
        return self.state_for_task(task_id)

    def capture_plan_result(
        self,
        ctx: TaskExecutionContext,
        *,
        plan_text: str,
        producer: str = "planner_kernel",
    ) -> str | None:
        text = str(plan_text or "").strip()
        if not text or self.artifact_store is None:
            return None
        uri, content_hash = self.artifact_store.store_text(text, extension="md")
        artifact = self.store.create_artifact(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="plan",
            uri=uri,
            content_hash=content_hash,
            producer=producer,
            retention_class="task",
            trust_tier="derived",
            metadata={"conversation_id": ctx.conversation_id},
        )
        self.store.append_event(
            event_type="plan.artifact_created",
            entity_type="task",
            entity_id=ctx.task_id,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            actor="kernel",
            payload={
                "artifact_ref": artifact.artifact_id,
                "step_id": ctx.step_id,
                "candidate_plan_refs": list(self.state_for_task(ctx.task_id).candidate_plan_refs) + [artifact.artifact_id],
                "plan_status": "drafted",
            },
        )
        self.store.append_event(
            event_type="plan.selected",
            entity_type="task",
            entity_id=ctx.task_id,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            actor="kernel",
            payload={
                "artifact_ref": artifact.artifact_id,
                "selected_plan_ref": artifact.artifact_id,
                "plan_status": "selected",
            },
        )
        return artifact.artifact_id

    def confirm_selected_plan(
        self,
        ctx: TaskExecutionContext,
        *,
        actor: str = "user",
    ) -> tuple[PlanningState, str | None]:
        state = self.state_for_task(ctx.task_id)
        selected = state.selected_plan_ref
        if not selected:
            return state, None
        decision_id = self.decision_service.record(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_type="planning",
            verdict="selected",
            reason="Selected plan was confirmed for execution.",
            evidence_refs=[selected],
            decided_by=actor,
        )
        self.store.append_event(
            event_type="plan.confirmed",
            entity_type="task",
            entity_id=ctx.task_id,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            actor=actor,
            payload={
                "artifact_ref": selected,
                "selected_plan_ref": selected,
                "decision_ref": decision_id,
                "plan_status": "executing",
            },
        )
        self.exit_planning(ctx.task_id, actor=actor)
        return self.state_for_task(ctx.task_id), decision_id

    def load_selected_plan_text(self, task_id: str) -> str | None:
        selected = self.state_for_task(task_id).selected_plan_ref
        if not selected or self.artifact_store is None:
            return None
        artifact = self.store.get_artifact(selected)
        if artifact is None:
            return None
        try:
            return self.artifact_store.read_text(artifact.uri)
        except OSError:
            return None

    def latest_plan_artifact_refs(self, task_id: str, *, limit: int = 10) -> list[str]:
        refs: list[str] = []
        for artifact in reversed(self.store.list_artifacts(task_id=task_id, limit=200)):
            if artifact.kind != "plan":
                continue
            refs.append(artifact.artifact_id)
            if len(refs) >= limit:
                break
        return refs

    def latest_planning_attempt(self, task_id: str) -> TaskExecutionContext | None:
        attempts = self.store.list_step_attempts(task_id=task_id, limit=20)
        for attempt in attempts:
            step = self.store.get_step(attempt.step_id)
            if step is None or step.kind != "plan":
                continue
            task = self.store.get_task(task_id)
            if task is None:
                return None
            return TaskExecutionContext(
                conversation_id=task.conversation_id,
                task_id=task.task_id,
                step_id=attempt.step_id,
                step_attempt_id=attempt.step_attempt_id,
                source_channel=task.source_channel,
                policy_profile=task.policy_profile,
                workspace_root=str(attempt.context.get("workspace_root", "") or ""),
                ingress_metadata=dict(attempt.context.get("ingress_metadata", {}) or {}),
            )
        return None


__all__ = ["PlanningService", "PlanningState"]
