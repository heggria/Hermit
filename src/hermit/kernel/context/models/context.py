from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hermit.kernel.authority.workspaces import (
    capture_execution_environment as capture_workspace_environment,
)


@dataclass
class TaskExecutionContext:
    conversation_id: str
    task_id: str
    step_id: str
    step_attempt_id: str
    source_channel: str
    actor_principal_id: str = "principal_user"
    policy_profile: str = "default"
    workspace_root: str = ""
    ingress_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    deadline: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "step_attempt_id": self.step_attempt_id,
            "source_channel": self.source_channel,
            "actor_principal_id": self.actor_principal_id,
            "policy_profile": self.policy_profile,
            "workspace_root": self.workspace_root,
            "ingress_metadata": dict(self.ingress_metadata),
            "created_at": self.created_at,
            "deadline": self.deadline,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskExecutionContext:
        required_fields = ("conversation_id", "task_id", "step_id", "step_attempt_id")
        for field_name in required_fields:
            if field_name not in data:
                raise ValueError(
                    f"TaskExecutionContext.from_dict: missing required field {field_name!r}"
                )
        return cls(
            conversation_id=str(data["conversation_id"]),
            task_id=str(data["task_id"]),
            step_id=str(data["step_id"]),
            step_attempt_id=str(data["step_attempt_id"]),
            source_channel=str(data.get("source_channel", "unknown")),
            actor_principal_id=str(
                data.get("actor_principal_id", data.get("actor", "principal_user"))
            ),
            policy_profile=str(data.get("policy_profile", "default")),
            workspace_root=str(data.get("workspace_root", "")),
            ingress_metadata=dict(data.get("ingress_metadata", {}) or {}),
            created_at=float(data.get("created_at", time.time())),
            deadline=float(data["deadline"]) if data.get("deadline") is not None else None,
        )

    def deadline_exceeded(self) -> bool:
        """Return True if the deadline has passed."""
        return self.deadline is not None and time.time() > self.deadline


@dataclass
class WorkingStateSnapshot:
    goal_summary: str = ""
    open_loops: list[str] = field(default_factory=list)
    active_constraints: list[str] = field(default_factory=list)
    pending_approvals: list[str] = field(default_factory=list)
    recent_results: list[str] = field(default_factory=list)
    planning_mode: bool = False
    candidate_plan_refs: list[str] = field(default_factory=list)
    selected_plan_ref: str = ""
    plan_status: str = "none"

    def __post_init__(self) -> None:
        self.goal_summary = self.goal_summary[:400]
        self.open_loops = [item[:200] for item in self.open_loops[:8]]
        self.active_constraints = [item[:200] for item in self.active_constraints[:8]]
        self.pending_approvals = [item[:200] for item in self.pending_approvals[:8]]
        self.recent_results = [item[:200] for item in self.recent_results[:8]]
        self.candidate_plan_refs = [str(item)[:200] for item in self.candidate_plan_refs[:8]]
        self.selected_plan_ref = self.selected_plan_ref[:200]
        self.plan_status = self.plan_status[:64] or "none"


@dataclass
class CompiledProviderInput:
    messages: list[dict[str, Any]] = field(default_factory=list)
    context_pack_ref: str | None = None
    ingress_artifact_refs: list[str] = field(default_factory=list)
    session_projection_ref: str | None = None
    source_mode: str = "compiled"


def capture_execution_environment(*, cwd: Path) -> dict[str, object]:
    return capture_workspace_environment(cwd=cwd)
