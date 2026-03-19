from __future__ import annotations

from hermit.runtime.capability.contracts.base import HookEvent, PluginContext


def register(ctx: PluginContext) -> None:
    ctx.add_hook(HookEvent.SYSTEM_PROMPT, _inject_instructions, priority=50)


def _inject_instructions() -> str:
    orchestrator_section = (
        "<orchestrator>\n"
        "You can use delegate_* tools to delegate specialized sub-tasks to expert subagents.\n"
        "Use delegation when a task requires deep domain expertise "
        "or would benefit from focused attention.\n"
        "</orchestrator>"
    )

    subtask_spawning_section = (
        "<subtask_spawning>\n"
        "You can spawn kernel-managed subtasks (child steps) when a goal benefits from\n"
        "parallel or sequential decomposition within the current task. This is distinct\n"
        "from delegate_* subagent delegation: subtask steps are tracked in the kernel\n"
        "ledger, support join barriers, and produce observable artifacts.\n"
        "\n"
        "Subtask spawning lifecycle:\n"
        "1. SUBTASK_SPAWN — fires when the kernel creates a child step under the current task.\n"
        "   Plugins listening to this event receive the parent_task_id, child_step_id, and\n"
        "   the step kind ('execute', 'research', 'code', etc.).\n"
        "2. SUBTASK_COMPLETE — fires when a child step reaches a terminal state (succeeded,\n"
        "   failed, or cancelled). Plugins receive the child_step_id, status, and result.\n"
        "\n"
        "Join strategies available when spawning multiple child steps:\n"
        "- all_required: parent resumes only after every child step succeeds.\n"
        "- any_success: parent resumes as soon as one child step succeeds.\n"
        "- best_effort: parent resumes after all children settle, even if some fail.\n"
        "\n"
        "Use subtask spawning when:\n"
        "- The task can be split into independent parallel workstreams.\n"
        "- You need durable, retryable, kernel-tracked steps rather than ephemeral calls.\n"
        "- The parent step must wait on child results before continuing (join barrier).\n"
        "</subtask_spawning>"
    )

    return "\n\n".join([orchestrator_section, subtask_spawning_section])
