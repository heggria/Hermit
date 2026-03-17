from __future__ import annotations

from hermit.runtime.capability.contracts.base import HookEvent, PluginContext


def register(ctx: PluginContext) -> None:
    ctx.add_hook(HookEvent.SYSTEM_PROMPT, _inject_instructions, priority=50)


def _inject_instructions() -> str:
    return (
        "<orchestrator>\n"
        "You can use delegate_* tools to delegate specialized sub-tasks to expert subagents.\n"
        "Use delegation when a task requires deep domain expertise "
        "or would benefit from focused attention.\n"
        "</orchestrator>"
    )
