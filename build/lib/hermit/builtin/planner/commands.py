"""Planner plugin backed by task-kernel conversation metadata + artifacts."""
from __future__ import annotations

import re
from typing import Any

from hermit.kernel.store import KernelStore
from hermit.plugin.base import CommandSpec, HookEvent

# Natural-language phrases that signal "I want to execute the plan now".
# Only matched when a plan file already exists to avoid false positives on task descriptions.
_EXECUTE_INTENT_RE = re.compile(
    r"(开始执行|执行吧|按计划执行|确认执行|执行计划|没问题.{0,6}执行|就这样执行"
    r"|go\s+ahead|run\s+the\s+plan|execute\s+the\s+plan|execute\s+it\b|confirm\s+and\s+(run|execute))",
    re.IGNORECASE,
)

_PLAN_MODE_PROMPT = (
    "\n\n<plan_mode>\n"
    "你当前处于规划模式。只读工具（搜索、读取文件等）可正常使用，以便收集制定计划所需的信息；"
    "有副作用的工具（写文件、执行命令、创建定时任务等）已禁用。\n"
    "请输出结构化的执行计划（Markdown 格式），包含：\n"
    "1. 任务概述\n"
    "2. 分步计划（每步说明操作和依据）\n"
    "3. 风险与注意事项\n"
    "可以先用只读工具调研，再输出最终计划；但不要执行任何有副作用的操作。\n"
    "</plan_mode>"
)

_PLANNER_KEY = "planner"


def _store_from_runner(runner: Any) -> KernelStore | None:
    controller = getattr(runner, "task_controller", None)
    return getattr(controller, "store", None)


def _artifact_store_from_runner(runner: Any) -> Any:
    return getattr(getattr(runner, "agent", None), "artifact_store", None)


def _planner_state(store: KernelStore | None, session_id: str) -> dict[str, Any]:
    if store is None:
        return {"mode": False, "plan_artifact_id": None}
    conversation = store.ensure_conversation(session_id, source_channel="chat")
    metadata = dict(conversation.metadata)
    state = metadata.get(_PLANNER_KEY)
    if isinstance(state, dict):
        return {"mode": bool(state.get("mode")), "plan_artifact_id": state.get("plan_artifact_id")}
    return {"mode": False, "plan_artifact_id": None}


def _set_planner_state(
    store: KernelStore | None,
    session_id: str,
    *,
    mode: bool,
    plan_artifact_id: str | None,
) -> None:
    if store is None:
        return
    conversation = store.ensure_conversation(session_id, source_channel="chat")
    metadata = dict(conversation.metadata)
    metadata[_PLANNER_KEY] = {
        "mode": mode,
        "plan_artifact_id": plan_artifact_id,
    }
    store.update_conversation_metadata(session_id, metadata)


def _load_plan_text(store: KernelStore | None, runner: Any, artifact_id: str | None) -> str | None:
    if store is None or not artifact_id:
        return None
    artifact = store.get_artifact(artifact_id)
    artifact_store = _artifact_store_from_runner(runner)
    if artifact is None or artifact_store is None:
        return None
    try:
        return artifact_store.read_text(artifact.uri)
    except OSError:
        return None


def _pre_run_hook(prompt: str, **kwargs: Any) -> str | dict[str, Any]:
    session_id = str(kwargs.get("session_id", ""))
    runner = kwargs.get("runner")
    store = _store_from_runner(runner)
    state = _planner_state(store, session_id)
    if not state["mode"]:
        return prompt

    # Natural-language execution intent: if the user says "开始执行" etc. and a plan
    # already exists, switch transparently to execution mode without needing /plan confirm.
    plan_content = _load_plan_text(store, runner, state["plan_artifact_id"])
    if plan_content and _EXECUTE_INTENT_RE.search(prompt):
        _set_planner_state(store, session_id, mode=False, plan_artifact_id=None)
        return (
            f"<execution_plan>\n{plan_content}\n</execution_plan>\n\n"
            "用户已确认执行。请严格按照以上计划逐步执行。每完成一步，简要报告结果后继续下一步。"
        )

    return {"prompt": prompt + _PLAN_MODE_PROMPT, "readonly_only": True}


def _post_run_hook(result: Any, **kwargs: Any) -> None:
    session_id = str(kwargs.get("session_id", ""))
    runner = kwargs.get("runner")
    store = _store_from_runner(runner)
    state = _planner_state(store, session_id)
    if not state["mode"]:
        return
    text = getattr(result, "text", None)
    if not text:
        return
    artifact_store = _artifact_store_from_runner(runner)
    if store is None or artifact_store is None:
        return
    uri, content_hash = artifact_store.store_text(text, extension="md")
    artifact = store.create_artifact(
        task_id=getattr(result, "task_id", None),
        step_id=getattr(result, "step_id", None),
        kind="plan",
        uri=uri,
        content_hash=content_hash,
        producer="planner",
        retention_class="task",
        trust_tier="derived",
        metadata={"conversation_id": session_id},
    )
    _set_planner_state(store, session_id, mode=True, plan_artifact_id=artifact.artifact_id)


def _cmd_plan(runner: Any, session_id: str, text: str) -> Any:
    from hermit.core.runner import DispatchResult

    store = _store_from_runner(runner)
    state = _planner_state(store, session_id)
    parts = text.strip().split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""

    if subcommand == "off":
        _set_planner_state(store, session_id, mode=False, plan_artifact_id=None)
        return DispatchResult("规划模式已关闭，所有工具已恢复。", is_command=True)

    if subcommand == "confirm":
        plan_content = _load_plan_text(store, runner, state["plan_artifact_id"])
        if not plan_content:
            return DispatchResult(
                "没有可执行的计划文件。请先在规划模式下发送任务生成计划，再使用 /plan confirm。",
                is_command=True,
            )
        _set_planner_state(store, session_id, mode=False, plan_artifact_id=None)

        execution_prompt = (
            f"<execution_plan>\n{plan_content}\n</execution_plan>\n\n"
            "请严格按照以上计划逐步执行。每完成一步，简要报告结果后继续下一步。"
        )
        result = runner.handle(session_id, execution_prompt)
        return DispatchResult(
            text=result.text or "",
            is_command=False,
            agent_result=result,
        )

    if state["mode"]:
        artifact = store.get_artifact(state["plan_artifact_id"]) if store and state["plan_artifact_id"] else None
        plan_path_str = artifact.uri if artifact is not None else "（尚未生成）"
        return DispatchResult(
            f"规划模式已开启。\n计划文件：{plan_path_str}\n\n"
            '发送任务即可生成计划；计划生成后，说"开始执行"或 /plan confirm 均可启动执行，/plan off 退出。',
            is_command=True,
        )

    _set_planner_state(store, session_id, mode=True, plan_artifact_id=None)
    plans_dir = getattr(getattr(runner, "agent", None), "artifact_store", None)
    plans_hint = getattr(plans_dir, "root_dir", "kernel artifact store")
    return DispatchResult(
        f"已进入规划模式。只读工具（搜索、读文件等）仍可使用，有副作用的操作已禁用。\n"
        f"计划将保存至 {plans_hint}\n\n"
        "发送你的任务，我将调研后输出结构化计划但不执行任何写操作。\n"
        '计划生成后，直接说"开始执行"或使用 /plan confirm 均可启动执行，/plan off 退出规划模式。',
        is_command=True,
    )


def register(ctx: Any) -> None:
    ctx.add_hook(HookEvent.PRE_RUN, _pre_run_hook, priority=100)
    ctx.add_hook(HookEvent.POST_RUN, _post_run_hook, priority=100)
    ctx.add_command(CommandSpec(
        name="/plan",
        help_text="进入/退出规划模式；/plan off 退出；/plan confirm 按计划执行",
        handler=_cmd_plan,
    ))
