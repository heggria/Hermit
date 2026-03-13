"""Planner plugin backed by task-kernel conversation metadata + artifacts."""
from __future__ import annotations

import re
from typing import Any

from hermit.i18n import resolve_locale, tr
from hermit.kernel.store import KernelStore
from hermit.plugin.base import CommandSpec, HookEvent

# Natural-language phrases that signal "I want to execute the plan now".
# Only matched when a plan file already exists to avoid false positives on task descriptions.
_EXECUTE_INTENT_RE = re.compile(
    r"(开始执行|执行吧|按计划执行|确认执行|执行计划|没问题.{0,6}执行|就这样执行"
    r"|go\s+ahead|run\s+the\s+plan|execute\s+the\s+plan|execute\s+it\b|confirm\s+and\s+(run|execute))",
    re.IGNORECASE,
)

_PLANNER_KEY = "planner"


def _locale_for_runner(runner: Any = None) -> str:
    settings = getattr(getattr(runner, "pm", None), "settings", None)
    return resolve_locale(getattr(settings, "locale", None))


def _t(
    message_key: str,
    *,
    runner: Any = None,
    locale: str | None = None,
    default: str | None = None,
    **kwargs: object,
) -> str:
    return tr(message_key, locale=locale or _locale_for_runner(runner), default=default, **kwargs)


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
        return _t(
            "kernel.planner.execute_confirmed_prompt",
            runner=runner,
            plan_content=plan_content,
        )

    return {
        "prompt": prompt + _t("kernel.planner.mode.prompt", runner=runner),
        "readonly_only": True,
    }


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
        return DispatchResult(_t("kernel.planner.closed", runner=runner), is_command=True)

    if subcommand == "confirm":
        plan_content = _load_plan_text(store, runner, state["plan_artifact_id"])
        if not plan_content:
            return DispatchResult(
                _t("kernel.planner.confirm_missing_plan", runner=runner),
                is_command=True,
            )
        _set_planner_state(store, session_id, mode=False, plan_artifact_id=None)

        execution_prompt = _t(
            "kernel.planner.execution_prompt",
            runner=runner,
            plan_content=plan_content,
        )
        result = runner.handle(session_id, execution_prompt)
        return DispatchResult(
            text=result.text or "",
            is_command=False,
            agent_result=result,
        )

    if state["mode"]:
        artifact = store.get_artifact(state["plan_artifact_id"]) if store and state["plan_artifact_id"] else None
        plan_path_str = artifact.uri if artifact is not None else _t("kernel.planner.plan_path.pending", runner=runner)
        return DispatchResult(
            _t("kernel.planner.status", runner=runner, plan_path=plan_path_str),
            is_command=True,
        )

    _set_planner_state(store, session_id, mode=True, plan_artifact_id=None)
    plans_dir = getattr(getattr(runner, "agent", None), "artifact_store", None)
    plans_hint = getattr(plans_dir, "root_dir", "kernel artifact store")
    return DispatchResult(
        _t("kernel.planner.entered", runner=runner, plans_hint=plans_hint),
        is_command=True,
    )


def register(ctx: Any) -> None:
    ctx.add_hook(HookEvent.PRE_RUN, _pre_run_hook, priority=100)
    ctx.add_hook(HookEvent.POST_RUN, _post_run_hook, priority=100)
    ctx.add_command(CommandSpec(
        name="/plan",
        help_text="kernel.planner.command.help",
        handler=_cmd_plan,
    ))
