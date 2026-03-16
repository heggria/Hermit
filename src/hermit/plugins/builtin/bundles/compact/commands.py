"""Compact command plugin: refresh conversation projection artifacts."""

from __future__ import annotations

from typing import Any

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.kernel.task.projections.conversation import ConversationProjectionService
from hermit.runtime.capability.contracts.base import CommandSpec


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


def _do_compact(runner: Any, session_id: str) -> tuple[bool, str]:
    store = getattr(getattr(runner, "task_controller", None), "store", None)
    if store is None:
        return False, _t("kernel.compact.empty", runner=runner)
    payload = ConversationProjectionService(
        store, getattr(runner.agent, "artifact_store", None)
    ).rebuild(session_id)
    preview = str(payload.get("summary", "") or "").strip()
    return True, _t(
        "kernel.compact.success",
        runner=runner,
        original_count=max(1, len(payload.get("latest_artifact_refs", []) or [])),
        preview=preview[:200] + ("…" if len(preview) > 200 else ""),
    )


def _cmd_compact(runner: Any, session_id: str, _text: str) -> Any:
    from hermit.runtime.control.runner.runner import DispatchResult

    _success, msg = _do_compact(runner, session_id)
    return DispatchResult(msg, is_command=True)


def register(ctx: Any) -> None:
    ctx.add_command(
        CommandSpec(
            name="/compact",
            help_text="kernel.compact.command.help",
            handler=_cmd_compact,
        )
    )
