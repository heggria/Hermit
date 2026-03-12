from __future__ import annotations

import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TaskExecutionContext:
    conversation_id: str
    task_id: str
    step_id: str
    step_attempt_id: str
    source_channel: str
    actor: str = "user"
    policy_profile: str = "default"
    workspace_root: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "step_attempt_id": self.step_attempt_id,
            "source_channel": self.source_channel,
            "actor": self.actor,
            "policy_profile": self.policy_profile,
            "workspace_root": self.workspace_root,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskExecutionContext":
        return cls(
            conversation_id=str(data["conversation_id"]),
            task_id=str(data["task_id"]),
            step_id=str(data["step_id"]),
            step_attempt_id=str(data["step_attempt_id"]),
            source_channel=str(data.get("source_channel", "unknown")),
            actor=str(data.get("actor", "user")),
            policy_profile=str(data.get("policy_profile", "default")),
            workspace_root=str(data.get("workspace_root", "")),
            created_at=float(data.get("created_at", time.time())),
        )


def capture_execution_environment(*, cwd: Path) -> dict[str, Any]:
    return {
        "cwd": str(cwd),
        "os": platform.platform(),
        "python": sys.version,
        "platform": sys.platform,
    }
